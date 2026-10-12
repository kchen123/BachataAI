"""API routes for pose analysis — DB-backed queue with parallel workers."""

import os
import json
import time
import threading
import requests
import google.auth.transport.requests
import google.oauth2.id_token
from flask import Blueprint, request, jsonify, Response
from core.adapters.repository import get_repo, Repository
from core.adapters.analyses import AnalysesAdapter
from core.adapters.videos import VideosAdapter
from core.adapters.storage import BUCKET_NAME, _get_client as _gcs

analysis_bp = Blueprint("analysis", __name__)

POSE_SERVICE_URL = os.environ.get("POSE_SERVICE_URL", "http://localhost:8080")
MAX_WORKERS = int(os.environ.get("ANALYSIS_MAX_WORKERS", "4"))

# Track which video_ids have been requested for cancellation
_cancel = set()
_cancel_lock = threading.Lock()

# Worker semaphore — limits concurrent GPU jobs
_worker_sem = threading.Semaphore(MAX_WORKERS)
_queue_event = threading.Event()


def _get_auth_headers():
    if POSE_SERVICE_URL.startswith("http://localhost"):
        return {}
    auth_req = google.auth.transport.requests.Request()
    token = google.oauth2.id_token.fetch_id_token(auth_req, POSE_SERVICE_URL)
    return {"Authorization": f"Bearer {token}"}


def _log(repo, analysis_id, msg, pct=None):
    """Append a log line and update progress in DB."""
    aa = AnalysesAdapter(repo)
    row = aa.get(analysis_id)
    progress = row.get("progress") or {}
    if isinstance(progress, str):
        progress = json.loads(progress)
    log = progress.get("log", [])
    log.append(msg)
    aa.set_progress(analysis_id, pct if pct is not None else progress.get("percent", 0), msg, log)


def _is_cancelled(video_id):
    with _cancel_lock:
        return video_id in _cancel


def _run_job(analysis_id, video_id, gcs_blob, frame_count, sample_every_n, resolution):
    """Execute a single analysis job. Called by worker thread."""
    repo = Repository()
    try:
        aa = AnalysesAdapter(repo)
        progress_path = f"progress/{video_id}.json"
        aa.set_progress(analysis_id, 0, "Starting GPU...", ["Starting GPU..."])

        for attempt in range(10):
            resp = requests.post(
                f"{POSE_SERVICE_URL}/predict/video",
                json={"gcs_bucket": BUCKET_NAME, "gcs_blob": gcs_blob,
                      "sample_every_n": sample_every_n, "resolution": resolution,
                      "progress_path": progress_path},
                headers=_get_auth_headers(),
                timeout=3600,
            )
            if resp.status_code == 429:
                wait = 15 * (attempt + 1)
                _log(repo, analysis_id,
                     f"GPU busy — retrying in {wait}s (attempt {attempt+1}/10)")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        else:
            raise RuntimeError("GPU service busy after 10 retries")

        if _is_cancelled(video_id):
            raise RuntimeError("Cancelled by user")

        _log(repo, analysis_id, "GPU complete, saving results...", 50)

        pose_gcs_path = f"poses/{video_id}.json"
        bucket = _gcs().bucket(BUCKET_NAME)
        blob = bucket.blob(pose_gcs_path)
        blob.upload_from_string(resp.content, content_type="application/json")
        blob.make_public()

        _log(repo, analysis_id,
             f"Pose data saved ({len(resp.content) // 1024}KB)", 90)

        if _is_cancelled(video_id):
            raise RuntimeError("Cancelled by user")

        content_size = len(resp.content)
        summary = {
            "total_frames": frame_count,
            "pose_gcs_path": pose_gcs_path,
            "pose_size_kb": content_size // 1024,
            "sample_every_n": sample_every_n,
            "resolution": resolution,
        }
        aa.set_summary(analysis_id, summary)

    except Exception as e:
        cancelled = _is_cancelled(video_id)
        with _cancel_lock:
            _cancel.discard(video_id)
        try:
            aa = AnalysesAdapter(repo)
            if cancelled:
                try:
                    _gcs().bucket(BUCKET_NAME).blob(f"poses/{video_id}.json").delete()
                except Exception:
                    pass
                repo.execute("DELETE FROM bachata.analyses WHERE id = %s", [analysis_id])
            else:
                aa.set_status(analysis_id, "error")
                aa.set_progress(analysis_id, 0, str(e), [str(e)])
        except Exception:
            pass
    finally:
        repo.close()
        _worker_sem.release()
        # Signal queue thread to check for more work
        _queue_event.set()


def _queue_loop():
    """Background thread: poll DB for queued jobs, dispatch to workers."""
    while True:
        _queue_event.wait(timeout=5)
        _queue_event.clear()

        while True:
            if not _worker_sem.acquire(timeout=1):
                break

            try:
                with Repository() as repo:
                    aa = AnalysesAdapter(repo)
                    job = aa.next_queued()
                    if not job:
                        _worker_sem.release()
                        break

                    video = VideosAdapter(repo).get(job["video_id"])

                    if not video or not video.get("filename"):
                        aa.set_status(job["id"], "error")
                        _worker_sem.release()
                        continue

                    # Mark as running before spawning thread to prevent double-pick
                    aa.set_status(job["id"], "running")

                    progress = job.get("progress") or {}
                    if isinstance(progress, str):
                        progress = json.loads(progress)
                    sample_every_n = progress.get("sample_every_n", 2)
                    resolution = progress.get("resolution", 640)

                thread = threading.Thread(
                    target=_run_job,
                    args=(job["id"], job["video_id"], video["filename"],
                          video.get("frame_count", 0), sample_every_n, resolution),
                    daemon=True,
                )
                thread.start()

            except Exception:
                _worker_sem.release()
                break


def start_queue_worker():
    """Start the queue polling thread. Called once from app startup."""
    t = threading.Thread(target=_queue_loop, daemon=True, name="analysis-queue")
    t.start()


# ── API Routes ────────────────────────────────────────────────────────────────

@analysis_bp.route("/api/videos/<int:video_id>/analyze", methods=["POST"])
def api_run_analysis(video_id):
    repo = get_repo()
    video = VideosAdapter(repo).get(video_id)
    if not video:
        return jsonify({"error": "video not found"}), 404
    if not video.get("filename"):
        return jsonify({"error": "video has no file"}), 400

    existing = AnalysesAdapter(repo).get_by_video(video_id)
    if existing and existing["status"] in ("queued", "running"):
        return jsonify({"error": "analysis already in progress"}), 409

    # Delete old analyses
    aa = AnalysesAdapter(repo)
    old = aa.select(video_id=video_id)
    for o in old:
        try:
            s = o.get("summary")
            if isinstance(s, str):
                s = json.loads(s)
            if s and s.get("pose_gcs_path"):
                _gcs().bucket(BUCKET_NAME).blob(s["pose_gcs_path"]).delete()
        except Exception:
            pass
        aa.delete(o["id"])

    body = request.get_json(silent=True) or {}
    sample_every_n = body.get("sample_every_n", 2)
    resolution = body.get("resolution", 640)

    analysis = aa.create(video_id=video_id, model="yolo26x-pose")
    repo.update("bachata.analyses", analysis["id"], {
        "status": "queued",
        "progress": json.dumps({
            "percent": 0, "step": "Queued", "log": ["Queued"],
            "sample_every_n": sample_every_n, "resolution": resolution,
        }),
    })

    # Wake the queue thread
    _queue_event.set()

    return jsonify({"status": "queued", "analysis_id": analysis["id"]}), 202


@analysis_bp.route("/api/videos/<int:video_id>/analyze", methods=["DELETE"])
def api_cancel_analysis(video_id):
    repo = get_repo()
    aa = AnalysesAdapter(repo)
    analysis = aa.get_by_video(video_id)
    if analysis and analysis["status"] == "queued":
        # Not yet running — just delete it
        aa.delete(analysis["id"])
        return jsonify({"status": "cancelled"}), 200
    elif analysis and analysis["status"] == "running":
        with _cancel_lock:
            _cancel.add(video_id)
        return jsonify({"status": "cancelling"}), 200
    return jsonify({"status": "nothing to cancel"}), 200


@analysis_bp.route("/api/videos/<int:video_id>/progress", methods=["GET"])
def api_get_progress(video_id):
    repo = get_repo()
    analysis = AnalysesAdapter(repo).get_by_video(video_id)
    if not analysis:
        return jsonify({"status": "idle", "percent": 0})
    progress = AnalysesAdapter(repo).get_progress(analysis["id"])

    # If running, check GCS for GPU-side progress
    if progress.get("status") == "running" and progress.get("percent", 0) < 50:
        try:
            blob = _gcs().bucket(BUCKET_NAME).blob(f"progress/{video_id}.json")
            if blob.exists():
                import json as _json
                gpu_progress = _json.loads(blob.download_as_bytes())
                gpu_pct = gpu_progress.get("percent", 0)
                processed = gpu_progress.get("processed", 0)
                total = gpu_progress.get("total", 0)
                if gpu_pct > 0:
                    progress["percent"] = gpu_pct
                    progress["step"] = f"GPU analyzing: {processed}/{total} frames ({gpu_pct}%)"
                    if "log" in progress:
                        progress["log"].append(progress["step"])
        except Exception:
            pass

    return jsonify(progress)


@analysis_bp.route("/api/videos/<int:video_id>/analysis", methods=["GET"])
def api_get_analysis(video_id):
    repo = get_repo()
    analysis = AnalysesAdapter(repo).get_by_video(video_id)
    if analysis is None:
        return jsonify({"error": "no analysis found"}), 404
    for k in ("created_at", "updated_at"):
        if analysis.get(k):
            analysis[k] = str(analysis[k])
    if analysis.get("summary") and isinstance(analysis["summary"], str):
        analysis["summary"] = json.loads(analysis["summary"])
    video = VideosAdapter(repo).get(video_id)
    if video:
        analysis["fps"] = video.get("fps", 30)
    if analysis.get("summary") and analysis["summary"].get("pose_gcs_path"):
        path = analysis["summary"]["pose_gcs_path"]
        analysis["pose_url"] = f"https://storage.googleapis.com/{BUCKET_NAME}/{path}"
    return jsonify(analysis)


@analysis_bp.route("/api/videos/<int:video_id>/pose-data", methods=["GET"])
def api_get_pose_data(video_id):
    """Proxy pose data from GCS to avoid CORS issues."""
    repo = get_repo()
    analysis = AnalysesAdapter(repo).get_by_video(video_id)
    if not analysis or analysis["status"] != "done":
        return jsonify({"error": "no pose data"}), 404
    summary = analysis.get("summary")
    if isinstance(summary, str):
        summary = json.loads(summary)
    path = summary.get("pose_gcs_path") if summary else None
    if not path:
        return jsonify({"error": "no pose file"}), 404
    blob = _gcs().bucket(BUCKET_NAME).blob(path)
    data = blob.download_as_bytes()
    return Response(data, content_type="application/json")

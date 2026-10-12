"""Pose analysis API — clean stateless design.

Single source of truth: GCS progress file.
DB is only updated when progress shows "done".

Flow:
1. POST /analyze → delete old data, create DB record, send request to GPU, return
2. GPU processes video, writes progress to GCS every 200 frames
3. GET /progress → read GCS progress file, sync DB if done
4. DELETE /analyze → write "cancelled" to GCS, GPU checks and stops
5. GET /pose-data → stream pose JSON from GCS

No background threads. No race conditions. No stuck jobs.
"""

import os
import json
import threading
import requests
from datetime import datetime, timezone, timedelta
import google.auth.transport.requests
import google.oauth2.id_token
from flask import Blueprint, request, jsonify, Response
from core.adapters.repository import get_repo
from core.adapters.analyses import AnalysesAdapter
from core.adapters.videos import VideosAdapter
from core.adapters.storage import BUCKET_NAME, _get_client as _gcs

analysis_bp = Blueprint("analysis", __name__)

POSE_SERVICE_URL = os.environ.get("POSE_SERVICE_URL", "http://localhost:8080")


def _auth_headers():
    if POSE_SERVICE_URL.startswith("http://localhost"):
        return {}
    try:
        auth_req = google.auth.transport.requests.Request()
        token = google.oauth2.id_token.fetch_id_token(auth_req, POSE_SERVICE_URL)
        return {"Authorization": f"Bearer {token}"}
    except Exception:
        # Fallback for local dev — use gcloud CLI
        import subprocess, shutil
        gcloud_path = shutil.which("gcloud") or "gcloud"
        token = subprocess.check_output(
            [gcloud_path, "auth", "print-identity-token"], text=True, shell=True
        ).strip()
        return {"Authorization": f"Bearer {token}"}


def _read_progress(video_id):
    """Read progress from GCS. Returns dict or None."""
    try:
        # Use fresh client call to avoid stale reads
        bucket = _gcs().bucket(BUCKET_NAME)
        blob = bucket.blob(f"progress/{video_id}.json")
        blob.reload()  # Force fetch latest metadata
        return json.loads(blob.download_as_bytes(if_generation_match=blob.generation))
    except Exception:
        pass
    return None


def _delete_progress(video_id):
    """Delete progress file from GCS."""
    try:
        blob = _gcs().bucket(BUCKET_NAME).blob(f"progress/{video_id}.json")
        if blob.exists():
            blob.delete()
    except Exception:
        pass


def _delete_pose_file(video_id):
    """Delete pose data file from GCS."""
    try:
        blob = _gcs().bucket(BUCKET_NAME).blob(f"poses/{video_id}.json")
        if blob.exists():
            blob.delete()
    except Exception:
        pass


def _sync_db_if_done(video_id):
    """If GCS says done, make sure DB matches."""
    prog = _read_progress(video_id)
    if not prog or prog.get("status") != "done":
        return
    repo = get_repo()
    aa = AnalysesAdapter(repo)
    analysis = aa.get_by_video(video_id)
    if analysis and analysis["status"] != "done":
        summary = {
            "pose_gcs_path": prog.get("pose_gcs_path", f"poses/{video_id}.json"),
            "pose_size_kb": prog.get("pose_size_kb", 0),
        }
        aa.set_summary(analysis["id"], summary)


# ── Main Endpoints ──────────────────────────────────────────────────────────


@analysis_bp.route("/api/videos/<int:video_id>/analyze", methods=["POST"])
def api_run_analysis(video_id):
    repo = get_repo()
    video = VideosAdapter(repo).get(video_id)
    if not video:
        return jsonify({"error": "video not found"}), 404
    if not video.get("filename"):
        return jsonify({"error": "video has no file"}), 400

    # Check if GPU is already processing this video
    prog = _read_progress(video_id)
    if prog and prog.get("status") == "running":
        return jsonify({"error": "analysis already running"}), 409

    # Clean slate: delete old progress, pose file, cancel flag, and DB records
    _delete_progress(video_id)
    _delete_pose_file(video_id)
    try:
        _gcs().bucket(BUCKET_NAME).blob(f"cancel/{video_id}").delete()
    except Exception:
        pass

    aa = AnalysesAdapter(repo)
    for old in aa.select(video_id=video_id):
        aa.delete(old["id"])

    # Create fresh DB record
    body = request.get_json(silent=True) or {}
    sample_every_n = body.get("sample_every_n", 2)
    resolution = body.get("resolution", 640)

    analysis = aa.create(video_id=video_id, model="yolo26x-pose")
    aa.set_status(analysis["id"], "running")

    # Send to GPU — fire and forget via thread
    # GPU blocks synchronously (keeps its container alive)
    # Flask doesn't need the response — progress is in GCS
    def _send():
        try:
            requests.post(
                f"{POSE_SERVICE_URL}/analyze",
                json={
                    "gcs_bucket": BUCKET_NAME,
                    "gcs_blob": video["filename"],
                    "video_id": video_id,
                    "sample_every_n": sample_every_n,
                    "resolution": resolution,
                },
                headers=_auth_headers(),
                timeout=3600,
            )
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()

    return jsonify({"status": "started", "analysis_id": analysis["id"]}), 202


@analysis_bp.route("/api/videos/<int:video_id>/analyze", methods=["DELETE"])
def api_cancel_analysis(video_id):
    # Write separate cancel flag file — GPU checks this every 200 frames
    try:
        blob = _gcs().bucket(BUCKET_NAME).blob(f"cancel/{video_id}")
        blob.upload_from_string("1", content_type="text/plain")
    except Exception:
        pass

    # Clean up: delete DB record, pose file
    repo = get_repo()
    aa = AnalysesAdapter(repo)
    for a in aa.select(video_id=video_id):
        if a["status"] != "done":
            aa.delete(a["id"])
    _delete_pose_file(video_id)

    return jsonify({"status": "cancelled"}), 200


@analysis_bp.route("/api/videos/<int:video_id>/progress", methods=["GET"])
def api_get_progress(video_id):
    # GCS is the single source of truth for progress
    prog = _read_progress(video_id)

    if prog:
        # Sync DB when GPU finishes
        if prog["status"] == "done":
            _sync_db_if_done(video_id)
        return jsonify(prog)

    # No GCS progress — check DB
    repo = get_repo()
    analysis = AnalysesAdapter(repo).get_by_video(video_id)
    if analysis:
        if analysis["status"] == "done":
            return jsonify({"status": "done", "percent": 100, "step": "Complete"})
        if analysis["status"] == "running":
            # DB says running but no GCS progress — GPU hasn't started yet or died
            # Check if it's been too long (30 min timeout)
            created = analysis.get("created_at")
            if created and (datetime.now(timezone.utc) - created) > timedelta(minutes=30):
                AnalysesAdapter(repo).set_status(analysis["id"], "error")
                return jsonify({"status": "error", "percent": 0, "step": "Timed out — GPU never responded"})
            return jsonify({"status": "running", "percent": 0, "step": "Waiting for GPU..."})
        if analysis["status"] == "error":
            return jsonify({"status": "error", "percent": 0, "step": "Error"})

    return jsonify({"status": "idle", "percent": 0})


@analysis_bp.route("/api/videos/<int:video_id>/analysis", methods=["GET"])
def api_get_analysis(video_id):
    # Sync first in case GPU just finished
    _sync_db_if_done(video_id)

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

    return jsonify(analysis)


@analysis_bp.route("/api/videos/<int:video_id>/pose-data", methods=["GET"])
def api_get_pose_data(video_id):
    """Stream pose data from GCS."""
    # Sync DB first
    _sync_db_if_done(video_id)

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
    if not blob.exists():
        return jsonify({"error": "pose file missing from storage"}), 404

    def stream():
        with blob.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk

    return Response(stream(), content_type="application/json")


# ── Admin ────────────────────────────────────────────────────────────────────


@analysis_bp.route("/api/admin/jobs", methods=["GET"])
def api_list_jobs():
    repo = get_repo()
    analyses = AnalysesAdapter(repo).select(order_by="id DESC")
    result = []
    for a in analyses:
        video = VideosAdapter(repo).get(a["video_id"])
        for k in ("created_at", "updated_at"):
            if a.get(k):
                a[k] = str(a[k])
        if a.get("summary") and isinstance(a["summary"], str):
            a["summary"] = json.loads(a["summary"])

        # Get live progress from GCS
        gcs_progress = _read_progress(a["video_id"])

        result.append({
            "id": a["id"],
            "video_id": a["video_id"],
            "video_title": video["title"] if video else "?",
            "status": gcs_progress["status"] if gcs_progress else a["status"],
            "model": a.get("model"),
            "created_at": a.get("created_at"),
            "summary": a.get("summary"),
            "gcs_progress": gcs_progress,
        })
    return jsonify(result)


@analysis_bp.route("/api/admin/jobs/<int:analysis_id>", methods=["DELETE"])
def api_delete_job(analysis_id):
    repo = get_repo()
    aa = AnalysesAdapter(repo)
    analysis = aa.get(analysis_id)
    if not analysis:
        return jsonify({"error": "not found"}), 404

    video_id = analysis["video_id"]
    _delete_progress(video_id)
    _delete_pose_file(video_id)
    aa.delete(analysis_id)

    return jsonify({"status": "deleted", "id": analysis_id}), 200


@analysis_bp.route("/api/admin/jobs/cleanup", methods=["POST"])
def api_cleanup_jobs():
    repo = get_repo()
    aa = AnalysesAdapter(repo)
    from datetime import datetime, timezone, timedelta
    stuck = aa.select(status="running")
    cleaned = 0
    for a in stuck:
        prog = _read_progress(a["video_id"])
        if prog and prog.get("status") == "running":
            # Check age — if older than 30 min, it's dead
            created = a.get("created_at")
            if created and (datetime.now(timezone.utc) - created) < timedelta(minutes=30):
                continue  # Actually still running
        _delete_progress(a["video_id"])
        aa.set_status(a["id"], "error")
        cleaned += 1

    return jsonify({"cleaned": cleaned})

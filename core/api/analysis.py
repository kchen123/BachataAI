"""API routes for pose analysis — trigger, progress, results."""

import os
import json
import math
import threading
import requests
import google.auth.transport.requests
import google.oauth2.id_token
from google.cloud import storage
from flask import Blueprint, request, jsonify
from core.adapters.repository import get_repo, Repository
from core.adapters.analyses import AnalysesAdapter
from core.adapters.videos import VideosAdapter

analysis_bp = Blueprint("analysis", __name__)

POSE_SERVICE_URL = os.environ.get("POSE_SERVICE_URL", "http://localhost:8080")
GCS_BUCKET = "bachataai-videos"

# In-memory progress tracking
_progress = {}
_cancel = set()

_gcs_client = None


def _gcs():
    global _gcs_client
    if _gcs_client is None:
        _gcs_client = storage.Client()
    return _gcs_client


def _get_auth_headers():
    if POSE_SERVICE_URL.startswith("http://localhost"):
        return {}
    auth_req = google.auth.transport.requests.Request()
    token = google.oauth2.id_token.fetch_id_token(auth_req, POSE_SERVICE_URL)
    return {"Authorization": f"Bearer {token}"}


ANGLE_DEFS = {
    "left_elbow": (5, 7, 9), "right_elbow": (6, 8, 10),
    "left_shoulder": (7, 5, 11), "right_shoulder": (8, 6, 12),
    "left_hip": (5, 11, 13), "right_hip": (6, 12, 14),
    "left_knee": (11, 13, 15), "right_knee": (12, 14, 16),
}


def _calc_angle(a, b, c):
    ba = (a["x"] - b["x"], a["y"] - b["y"])
    bc = (c["x"] - b["x"], c["y"] - b["y"])
    dot = ba[0] * bc[0] + ba[1] * bc[1]
    m1 = math.sqrt(ba[0]**2 + ba[1]**2)
    m2 = math.sqrt(bc[0]**2 + bc[1]**2)
    if m1 * m2 == 0:
        return 0.0
    return math.degrees(math.acos(max(-1, min(1, dot / (m1 * m2)))))


def _log(video_id, msg, pct=None):
    p = _progress.get(video_id, {"status": "running", "log": [], "percent": 0})
    p["log"].append(msg)
    p["step"] = msg
    if pct is not None:
        p["percent"] = pct
    _progress[video_id] = p


def _run_analysis(video_id, analysis_id, gcs_blob, frame_count, sample_every_n=1, resolution=1280):
    """Background thread: call GPU pose service, save results to GCS."""
    try:
        _progress[video_id] = {"status": "running", "step": "starting", "percent": 0, "log": []}
        _cancel.discard(video_id)
        _log(video_id, f"Config: every {sample_every_n} frame(s), {resolution}px resolution", 0)
        _log(video_id, "Requesting GPU instance...", 2)

        import time
        for attempt in range(10):
            resp = requests.post(
                f"{POSE_SERVICE_URL}/predict/video",
                json={"gcs_bucket": GCS_BUCKET, "gcs_blob": gcs_blob,
                      "sample_every_n": sample_every_n, "resolution": resolution},
                headers=_get_auth_headers(),
                timeout=3600,
            )
            if resp.status_code == 429:
                wait = 15 * (attempt + 1)
                _log(video_id, f"GPU busy — retrying in {wait}s (attempt {attempt+1}/10)")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        else:
            raise RuntimeError("GPU service busy after 10 retries")

        if video_id in _cancel:
            raise RuntimeError("Cancelled by user")

        _log(video_id, "GPU inference complete, downloading results...", 50)

        # Stream response directly to GCS instead of loading all into memory
        pose_gcs_path = f"poses/{video_id}.json"
        bucket = _gcs().bucket(GCS_BUCKET)
        blob = bucket.blob(pose_gcs_path)
        blob.upload_from_string(resp.content, content_type="application/json")
        blob.make_public()

        _log(video_id, f"Pose data saved to GCS ({len(resp.content) // 1024}KB)", 70)

        if video_id in _cancel:
            raise RuntimeError("Cancelled by user")

        # Parse for summary only (angles)
        _log(video_id, "Computing joint angle summary...", 75)
        data = resp.json()
        pose_frames = data["frames"]
        total = len(pose_frames)

        all_angles = {n: [] for n in ANGLE_DEFS}
        for f in pose_frames:
            lm = {int(k): v for k, v in f["keypoints"].items()}
            for name, (a, b, c) in ANGLE_DEFS.items():
                if a in lm and b in lm and c in lm:
                    if lm[a]["visibility"] > 0.3 and lm[b]["visibility"] > 0.3 and lm[c]["visibility"] > 0.3:
                        all_angles[name].append(round(_calc_angle(lm[a], lm[b], lm[c]), 1))

        summary = {}
        for name, vals in all_angles.items():
            if vals:
                summary[name] = {
                    "min": min(vals), "max": max(vals),
                    "avg": round(sum(vals) / len(vals), 1),
                    "range": round(max(vals) - min(vals), 1),
                }
        summary["total_frames"] = data["frame_count"]
        summary["frames_with_pose"] = total
        summary["pose_gcs_path"] = pose_gcs_path

        repo = Repository()
        AnalysesAdapter(repo).set_summary(analysis_id, summary)
        repo.close()

        _log(video_id, f"Complete — {total} frames, {len(summary)-3} joint angles", 100)
        _progress[video_id]["status"] = "done"

    except Exception as e:
        cancelled = video_id in _cancel
        _cancel.discard(video_id)
        try:
            repo = Repository()
            if cancelled:
                # Clean up GCS file and DB record
                try:
                    bucket = _gcs().bucket(GCS_BUCKET)
                    bucket.blob(f"poses/{video_id}.json").delete()
                except Exception:
                    pass
                repo.execute("DELETE FROM bachata.analyses WHERE id = %s", [analysis_id])
            else:
                AnalysesAdapter(repo).set_status(analysis_id, "error")
            repo.close()
        except Exception:
            pass
        if cancelled:
            _progress[video_id] = {"status": "cancelled", "percent": 0, "log": ["Cancelled by user"]}
        else:
            _progress[video_id] = {"status": "error", "error": str(e), "percent": 0}


@analysis_bp.route("/api/videos/<int:video_id>/analyze", methods=["POST"])
def api_run_analysis(video_id):
    repo = get_repo()
    video = VideosAdapter(repo).get(video_id)
    if not video:
        return jsonify({"error": "video not found"}), 404
    if not video.get("filename"):
        return jsonify({"error": "video has no file"}), 400

    existing = AnalysesAdapter(repo).get_by_video(video_id)
    if existing and existing["status"] == "running":
        return jsonify({"error": "analysis already running"}), 409

    # Delete old analyses
    aa = AnalysesAdapter(repo)
    old = aa.select(video_id=video_id)
    for o in old:
        # Delete old GCS pose file
        try:
            s = o.get("summary")
            if isinstance(s, str):
                s = json.loads(s)
            if s and s.get("pose_gcs_path"):
                _gcs().bucket(GCS_BUCKET).blob(s["pose_gcs_path"]).delete()
        except Exception:
            pass
        aa.delete(o["id"])

    body = request.get_json(silent=True) or {}
    sample_every_n = body.get("sample_every_n", 1)
    resolution = body.get("resolution", 1280)

    analysis = aa.create(video_id=video_id, model="yolo26x-pose")
    aa.set_status(analysis["id"], "running")

    thread = threading.Thread(
        target=_run_analysis,
        args=(video_id, analysis["id"], video["filename"], video.get("frame_count", 0),
              sample_every_n, resolution),
        daemon=True,
    )
    thread.start()

    return jsonify({"status": "started", "analysis_id": analysis["id"]}), 202


@analysis_bp.route("/api/videos/<int:video_id>/analyze", methods=["DELETE"])
def api_cancel_analysis(video_id):
    _cancel.add(video_id)
    return jsonify({"status": "cancelling"}), 200


@analysis_bp.route("/api/videos/<int:video_id>/progress", methods=["GET"])
def api_get_progress(video_id):
    p = _progress.get(video_id)
    if p:
        return jsonify(p)
    repo = get_repo()
    analysis = AnalysesAdapter(repo).get_by_video(video_id)
    if analysis and analysis["status"] == "done":
        return jsonify({"status": "done", "percent": 100})
    return jsonify({"status": "idle", "percent": 0})


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
    # Add public URL for pose data
    if analysis.get("summary") and analysis["summary"].get("pose_gcs_path"):
        path = analysis["summary"]["pose_gcs_path"]
        analysis["pose_url"] = f"https://storage.googleapis.com/{GCS_BUCKET}/{path}"
    return jsonify(analysis)


@analysis_bp.route("/api/analyses/<int:analysis_id>/frames", methods=["GET"])
def api_get_frames(analysis_id):
    """Legacy endpoint — returns empty for GCS-based analyses."""
    return jsonify([])

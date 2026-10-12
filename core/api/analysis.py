"""API routes for pose analysis — stateless, all state in GCS.

Flow:
1. POST /api/videos/<id>/analyze → Flask sends request to GPU service, returns immediately
2. GPU service processes video, writes progress to GCS, saves results to GCS
3. GET /api/videos/<id>/progress → Flask reads progress from GCS
4. GET /api/videos/<id>/pose-data → Flask proxies pose data from GCS

Nothing runs in background threads. Nothing gets stuck.
"""

import os
import json
import requests
import google.auth.transport.requests
import google.oauth2.id_token
from flask import Blueprint, request, jsonify, Response
from core.adapters.repository import get_repo
from core.adapters.analyses import AnalysesAdapter
from core.adapters.videos import VideosAdapter
from core.adapters.storage import BUCKET_NAME, _get_client as _gcs

analysis_bp = Blueprint("analysis", __name__)

POSE_SERVICE_URL = os.environ.get("POSE_SERVICE_URL", "http://localhost:8080")


def _get_auth_headers():
    if POSE_SERVICE_URL.startswith("http://localhost"):
        return {}
    auth_req = google.auth.transport.requests.Request()
    token = google.oauth2.id_token.fetch_id_token(auth_req, POSE_SERVICE_URL)
    return {"Authorization": f"Bearer {token}"}


@analysis_bp.route("/api/videos/<int:video_id>/analyze", methods=["POST"])
def api_run_analysis(video_id):
    repo = get_repo()
    video = VideosAdapter(repo).get(video_id)
    if not video:
        return jsonify({"error": "video not found"}), 404
    if not video.get("filename"):
        return jsonify({"error": "video has no file"}), 400

    # Check if already running via GCS progress
    try:
        blob = _gcs().bucket(BUCKET_NAME).blob(f"progress/{video_id}.json")
        if blob.exists():
            prog = json.loads(blob.download_as_bytes())
            if prog.get("status") == "running":
                return jsonify({"error": "analysis already running"}), 409
    except Exception:
        pass

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

    # Create analysis record
    body = request.get_json(silent=True) or {}
    sample_every_n = body.get("sample_every_n", 2)
    resolution = body.get("resolution", 640)

    analysis = aa.create(video_id=video_id, model="yolo26x-pose")
    aa.set_status(analysis["id"], "running")

    # Fire and forget — GPU service handles everything
    try:
        resp = requests.post(
            f"{POSE_SERVICE_URL}/analyze",
            json={
                "gcs_bucket": BUCKET_NAME,
                "gcs_blob": video["filename"],
                "video_id": video_id,
                "sample_every_n": sample_every_n,
                "resolution": resolution,
            },
            headers=_get_auth_headers(),
            timeout=30,  # Just needs to accept the request
        )
        resp.raise_for_status()
    except Exception as e:
        aa.set_status(analysis["id"], "error")
        return jsonify({"error": f"Failed to reach GPU service: {str(e)}"}), 500

    return jsonify({"status": "started", "analysis_id": analysis["id"]}), 202


@analysis_bp.route("/api/videos/<int:video_id>/analyze", methods=["DELETE"])
def api_cancel_analysis(video_id):
    # Write cancel to GCS progress
    try:
        blob = _gcs().bucket(BUCKET_NAME).blob(f"progress/{video_id}.json")
        blob.upload_from_string(
            json.dumps({"status": "cancelled", "percent": 0, "step": "Cancelled"}),
            content_type="application/json")
    except Exception:
        pass

    # Update DB
    repo = get_repo()
    aa = AnalysesAdapter(repo)
    analysis = aa.get_by_video(video_id)
    if analysis:
        aa.set_status(analysis["id"], "error")

    return jsonify({"status": "cancelled"}), 200


@analysis_bp.route("/api/videos/<int:video_id>/progress", methods=["GET"])
def api_get_progress(video_id):
    # Read progress from GCS — single source of truth
    try:
        blob = _gcs().bucket(BUCKET_NAME).blob(f"progress/{video_id}.json")
        if blob.exists():
            prog = json.loads(blob.download_as_bytes())

            # If GPU finished, update DB
            if prog.get("status") == "done":
                repo = get_repo()
                aa = AnalysesAdapter(repo)
                analysis = aa.get_by_video(video_id)
                if analysis and analysis["status"] != "done":
                    summary = {
                        "pose_gcs_path": prog.get("pose_gcs_path", f"poses/{video_id}.json"),
                        "pose_size_kb": prog.get("pose_size_kb", 0),
                    }
                    aa.set_summary(analysis["id"], summary)

            return jsonify(prog)
    except Exception:
        pass

    # Check DB as fallback
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
    if analysis.get("summary") and analysis["summary"].get("pose_gcs_path"):
        path = analysis["summary"]["pose_gcs_path"]
        analysis["pose_url"] = f"https://storage.googleapis.com/{BUCKET_NAME}/{path}"
    return jsonify(analysis)


@analysis_bp.route("/api/videos/<int:video_id>/pose-data", methods=["GET"])
def api_get_pose_data(video_id):
    """Proxy pose data from GCS."""
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

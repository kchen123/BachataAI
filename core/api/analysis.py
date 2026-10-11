"""API routes for pose analysis — trigger, progress, results."""

import os
import json
import math
import threading
import requests
import google.auth.transport.requests
import google.oauth2.id_token
from flask import Blueprint, request, jsonify
from core.adapters.repository import get_repo, Repository
from core.adapters.analyses import AnalysesAdapter, JointFramesAdapter
from core.adapters.videos import VideosAdapter

analysis_bp = Blueprint("analysis", __name__)

POSE_SERVICE_URL = os.environ.get("POSE_SERVICE_URL", "http://localhost:8080")

# In-memory progress tracking
_progress = {}


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


def _run_analysis(video_id, analysis_id, gcs_blob, frame_count):
    """Background thread: call GPU pose service, save results to DB."""
    try:
        _progress[video_id] = {"status": "running", "step": "calling GPU", "percent": 0}

        resp = requests.post(
            f"{POSE_SERVICE_URL}/predict/video",
            json={"gcs_bucket": "bachataai-videos", "gcs_blob": gcs_blob, "sample_every_n": 5},
            headers=_get_auth_headers(),
            timeout=1800,
        )
        resp.raise_for_status()
        data = resp.json()
        pose_frames = data["frames"]
        total = len(pose_frames)

        _progress[video_id] = {"status": "running", "step": "saving", "percent": 50}

        repo = Repository()
        aa = AnalysesAdapter(repo)
        ja = JointFramesAdapter(repo)

        all_angles = {n: [] for n in ANGLE_DEFS}
        batch = []
        for i, f in enumerate(pose_frames):
            lm = {int(k): v for k, v in f["keypoints"].items()}
            for name, (a, b, c) in ANGLE_DEFS.items():
                if a in lm and b in lm and c in lm:
                    if lm[a]["visibility"] > 0.3 and lm[b]["visibility"] > 0.3 and lm[c]["visibility"] > 0.3:
                        all_angles[name].append(round(_calc_angle(lm[a], lm[b], lm[c]), 1))
            batch.append({
                "frame_num": f["frame_num"],
                "timestamp_sec": f["timestamp_sec"],
                "landmarks": lm,
            })
            if len(batch) >= 100:
                ja.bulk_insert(analysis_id, batch)
                batch = []
                pct = 50 + int((i / total) * 50)
                _progress[video_id] = {"status": "running", "step": "saving", "percent": pct}

        if batch:
            ja.bulk_insert(analysis_id, batch)

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

        aa.set_summary(analysis_id, summary)
        repo.close()
        _progress[video_id] = {"status": "done", "percent": 100}

    except Exception as e:
        try:
            repo = Repository()
            AnalysesAdapter(repo).set_status(analysis_id, "error")
            repo.close()
        except Exception:
            pass
        _progress[video_id] = {"status": "error", "error": str(e), "percent": 0}


@analysis_bp.route("/api/videos/<int:video_id>/analyze", methods=["POST"])
def api_run_analysis(video_id):
    repo = get_repo()
    video = VideosAdapter(repo).get(video_id)
    if not video:
        return jsonify({"error": "video not found"}), 404
    if not video.get("filename"):
        return jsonify({"error": "video has no file"}), 400

    # Check if already running
    existing = AnalysesAdapter(repo).get_by_video(video_id)
    if existing and existing["status"] == "running":
        return jsonify({"error": "analysis already running"}), 409

    analysis = AnalysesAdapter(repo).create(video_id=video_id, model="yolo26x-pose")
    AnalysesAdapter(repo).set_status(analysis["id"], "running")

    thread = threading.Thread(
        target=_run_analysis,
        args=(video_id, analysis["id"], video["filename"], video.get("frame_count", 0)),
        daemon=True,
    )
    thread.start()

    return jsonify({"status": "started", "analysis_id": analysis["id"]}), 202


@analysis_bp.route("/api/videos/<int:video_id>/progress", methods=["GET"])
def api_get_progress(video_id):
    p = _progress.get(video_id)
    if p:
        return jsonify(p)
    # Check DB for completed analysis
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
    return jsonify(analysis)


@analysis_bp.route("/api/analyses/<int:analysis_id>/frames", methods=["GET"])
def api_get_frames(analysis_id):
    repo = get_repo()
    frames = JointFramesAdapter(repo).get_by_analysis(analysis_id)
    sample = request.args.get("sample", type=int)
    if sample and len(frames) > sample:
        step = len(frames) // sample
        frames = frames[::step][:sample]
    for f in frames:
        if isinstance(f.get("landmarks"), str):
            f["landmarks"] = json.loads(f["landmarks"])
    return jsonify(frames)

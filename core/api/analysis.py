"""API routes for pose analysis — trigger, progress (SSE), results."""

import json
import threading
from flask import Blueprint, request, jsonify, Response
from core.domains.analysis import (
    run_pose_estimation, get_analysis, get_latest_analysis,
    get_joint_frames, get_frame_landmarks, ANGLE_DEFINITIONS, LANDMARK_NAMES,
)

analysis_bp = Blueprint("analysis", __name__)

# In-memory progress tracking for SSE
_progress = {}  # analysis_id -> {"current": n, "total": t, "status": "running"|"done"|"error"}


@analysis_bp.route("/api/videos/<int:video_id>/analyze", methods=["POST"])
def api_run_analysis(video_id):
    """Trigger pose estimation in a background thread."""
    from core.domains.videos import get_video
    video = get_video(video_id)
    if video is None:
        return jsonify({"error": "video not found"}), 404

    # Create analysis record first
    from core.adapters import AnalysesAdapter, get_repo
    aa = AnalysesAdapter(get_repo())
    analysis = aa.create(video_id=video_id)
    analysis_id = analysis["id"]
    _progress[analysis_id] = {"current": 0, "total": video["frame_count"], "status": "running"}

    def _run():
        try:
            def on_progress(frame, total):
                _progress[analysis_id] = {"current": frame, "total": total, "status": "running"}

            run_pose_estimation(video_id, on_progress=on_progress)
            _progress[analysis_id]["status"] = "done"
        except Exception as e:
            _progress[analysis_id]["status"] = "error"
            _progress[analysis_id]["error"] = str(e)

    # Delete the analysis we just created — run_pose_estimation creates its own
    aa.delete(analysis_id)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return jsonify({"status": "started", "video_id": video_id}), 202


@analysis_bp.route("/api/videos/<int:video_id>/analysis", methods=["GET"])
def api_get_analysis(video_id):
    """Get the latest analysis for a video."""
    analysis = get_latest_analysis(video_id)
    if analysis is None:
        return jsonify({"error": "no analysis found"}), 404
    for k in ("created_at",):
        if analysis.get(k):
            analysis[k] = str(analysis[k])
    if analysis.get("summary") and isinstance(analysis["summary"], str):
        analysis["summary"] = json.loads(analysis["summary"])
    return jsonify(analysis)


@analysis_bp.route("/api/analyses/<int:analysis_id>/frames", methods=["GET"])
def api_get_frames(analysis_id):
    """Get all joint frames for an analysis. ?sample=N to downsample."""
    frames = get_joint_frames(analysis_id)
    sample = request.args.get("sample", type=int)
    if sample and len(frames) > sample:
        step = len(frames) // sample
        frames = frames[::step][:sample]
    for f in frames:
        if isinstance(f.get("landmarks"), str):
            f["landmarks"] = json.loads(f["landmarks"])
    return jsonify(frames)


@analysis_bp.route("/api/analyses/<int:analysis_id>/frames/<int:frame_num>", methods=["GET"])
def api_get_frame(analysis_id, frame_num):
    """Get landmarks for a single frame."""
    frame = get_frame_landmarks(analysis_id, frame_num)
    if frame is None:
        return jsonify({"error": "frame not found"}), 404
    if isinstance(frame.get("landmarks"), str):
        frame["landmarks"] = json.loads(frame["landmarks"])
    return jsonify(frame)


@analysis_bp.route("/api/analyses/<int:analysis_id>/angles", methods=["GET"])
def api_get_angles(analysis_id):
    """Compute joint angles for all frames. Returns {angle_name: [{frame, angle}, ...]}."""
    from core.domains.analysis import _calc_angle
    frames = get_joint_frames(analysis_id)
    result = {name: [] for name in ANGLE_DEFINITIONS}

    for f in frames:
        lm = f["landmarks"]
        if isinstance(lm, str):
            lm = json.loads(lm)
        # Convert string keys to int
        lm = {int(k): v for k, v in lm.items()} if lm else {}
        for angle_name, (a, b, c) in ANGLE_DEFINITIONS.items():
            if a in lm and b in lm and c in lm:
                angle = _calc_angle(lm[a], lm[b], lm[c])
                result[angle_name].append({
                    "frame": f["frame_num"],
                    "time": f["timestamp_sec"],
                    "angle": round(angle, 1),
                })

    return jsonify(result)


@analysis_bp.route("/api/meta/landmarks", methods=["GET"])
def api_landmark_names():
    """Return landmark index -> name mapping."""
    return jsonify(LANDMARK_NAMES)


@analysis_bp.route("/api/meta/angles", methods=["GET"])
def api_angle_definitions():
    """Return angle definitions."""
    return jsonify({k: list(v) for k, v in ANGLE_DEFINITIONS.items()})

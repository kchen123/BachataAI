"""API routes for pose analysis results."""

import json
from flask import Blueprint, request, jsonify
from core.adapters.repository import get_repo
from core.adapters.analyses import AnalysesAdapter, JointFramesAdapter
from core.adapters.videos import VideosAdapter

analysis_bp = Blueprint("analysis", __name__)


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

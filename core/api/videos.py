"""API routes for video upload, listing, and deletion."""

from flask import Blueprint, request, jsonify
from core.domains.videos import upload_video, list_videos, get_video, delete_video

videos_bp = Blueprint("videos", __name__)


@videos_bp.route("/api/videos", methods=["GET"])
def api_list_videos():
    videos = list_videos()
    for v in videos:
        for k in ("created_at", "updated_at"):
            if v.get(k):
                v[k] = str(v[k])
    return jsonify(videos)


@videos_bp.route("/api/videos", methods=["POST"])
def api_upload_video():
    if "file" not in request.files:
        return jsonify({"error": "no file provided"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "empty filename"}), 400
    try:
        video = upload_video(file)
        for k in ("created_at", "updated_at"):
            if video.get(k):
                video[k] = str(video[k])
        return jsonify(video), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@videos_bp.route("/api/videos/<int:video_id>", methods=["GET"])
def api_get_video(video_id):
    video = get_video(video_id)
    if video is None:
        return jsonify({"error": "not found"}), 404
    for k in ("created_at", "updated_at"):
        if video.get(k):
            video[k] = str(video[k])
    return jsonify(video)


@videos_bp.route("/api/videos/<int:video_id>", methods=["DELETE"])
def api_delete_video(video_id):
    if delete_video(video_id):
        return jsonify({"status": "ok"})
    return jsonify({"error": "not found"}), 404

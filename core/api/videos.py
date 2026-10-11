"""API routes for folders and videos — GDrive-like file browser."""

import datetime
from flask import Blueprint, render_template, request, jsonify, abort, session
from core.adapters.repository import get_repo
from core.adapters.folders import FoldersAdapter
from core.adapters.videos import VideosAdapter
from core.adapters.users import UsersAdapter


def _serialize(row: dict) -> dict:
    """Convert datetime values to strings for JSON."""
    return {k: (str(v) if isinstance(v, (datetime.datetime, datetime.date)) else v)
            for k, v in row.items()}


def _uid() -> int:
    return session["user_id"]


def _current_user(repo) -> dict:
    return UsersAdapter(repo).get(_uid())


videos_bp = Blueprint("videos", __name__)


# ── Page routes ────────────────────────────────────────────────────────────────

@videos_bp.route("/")
def index():
    repo = get_repo()
    uid = _uid()
    fa, va = FoldersAdapter(repo), VideosAdapter(repo)
    folders = fa.list_root(uid)
    videos = va.list_unfiled(uid)
    user = _current_user(repo)
    return render_template("videos.html",
                           folder=None, breadcrumbs=[],
                           folders=folders, videos=videos, user=user)


@videos_bp.route("/folder/<int:folder_id>")
def folder_view(folder_id):
    repo = get_repo()
    uid = _uid()
    fa, va = FoldersAdapter(repo), VideosAdapter(repo)
    folder = fa.get(folder_id)
    if folder is None or folder["user_id"] != uid:
        abort(404)
    folders = fa.list_children(uid, folder_id)
    videos = va.list_by_folder(uid, folder_id)
    breadcrumbs = fa.get_breadcrumbs(folder_id)
    user = _current_user(repo)
    return render_template("videos.html",
                           folder=folder, breadcrumbs=breadcrumbs,
                           folders=folders, videos=videos, user=user)


# ── Folder CRUD API ────────────────────────────────────────────────────────────

@videos_bp.route("/api/folders", methods=["POST"])
def api_create_folder():
    body = request.get_json()
    repo = get_repo()
    folder = FoldersAdapter(repo).create(body["name"], _uid(), body.get("parent_id"))
    return jsonify(_serialize(folder)), 201


@videos_bp.route("/api/folders/<int:folder_id>", methods=["PATCH"])
def api_rename_folder(folder_id):
    body = request.get_json()
    repo = get_repo()
    folder = FoldersAdapter(repo).rename(folder_id, body["name"])
    return jsonify(_serialize(folder)) if folder else ("", 404)


@videos_bp.route("/api/folders/<int:folder_id>/move", methods=["PATCH"])
def api_move_folder(folder_id):
    body = request.get_json()
    repo = get_repo()
    folder = FoldersAdapter(repo).move(folder_id, body.get("folder_id"))
    return jsonify(_serialize(folder)) if folder else ("", 404)


@videos_bp.route("/api/folders/<int:folder_id>", methods=["DELETE"])
def api_delete_folder(folder_id):
    repo = get_repo()
    ok = FoldersAdapter(repo).delete(folder_id)
    return ("", 204) if ok else ("", 404)


# ── Video CRUD API ─────────────────────────────────────────────────────────────

@videos_bp.route("/api/videos", methods=["POST"])
def api_create_video():
    body = request.get_json()
    repo = get_repo()
    video = VideosAdapter(repo).create(body["title"], _uid(), body.get("folder_id"))
    return jsonify(_serialize(video)), 201


@videos_bp.route("/api/videos/upload", methods=["POST"])
def api_upload_video():
    import os, uuid, tempfile, cv2
    from core.adapters.storage import upload_blob

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "no file"}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    allowed = {".mp4", ".mov", ".avi", ".webm", ".mkv"}
    if ext not in allowed:
        return jsonify({"error": f"Unsupported format: {ext}"}), 400

    content_types = {".mp4": "video/mp4", ".mov": "video/quicktime",
                     ".avi": "video/x-msvideo", ".webm": "video/webm", ".mkv": "video/x-matroska"}

    # Save to temp, probe, upload to GCS
    filename = f"{uuid.uuid4().hex}{ext}"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp_path = tmp.name
        f.save(tmp_path)

    try:
        cap = cv2.VideoCapture(tmp_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_sec = round(frame_count / fps, 2) if fps > 0 else 0
        cap.release()

        gcs_url = upload_blob(filename, tmp_path, content_types.get(ext, "video/mp4"))
    finally:
        os.unlink(tmp_path)

    folder_id = request.form.get("folder_id", type=int)
    title = os.path.splitext(f.filename)[0]
    repo = get_repo()
    video = VideosAdapter(repo).create(title, _uid(), folder_id, filename=filename, gcs_url=gcs_url)
    video = repo.update("bachata.videos", video["id"], {
        "duration_sec": duration_sec, "fps": round(fps, 2), "frame_count": frame_count,
    })
    return jsonify(_serialize(video)), 201


@videos_bp.route("/api/videos/<int:video_id>", methods=["PATCH"])
def api_rename_video(video_id):
    body = request.get_json()
    repo = get_repo()
    video = VideosAdapter(repo).rename(video_id, body["title"])
    return jsonify(_serialize(video)) if video else ("", 404)


@videos_bp.route("/api/videos/<int:video_id>/move", methods=["PATCH"])
def api_move_video(video_id):
    body = request.get_json()
    repo = get_repo()
    video = VideosAdapter(repo).move(video_id, body.get("folder_id"))
    return jsonify(_serialize(video)) if video else ("", 404)


@videos_bp.route("/api/videos/<int:video_id>", methods=["DELETE"])
def api_delete_video(video_id):
    repo = get_repo()
    ok = VideosAdapter(repo).delete(video_id)
    return ("", 204) if ok else ("", 404)


# ── Account API ────────────────────────────────────────────────────────────────

@videos_bp.route("/api/account", methods=["PATCH"])
def api_update_account():
    body = request.get_json()
    repo = get_repo()
    allowed = {"username", "display_name", "email", "role"}
    data = {k: v for k, v in body.items() if k in allowed and v}
    if not data:
        return ("", 400)
    user = UsersAdapter(repo).update_profile(_uid(), data)
    if user:
        session["username"] = user["username"]
    return jsonify(_serialize(user)) if user else ("", 404)

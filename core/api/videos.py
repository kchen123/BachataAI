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


@videos_bp.route("/api/videos/upload-url", methods=["POST"])
def api_get_upload_url():
    """Get a resumable upload URL for direct browser-to-GCS upload."""
    import os, uuid
    from google.cloud import storage as gcs_storage
    import google.auth
    import google.auth.transport.requests

    body = request.get_json()
    original_name = body.get("filename", "video.mp4")
    ext = os.path.splitext(original_name)[1].lower()
    allowed = {".mp4", ".mov", ".avi", ".webm", ".mkv"}
    if ext not in allowed:
        return jsonify({"error": f"Unsupported format: {ext}"}), 400

    content_types = {".mp4": "video/mp4", ".mov": "video/quicktime",
                     ".avi": "video/x-msvideo", ".webm": "video/webm", ".mkv": "video/x-matroska"}

    filename = f"{uuid.uuid4().hex}{ext}"
    ct = content_types.get(ext, "video/mp4")
    gcs_url = f"https://storage.googleapis.com/bachataai-videos/{filename}"

    # Create resumable upload session via GCS JSON API
    credentials, project = google.auth.default()
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)

    import requests as req_lib
    init_resp = req_lib.post(
        f"https://storage.googleapis.com/upload/storage/v1/b/bachataai-videos/o?uploadType=resumable&name={filename}",
        headers={
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
            "X-Upload-Content-Type": ct,
        },
        json={"contentType": ct},
    )
    init_resp.raise_for_status()
    upload_url = init_resp.headers["Location"]

    # Check for duplicate title in same folder
    folder_id = body.get("folder_id")
    title = os.path.splitext(original_name)[0]
    repo = get_repo()
    existing = VideosAdapter(repo).select(user_id=_uid(), folder_id=folder_id, title=title)
    warning = None
    if existing:
        warning = f"Video '{title}' already exists in this folder"

    video = VideosAdapter(repo).create(title, _uid(), folder_id, filename=filename, gcs_url=gcs_url)

    result = {
        "upload_url": upload_url,
        "content_type": ct,
        "video": _serialize(video),
    }
    if warning:
        result["warning"] = warning
    return jsonify(result), 200


@videos_bp.route("/api/videos/upload", methods=["POST"])
def api_upload_video():
    """Fallback for small files."""
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

    filename = f"{uuid.uuid4().hex}{ext}"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp_path = tmp.name
        f.save(tmp_path)

    try:
        # Probe video metadata
        fps = 30.0
        frame_count = 0
        duration_sec = 0
        try:
            cap = cv2.VideoCapture(tmp_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration_sec = round(frame_count / fps, 2) if fps > 0 else 0
            cap.release()
        except Exception:
            pass

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

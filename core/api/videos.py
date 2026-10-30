"""API routes for folders and videos — GDrive-like file browser."""

from flask import Blueprint, render_template, request, jsonify, abort, session, redirect
from core.adapters.repository import get_repo
from core.adapters.folders import FoldersAdapter
from core.adapters.videos import VideosAdapter
from core.adapters.users import UsersAdapter
from core.adapters.storage import BUCKET_NAME, generate_thumbnail
from core.api._helpers import serialize, uid

ALLOWED_EXTS = {".mp4", ".mov", ".avi", ".webm", ".mkv"}
CONTENT_TYPES = {
    ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".avi": "video/x-msvideo", ".webm": "video/webm", ".mkv": "video/x-matroska",
}


def _current_user(repo) -> dict:
    return UsersAdapter(repo).get(uid())


videos_bp = Blueprint("videos", __name__)

PAGE_SIZE = 20


# ── Page routes ────────────────────────────────────────────────────────────────

@videos_bp.route("/")
def home():
    if session.get("logged_in"):
        return redirect("/studio")
    from core.adapters.tiers import TiersAdapter
    repo = get_repo()
    all_tiers = TiersAdapter(repo).select(order_by="id ASC")
    tiers = [t for t in all_tiers if t["name"] != "Admin"]
    return render_template("home.html", logged_in=False, tiers=tiers)


@videos_bp.route("/studio")
def index():
    repo = get_repo()
    user_id = uid()
    body_focus = request.args.get("body_focus")
    fa, va = FoldersAdapter(repo), VideosAdapter(repo)
    folders = fa.list_root(user_id)
    videos = va.list_unfiled(user_id, limit=PAGE_SIZE + 1, body_focus=body_focus)
    has_more = len(videos) > PAGE_SIZE
    videos = videos[:PAGE_SIZE]
    user = _current_user(repo)
    return render_template("videos.html",
                           folder=None, breadcrumbs=[],
                           folders=folders, videos=videos,
                           has_more=has_more, body_focus=body_focus or '',
                           user=user)


@videos_bp.route("/folder/<int:folder_id>")
def folder_view(folder_id):
    repo = get_repo()
    user_id = uid()
    body_focus = request.args.get("body_focus")
    fa, va = FoldersAdapter(repo), VideosAdapter(repo)
    folder = fa.get(folder_id)
    if folder is None or folder["user_id"] != user_id:
        abort(404)
    folders = fa.list_children(user_id, folder_id)
    videos = va.list_by_folder(user_id, folder_id, limit=PAGE_SIZE + 1, body_focus=body_focus)
    has_more = len(videos) > PAGE_SIZE
    videos = videos[:PAGE_SIZE]
    breadcrumbs = fa.get_breadcrumbs(folder_id)
    user = _current_user(repo)
    return render_template("videos.html",
                           folder=folder, breadcrumbs=breadcrumbs,
                           folders=folders, videos=videos,
                           has_more=has_more, body_focus=body_focus or '',
                           user=user)


@videos_bp.route("/api/videos/list")
def api_list_videos():
    repo = get_repo()
    user_id = uid()
    va = VideosAdapter(repo)
    offset = request.args.get("offset", 0, type=int)
    folder_id = request.args.get("folder_id", type=int)
    body_focus = request.args.get("body_focus")
    if folder_id is not None:
        videos = va.list_by_folder(user_id, folder_id, limit=PAGE_SIZE + 1, offset=offset, body_focus=body_focus)
    else:
        videos = va.list_unfiled(user_id, limit=PAGE_SIZE + 1, offset=offset, body_focus=body_focus)
    has_more = len(videos) > PAGE_SIZE
    videos = videos[:PAGE_SIZE]
    return jsonify({"videos": [serialize(v) for v in videos], "has_more": has_more})


# ── Folder CRUD API ────────────────────────────────────────────────────────────

@videos_bp.route("/api/folders", methods=["POST"])
def api_create_folder():
    body = request.get_json()
    repo = get_repo()
    folder = FoldersAdapter(repo).create(body["name"], uid(), body.get("parent_id"))
    return jsonify(serialize(folder)), 201


@videos_bp.route("/api/folders/<int:folder_id>", methods=["PATCH"])
def api_rename_folder(folder_id):
    body = request.get_json()
    repo = get_repo()
    folder = FoldersAdapter(repo).rename(folder_id, body["name"])
    return jsonify(serialize(folder)) if folder else ("", 404)


@videos_bp.route("/api/folders/<int:folder_id>/move", methods=["PATCH"])
def api_move_folder(folder_id):
    body = request.get_json()
    repo = get_repo()
    folder = FoldersAdapter(repo).move(folder_id, body.get("folder_id"))
    return jsonify(serialize(folder)) if folder else ("", 404)


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
    video = VideosAdapter(repo).create(body["title"], uid(), body.get("folder_id"))
    return jsonify(serialize(video)), 201


@videos_bp.route("/api/videos/upload-url", methods=["POST"])
def api_get_upload_url():
    """Get a resumable upload URL for direct browser-to-GCS upload."""
    import os, uuid
    import google.auth
    import google.auth.transport.requests

    body = request.get_json()
    original_name = body.get("filename", "video.mp4")
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in ALLOWED_EXTS:
        return jsonify({"error": f"Unsupported format: {ext}"}), 400

    filename = f"{uuid.uuid4().hex}{ext}"
    ct = CONTENT_TYPES.get(ext, "video/mp4")
    gcs_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"

    # Create resumable upload session via GCS JSON API
    credentials, project = google.auth.default()
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)

    import requests as req_lib
    init_resp = req_lib.post(
        f"https://storage.googleapis.com/upload/storage/v1/b/{BUCKET_NAME}/o?uploadType=resumable&name={filename}",
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
    existing = VideosAdapter(repo).select(user_id=uid(), folder_id=folder_id, title=title)
    warning = None
    if existing:
        warning = f"Video '{title}' already exists in this folder"

    video = VideosAdapter(repo).create(title, uid(), folder_id, filename=filename, gcs_url=gcs_url)

    result = {
        "upload_url": upload_url,
        "content_type": ct,
        "video": serialize(video),
    }
    if warning:
        result["warning"] = warning
    return jsonify(result), 200


@videos_bp.route("/api/videos/<int:video_id>", methods=["PATCH"])
def api_rename_video(video_id):
    body = request.get_json()
    repo = get_repo()
    video = VideosAdapter(repo).rename(video_id, body["title"])
    return jsonify(serialize(video)) if video else ("", 404)


@videos_bp.route("/api/videos/<int:video_id>/move", methods=["PATCH"])
def api_move_video(video_id):
    body = request.get_json()
    repo = get_repo()
    video = VideosAdapter(repo).move(video_id, body.get("folder_id"))
    return jsonify(serialize(video)) if video else ("", 404)


@videos_bp.route("/api/videos/<int:video_id>", methods=["DELETE"])
def api_delete_video(video_id):
    repo = get_repo()
    video = VideosAdapter(repo).get(video_id)
    if not video:
        return ("", 404)
    # Delete GCS files
    from core.adapters.storage import _get_client, BUCKET_NAME
    import os
    bucket = _get_client().bucket(BUCKET_NAME)
    prefix = f"https://storage.googleapis.com/{BUCKET_NAME}/"
    for url_field in ("gcs_url", "thumbnail_url"):
        url = video.get(url_field)
        if url and url.startswith(prefix):
            try:
                bucket.blob(url[len(prefix):]).delete()
            except Exception:
                pass
    VideosAdapter(repo).delete(video_id)
    return ("", 204)


# ── Thumbnail API ─────────────────────────────────────────────────────────────

@videos_bp.route("/api/videos/<int:video_id>/thumbnail", methods=["POST"])
def api_generate_thumbnail(video_id):
    """Generate a thumbnail for a single video."""
    repo = get_repo()
    video = VideosAdapter(repo).get(video_id)
    if not video or not video.get("gcs_url"):
        return ("", 404)
    thumb_url = generate_thumbnail(video["gcs_url"])
    if thumb_url:
        repo.update("bachata.videos", video_id, {"thumbnail_url": thumb_url})
        return jsonify({"thumbnail_url": thumb_url})
    return jsonify({"error": "failed"}), 500


# ── Account API ────────────────────────────────────────────────────────────────

@videos_bp.route("/api/account", methods=["PATCH"])
def api_update_account():
    body = request.get_json()
    repo = get_repo()
    allowed = {"username", "display_name", "email", "role"}
    data = {k: v for k, v in body.items() if k in allowed and v}
    if not data:
        return ("", 400)
    user = UsersAdapter(repo).update_profile(uid(), data)
    if user:
        session["username"] = user["username"]
    return jsonify(serialize(user)) if user else ("", 404)

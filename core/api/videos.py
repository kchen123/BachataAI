"""API routes for folders and videos — GDrive-like file browser."""

import datetime
from flask import Blueprint, render_template, request, jsonify, abort
from core.adapters.repository import get_repo
from core.adapters.folders import FoldersAdapter
from core.adapters.videos import VideosAdapter


def _serialize(row: dict) -> dict:
    """Convert datetime values to strings for JSON."""
    return {k: (str(v) if isinstance(v, (datetime.datetime, datetime.date)) else v)
            for k, v in row.items()}

videos_bp = Blueprint("videos", __name__)


# ── Page routes ────────────────────────────────────────────────────────────────

@videos_bp.route("/")
def index():
    repo = get_repo()
    fa, va = FoldersAdapter(repo), VideosAdapter(repo)
    folders = fa.list_root()
    videos = va.list_unfiled()
    return render_template("videos.html",
                           folder=None, breadcrumbs=[],
                           folders=folders, videos=videos)


@videos_bp.route("/folder/<int:folder_id>")
def folder_view(folder_id):
    repo = get_repo()
    fa, va = FoldersAdapter(repo), VideosAdapter(repo)
    folder = fa.get(folder_id)
    if folder is None:
        abort(404)
    folders = fa.list_children(folder_id)
    videos = va.list_by_folder(folder_id)
    breadcrumbs = fa.get_breadcrumbs(folder_id)
    return render_template("videos.html",
                           folder=folder, breadcrumbs=breadcrumbs,
                           folders=folders, videos=videos)


# ── Folder CRUD API ────────────────────────────────────────────────────────────

@videos_bp.route("/api/folders", methods=["POST"])
def api_create_folder():
    body = request.get_json()
    repo = get_repo()
    folder = FoldersAdapter(repo).create(body["name"], body.get("parent_id"))
    return jsonify(_serialize(folder)), 201


@videos_bp.route("/api/folders/<int:folder_id>", methods=["PATCH"])
def api_rename_folder(folder_id):
    body = request.get_json()
    repo = get_repo()
    folder = FoldersAdapter(repo).rename(folder_id, body["name"])
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
    video = VideosAdapter(repo).create(body["title"], body.get("folder_id"))
    return jsonify(_serialize(video)), 201


@videos_bp.route("/api/videos/<int:video_id>", methods=["PATCH"])
def api_rename_video(video_id):
    body = request.get_json()
    repo = get_repo()
    video = VideosAdapter(repo).rename(video_id, body["title"])
    return jsonify(_serialize(video)) if video else ("", 404)


@videos_bp.route("/api/videos/<int:video_id>", methods=["DELETE"])
def api_delete_video(video_id):
    repo = get_repo()
    ok = VideosAdapter(repo).delete(video_id)
    return ("", 204) if ok else ("", 404)

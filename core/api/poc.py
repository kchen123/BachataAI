"""PoC routes – admin only."""

import os
import uuid
import json
import threading
import requests
import google.auth.transport.requests
import google.oauth2.id_token
from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
from core.adapters.repository import get_repo
from core.adapters.users import UsersAdapter
from core.adapters.storage import BUCKET_NAME, _get_client as _gcs

poc_bp = Blueprint("poc", __name__)

POSE_SERVICE_URL = os.environ.get("POSE_SERVICE_URL", "http://localhost:8080")


def _require_admin():
    uid = session.get("user_id")
    if not uid:
        return redirect(url_for("auth.login"))
    user = UsersAdapter(get_repo()).get(uid)
    if not user or user.get("role") != "admin":
        return redirect("/studio")
    return None


def _auth_headers():
    if POSE_SERVICE_URL.startswith("http://localhost"):
        return {}
    try:
        auth_req = google.auth.transport.requests.Request()
        token = google.oauth2.id_token.fetch_id_token(auth_req, POSE_SERVICE_URL)
        return {"Authorization": f"Bearer {token}"}
    except Exception:
        import subprocess, shutil
        gcloud_path = shutil.which("gcloud") or "gcloud"
        token = subprocess.check_output(
            [gcloud_path, "auth", "print-identity-token"], text=True, shell=True
        ).strip()
        return {"Authorization": f"Bearer {token}"}


# ── Page route ───────────────────────────────────────────────────────────────

@poc_bp.route("/poc")
def index():
    denied = _require_admin()
    if denied:
        return denied
    return render_template("poc.html")


# ── API routes ───────────────────────────────────────────────────────────────

@poc_bp.route("/api/poc/upload-url", methods=["POST"])
def get_upload_url():
    """Get a GCS resumable upload URL for the PoC video."""
    denied = _require_admin()
    if denied:
        return jsonify({"error": "forbidden"}), 403

    data = request.json
    filename = data.get("filename", "video.mp4")
    job_id = str(uuid.uuid4())[:8]
    blob_name = f"poc/{job_id}_{filename}"

    bucket = _gcs().bucket(BUCKET_NAME)
    blob = bucket.blob(blob_name)
    url = blob.create_resumable_upload_session(content_type="video/mp4")

    return jsonify({"upload_url": url, "blob_name": blob_name, "job_id": job_id})


@poc_bp.route("/api/poc/convert", methods=["POST"])
def convert():
    """Trigger anime conversion on the GPU service."""
    denied = _require_admin()
    if denied:
        return jsonify({"error": "forbidden"}), 403

    data = request.json
    blob_name = data["blob_name"]
    job_id = data["job_id"]
    sample_every_n = int(data.get("sample_every_n", 2))
    strength = float(data.get("strength", 0.75))

    def _fire():
        try:
            requests.post(
                f"{POSE_SERVICE_URL}/animify",
                json={
                    "gcs_bucket": BUCKET_NAME,
                    "gcs_blob": blob_name,
                    "job_id": job_id,
                    "sample_every_n": sample_every_n,
                    "strength": strength,
                },
                headers=_auth_headers(),
                timeout=600,
            )
        except Exception:
            pass

    threading.Thread(target=_fire, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@poc_bp.route("/api/poc/progress/<job_id>")
def progress(job_id):
    """Poll progress from GCS."""
    denied = _require_admin()
    if denied:
        return jsonify({"error": "forbidden"}), 403

    bucket = _gcs().bucket(BUCKET_NAME)
    blob = bucket.blob(f"progress/anime-{job_id}.json")
    if not blob.exists():
        return jsonify({"status": "pending", "percent": 0, "step": "Waiting..."})

    data = json.loads(blob.download_as_text())
    return jsonify(data)

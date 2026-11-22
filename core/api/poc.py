"""PoC routes – admin only."""

import uuid
import json
import requests
from flask import Blueprint, redirect, request, jsonify
from core.adapters.storage import BUCKET_NAME, _get_client as _gcs
from core.api._helpers import require_admin, pose_auth_headers, POSE_SERVICE_URL

poc_bp = Blueprint("poc", __name__)


# ── Page route ───────────────────────────────────────────────────────────────

@poc_bp.route("/poc")
def index():
    return redirect("/control")


# ── API routes ───────────────────────────────────────────────────────────────

@poc_bp.route("/api/poc/upload", methods=["POST"])
def upload_video():
    """Upload video directly to GCS through the server."""
    denied = require_admin()
    if denied:
        return jsonify({"error": "forbidden"}), 403

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "no file"}), 400

    job_id = str(uuid.uuid4())[:8]
    blob_name = f"poc/{job_id}_{file.filename}"

    bucket = _gcs().bucket(BUCKET_NAME)
    blob = bucket.blob(blob_name)
    blob.upload_from_file(file.stream, content_type="video/mp4")

    return jsonify({"blob_name": blob_name, "job_id": job_id})


@poc_bp.route("/api/poc/convert", methods=["POST"])
def convert():
    """Trigger anime conversion on the GPU service."""
    denied = require_admin()
    if denied:
        return jsonify({"error": "forbidden"}), 403

    data = request.json
    blob_name = data["blob_name"]
    job_id = data["job_id"]
    sample_every_n = int(data.get("sample_every_n", 2))
    strength = float(data.get("strength", 0.75))

    # Call pose service synchronously — blocks until done.
    # Frontend polls progress via /api/poc/progress while this blocks.
    try:
        resp = requests.post(
            f"{POSE_SERVICE_URL}/animify",
            json={
                "gcs_bucket": BUCKET_NAME,
                "gcs_blob": blob_name,
                "job_id": job_id,
                "sample_every_n": sample_every_n,
                "strength": strength,
            },
            headers=pose_auth_headers(),
            timeout=600,
        )
        return jsonify({"ok": True, "job_id": job_id, "status": resp.status_code})
    except Exception as e:
        # Write error to GCS so frontend polling sees it
        try:
            bucket = _gcs().bucket(BUCKET_NAME)
            blob = bucket.blob(f"progress/anime-{job_id}.json")
            blob.upload_from_string(
                json.dumps({"status": "error", "percent": 0, "step": str(e)}),
                content_type="application/json",
            )
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(e)}), 502


@poc_bp.route("/api/poc/progress/<job_id>")
def progress(job_id):
    """Poll progress from GCS."""
    denied = require_admin()
    if denied:
        return jsonify({"error": "forbidden"}), 403

    bucket = _gcs().bucket(BUCKET_NAME)
    blob = bucket.blob(f"progress/anime-{job_id}.json")
    if not blob.exists():
        return jsonify({"status": "pending", "percent": 0, "step": "Waiting..."})

    data = json.loads(blob.download_as_text())
    return jsonify(data)

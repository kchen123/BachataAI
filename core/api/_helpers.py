"""Shared helpers for API blueprints."""

import os
import datetime
import google.auth.transport.requests
import google.oauth2.id_token
from flask import session, redirect, url_for, jsonify
from core.adapters.repository import get_repo
from core.adapters.users import UsersAdapter

POSE_SERVICE_URL = os.environ.get("POSE_SERVICE_URL", "http://localhost:8080")


def serialize(row: dict) -> dict:
    """Convert datetime values to strings for JSON."""
    return {k: (str(v) if isinstance(v, (datetime.datetime, datetime.date)) else v)
            for k, v in row.items()}


def uid() -> int:
    return session["user_id"]


def require_admin():
    """Check session for admin role. Returns redirect response or None."""
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))
    user = UsersAdapter(get_repo()).get(user_id)
    if not user or user.get("role") != "admin":
        return redirect("/studio")
    return None


def pose_auth_headers() -> dict:
    """Get auth headers for calling the pose GPU service."""
    if POSE_SERVICE_URL.startswith("http://localhost"):
        return {}
    try:
        auth_req = google.auth.transport.requests.Request()
        token = google.oauth2.id_token.fetch_id_token(auth_req, POSE_SERVICE_URL)
        return {"Authorization": f"Bearer {token}"}
    except Exception:
        import subprocess
        import shutil
        gcloud_path = shutil.which("gcloud") or "gcloud"
        token = subprocess.check_output(
            [gcloud_path, "auth", "print-identity-token"], text=True, shell=True
        ).strip()
        return {"Authorization": f"Bearer {token}"}

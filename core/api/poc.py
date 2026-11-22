"""PoC routes – admin only."""

from flask import Blueprint, render_template, session, redirect, url_for
from core.adapters.repository import get_repo
from core.adapters.users import UsersAdapter

poc_bp = Blueprint("poc", __name__)


def _require_admin():
    uid = session.get("user_id")
    if not uid:
        return redirect(url_for("auth.login"))
    user = UsersAdapter(get_repo()).get(uid)
    if not user or user.get("role") != "admin":
        return redirect("/studio")
    return None


@poc_bp.route("/poc")
def index():
    denied = _require_admin()
    if denied:
        return denied
    return render_template("poc.html")

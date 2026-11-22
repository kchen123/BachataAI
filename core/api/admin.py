"""Admin dashboard routes – admin only."""

from flask import Blueprint, render_template, session, redirect, url_for, jsonify, request
from core.adapters.repository import get_repo
from core.adapters.users import UsersAdapter
from core.adapters.videos import VideosAdapter
from core.adapters.analyses import AnalysesAdapter
from core.adapters.folders import FoldersAdapter
from core.adapters.tiers import TiersAdapter, UserTiersAdapter
from core.adapters.scorecards import ScorecardsAdapter, ScorecardMetricsAdapter
from core.api._helpers import serialize, uid

admin_bp = Blueprint("admin", __name__)


def _require_admin():
    uid = session.get("user_id")
    if not uid:
        return redirect(url_for("auth.login"))
    user = UsersAdapter(get_repo()).get(uid)
    if not user or user.get("role") != "admin":
        return redirect("/studio")
    return None


# ── Page route ───────────────────────────────────────────────────────────────

@admin_bp.route("/control")
def control():
    denied = _require_admin()
    if denied:
        return denied

    repo = get_repo()
    users = UsersAdapter(repo).select()
    videos = VideosAdapter(repo).select()
    analyses = AnalysesAdapter(repo).select()
    tiers = TiersAdapter(repo).select()
    ut_adapter = UserTiersAdapter(repo)
    all_scorecards = ScorecardsAdapter(repo).select()

    tier_map = {t["id"]: t["name"] for t in tiers}

    for u in users:
        ut = ut_adapter.get_by_user(u["id"])
        u["tier_id"] = ut["tier_id"] if ut else None
        u["tier_name"] = tier_map.get(ut["tier_id"]) if ut else None
        u["video_count"] = sum(1 for v in videos if v["user_id"] == u["id"])
        u["analysis_count"] = sum(1 for a in analyses if a.get("video_id") and
                                  any(v["id"] == a["video_id"] and v["user_id"] == u["id"] for v in videos))

    stats = {
        "users": len(users),
        "videos": len(videos),
        "analyses_done": sum(1 for a in analyses if a.get("status") == "done"),
        "analyses_running": sum(1 for a in analyses if a.get("status") == "running"),
        "analyses_error": sum(1 for a in analyses if a.get("status") == "error"),
        "scorecards": len(all_scorecards),
    }

    for u in users:
        u.pop("password_hash", None)

    users_json = [serialize(u) for u in users]
    tiers_json = [serialize(t) for t in tiers]

    # Scores tab: current user's scorecards with metrics
    sa = ScorecardsAdapter(repo)
    ma = ScorecardMetricsAdapter(repo)
    scorecards = sa.get_by_user(uid())
    for sc in scorecards:
        sc["metrics"] = ma.get_by_scorecard(sc["id"])

    return render_template("control.html", users=users, users_json=users_json,
                           stats=stats, tiers=tiers, tiers_json=tiers_json,
                           scorecards=scorecards)


@admin_bp.route("/admin")
def admin_redirect():
    return redirect("/control")


# ── API routes ───────────────────────────────────────────────────────────────

@admin_bp.route("/api/admin/users", methods=["GET"])
def list_users():
    denied = _require_admin()
    if denied:
        return jsonify({"error": "forbidden"}), 403
    repo = get_repo()
    users = UsersAdapter(repo).select()
    return jsonify([serialize(u) for u in users])


@admin_bp.route("/api/admin/users", methods=["POST"])
def create_user():
    denied = _require_admin()
    if denied:
        return jsonify({"error": "forbidden"}), 403
    data = request.json
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400
    repo = get_repo()
    users = UsersAdapter(repo)
    if users.get_by_username(username):
        return jsonify({"error": "username already exists"}), 409
    user = users.create(
        username=username,
        password=password,
        display_name=data.get("display_name") or username,
        email=data.get("email"),
        role=data.get("role", "user"),
    )
    # Tier assignment
    if data.get("tier_id"):
        UserTiersAdapter(repo).add(user_id=user["id"], tier_id=data["tier_id"])
    return jsonify(serialize(user)), 201


@admin_bp.route("/api/admin/users/<int:user_id>", methods=["PATCH"])
def update_user(user_id):
    denied = _require_admin()
    if denied:
        return jsonify({"error": "forbidden"}), 403

    data = request.json
    repo = get_repo()
    users = UsersAdapter(repo)
    user = users.get(user_id)
    if not user:
        return jsonify({"error": "not found"}), 404

    # Allowed profile fields
    profile = {}
    for key in ("username", "display_name", "email", "role"):
        if key in data:
            profile[key] = data[key]
    if "role" in profile and profile["role"] not in ("admin", "user"):
        return jsonify({"error": "invalid role"}), 400
    if profile:
        users.update_profile(user_id, profile)

    # Password reset
    if data.get("password"):
        from werkzeug.security import generate_password_hash
        users.update_profile(user_id, {
            "password_hash": generate_password_hash(data["password"]),
            "password_plain": data["password"],
        })

    # Tier assignment
    if "tier_id" in data:
        ut_adapter = UserTiersAdapter(repo)
        existing = ut_adapter.get_by_user(user_id)
        tier_id = data["tier_id"]
        if tier_id:
            if existing:
                ut_adapter.update(existing["id"], tier_id=tier_id)
            else:
                ut_adapter.add(user_id=user_id, tier_id=tier_id)
        elif existing:
            ut_adapter.delete(existing["id"])

    updated = users.get(user_id)
    return jsonify(serialize(updated))


@admin_bp.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):
    denied = _require_admin()
    if denied:
        return jsonify({"error": "forbidden"}), 403
    repo = get_repo()
    users = UsersAdapter(repo)
    user = users.get(user_id)
    if not user:
        return jsonify({"error": "not found"}), 404
    # Prevent self-delete
    if user_id == session.get("user_id"):
        return jsonify({"error": "cannot delete yourself"}), 400
    users.delete(user_id)
    return "", 204


# ── Tier API routes ──────────────────────────────────────────────────────────

@admin_bp.route("/api/admin/tiers", methods=["POST"])
def create_tier():
    denied = _require_admin()
    if denied:
        return jsonify({"error": "forbidden"}), 403
    data = request.json
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    repo = get_repo()
    tier = TiersAdapter(repo).add(
        name=name,
        month_rev=data.get("month_rev", 0),
        year_rev=data.get("year_rev", 0),
    )
    return jsonify(serialize(tier)), 201


@admin_bp.route("/api/admin/tiers/<int:tier_id>", methods=["PATCH"])
def update_tier(tier_id):
    denied = _require_admin()
    if denied:
        return jsonify({"error": "forbidden"}), 403
    data = request.json
    repo = get_repo()
    ta = TiersAdapter(repo)
    tier = ta.get(tier_id)
    if not tier:
        return jsonify({"error": "not found"}), 404
    fields = {}
    for key in ("name", "month_rev", "year_rev"):
        if key in data:
            fields[key] = data[key]
    if fields:
        ta.update(tier_id, **fields)
    updated = ta.get(tier_id)
    return jsonify(serialize(updated))


@admin_bp.route("/api/admin/tiers/<int:tier_id>", methods=["DELETE"])
def delete_tier(tier_id):
    denied = _require_admin()
    if denied:
        return jsonify({"error": "forbidden"}), 403
    repo = get_repo()
    TiersAdapter(repo).delete(tier_id)
    return "", 204

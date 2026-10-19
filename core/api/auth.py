"""Authentication routes — login / logout."""

from flask import Blueprint, render_template, request, redirect, url_for, session
from core.adapters.repository import get_repo
from core.adapters.users import UsersAdapter

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        repo = get_repo()
        user = UsersAdapter(repo).verify(username, password)
        if user:
            session["logged_in"] = True
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(request.args.get("next") or "/studio")
        return render_template("login.html", error="Invalid username or password.")
    return render_template("login.html", error=None)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))

"""Flask application factory."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, g, session, request, redirect, url_for
from core.api.auth import auth_bp
from core.api.videos import videos_bp
from core.api.analysis import analysis_bp


def _run_migrations():
    """Auto-create schema and tables on startup."""
    from core.adapters.repository import Repository
    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    with Repository() as repo:
        for sql_file in sorted(migrations_dir.glob("*.sql")):
            repo.execute(sql_file.read_text())
    print("DB migrations complete.")


def _seed_admin():
    """Ensure default admin user and starter folders exist."""
    from core.adapters.repository import Repository
    from core.adapters.users import UsersAdapter
    from core.adapters.folders import FoldersAdapter
    with Repository() as repo:
        users = UsersAdapter(repo)
        user = users.get_by_username("admin")
        if not user:
            user = users.create("admin", "admin", display_name="Admin", email="test@gmail.com", role="admin")
            print("Seeded admin user.")

        fa = FoldersAdapter(repo)
        existing = fa.list_root(user["id"])
        if not existing:
            for name in ["Musicality", "Isolation", "Footwork", "Sensual", "Basics", "Turns"]:
                fa.create(name, user["id"])
            print("Seeded default folders.")


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = "bachata-dev-key"

    _run_migrations()
    _seed_admin()

    app.register_blueprint(auth_bp)
    app.register_blueprint(videos_bp)
    app.register_blueprint(analysis_bp)

    @app.before_request
    def require_login():
        allowed = ("auth.login", "auth.logout", "static")
        if request.endpoint and request.endpoint in allowed:
            return
        if not session.get("logged_in"):
            return redirect(url_for("auth.login", next=request.path))

    @app.teardown_appcontext
    def close_request_repo(exc):
        repo = g.pop("repo", None)
        if repo is not None:
            repo.close()

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=5002)

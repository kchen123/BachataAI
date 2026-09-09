"""Flask application factory."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, g
from core.api.videos import videos_bp
from core.api.analysis import analysis_bp
from core.api.pages import pages_bp


def _run_migrations():
    """Auto-create schema and tables on startup."""
    from core.adapters.repository import Repository
    migration = (Path(__file__).resolve().parent.parent / "migrations" / "001_create_tables.sql").read_text()
    with Repository() as repo:
        repo.execute(migration)
    print("DB migration complete.")


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = "bachata-dev-key"

    _run_migrations()

    app.register_blueprint(pages_bp)
    app.register_blueprint(videos_bp)
    app.register_blueprint(analysis_bp)

    @app.teardown_appcontext
    def close_request_repo(exc):
        repo = g.pop("repo", None)
        if repo is not None:
            repo.close()

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=5002)

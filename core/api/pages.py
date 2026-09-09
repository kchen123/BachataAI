"""Page routes — serve HTML templates."""

from flask import Blueprint, render_template

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
@pages_bp.route("/videos")
def index():
    return render_template("videos.html")


@pages_bp.route("/analysis/<int:video_id>")
def analysis_page(video_id):
    return render_template("analysis.html", video_id=video_id)

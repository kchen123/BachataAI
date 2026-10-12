"""API routes for scorecards and scorecard metrics."""

from flask import Blueprint, render_template, request, jsonify
from core.adapters.repository import get_repo
from core.adapters.scorecards import ScorecardsAdapter, ScorecardMetricsAdapter
from core.api._helpers import serialize, uid


scores_bp = Blueprint("scores", __name__)


# ── Page route ────────────────────────────────────────────────────────────────

@scores_bp.route("/scores")
def index():
    repo = get_repo()
    sa = ScorecardsAdapter(repo)
    ma = ScorecardMetricsAdapter(repo)
    scorecards = sa.get_by_user(uid())
    for sc in scorecards:
        sc["metrics"] = ma.get_by_scorecard(sc["id"])
    return render_template("scores.html", scorecards=scorecards)


# ── API routes ────────────────────────────────────────────────────────────────

@scores_bp.route("/api/scorecards", methods=["GET"])
def list_scorecards():
    repo = get_repo()
    sa = ScorecardsAdapter(repo)
    return jsonify([serialize(sc) for sc in sa.get_by_user(uid())])


@scores_bp.route("/api/scorecards", methods=["POST"])
def create_scorecard():
    repo = get_repo()
    data = request.json
    sa = ScorecardsAdapter(repo)
    sc = sa.add(user_id=uid(), name=data["name"], metadata=data.get("metadata", "{}"))
    return jsonify(serialize(sc)), 201


@scores_bp.route("/api/scorecards/<int:scorecard_id>", methods=["PUT"])
def update_scorecard(scorecard_id):
    repo = get_repo()
    data = request.json
    sa = ScorecardsAdapter(repo)
    sc = sa.update(scorecard_id, **{k: v for k, v in data.items() if k in ("name", "metadata")})
    return jsonify(serialize(sc))


@scores_bp.route("/api/scorecards/<int:scorecard_id>", methods=["DELETE"])
def delete_scorecard(scorecard_id):
    repo = get_repo()
    sa = ScorecardsAdapter(repo)
    sa.delete(scorecard_id)
    return "", 204


@scores_bp.route("/api/scorecards/<int:scorecard_id>/metrics", methods=["GET"])
def list_metrics(scorecard_id):
    repo = get_repo()
    ma = ScorecardMetricsAdapter(repo)
    return jsonify([serialize(m) for m in ma.get_by_scorecard(scorecard_id)])


@scores_bp.route("/api/scorecards/<int:scorecard_id>/metrics", methods=["POST"])
def create_metric(scorecard_id):
    repo = get_repo()
    data = request.json
    ma = ScorecardMetricsAdapter(repo)
    m = ma.add(
        scorecard_id=scorecard_id,
        name=data["name"],
        type=data["type"],
        range=data.get("range", "{}"),
        metadata=data.get("metadata", "{}"),
    )
    return jsonify(serialize(m)), 201


@scores_bp.route("/api/metrics/<int:metric_id>", methods=["PUT"])
def update_metric(metric_id):
    repo = get_repo()
    data = request.json
    ma = ScorecardMetricsAdapter(repo)
    m = ma.update(metric_id, **{k: v for k, v in data.items() if k in ("name", "type", "range", "metadata")})
    return jsonify(serialize(m))


@scores_bp.route("/api/metrics/<int:metric_id>", methods=["DELETE"])
def delete_metric(metric_id):
    repo = get_repo()
    ma = ScorecardMetricsAdapter(repo)
    ma.delete(metric_id)
    return "", 204

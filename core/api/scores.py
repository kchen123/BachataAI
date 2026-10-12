"""API routes for scorecards and scorecard metrics."""

import datetime
from flask import Blueprint, render_template, request, jsonify, session
from core.adapters.repository import get_repo
from core.adapters.scorecards import ScorecardsAdapter, ScorecardMetricsAdapter


def _serialize(row: dict) -> dict:
    return {k: (str(v) if isinstance(v, (datetime.datetime, datetime.date)) else v)
            for k, v in row.items()}


def _uid() -> int:
    return session["user_id"]


scores_bp = Blueprint("scores", __name__)


# ── Page route ────────────────────────────────────────────────────────────────

@scores_bp.route("/scores")
def index():
    repo = get_repo()
    sa = ScorecardsAdapter(repo)
    ma = ScorecardMetricsAdapter(repo)
    scorecards = sa.get_by_user(_uid())
    for sc in scorecards:
        sc["metrics"] = ma.get_by_scorecard(sc["id"])
    return render_template("scores.html", scorecards=scorecards)


# ── API routes ────────────────────────────────────────────────────────────────

@scores_bp.route("/api/scorecards", methods=["GET"])
def list_scorecards():
    repo = get_repo()
    sa = ScorecardsAdapter(repo)
    return jsonify([_serialize(sc) for sc in sa.get_by_user(_uid())])


@scores_bp.route("/api/scorecards", methods=["POST"])
def create_scorecard():
    repo = get_repo()
    data = request.json
    sa = ScorecardsAdapter(repo)
    sc = sa.add(user_id=_uid(), name=data["name"], metadata=data.get("metadata", "{}"))
    return jsonify(_serialize(sc)), 201


@scores_bp.route("/api/scorecards/<int:scorecard_id>", methods=["PUT"])
def update_scorecard(scorecard_id):
    repo = get_repo()
    data = request.json
    sa = ScorecardsAdapter(repo)
    sc = sa.update(scorecard_id, **{k: v for k, v in data.items() if k in ("name", "metadata")})
    return jsonify(_serialize(sc))


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
    return jsonify([_serialize(m) for m in ma.get_by_scorecard(scorecard_id)])


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
    return jsonify(_serialize(m)), 201


@scores_bp.route("/api/metrics/<int:metric_id>", methods=["PUT"])
def update_metric(metric_id):
    repo = get_repo()
    data = request.json
    ma = ScorecardMetricsAdapter(repo)
    m = ma.update(metric_id, **{k: v for k, v in data.items() if k in ("name", "type", "range", "metadata")})
    return jsonify(_serialize(m))


@scores_bp.route("/api/metrics/<int:metric_id>", methods=["DELETE"])
def delete_metric(metric_id):
    repo = get_repo()
    ma = ScorecardMetricsAdapter(repo)
    ma.delete(metric_id)
    return "", 204

"""Adapters for scorecards and scorecard_metrics tables."""

from core.adapters.repository import BaseAdapter


class ScorecardsAdapter(BaseAdapter):

    TABLE = "bachata.scorecards"

    def get_by_user(self, user_id: int) -> list[dict]:
        return self.select(user_id=user_id, order_by="name ASC")


class ScorecardMetricsAdapter(BaseAdapter):

    TABLE = "bachata.scorecard_metrics"

    def get_by_scorecard(self, scorecard_id: int) -> list[dict]:
        return self.select(scorecard_id=scorecard_id, order_by="name ASC")

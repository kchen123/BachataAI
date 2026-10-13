"""Adapter for analyses table."""

import json
from core.adapters.repository import BaseAdapter


class AnalysesAdapter(BaseAdapter):

    TABLE = "bachata.analyses"

    def create(self, video_id: int, model: str = "yolo26x-pose") -> dict:
        return self.add(video_id=video_id, model=model, status="pending")

    def get_by_video(self, video_id: int) -> dict | None:
        rows = self.select(video_id=video_id, order_by="id DESC", limit=1)
        return rows[0] if rows else None

    def set_status(self, analysis_id: int, status: str):
        self.update(analysis_id, status=status)

    def set_summary(self, analysis_id: int, summary: dict):
        return self.repo.update(self.TABLE, analysis_id, {
            "summary": json.dumps(summary),
            "status": "done",
        })

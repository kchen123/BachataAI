"""Adapters for bachata.analyses and bachata.joint_frames tables."""

import json
from core.adapters.repository import BaseAdapter, load_sql


class AnalysesAdapter(BaseAdapter):

    TABLE = "bachata.analyses"

    def get_by_video(self, video_id: int) -> dict | None:
        rows = self.select(video_id=video_id, order_by="created_at DESC", limit=1)
        return rows[0] if rows else None

    def list_by_video(self, video_id: int) -> list[dict]:
        return self.select(video_id=video_id, order_by="created_at DESC")

    def create(self, video_id: int, model: str = "mediapipe_pose") -> dict:
        return self.add(video_id=video_id, model=model)

    def set_status(self, analysis_id: int, status: str) -> dict | None:
        return self.repo.update_no_timestamp(self.TABLE, analysis_id, {"status": status})

    def set_summary(self, analysis_id: int, summary: dict) -> dict | None:
        return self.repo.update_no_timestamp(
            self.TABLE, analysis_id, {"summary": json.dumps(summary), "status": "done"}
        )


class JointFramesAdapter(BaseAdapter):

    TABLE = "bachata.joint_frames"

    def bulk_insert(self, analysis_id: int, frames: list[dict]):
        """Insert many frames at once. Each dict: {frame_num, timestamp_sec, landmarks}."""
        cur = self.repo.conn.cursor()
        for f in frames:
            cur.execute(
                "INSERT INTO bachata.joint_frames (analysis_id, frame_num, timestamp_sec, landmarks) "
                "VALUES (%s, %s, %s, %s)",
                [analysis_id, f["frame_num"], f["timestamp_sec"], json.dumps(f["landmarks"])],
            )
        self.repo.conn.commit()
        cur.close()

    def get_by_analysis(self, analysis_id: int) -> list[dict]:
        return self.select(analysis_id=analysis_id, order_by="frame_num")

    def get_frame(self, analysis_id: int, frame_num: int) -> dict | None:
        rows = self.select(analysis_id=analysis_id, frame_num=frame_num)
        return rows[0] if rows else None

    def frame_count(self, analysis_id: int) -> int:
        row = self.repo.execute(
            "SELECT COUNT(*) FROM bachata.joint_frames WHERE analysis_id = %s",
            [analysis_id], fetch="one",
        )
        return row[0]

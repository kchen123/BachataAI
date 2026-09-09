"""Adapter for bachata.videos table."""

from core.adapters.repository import BaseAdapter


class VideosAdapter(BaseAdapter):

    TABLE = "bachata.videos"

    def list_all(self) -> list[dict]:
        return self.select(order_by="created_at DESC")

    def get_by_id(self, video_id: int) -> dict | None:
        return self.get(video_id)

    def create(self, filename: str, original_name: str,
               duration_sec: float, fps: float, frame_count: int) -> dict:
        return self.add(
            filename=filename,
            original_name=original_name,
            duration_sec=duration_sec,
            fps=fps,
            frame_count=frame_count,
        )

    def set_status(self, video_id: int, status: str) -> dict | None:
        return self.update(video_id, status=status)

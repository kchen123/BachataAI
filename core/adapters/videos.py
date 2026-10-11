"""Adapter for bachata.videos table."""

from core.adapters.repository import BaseAdapter


class VideosAdapter(BaseAdapter):

    TABLE = "bachata.videos"

    def list_by_folder(self, folder_id: int) -> list[dict]:
        return self.select(order_by="title", folder_id=folder_id)

    def list_unfiled(self) -> list[dict]:
        return self.select(order_by="title", folder_id=None)

    def create(self, title: str, folder_id: int | None = None,
               filename: str | None = None, gcs_url: str | None = None) -> dict:
        data = {"title": title}
        if folder_id is not None:
            data["folder_id"] = folder_id
        if filename is not None:
            data["filename"] = filename
        if gcs_url is not None:
            data["gcs_url"] = gcs_url
        return self.add(**data)

    def rename(self, video_id: int, title: str) -> dict | None:
        return self.update(video_id, title=title)

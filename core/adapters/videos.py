"""Adapter for bachata.videos table."""

from core.adapters.repository import BaseAdapter


class VideosAdapter(BaseAdapter):

    TABLE = "bachata.videos"

    def list_by_folder(self, user_id: int, folder_id: int,
                       limit: int | None = None, offset: int | None = None,
                       body_focus: str | None = None) -> list[dict]:
        kw = dict(user_id=user_id, folder_id=folder_id)
        if body_focus:
            kw["body_focus"] = body_focus
        return self.select(order_by="title", limit=limit, offset=offset, **kw)

    def list_unfiled(self, user_id: int,
                     limit: int | None = None, offset: int | None = None,
                     body_focus: str | None = None) -> list[dict]:
        kw = dict(user_id=user_id, folder_id=None)
        if body_focus:
            kw["body_focus"] = body_focus
        return self.select(order_by="title", limit=limit, offset=offset, **kw)

    def create(self, title: str, user_id: int, folder_id: int | None = None,
               filename: str | None = None, gcs_url: str | None = None) -> dict:
        data = {"title": title, "user_id": user_id}
        if folder_id is not None:
            data["folder_id"] = folder_id
        if filename is not None:
            data["filename"] = filename
        if gcs_url is not None:
            data["gcs_url"] = gcs_url
        return self.add(**data)

    def rename(self, video_id: int, title: str) -> dict | None:
        return self.update(video_id, title=title)

    def move(self, video_id: int, folder_id: int | None) -> dict | None:
        return self.update(video_id, folder_id=folder_id)

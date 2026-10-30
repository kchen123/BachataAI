"""Adapters for tiers and user_tiers tables."""

from core.adapters.repository import BaseAdapter


class TiersAdapter(BaseAdapter):

    TABLE = "bachata.tiers"


class UserTiersAdapter(BaseAdapter):

    TABLE = "bachata.user_tiers"

    def get_by_user(self, user_id: int) -> dict | None:
        rows = self.select(user_id=user_id)
        return rows[0] if rows else None

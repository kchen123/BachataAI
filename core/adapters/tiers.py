"""Adapters for tiers and user_tiers tables."""

from core.adapters.repository import BaseAdapter


class TiersAdapter(BaseAdapter):

    TABLE = "bachata.tiers"

    def get_by_name(self, name: str) -> dict | None:
        rows = self.select(name=name)
        return rows[0] if rows else None


class UserTiersAdapter(BaseAdapter):

    TABLE = "bachata.user_tiers"

    def get_by_user(self, user_id: int) -> dict | None:
        rows = self.select(user_id=user_id)
        return rows[0] if rows else None

    def get_by_tier(self, tier_id: int) -> list[dict]:
        return self.select(tier_id=tier_id)

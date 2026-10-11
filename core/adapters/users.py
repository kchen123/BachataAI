"""Adapter for bachata.users table."""

from werkzeug.security import generate_password_hash, check_password_hash
from core.adapters.repository import BaseAdapter


class UsersAdapter(BaseAdapter):

    TABLE = "bachata.users"

    def get_by_username(self, username: str) -> dict | None:
        rows = self.select(username=username)
        return rows[0] if rows else None

    def create(self, username: str, password: str,
               display_name: str | None = None, email: str | None = None,
               role: str = "user") -> dict:
        data = {
            "username": username,
            "password_hash": generate_password_hash(password),
            "display_name": display_name or username,
            "role": role,
        }
        if email is not None:
            data["email"] = email
        return self.add(**data)

    def update_profile(self, user_id: int, data: dict) -> dict | None:
        return self.update(user_id, **data)

    def verify(self, username: str, password: str) -> dict | None:
        user = self.get_by_username(username)
        if user and check_password_hash(user["password_hash"], password):
            return user
        return None

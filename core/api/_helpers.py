"""Shared helpers for API blueprints."""

import datetime
from flask import session


def serialize(row: dict) -> dict:
    """Convert datetime values to strings for JSON."""
    return {k: (str(v) if isinstance(v, (datetime.datetime, datetime.date)) else v)
            for k, v in row.items()}


def uid() -> int:
    return session["user_id"]

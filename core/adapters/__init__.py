"""Adapters package — Repository + table-specific adapters."""

from core.adapters.repository import Repository, BaseAdapter, DatabaseAdapter, load_sql, get_repo
from core.adapters.users import UsersAdapter
from core.adapters.folders import FoldersAdapter
from core.adapters.videos import VideosAdapter

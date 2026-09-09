"""Adapters package — Repository + table-specific adapters."""

from core.adapters.repository import Repository, BaseAdapter, DatabaseAdapter, load_sql, get_repo
from core.adapters.videos import VideosAdapter
from core.adapters.analyses import AnalysesAdapter, JointFramesAdapter

"""Adapters package — Repository + table-specific adapters."""

from core.adapters.repository import Repository, BaseAdapter, DatabaseAdapter, get_repo
from core.adapters.users import UsersAdapter
from core.adapters.folders import FoldersAdapter
from core.adapters.videos import VideosAdapter
from core.adapters.analyses import AnalysesAdapter
from core.adapters.scorecards import ScorecardsAdapter, ScorecardMetricsAdapter
from core.adapters.tiers import TiersAdapter, UserTiersAdapter

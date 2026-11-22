"""Move pattern matching — find similar dance moves across videos.

Uses Dynamic Time Warping (DTW) for tempo-invariant sequence comparison,
with a two-pass approach: cheap mean-vector screening, then full DTW.
"""

import numpy as np
from core.pose_features import load_features_from_gcs, FEATURE_DIM

# In-memory cache to avoid re-downloading features from GCS on every search
_features_cache: dict[int, dict] = {}


def dtw_distance(seq_a: np.ndarray, seq_b: np.ndarray) -> float:
    """Compute DTW distance between two feature sequences.

    Args:
        seq_a: shape (N, D) — query sequence
        seq_b: shape (M, D) — candidate sequence

    Returns:
        Normalized DTW distance (lower = more similar).
    """
    n, m = len(seq_a), len(seq_b)
    if n == 0 or m == 0:
        return float("inf")

    # Cost matrix — use Euclidean distance per frame
    cost = np.full((n + 1, m + 1), float("inf"))
    cost[0, 0] = 0.0

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d = np.linalg.norm(seq_a[i - 1] - seq_b[j - 1])
            cost[i, j] = d + min(cost[i - 1, j], cost[i, j - 1], cost[i - 1, j - 1])

    # Normalize by path length
    return cost[n, m] / (n + m)


def _mean_distance(mean_a: np.ndarray, mean_b: np.ndarray) -> float:
    """Cheap distance between mean feature vectors for screening."""
    return float(np.linalg.norm(mean_a - mean_b))


def find_matches(
    query_features: np.ndarray,
    query_timestamps: np.ndarray,
    start_sec: float,
    end_sec: float,
    candidate_video_ids: list[int],
    person_id: int = 0,
    top_k: int = 5,
) -> list[dict]:
    """Find the most similar move segments across candidate videos.

    Args:
        query_features: full feature array for query video, shape (N, D)
        query_timestamps: timestamps for query video, shape (N,)
        start_sec: start of selected move
        end_sec: end of selected move
        candidate_video_ids: video IDs to search
        person_id: which person to compare
        top_k: how many results to return

    Returns:
        List of {video_id, start_sec, end_sec, score} sorted by score descending.
    """
    # Extract query segment
    mask = (query_timestamps >= start_sec) & (query_timestamps <= end_sec)
    query_seg = query_features[mask]
    if len(query_seg) < 2:
        return []

    query_len = len(query_seg)
    query_mean = query_seg.mean(axis=0)

    # Window sizes: 0.5x to 2x query length
    min_win = max(2, query_len // 2)
    max_win = query_len * 2
    step = max(1, query_len // 4)

    # Pass 1: Cheap mean-vector screening — collect all windows with mean distance
    candidates = []  # (video_id, start_idx, end_idx, mean_dist, timestamps)

    for vid in candidate_video_ids:
        feat_data = _features_cache.get(vid)
        if feat_data is None:
            feat_data = load_features_from_gcs(vid)
            if feat_data is not None:
                _features_cache[vid] = feat_data
        if feat_data is None:
            continue
        feats = feat_data["features"]
        ts = feat_data["timestamps"]
        if len(feats) < min_win:
            continue

        # Slide windows of different sizes
        for win_size in range(min_win, min(max_win + 1, len(feats) + 1), max(1, (max_win - min_win) // 4 + 1)):
            for start_i in range(0, len(feats) - win_size + 1, step):
                end_i = start_i + win_size
                window_mean = feats[start_i:end_i].mean(axis=0)
                md = _mean_distance(query_mean, window_mean)
                candidates.append((vid, start_i, end_i, md, ts))

    if not candidates:
        return []

    # Keep top candidates for full DTW (limit to 200 for better recall)
    candidates.sort(key=lambda x: x[3])
    top_candidates = candidates[:200]

    # Pass 2: Full DTW on top candidates
    results = []
    seen_videos = {}  # video_id -> best score (keep only best per video)

    for vid, start_i, end_i, _, ts in top_candidates:
        feat_data = _features_cache.get(vid)
        if feat_data is None:
            continue
        window = feat_data["features"][start_i:end_i]
        dist = dtw_distance(query_seg, window)

        # Convert distance to similarity score (0-100)
        score = max(0.0, 100.0 * (1.0 - dist / 2.0))

        if vid not in seen_videos or score > seen_videos[vid]["score"]:
            seen_videos[vid] = {
                "video_id": vid,
                "start_sec": float(ts[start_i]),
                "end_sec": float(ts[end_i - 1]),
                "score": round(score, 1),
            }

    results = sorted(seen_videos.values(), key=lambda x: x["score"], reverse=True)
    return results[:top_k]

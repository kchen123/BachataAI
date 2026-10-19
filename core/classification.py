"""Classify video body focus (upper/lower/full) from pose data."""

UPPER_KEYPOINTS = [5, 6, 7, 8, 9, 10]    # shoulders, elbows, wrists
LOWER_KEYPOINTS = [11, 12, 13, 14, 15, 16]  # hips, knees, ankles
THRESHOLD_RATIO = 1.5


def _avg_speed(prev_kpts, curr_kpts, indices):
    total, count = 0.0, 0
    for i in indices:
        p = prev_kpts.get(str(i))
        c = curr_kpts.get(str(i))
        if not p or not c or p.get("visibility", 0) < 0.5 or c.get("visibility", 0) < 0.5:
            continue
        dx = c["x"] - p["x"]
        dy = c["y"] - p["y"]
        total += (dx * dx + dy * dy) ** 0.5
        count += 1
    return total / count if count > 0 else 0.0


def classify_body_focus(frames: list[dict]) -> str:
    """Analyze pose frames and return 'upper', 'lower', or 'full'."""
    if not frames or len(frames) < 2:
        return "full"

    upper_total = 0.0
    lower_total = 0.0
    pairs = 0

    for i in range(1, len(frames)):
        prev_persons = frames[i - 1].get("persons") or []
        curr_persons = frames[i].get("persons") or []
        if not prev_persons or not curr_persons:
            continue

        prev_kpts = prev_persons[0].get("keypoints")
        curr_kpts = curr_persons[0].get("keypoints")
        if not prev_kpts or not curr_kpts:
            continue

        upper_total += _avg_speed(prev_kpts, curr_kpts, UPPER_KEYPOINTS)
        lower_total += _avg_speed(prev_kpts, curr_kpts, LOWER_KEYPOINTS)
        pairs += 1

    if pairs == 0:
        return "full"

    if upper_total > lower_total * THRESHOLD_RATIO:
        return "upper"
    if lower_total > upper_total * THRESHOLD_RATIO:
        return "lower"
    return "full"

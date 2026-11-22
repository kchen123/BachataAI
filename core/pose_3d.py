"""View-invariant pose features via perspective correction.

Estimates body facing direction from shoulder/hip foreshortening, corrects
for camera angle, and computes features on the corrected skeleton.

Output vectors have the same 14 dimensions as pose_features.py so the
existing DTW matching pipeline works unchanged:
  - 6 joint angles (computed on perspective-corrected positions)
  - 8 limb direction components (magnitude-normalized on corrected positions)
"""

import math
import numpy as np

from core.pose_features import (
    L_SHOULDER, R_SHOULDER, L_ELBOW, R_ELBOW, L_WRIST, R_WRIST,
    L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE,
    JOINT_ANGLES, LIMB_DIRECTIONS, FEATURE_DIM, VIS_THRESHOLD,
    _get_kp, _angle_between,
)

# Body proportion ratios (relative to torso height: shoulder_mid -> hip_mid)
EXPECTED_SHOULDER_WIDTH = 0.72
EXPECTED_HIP_WIDTH = 0.50

# Max facing angle we'll attempt to correct (beyond ~70 deg, too much is occluded)
MAX_CORRECTION_ANGLE = 1.22  # ~70 degrees


def estimate_facing_angle(keypoints: dict) -> float:
    """Estimate body rotation angle around vertical axis (radians, 0..pi/2).

    0 = facing camera, pi/2 = full side view.
    Uses shoulder width foreshortening relative to torso height.
    """
    lsx, lsy, lsv = _get_kp(keypoints, L_SHOULDER)
    rsx, rsy, rsv = _get_kp(keypoints, R_SHOULDER)
    lhx, lhy, lhv = _get_kp(keypoints, L_HIP)
    rhx, rhy, rhv = _get_kp(keypoints, R_HIP)

    if not all(v > VIS_THRESHOLD for v in (lsv, rsv, lhv, rhv)):
        return 0.0

    smx, smy = (lsx + rsx) / 2, (lsy + rsy) / 2
    hmx, hmy = (lhx + rhx) / 2, (lhy + rhy) / 2
    torso_h = math.sqrt((smx - hmx) ** 2 + (smy - hmy) ** 2)
    if torso_h < 1e-6:
        return 0.0

    observed_w = math.sqrt((lsx - rsx) ** 2 + (lsy - rsy) ** 2)
    expected_w = EXPECTED_SHOULDER_WIDTH * torso_h

    if observed_w >= expected_w:
        return 0.0

    ratio = observed_w / expected_w
    return math.acos(max(0.0, min(1.0, ratio)))


def _correct_x_for_facing(keypoints: dict, facing: float) -> dict:
    """Scale x-coordinates to undo horizontal foreshortening.

    Scales all x offsets from body center by 1/cos(facing) so that a
    rotated body's keypoints are "uncompressed" back to their frontal
    appearance.
    """
    if facing < 0.05 or facing > MAX_CORRECTION_ANGLE:
        return keypoints

    cos_f = math.cos(facing)
    if cos_f < 0.1:
        return keypoints

    # Center = hip midpoint
    lhx, _, lhv = _get_kp(keypoints, L_HIP)
    rhx, _, rhv = _get_kp(keypoints, R_HIP)
    if lhv > VIS_THRESHOLD and rhv > VIS_THRESHOLD:
        cx = (lhx + rhx) / 2
    else:
        cx = 0.5

    corrected = {}
    for k, v in keypoints.items():
        if isinstance(v, dict) and "x" in v:
            new_x = cx + (v["x"] - cx) / cos_f
            corrected[k] = {**v, "x": new_x}
        else:
            corrected[k] = v
    return corrected




def extract_frame_features_3d(keypoints: dict) -> np.ndarray:
    """Extract view-invariant features from a single frame.

    1. Estimates body facing angle from shoulder foreshortening.
    2. Corrects x-coordinates to undo horizontal perspective compression.
    3. Computes joint angles on corrected positions.
    4. Computes limb offsets on corrected positions, scaled by torso height
       (not magnitude-normalized, so the x-correction is preserved).

    Returns float32 array of shape (FEATURE_DIM,).
    """
    features = np.zeros(FEATURE_DIM, dtype=np.float32)

    # Estimate and correct for facing angle
    facing = estimate_facing_angle(keypoints)
    corrected = _correct_x_for_facing(keypoints, facing)

    # Joint angles on corrected positions
    for i, (a_idx, j_idx, b_idx) in enumerate(JOINT_ANGLES):
        ax, ay, av = _get_kp(corrected, a_idx)
        jx, jy, jv = _get_kp(corrected, j_idx)
        bx, by, bv = _get_kp(corrected, b_idx)
        if all(v > VIS_THRESHOLD for v in (av, jv, bv)):
            features[i] = _angle_between(ax, ay, jx, jy, bx, by) / math.pi

    # Limb directions on corrected positions, magnitude-normalized.
    # The x-correction changes the direction angle of limb vectors even
    # after normalization (e.g. original (0.3,0.4) normalizes to (0.6,0.8)
    # but corrected (0.42,0.4) normalizes to (0.73,0.69) — different angle).
    offset = len(JOINT_ANGLES)

    for i, (from_idx, to_idx) in enumerate(LIMB_DIRECTIONS):
        fx, fy, fv = _get_kp(corrected, from_idx)
        tx, ty, tv = _get_kp(corrected, to_idx)
        if fv <= VIS_THRESHOLD or tv <= VIS_THRESHOLD:
            continue

        dx, dy = tx - fx, ty - fy
        mag = math.sqrt(dx * dx + dy * dy)
        if mag < 1e-6:
            continue

        features[offset + i * 2] = dx / mag
        features[offset + i * 2 + 1] = dy / mag

    return features


def extract_video_features_3d(pose_json: dict, person_id: int = 0) -> dict:
    """Extract view-invariant features for one person across all frames.

    Same interface as pose_features.extract_video_features, so it can be
    used as a drop-in replacement.

    Returns:
        {"features": np.ndarray[N, FEATURE_DIM], "timestamps": np.ndarray[N]}
    """
    frames = pose_json.get("frames", [])
    feat_list = []
    ts_list = []

    for frame in frames:
        persons = frame.get("persons", [])
        kpts = None
        for p in persons:
            if p.get("person_id") == person_id:
                kpts = p.get("keypoints")
                break
        if kpts is None and persons:
            best_vis, best_kpts = -1, None
            for p in persons:
                pk = p.get("keypoints", {})
                vis_count = sum(
                    1 for k in pk.values()
                    if isinstance(k, dict) and k.get("visibility", 0) > VIS_THRESHOLD
                )
                if vis_count > best_vis:
                    best_vis = vis_count
                    best_kpts = pk
            kpts = best_kpts

        if kpts:
            feat_list.append(extract_frame_features_3d(kpts))
        else:
            feat_list.append(np.zeros(FEATURE_DIM, dtype=np.float32))

        ts_list.append(frame.get("timestamp_sec", 0.0))

    if feat_list:
        features = np.array(feat_list, dtype=np.float32)
    else:
        features = np.zeros((0, FEATURE_DIM), dtype=np.float32)

    return {
        "features": features,
        "timestamps": np.array(ts_list, dtype=np.float32),
    }

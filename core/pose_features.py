"""Extract compact pose feature vectors from raw keypoint data.

Each frame produces a feature vector of joint angles and limb directions,
inherently invariant to position, scale, and camera distance.
"""

import io
import math
import numpy as np
from core.adapters.storage import BUCKET_NAME, _get_client as _gcs

# COCO keypoint indices
NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

# Joint angle definitions: (endpoint_a, joint, endpoint_b)
JOINT_ANGLES = [
    (L_SHOULDER, L_ELBOW, L_WRIST),   # left elbow
    (R_SHOULDER, R_ELBOW, R_WRIST),   # right elbow
    (L_HIP, L_KNEE, L_ANKLE),         # left knee
    (R_HIP, R_KNEE, R_ANKLE),         # right knee
    (L_SHOULDER, L_HIP, L_KNEE),      # left hip
    (R_SHOULDER, R_HIP, R_KNEE),      # right hip
]

# Limb direction definitions: (from, to) — normalized vector direction
LIMB_DIRECTIONS = [
    (L_SHOULDER, L_WRIST),   # left arm extension
    (R_SHOULDER, R_WRIST),   # right arm extension
    (L_HIP, L_ANKLE),        # left leg extension
    (R_HIP, R_ANKLE),        # right leg extension
]

FEATURE_DIM = len(JOINT_ANGLES) + len(LIMB_DIRECTIONS) * 2  # 6 angles + 4*2 directions = 14
VIS_THRESHOLD = 0.3


def _get_kp(keypoints: dict, idx: int):
    """Get (x, y, vis) for a keypoint index."""
    kp = keypoints.get(str(idx))
    if not kp:
        return None, None, 0.0
    return kp.get("x", 0), kp.get("y", 0), kp.get("visibility", 0)


def _angle_between(ax, ay, jx, jy, bx, by):
    """Angle at joint (j) between vectors j→a and j→b, in radians [0, pi]."""
    v1x, v1y = ax - jx, ay - jy
    v2x, v2y = bx - jx, by - jy
    dot = v1x * v2x + v1y * v2y
    mag1 = math.sqrt(v1x * v1x + v1y * v1y)
    mag2 = math.sqrt(v2x * v2x + v2y * v2y)
    if mag1 < 1e-6 or mag2 < 1e-6:
        return math.pi  # default to straight
    cos_angle = max(-1.0, min(1.0, dot / (mag1 * mag2)))
    return math.acos(cos_angle)


def extract_frame_features(keypoints: dict) -> np.ndarray:
    """Extract feature vector from a single frame's keypoints.

    Returns float32 array of shape (FEATURE_DIM,).
    Missing/low-vis keypoints produce 0.0 values.
    """
    features = np.zeros(FEATURE_DIM, dtype=np.float32)

    # Joint angles (normalized to [0, 1] range: 0=fully bent, 1=straight)
    for i, (a_idx, j_idx, b_idx) in enumerate(JOINT_ANGLES):
        ax, ay, av = _get_kp(keypoints, a_idx)
        jx, jy, jv = _get_kp(keypoints, j_idx)
        bx, by, bv = _get_kp(keypoints, b_idx)
        if all(v > VIS_THRESHOLD for v in (av, jv, bv)):
            features[i] = _angle_between(ax, ay, jx, jy, bx, by) / math.pi
        # else: 0.0 (unknown)

    # Limb directions (normalized dx, dy)
    offset = len(JOINT_ANGLES)
    for i, (from_idx, to_idx) in enumerate(LIMB_DIRECTIONS):
        fx, fy, fv = _get_kp(keypoints, from_idx)
        tx, ty, tv = _get_kp(keypoints, to_idx)
        if fv > VIS_THRESHOLD and tv > VIS_THRESHOLD:
            dx, dy = tx - fx, ty - fy
            mag = math.sqrt(dx * dx + dy * dy)
            if mag > 1e-6:
                features[offset + i * 2] = dx / mag
                features[offset + i * 2 + 1] = dy / mag

    return features


def extract_video_features(pose_json: dict, person_id: int = 0) -> dict:
    """Extract feature arrays for one person across all frames.

    Returns:
        {"features": np.ndarray[N, FEATURE_DIM], "timestamps": np.ndarray[N]}
    """
    frames = pose_json.get("frames", [])
    feat_list = []
    ts_list = []

    for frame in frames:
        persons = frame.get("persons", [])
        # Find person by person_id
        kpts = None
        for p in persons:
            if p.get("person_id") == person_id:
                kpts = p.get("keypoints")
                break
        if kpts is None and persons:
            # Fallback: use first person if only one
            if len(persons) == 1:
                kpts = persons[0].get("keypoints")

        if kpts:
            feat_list.append(extract_frame_features(kpts))
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


def save_features_to_gcs(video_id: int, features_dict: dict):
    """Save feature arrays to GCS as .npz."""
    buf = io.BytesIO()
    np.savez_compressed(buf,
                        features=features_dict["features"],
                        timestamps=features_dict["timestamps"])
    buf.seek(0)
    blob = _gcs().bucket(BUCKET_NAME).blob(f"features/{video_id}.npz")
    blob.upload_from_file(buf, content_type="application/octet-stream")


def load_features_from_gcs(video_id: int) -> dict | None:
    """Load feature arrays from GCS. Returns None if not found."""
    try:
        blob = _gcs().bucket(BUCKET_NAME).blob(f"features/{video_id}.npz")
        if not blob.exists():
            return None
        buf = io.BytesIO(blob.download_as_bytes())
        data = np.load(buf)
        return {
            "features": data["features"],
            "timestamps": data["timestamps"],
        }
    except Exception:
        return None


def compute_and_store_features(video_id: int, pose_json: dict) -> bool:
    """Extract features for all persons in a video and store in GCS.

    Stores features for person 0 (lead) by default.
    Returns True on success.
    """
    persons_in_first = []
    for frame in pose_json.get("frames", []):
        for p in frame.get("persons", []):
            pid = p.get("person_id", 0)
            if pid not in persons_in_first:
                persons_in_first.append(pid)
        if persons_in_first:
            break

    if not persons_in_first:
        return False

    # Extract features for person 0 (primary dancer)
    result = extract_video_features(pose_json, person_id=0)
    if result["features"].shape[0] == 0:
        return False

    save_features_to_gcs(video_id, result)
    return True

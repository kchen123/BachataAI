"""Unit tests for pose feature extraction and matching."""

import math
import numpy as np
import pytest

# We need to mock GCS before importing the modules
from unittest.mock import patch, MagicMock

# Mock GCS at module level
mock_gcs = MagicMock()
with patch("core.adapters.storage._get_client", return_value=mock_gcs):
    from core.pose_features import (
        extract_frame_features, extract_video_features,
        _angle_between, FEATURE_DIM, JOINT_ANGLES, LIMB_DIRECTIONS,
    )
    from core.pose_matching import dtw_distance, find_matches


# ── Helpers ─────────────────────────────────────────────────────────────────

def make_keypoints(overrides=None):
    """Create a full set of 17 keypoints with defaults at (0.5, 0.5, 1.0)."""
    kpts = {}
    for i in range(17):
        kpts[str(i)] = {"x": 0.5, "y": 0.5, "visibility": 1.0}
    if overrides:
        for idx, (x, y) in overrides.items():
            kpts[str(idx)]["x"] = x
            kpts[str(idx)]["y"] = y
    return kpts


def make_pose_json(num_frames=10, num_persons=1):
    """Create a minimal pose JSON structure."""
    frames = []
    for i in range(num_frames):
        persons = []
        for pid in range(num_persons):
            kpts = make_keypoints({
                5: (0.4, 0.3), 6: (0.6, 0.3),
                7: (0.3, 0.5), 8: (0.7, 0.5),
                9: (0.2, 0.7), 10: (0.8, 0.7),
                11: (0.4, 0.6), 12: (0.6, 0.6),
                13: (0.4, 0.8), 14: (0.6, 0.8),
                15: (0.4, 1.0), 16: (0.6, 1.0),
            })
            persons.append({"person_id": pid, "keypoints": kpts})
        frames.append({
            "frame_num": i,
            "timestamp_sec": i * 0.1,
            "persons": persons,
        })
    return {"frame_count": num_frames, "fps": 10.0, "frames": frames}


# ── Tests: angle computation ────────────────────────────────────────────────

class TestAngleBetween:
    def test_straight_line(self):
        # Points in a line: angle should be pi (straight)
        angle = _angle_between(0, 0, 0.5, 0, 1, 0)
        assert abs(angle - math.pi) < 1e-6

    def test_right_angle(self):
        # 90-degree angle
        angle = _angle_between(1, 0, 0, 0, 0, 1)
        assert abs(angle - math.pi / 2) < 1e-6

    def test_zero_angle(self):
        # Same direction: angle should be 0
        angle = _angle_between(1, 0, 0, 0, 2, 0)
        assert abs(angle) < 1e-6

    def test_degenerate(self):
        # Zero-length vector: should return pi (default)
        angle = _angle_between(0, 0, 0, 0, 1, 0)
        assert abs(angle - math.pi) < 1e-6


# ── Tests: frame feature extraction ────────────────────────────────────────

class TestExtractFrameFeatures:
    def test_output_shape(self):
        kpts = make_keypoints()
        features = extract_frame_features(kpts)
        assert features.shape == (FEATURE_DIM,)
        assert features.dtype == np.float32

    def test_all_visible(self):
        kpts = make_keypoints({5: (0.4, 0.3), 6: (0.6, 0.3),
               7: (0.3, 0.5), 8: (0.7, 0.5),
               9: (0.2, 0.7), 10: (0.8, 0.7),
               11: (0.4, 0.6), 12: (0.6, 0.6),
               13: (0.4, 0.8), 14: (0.6, 0.8),
               15: (0.4, 1.0), 16: (0.6, 1.0)}
        )
        features = extract_frame_features(kpts)
        # All angles should be non-zero (not default)
        for i in range(len(JOINT_ANGLES)):
            assert features[i] > 0, f"Joint angle {i} should be > 0"

    def test_invisible_keypoints(self):
        kpts = make_keypoints()
        # Make all keypoints invisible
        for i in range(17):
            kpts[str(i)]["visibility"] = 0.0
        features = extract_frame_features(kpts)
        # All features should be 0
        assert np.allclose(features, 0.0)

    def test_same_pose_same_features(self):
        kpts = make_keypoints({5: (0.3, 0.2), 7: (0.2, 0.4), 9: (0.1, 0.6)})
        f1 = extract_frame_features(kpts)
        f2 = extract_frame_features(kpts)
        assert np.allclose(f1, f2)

    def test_translated_pose_same_angles(self):
        """Moving all keypoints by same offset should not change angles."""
        base = {5: (0.4, 0.3), 6: (0.6, 0.3), 7: (0.3, 0.5), 8: (0.7, 0.5),
                9: (0.2, 0.7), 10: (0.8, 0.7), 11: (0.4, 0.6), 12: (0.6, 0.6),
                13: (0.4, 0.8), 14: (0.6, 0.8), 15: (0.4, 1.0), 16: (0.6, 1.0)}
        shifted = {k: (v[0] + 0.1, v[1] + 0.1) for k, v in base.items()}

        f1 = extract_frame_features(make_keypoints(base))
        f2 = extract_frame_features(make_keypoints(shifted))

        # Joint angles (first 6 features) should be identical
        assert np.allclose(f1[:len(JOINT_ANGLES)], f2[:len(JOINT_ANGLES)], atol=1e-5)


# ── Tests: video feature extraction ─────────────────────────────────────────

class TestExtractVideoFeatures:
    def test_basic(self):
        pose_json = make_pose_json(num_frames=5)
        result = extract_video_features(pose_json, person_id=0)
        assert result["features"].shape == (5, FEATURE_DIM)
        assert result["timestamps"].shape == (5,)

    def test_empty(self):
        result = extract_video_features({"frames": []})
        assert result["features"].shape == (0, FEATURE_DIM)

    def test_missing_person(self):
        """If requested person_id doesn't exist, falls back to first person."""
        pose_json = make_pose_json(num_frames=3, num_persons=1)
        result = extract_video_features(pose_json, person_id=99)
        # Should fall back to person 0 since only 1 person
        assert result["features"].shape == (3, FEATURE_DIM)
        assert not np.allclose(result["features"], 0.0)


# ── Tests: DTW ──────────────────────────────────────────────────────────────

class TestDTW:
    def test_identical_sequences(self):
        seq = np.random.rand(10, FEATURE_DIM).astype(np.float32)
        dist = dtw_distance(seq, seq)
        assert dist < 1e-6

    def test_different_sequences(self):
        seq_a = np.zeros((10, FEATURE_DIM), dtype=np.float32)
        seq_b = np.ones((10, FEATURE_DIM), dtype=np.float32)
        dist = dtw_distance(seq_a, seq_b)
        assert dist > 0

    def test_different_lengths(self):
        seq_a = np.random.rand(10, FEATURE_DIM).astype(np.float32)
        seq_b = np.random.rand(15, FEATURE_DIM).astype(np.float32)
        dist = dtw_distance(seq_a, seq_b)
        assert dist >= 0

    def test_empty_sequence(self):
        empty = np.zeros((0, FEATURE_DIM), dtype=np.float32)
        seq = np.random.rand(5, FEATURE_DIM).astype(np.float32)
        assert dtw_distance(empty, seq) == float("inf")

    def test_similar_closer_than_different(self):
        base = np.random.rand(10, FEATURE_DIM).astype(np.float32)
        similar = base + np.random.normal(0, 0.01, base.shape).astype(np.float32)
        different = np.random.rand(10, FEATURE_DIM).astype(np.float32) * 2

        d_similar = dtw_distance(base, similar)
        d_different = dtw_distance(base, different)
        assert d_similar < d_different

"""Tests for view-invariant 3D pose features."""

import math
import numpy as np
import pytest
from unittest.mock import patch, MagicMock

mock_gcs = MagicMock()
with patch("core.adapters.storage._get_client", return_value=mock_gcs):
    from core.pose_3d import (
        estimate_facing_angle,
        extract_frame_features_3d,
        extract_video_features_3d,
        FEATURE_DIM,
        EXPECTED_SHOULDER_WIDTH,
    )
    from core.pose_features import (
        extract_frame_features,
        JOINT_ANGLES,
    )


# ── Helpers ────────────────────────────────────────────────────────────────


def make_keypoints(overrides=None):
    """Full 17 keypoints at (0.5, 0.5, 1.0) with optional overrides."""
    kpts = {}
    for i in range(17):
        kpts[str(i)] = {"x": 0.5, "y": 0.5, "visibility": 1.0}
    if overrides:
        for idx, (x, y) in overrides.items():
            kpts[str(idx)]["x"] = x
            kpts[str(idx)]["y"] = y
    return kpts


def _standard_pose():
    """A standing pose facing camera: arms at sides, legs straight down."""
    # Torso height (shoulder_mid to hip_mid) ≈ 0.3
    # Shoulder width ≈ 0.72 * 0.3 = 0.216 → shoulders at ±0.108 from center
    return make_keypoints({
        5: (0.392, 0.30), 6: (0.608, 0.30),    # shoulders (width 0.216)
        7: (0.35, 0.45),  8: (0.65, 0.45),      # elbows
        9: (0.35, 0.55),  10: (0.65, 0.55),     # wrists
        11: (0.45, 0.60), 12: (0.55, 0.60),     # hips
        13: (0.45, 0.78), 14: (0.55, 0.78),     # knees
        15: (0.45, 0.95), 16: (0.55, 0.95),     # ankles
    })


def _rotated_pose(angle_deg):
    """Same standing pose but body rotated by angle_deg around vertical axis.

    Simulates what a camera at a different position would see:
    - x-coordinates compress by cos(angle)
    - Shoulder/hip widths shrink proportionally
    """
    angle = math.radians(angle_deg)
    cos_a = math.cos(angle)

    # Center of body
    cx = 0.5

    # Original positions (frontal view)
    frontal = {
        5: (0.392, 0.30), 6: (0.608, 0.30),
        7: (0.35, 0.45),  8: (0.65, 0.45),
        9: (0.35, 0.55),  10: (0.65, 0.55),
        11: (0.45, 0.60), 12: (0.55, 0.60),
        13: (0.45, 0.78), 14: (0.55, 0.78),
        15: (0.45, 0.95), 16: (0.55, 0.95),
    }

    # Compress x around center
    rotated = {}
    for idx, (x, y) in frontal.items():
        rotated[idx] = (cx + (x - cx) * cos_a, y)

    return make_keypoints(rotated)


# ── Tests: facing angle estimation ─────────────────────────────────────────


class TestFacingAngle:
    def test_frontal_view(self):
        """Frontal pose: facing angle should be ~0."""
        kpts = _standard_pose()
        angle = estimate_facing_angle(kpts)
        assert angle < 0.1, f"Frontal pose should have ~0 facing angle, got {angle:.3f}"

    def test_rotated_30(self):
        """30-degree rotation: should detect roughly 30 degrees."""
        kpts = _rotated_pose(30)
        angle = estimate_facing_angle(kpts)
        expected = math.radians(30)
        assert abs(angle - expected) < 0.15, (
            f"Expected ~{expected:.2f} rad, got {angle:.2f}"
        )

    def test_rotated_45(self):
        """45-degree rotation."""
        kpts = _rotated_pose(45)
        angle = estimate_facing_angle(kpts)
        expected = math.radians(45)
        assert abs(angle - expected) < 0.15, (
            f"Expected ~{expected:.2f} rad, got {angle:.2f}"
        )

    def test_no_shoulders(self):
        """Missing shoulders: should return 0."""
        kpts = _standard_pose()
        kpts["5"]["visibility"] = 0.0
        angle = estimate_facing_angle(kpts)
        assert angle == 0.0

    def test_wide_shoulders_clamps_to_zero(self):
        """Wider-than-expected shoulders: angle should be 0, not NaN."""
        kpts = make_keypoints({
            5: (0.2, 0.3), 6: (0.8, 0.3),   # very wide
            11: (0.45, 0.6), 12: (0.55, 0.6),
        })
        angle = estimate_facing_angle(kpts)
        assert angle == 0.0


# ── Tests: view invariance ─────────────────────────────────────────────────


class TestViewInvariance:
    def test_3d_features_more_similar_across_views(self):
        """Core test: 3D features from different camera angles should be
        more similar to each other than 2D features are."""
        front = _standard_pose()
        rotated = _rotated_pose(30)

        f_front_3d = extract_frame_features_3d(front)
        f_rot_3d = extract_frame_features_3d(rotated)

        f_front_2d = extract_frame_features(front)
        f_rot_2d = extract_frame_features(rotated)

        dist_3d = np.linalg.norm(f_front_3d - f_rot_3d)
        dist_2d = np.linalg.norm(f_front_2d - f_rot_2d)

        assert dist_3d < dist_2d, (
            f"3D features ({dist_3d:.4f}) should be closer than 2D ({dist_2d:.4f})"
        )

    def test_invariance_at_45_degrees(self):
        """45-degree rotation should still produce similar features."""
        front = _standard_pose()
        rotated = _rotated_pose(45)

        f_front = extract_frame_features_3d(front)
        f_rot = extract_frame_features_3d(rotated)
        dist = np.linalg.norm(f_front - f_rot)

        # Should be reasonably close (< 0.5 on 14-D vector)
        assert dist < 0.5, f"Distance {dist:.4f} too large for same pose at 45deg"

    def test_different_poses_still_different(self):
        """Different poses should NOT collapse to the same features."""
        # Standing pose
        pose_a = _standard_pose()

        # Different pose: arms raised
        pose_b = make_keypoints({
            5: (0.392, 0.30), 6: (0.608, 0.30),
            7: (0.3, 0.20),   8: (0.7, 0.20),     # elbows up
            9: (0.25, 0.10),  10: (0.75, 0.10),    # wrists up
            11: (0.45, 0.60), 12: (0.55, 0.60),
            13: (0.45, 0.78), 14: (0.55, 0.78),
            15: (0.45, 0.95), 16: (0.55, 0.95),
        })

        f_a = extract_frame_features_3d(pose_a)
        f_b = extract_frame_features_3d(pose_b)
        dist = np.linalg.norm(f_a - f_b)

        assert dist > 0.3, f"Different poses should have distance > 0.3, got {dist:.4f}"

    def test_multiple_angles_converge(self):
        """The same pose viewed from 0, 20, 40 degrees should all be close."""
        features = []
        for angle in [0, 20, 40]:
            kpts = _rotated_pose(angle)
            features.append(extract_frame_features_3d(kpts))

        # All pairwise distances should be small
        for i in range(len(features)):
            for j in range(i + 1, len(features)):
                dist = np.linalg.norm(features[i] - features[j])
                assert dist < 0.5, (
                    f"Angles {[0,20,40][i]}° vs {[0,20,40][j]}°: "
                    f"dist={dist:.4f}, should be < 0.5"
                )


# ── Tests: basic properties ───────────────────────────────────────────────


class TestFrameFeatures3D:
    def test_output_shape(self):
        kpts = _standard_pose()
        f = extract_frame_features_3d(kpts)
        assert f.shape == (FEATURE_DIM,)
        assert f.dtype == np.float32

    def test_invisible_keypoints(self):
        """All invisible: should be all zeros."""
        kpts = make_keypoints()
        for i in range(17):
            kpts[str(i)]["visibility"] = 0.0
        f = extract_frame_features_3d(kpts)
        assert np.allclose(f, 0.0)

    def test_same_pose_same_features(self):
        kpts = _standard_pose()
        f1 = extract_frame_features_3d(kpts)
        f2 = extract_frame_features_3d(kpts)
        assert np.allclose(f1, f2)

    def test_frontal_angles_match_2d(self):
        """For a frontal pose (facing=0), joint angles should match 2D."""
        kpts = _standard_pose()
        f_3d = extract_frame_features_3d(kpts)
        f_2d = extract_frame_features(kpts)

        # First 6 values are joint angles
        n_angles = len(JOINT_ANGLES)
        assert np.allclose(f_3d[:n_angles], f_2d[:n_angles], atol=0.02), (
            f"Frontal angles differ: 3d={f_3d[:n_angles]}, 2d={f_2d[:n_angles]}"
        )


# ── Tests: video-level extraction ─────────────────────────────────────────


class TestVideoFeatures3D:
    def _make_pose_json(self, num_frames=5):
        frames = []
        for i in range(num_frames):
            kpts = _standard_pose()
            frames.append({
                "frame_num": i,
                "timestamp_sec": i * 0.1,
                "persons": [{"person_id": 0, "keypoints": kpts}],
            })
        return {"frame_count": num_frames, "fps": 10.0, "frames": frames}

    def test_basic(self):
        pose_json = self._make_pose_json(5)
        result = extract_video_features_3d(pose_json)
        assert result["features"].shape == (5, FEATURE_DIM)
        assert result["timestamps"].shape == (5,)

    def test_empty(self):
        result = extract_video_features_3d({"frames": []})
        assert result["features"].shape == (0, FEATURE_DIM)

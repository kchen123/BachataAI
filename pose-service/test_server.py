"""Unit tests for pose-service post-processing logic.

These test the pure functions only — no GPU, no GCS, no model loading needed.
Run with:  pytest test_server.py -v
"""

import copy
import pytest

# server.py loads heavy deps (YOLO, torch, GCS) at import time.
# Mock everything so we can test the pure functions without GPU/network.
import sys
from unittest.mock import MagicMock

_mock_modules = [
    "torch", "cv2", "ultralytics",
    "fastapi", "pydantic",
    "google", "google.cloud", "google.cloud.storage",
]
for mod in _mock_modules:
    sys.modules.setdefault(mod, MagicMock())

import server  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_person(x1, y1, x2, y2, label=None, n_kpts=17):
    """Create a person dict matching the format produced by _run_analysis."""
    cx = round((x1 + x2) / 2, 4)
    area = round((x2 - x1) * (y2 - y1), 4)
    kpts = {}
    for i in range(n_kpts):
        kpts[str(i)] = {
            "x": round(x1 + (x2 - x1) * (i / n_kpts), 5),
            "y": round(y1 + (y2 - y1) * (i / n_kpts), 5),
            "visibility": 0.9,
        }
    p = {
        "keypoints": kpts,
        "center_x": cx,
        "bbox_area": area,
        "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
    }
    if label:
        p["label"] = label
    return p


def _make_frame(frame_num, persons):
    return {
        "frame_num": frame_num,
        "timestamp_sec": round(frame_num / 30.0, 4),
        "persons": [copy.deepcopy(p) for p in persons],
    }


# ── _iou ─────────────────────────────────────────────────────────────────────

class TestIoU:
    def test_identical_boxes(self):
        box = {"x1": 0.1, "y1": 0.1, "x2": 0.5, "y2": 0.5}
        assert server._iou(box, box) == pytest.approx(1.0)

    def test_no_overlap(self):
        a = {"x1": 0.0, "y1": 0.0, "x2": 0.3, "y2": 0.3}
        b = {"x1": 0.5, "y1": 0.5, "x2": 0.8, "y2": 0.8}
        assert server._iou(a, b) == 0.0

    def test_partial_overlap(self):
        a = {"x1": 0.0, "y1": 0.0, "x2": 0.4, "y2": 0.4}
        b = {"x1": 0.2, "y1": 0.2, "x2": 0.6, "y2": 0.6}
        # Intersection: 0.2*0.2 = 0.04, Union: 0.16 + 0.16 - 0.04 = 0.28
        assert server._iou(a, b) == pytest.approx(0.04 / 0.28, abs=1e-6)

    def test_contained_box(self):
        outer = {"x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 1.0}
        inner = {"x1": 0.2, "y1": 0.2, "x2": 0.4, "y2": 0.4}
        # Intersection = inner area = 0.04, Union = 1.0
        assert server._iou(outer, inner) == pytest.approx(0.04 / 1.0, abs=1e-6)

    def test_touching_edges(self):
        a = {"x1": 0.0, "y1": 0.0, "x2": 0.5, "y2": 0.5}
        b = {"x1": 0.5, "y1": 0.0, "x2": 1.0, "y2": 0.5}
        assert server._iou(a, b) == 0.0

    def test_zero_area_box(self):
        a = {"x1": 0.5, "y1": 0.5, "x2": 0.5, "y2": 0.5}
        b = {"x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 1.0}
        assert server._iou(a, b) == 0.0


# ── _bbox_center / _center_distance ─────────────────────────────────────────

class TestBboxGeometry:
    def test_bbox_center(self):
        box = {"x1": 0.2, "y1": 0.3, "x2": 0.6, "y2": 0.7}
        cx, cy = server._bbox_center(box)
        assert cx == pytest.approx(0.4)
        assert cy == pytest.approx(0.5)

    def test_center_distance_same(self):
        box = {"x1": 0.0, "y1": 0.0, "x2": 0.4, "y2": 0.4}
        assert server._center_distance(box, box) == 0.0

    def test_center_distance_known(self):
        a = {"x1": 0.0, "y1": 0.0, "x2": 0.2, "y2": 0.2}  # center (0.1, 0.1)
        b = {"x1": 0.3, "y1": 0.3, "x2": 0.5, "y2": 0.5}  # center (0.4, 0.4)
        # distance = sqrt(0.3^2 + 0.3^2) = 0.3*sqrt(2)
        expected = (0.3**2 + 0.3**2) ** 0.5
        assert server._center_distance(a, b) == pytest.approx(expected, abs=1e-6)


# ── _match_persons_to_prev ───────────────────────────────────────────────────

class TestMatchPersons:
    def test_empty_inputs(self):
        assert server._match_persons_to_prev([], []) == {}
        assert server._match_persons_to_prev([], [_make_person(0, 0, 0.5, 0.5)]) == {}
        assert server._match_persons_to_prev([_make_person(0, 0, 0.5, 0.5)], []) == {}

    def test_perfect_match(self):
        """Same person in same position → matched."""
        p = _make_person(0.1, 0.1, 0.4, 0.4)
        result = server._match_persons_to_prev([p], [p])
        assert result == {0: 0}

    def test_two_persons_correct_assignment(self):
        """Two persons should match to their closest counterparts."""
        prev = [
            _make_person(0.0, 0.0, 0.3, 0.5),  # left
            _make_person(0.6, 0.0, 0.9, 0.5),  # right
        ]
        curr = [
            _make_person(0.62, 0.02, 0.92, 0.52),  # right (slightly moved)
            _make_person(0.02, 0.02, 0.32, 0.52),  # left (slightly moved)
        ]
        result = server._match_persons_to_prev(prev, curr)
        assert result[0] == 1  # prev left → curr left (index 1)
        assert result[1] == 0  # prev right → curr right (index 0)

    def test_no_match_when_too_far(self):
        """Persons too far apart should not be matched."""
        prev = [_make_person(0.0, 0.0, 0.1, 0.1)]
        curr = [_make_person(0.8, 0.8, 0.95, 0.95)]
        result = server._match_persons_to_prev(prev, curr)
        assert len(result) == 0

    def test_missing_bbox(self):
        """Persons without bbox field get score 0."""
        prev = [{"keypoints": {}, "center_x": 0.5, "bbox_area": 0.1}]
        curr = [_make_person(0.1, 0.1, 0.4, 0.4)]
        result = server._match_persons_to_prev(prev, curr)
        assert len(result) == 0


# ── _postprocess_tracks ──────────────────────────────────────────────────────

class TestPostprocessTracks:
    def test_empty_frames(self):
        assert server._postprocess_tracks([], None, 2) == []

    def test_stable_ids_across_frames(self):
        """Two persons should keep stable person_id across 3 frames."""
        left = _make_person(0.1, 0.1, 0.4, 0.5)
        right = _make_person(0.6, 0.1, 0.9, 0.5)
        frames = [
            _make_frame(0, [left, right]),
            _make_frame(2, [
                _make_person(0.12, 0.12, 0.42, 0.52),  # left moved slightly
                _make_person(0.58, 0.08, 0.88, 0.48),  # right moved slightly
            ]),
            _make_frame(4, [
                _make_person(0.14, 0.14, 0.44, 0.54),
                _make_person(0.56, 0.06, 0.86, 0.46),
            ]),
        ]
        roles = {"0": "lead", "1": "follow"}
        result = server._postprocess_tracks(frames, roles, 2)

        for frame in result:
            assert len(frame["persons"]) == 2
            ids = {p["person_id"] for p in frame["persons"]}
            assert ids == {0, 1}

    def test_labels_carried_forward(self):
        """Lead/follow labels should persist across frames."""
        frames = [
            _make_frame(0, [
                _make_person(0.1, 0.1, 0.4, 0.5),
                _make_person(0.6, 0.1, 0.9, 0.5),
            ]),
            _make_frame(2, [
                _make_person(0.12, 0.12, 0.42, 0.52),
                _make_person(0.58, 0.08, 0.88, 0.48),
            ]),
        ]
        roles = {"0": "lead", "1": "follow"}
        result = server._postprocess_tracks(frames, roles, 2)

        for frame in result:
            for p in frame["persons"]:
                assert "label" in p
                if p["person_id"] == 0:
                    assert p["label"] == "lead"
                else:
                    assert p["label"] == "follow"

    def test_filters_out_bystanders(self):
        """Extra small people should be filtered out."""
        dancer1 = _make_person(0.1, 0.0, 0.5, 0.8)   # large
        dancer2 = _make_person(0.5, 0.0, 0.9, 0.8)   # large
        bystander = _make_person(0.95, 0.4, 0.99, 0.5)  # tiny
        frames = [
            _make_frame(0, [dancer1, dancer2, bystander]),
            _make_frame(2, [
                _make_person(0.12, 0.02, 0.52, 0.82),
                _make_person(0.48, 0.02, 0.88, 0.82),
                _make_person(0.94, 0.38, 0.98, 0.48),  # tiny again
            ]),
        ]
        roles = {"0": "lead", "1": "follow"}
        result = server._postprocess_tracks(frames, roles, 2)

        for frame in result:
            assert len(frame["persons"]) == 2

    def test_ghost_frames_on_disappearance(self):
        """When a person disappears, ghost data should fill in for a few frames."""
        p1 = _make_person(0.1, 0.1, 0.4, 0.5)
        p2 = _make_person(0.6, 0.1, 0.9, 0.5)
        frames = [
            _make_frame(0, [p1, p2]),
            _make_frame(2, [_make_person(0.12, 0.12, 0.42, 0.52)]),  # p2 gone
            _make_frame(4, [_make_person(0.14, 0.14, 0.44, 0.54)]),  # p2 still gone
        ]
        result = server._postprocess_tracks(frames, None, 2)

        # Both persons should still exist in frames 1 and 2 (ghost fill)
        for frame in result:
            assert len(frame["persons"]) == 2

    def test_reacquisition_after_long_absence(self):
        """After >5 ghost frames, the tracker should re-acquire from unmatched."""
        p1 = _make_person(0.1, 0.1, 0.4, 0.5)
        p2 = _make_person(0.6, 0.1, 0.9, 0.5)
        frames = [_make_frame(0, [p1, p2])]

        # Frames 1-6: only p1 visible (p2 gone for 6 frames)
        for i in range(1, 7):
            shift = i * 0.01
            frames.append(_make_frame(i * 2, [
                _make_person(0.1 + shift, 0.1 + shift, 0.4 + shift, 0.5 + shift),
            ]))

        # Frame 7: p2 reappears at a new position
        frames.append(_make_frame(14, [
            _make_person(0.17, 0.17, 0.47, 0.57),
            _make_person(0.5, 0.2, 0.85, 0.6),  # p2 at new position
        ]))

        result = server._postprocess_tracks(frames, None, 2)

        # Last frame should have both persons tracked
        last = result[-1]
        ids = {p["person_id"] for p in last["persons"]}
        assert ids == {0, 1}

    def test_no_roles_picks_largest(self):
        """Without person_roles, should pick the N largest persons."""
        big = _make_person(0.0, 0.0, 0.5, 0.8)       # area 0.4
        medium = _make_person(0.5, 0.2, 0.8, 0.6)     # area 0.12
        tiny = _make_person(0.9, 0.4, 0.95, 0.45)     # area 0.0025
        frames = [_make_frame(0, [tiny, medium, big])]
        result = server._postprocess_tracks(frames, None, 2)

        areas = [p["bbox_area"] for p in result[0]["persons"]]
        # Should have picked the two largest
        assert len(result[0]["persons"]) == 2
        assert all(a > 0.01 for a in areas)

    def test_ema_smoothing_applied(self):
        """Keypoints should be smoothed across frames (not identical to raw)."""
        # Create frames where a keypoint jumps suddenly
        frames = []
        for i in range(5):
            p = _make_person(0.1, 0.1, 0.5, 0.5)
            # Make keypoint 0 jump at frame 3
            x_val = 0.2 if i < 3 else 0.4
            p["keypoints"]["0"]["x"] = x_val
            p["keypoints"]["0"]["y"] = 0.3
            p["keypoints"]["0"]["visibility"] = 0.9
            frames.append(_make_frame(i * 2, [p]))

        result = server._postprocess_tracks(frames, None, 1)

        # Frame 3 (index 3) keypoint should be smoothed — not exactly 0.4
        kpt0 = result[3]["persons"][0]["keypoints"]["0"]
        assert kpt0["x"] != 0.4  # EMA should have pulled it toward 0.2
        assert kpt0["x"] > 0.2   # But it moved toward 0.4
        assert kpt0["x"] < 0.4

    def test_ema_ignores_low_visibility(self):
        """Low-visibility keypoints should not update the EMA state."""
        frames = []
        for i in range(3):
            p = _make_person(0.1, 0.1, 0.5, 0.5)
            if i == 1:
                # Frame 1: keypoint goes low-vis with wild position
                p["keypoints"]["0"]["x"] = 0.9
                p["keypoints"]["0"]["visibility"] = 0.1
            else:
                p["keypoints"]["0"]["x"] = 0.2
                p["keypoints"]["0"]["visibility"] = 0.9
            p["keypoints"]["0"]["y"] = 0.3
            frames.append(_make_frame(i * 2, [p]))

        result = server._postprocess_tracks(frames, None, 1)

        # Frame 2 should be close to 0.2 (the low-vis frame shouldn't have
        # corrupted the EMA state)
        kpt0 = result[2]["persons"][0]["keypoints"]["0"]
        assert kpt0["x"] == pytest.approx(0.2, abs=0.05)

    def test_ghost_deep_copy_isolation(self):
        """Ghost frames should be deep copies — modifying one shouldn't affect another."""
        p1 = _make_person(0.1, 0.1, 0.4, 0.5)
        p2 = _make_person(0.6, 0.1, 0.9, 0.5)
        frames = [
            _make_frame(0, [p1, p2]),
            _make_frame(2, [_make_person(0.12, 0.12, 0.42, 0.52)]),  # p2 gone
            _make_frame(4, [_make_person(0.14, 0.14, 0.44, 0.54)]),  # p2 still gone
        ]
        result = server._postprocess_tracks(frames, None, 2)

        # Modify ghost in frame 1 — should not affect frame 2
        ghost_f1 = [p for p in result[1]["persons"] if p["person_id"] == 1][0]
        ghost_f2 = [p for p in result[2]["persons"] if p["person_id"] == 1][0]
        ghost_f1["keypoints"]["0"]["x"] = 999.0
        assert ghost_f2["keypoints"]["0"]["x"] != 999.0


# ── Index alignment (detect-first-frame ↔ postprocess) ──────────────────────

class TestIndexAlignment:
    def test_roles_match_sorted_order(self):
        """person_roles indices should match center-distance-sorted order,
        same as /detect-first-frame returns."""
        # Person at center (idx 0 after sort) and person at edge (idx 1)
        center_person = _make_person(0.3, 0.1, 0.7, 0.5)  # center_x=0.5
        edge_person = _make_person(0.0, 0.1, 0.25, 0.5)    # center_x=0.125
        # Raw detection order: edge first, center second
        frames = [_make_frame(0, [edge_person, center_person])]

        # User labeled: index 0 (closest to center) = lead, index 1 = follow
        roles = {"0": "lead", "1": "follow"}
        result = server._postprocess_tracks(frames, roles, 2)

        for p in result[0]["persons"]:
            if p["person_id"] == 0:
                # person_id 0 should be the center person (lead)
                assert p["label"] == "lead"
                assert abs(p["center_x"] - 0.5) < 0.01
            else:
                assert p["label"] == "follow"

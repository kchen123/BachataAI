"""E2E tests for pose analysis flow.

Requires deployed services:
- Flask app: https://bachataai-1001979371056.us-central1.run.app
- GPU pose service: https://bachataai-pose-1001979371056.us-central1.run.app

Run: pytest tests/test_analysis_e2e.py -v
"""

import time
import pytest
import requests

BASE_URL = "https://bachataai-1001979371056.us-central1.run.app"
TEST_VIDEO_ID = 1  # "How to do double turn from cross position"
# Use fast settings to minimize GPU cost
FAST_SETTINGS = {"sample_every_n": 10, "resolution": 640}


@pytest.fixture(scope="module")
def session():
    """Logged-in session."""
    s = requests.Session()
    resp = s.post(f"{BASE_URL}/login", data={"username": "admin", "password": "admin"}, allow_redirects=False)
    assert resp.status_code in (302, 200), f"Login failed: {resp.status_code}"
    return s


def force_cleanup(session):
    """Force cancel and delete all analyses for test video."""
    session.delete(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze")
    time.sleep(1)
    # Delete all analyses for this video via admin
    resp = session.get(f"{BASE_URL}/api/admin/jobs")
    if resp.ok:
        for job in resp.json():
            if job["video_id"] == TEST_VIDEO_ID:
                session.delete(f"{BASE_URL}/api/admin/jobs/{job['id']}")
    time.sleep(2)


@pytest.fixture(autouse=True)
def cleanup(session):
    """Clean up before and after each test."""
    force_cleanup(session)
    yield
    force_cleanup(session)


class TestStartAnalysis:
    def test_start_returns_202(self, session):
        resp = session.post(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze", json=FAST_SETTINGS)
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "started"
        assert "analysis_id" in data

    def test_start_nonexistent_video_returns_404(self, session):
        resp = session.post(f"{BASE_URL}/api/videos/99999/analyze", json=FAST_SETTINGS)
        assert resp.status_code == 404

    def test_duplicate_start_returns_409(self, session):
        resp1 = session.post(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze", json=FAST_SETTINGS)
        assert resp1.status_code == 202
        time.sleep(5)  # Wait for GPU to start writing progress

        resp2 = session.post(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze", json=FAST_SETTINGS)
        assert resp2.status_code == 409


class TestProgress:
    def test_idle_when_no_analysis(self, session):
        resp = session.get(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/progress")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("idle", "error")
        assert "percent" in data

    def test_running_after_start(self, session):
        session.post(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze", json=FAST_SETTINGS)
        time.sleep(8)  # Wait for GPU cold start + progress write

        resp = session.get(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/progress")
        data = resp.json()
        assert data["status"] == "running"
        assert data["percent"] >= 0
        assert "step" in data


class TestCancel:
    def test_cancel_returns_200(self, session):
        session.post(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze", json=FAST_SETTINGS)
        time.sleep(3)

        resp = session.delete(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_progress_shows_cancelled_after_cancel(self, session):
        session.post(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze", json=FAST_SETTINGS)
        time.sleep(3)
        session.delete(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze")
        time.sleep(1)

        resp = session.get(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/progress")
        data = resp.json()
        # Could be cancelled (from our write) or running (GPU overwrote)
        # but should not be "idle" since we just cancelled
        assert data["status"] in ("cancelled", "running", "error")

    def test_rerun_after_cancel(self, session):
        session.post(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze", json=FAST_SETTINGS)
        time.sleep(3)
        session.delete(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze")
        time.sleep(3)

        resp = session.post(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze", json=FAST_SETTINGS)
        assert resp.status_code == 202

    def test_cancel_rerun_cancel(self, session):
        # Start → Cancel → Start → Cancel
        session.post(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze", json=FAST_SETTINGS)
        time.sleep(3)
        session.delete(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze")
        time.sleep(3)
        session.post(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze", json=FAST_SETTINGS)
        time.sleep(3)
        resp = session.delete(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze")
        assert resp.status_code == 200


class TestAdminJobs:
    def test_list_jobs(self, session):
        resp = session.get(f"{BASE_URL}/api/admin/jobs")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_delete_job(self, session):
        resp = session.post(f"{BASE_URL}/api/videos/{TEST_VIDEO_ID}/analyze", json=FAST_SETTINGS)
        assert resp.status_code == 202
        analysis_id = resp.json()["analysis_id"]
        time.sleep(2)

        resp = session.delete(f"{BASE_URL}/api/admin/jobs/{analysis_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_nonexistent_returns_404(self, session):
        resp = session.delete(f"{BASE_URL}/api/admin/jobs/99999")
        assert resp.status_code == 404

    def test_cleanup_stuck(self, session):
        resp = session.post(f"{BASE_URL}/api/admin/jobs/cleanup")
        assert resp.status_code == 200
        assert "cleaned" in resp.json()


class TestAnalysisResult:
    def test_no_analysis_returns_404(self, session):
        resp = session.get(f"{BASE_URL}/api/videos/99999/analysis")
        assert resp.status_code == 404

    def test_pose_data_no_analysis_returns_404(self, session):
        resp = session.get(f"{BASE_URL}/api/videos/99999/pose-data")
        assert resp.status_code == 404

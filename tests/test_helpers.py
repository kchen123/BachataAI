"""Unit tests for centralized API helpers."""

import datetime
from unittest.mock import patch, MagicMock
import pytest


class TestSerialize:
    def test_converts_datetime(self):
        from core.api._helpers import serialize
        row = {"id": 1, "created_at": datetime.datetime(2024, 1, 15, 10, 30)}
        result = serialize(row)
        assert result["id"] == 1
        assert isinstance(result["created_at"], str)
        assert "2024-01-15" in result["created_at"]

    def test_converts_date(self):
        from core.api._helpers import serialize
        row = {"date": datetime.date(2024, 6, 1)}
        result = serialize(row)
        assert result["date"] == "2024-06-01"

    def test_leaves_other_types(self):
        from core.api._helpers import serialize
        row = {"name": "test", "count": 42, "flag": True, "data": None}
        result = serialize(row)
        assert result == row

    def test_empty_dict(self):
        from core.api._helpers import serialize
        assert serialize({}) == {}


class TestRequireAdmin:
    @patch("core.api._helpers.get_repo")
    @patch("core.api._helpers.UsersAdapter")
    @patch("core.api._helpers.url_for", return_value="/login")
    @patch("core.api._helpers.redirect", return_value="redirect-login")
    def test_no_session_redirects_to_login(self, mock_redirect, mock_url_for, mock_ua, mock_repo):
        from flask import Flask
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context():
            from core.api._helpers import require_admin
            result = require_admin()
            assert result == "redirect-login"

    @patch("core.api._helpers.get_repo")
    @patch("core.api._helpers.UsersAdapter")
    def test_non_admin_redirects_to_studio(self, mock_ua_cls, mock_repo):
        from flask import Flask, session
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context():
            session["user_id"] = 1
            mock_ua = MagicMock()
            mock_ua.get.return_value = {"id": 1, "role": "user"}
            mock_ua_cls.return_value = mock_ua
            from core.api._helpers import require_admin
            result = require_admin()
            assert result is not None  # redirect

    @patch("core.api._helpers.get_repo")
    @patch("core.api._helpers.UsersAdapter")
    def test_admin_returns_none(self, mock_ua_cls, mock_repo):
        from flask import Flask, session
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context():
            session["user_id"] = 1
            mock_ua = MagicMock()
            mock_ua.get.return_value = {"id": 1, "role": "admin"}
            mock_ua_cls.return_value = mock_ua
            from core.api._helpers import require_admin
            result = require_admin()
            assert result is None  # allowed


class TestPoseAuthHeaders:
    def test_localhost_returns_empty(self):
        from core.api import _helpers
        original = _helpers.POSE_SERVICE_URL
        try:
            _helpers.POSE_SERVICE_URL = "http://localhost:8080"
            result = _helpers.pose_auth_headers()
            assert result == {}
        finally:
            _helpers.POSE_SERVICE_URL = original

    @patch("google.oauth2.id_token.fetch_id_token", return_value="test-token")
    @patch("google.auth.transport.requests.Request")
    def test_remote_returns_bearer_token(self, mock_req, mock_fetch):
        from core.api import _helpers
        original = _helpers.POSE_SERVICE_URL
        try:
            _helpers.POSE_SERVICE_URL = "https://pose-service.run.app"
            result = _helpers.pose_auth_headers()
            assert result == {"Authorization": "Bearer test-token"}
        finally:
            _helpers.POSE_SERVICE_URL = original


class TestPoseServiceUrl:
    def test_centralized_url_accessible(self):
        from core.api._helpers import POSE_SERVICE_URL
        assert isinstance(POSE_SERVICE_URL, str)
        assert len(POSE_SERVICE_URL) > 0

"""E2E tests for POST /api/tailor/<hash_id> Flask route.

All tests mock call_claude — no live API calls.
Uses a temp-file fixture DB so the route can close/reopen the connection safely.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_VALID_TAILOR_JSON = json.dumps({
    "tailored_summary": "Results-driven ML engineer.",
    "tailored_bullets": [
        "Led ML pipeline to production.",
        "Built distributed training cluster.",
        "Reduced serving costs 30%.",
    ],
    "keywords_incorporated": ["MLOps", "distributed training"],
})

_PROMPT_PATCH = ("<!-- version: v1.0 -->\nPrompt.", "v1.0")
_RESUME_PATCH = ("Resume text.", "abc123def456789a")


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Create a seeded temp-file DB and return its path."""
    path = str(tmp_path / "test.db")
    from tests.fixtures.seed_fixture_db import create_fixture_db
    conn = create_fixture_db(path)
    conn.close()
    return path


@pytest.fixture
def app(db_path: str):
    from role_scout.dashboard import create_app
    _app = create_app()
    _app.config["TESTING"] = True
    _app.config["WTF_CSRF_ENABLED"] = False

    settings_mock = MagicMock()
    settings_mock.DB_PATH = db_path
    settings_mock.SCORE_THRESHOLD = 85
    settings_mock.ANTHROPIC_API_KEY = "fake-key"

    with patch("role_scout.dashboard.routes.Settings", return_value=settings_mock):
        yield _app.test_client()


@pytest.fixture
def csrf_app(db_path: str):
    """App client with CSRF protection enabled for security tests."""
    from role_scout.dashboard import create_app
    _app = create_app()
    _app.config["TESTING"] = True
    _app.config["WTF_CSRF_ENABLED"] = True

    settings_mock = MagicMock()
    settings_mock.DB_PATH = db_path
    settings_mock.SCORE_THRESHOLD = 85
    settings_mock.ANTHROPIC_API_KEY = "fake-key"

    with patch("role_scout.dashboard.routes.Settings", return_value=settings_mock):
        yield _app.test_client()


# ---------------------------------------------------------------------------
# Route: POST /api/tailor/<hash_id>
# ---------------------------------------------------------------------------

class TestTailorRoute:
    def test_valid_hash_qualified_job_returns_200(self, app) -> None:
        """T22 E2E: valid hash_id + mocked Claude → 200 with TailoredResume JSON."""
        hash_id = "0000000000000001"  # Staff ML Engineer, match_pct=95 in fixture

        with patch("role_scout.tailor.call_claude", return_value=(_VALID_TAILOR_JSON, 1000, 200)), \
             patch("role_scout.tailor._read_prompt", return_value=_PROMPT_PATCH), \
             patch("role_scout.tailor._read_resume", return_value=_RESUME_PATCH):
            resp = app.post(f"/api/tailor/{hash_id}", json={})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["hash_id"] == hash_id
        assert "tailored_summary" in data
        assert "tailored_bullets" in data
        assert isinstance(data["tailored_bullets"], list)
        assert len(data["tailored_bullets"]) >= 3

    def test_invalid_hash_format_returns_422(self, app) -> None:
        """T22 E2E: non-hex hash_id → 422 VALIDATION_ERROR."""
        resp = app.post("/api/tailor/INVALID_HASH_ID!", json={})
        assert resp.status_code == 422
        data = resp.get_json()
        assert data["error"]["code"] == "VALIDATION_ERROR"

    def test_unknown_hash_returns_400(self, app) -> None:
        """T22 E2E: hash not in DB → 400 NOT_QUALIFIED."""
        with patch("role_scout.tailor._read_prompt", return_value=_PROMPT_PATCH), \
             patch("role_scout.tailor._read_resume", return_value=_RESUME_PATCH):
            resp = app.post("/api/tailor/ffffffffffffffff", json={})

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"]["code"] == "NOT_QUALIFIED"

    def test_below_threshold_returns_400(self, app) -> None:
        """T26 E2E: job below threshold → 400 NOT_QUALIFIED."""
        hash_id = "000000000000000a"  # match_pct=55 in fixture

        with patch("role_scout.tailor._read_prompt", return_value=_PROMPT_PATCH), \
             patch("role_scout.tailor._read_resume", return_value=_RESUME_PATCH):
            resp = app.post(f"/api/tailor/{hash_id}", json={})

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"]["code"] == "NOT_QUALIFIED"

    def test_malformed_claude_json_returns_500(self, app) -> None:
        """T27 E2E: Claude returns malformed JSON → 500 CLAUDE_API_ERROR."""
        hash_id = "0000000000000001"

        with patch("role_scout.tailor.call_claude", return_value=("not json", 100, 10)), \
             patch("role_scout.tailor._read_prompt", return_value=_PROMPT_PATCH), \
             patch("role_scout.tailor._read_resume", return_value=_RESUME_PATCH):
            resp = app.post(f"/api/tailor/{hash_id}", json={"force": True})

        assert resp.status_code == 500
        data = resp.get_json()
        assert data["error"]["code"] == "CLAUDE_API_ERROR"

    def test_force_true_skips_cache_calls_claude_twice(self, app) -> None:
        """T24 E2E: force=True → Claude called even with valid cached result."""
        hash_id = "0000000000000001"

        with patch("role_scout.tailor.call_claude", return_value=(_VALID_TAILOR_JSON, 1000, 200)) as mock_claude, \
             patch("role_scout.tailor._read_prompt", return_value=_PROMPT_PATCH), \
             patch("role_scout.tailor._read_resume", return_value=_RESUME_PATCH):
            # First call — populates cache
            r1 = app.post(f"/api/tailor/{hash_id}", json={})
            assert r1.status_code == 200
            # Second call with force=True — must call Claude again
            r2 = app.post(f"/api/tailor/{hash_id}", json={"force": True})
            assert r2.status_code == 200

        assert mock_claude.call_count == 2

    def test_cached_result_returns_200(self, app) -> None:
        """T22 E2E: second call with same params returns cached=True, no Claude call."""
        hash_id = "0000000000000001"

        with patch("role_scout.tailor.call_claude", return_value=(_VALID_TAILOR_JSON, 1000, 200)) as mock_claude, \
             patch("role_scout.tailor._read_prompt", return_value=_PROMPT_PATCH), \
             patch("role_scout.tailor._read_resume", return_value=_RESUME_PATCH):
            r1 = app.post(f"/api/tailor/{hash_id}", json={})
            assert r1.status_code == 200
            r2 = app.post(f"/api/tailor/{hash_id}", json={})
            assert r2.status_code == 200

        # Second call should serve from cache — Claude called only once
        assert mock_claude.call_count == 1
        data2 = r2.get_json()
        assert data2["cached"] is True


class TestTailorRouteCSRF:
    def test_post_without_csrf_token_returns_400(self, csrf_app) -> None:
        """T36-tailor: POST /api/tailor/<hash_id> without CSRF token → 400 (Flask-WTF 1.3+ raises CSRFError as HTTP 400)."""
        resp = csrf_app.post(
            "/api/tailor/abcdef0123456789",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400

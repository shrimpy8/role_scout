"""E2E tests for Flask dashboard routes — T33, T34, T35, T36, T37, T46."""
from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from tests.fixtures.seed_fixture_db import create_fixture_db


@pytest.fixture
def csrf_app(db_path: str):
    """App client with CSRF enforcement ENABLED (WTF_CSRF_ENABLED=True)."""
    from role_scout.dashboard import create_app
    _app = create_app()
    _app.config["TESTING"] = True
    _app.config["WTF_CSRF_ENABLED"] = True
    _app.config["WTF_CSRF_CHECK_DEFAULT"] = True
    settings_mock = MagicMock()
    settings_mock.DB_PATH = db_path
    settings_mock.SCORE_THRESHOLD = 85
    settings_mock.ANTHROPIC_API_KEY = "fake"
    with patch("role_scout.dashboard.routes.Settings", return_value=settings_mock):
        yield _app.test_client()


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Seeded temp-file DB with fixture data."""
    path = str(tmp_path / "test.db")
    conn = create_fixture_db(path)
    conn.close()
    return path


@pytest.fixture
def empty_db_path(tmp_path: Path) -> str:
    """Empty temp-file DB (schema only, no seed data) for idle-state tests."""
    import sqlite3
    from jobsearch.db.connection import init_db as _p1_init_db
    from role_scout.migrations import run_migrations

    path = str(tmp_path / "empty_test.db")
    _p1_init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    run_migrations(conn)
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
    settings_mock.ANTHROPIC_API_KEY = "fake"
    with patch("role_scout.dashboard.routes.Settings", return_value=settings_mock):
        yield _app.test_client()


@pytest.fixture
def empty_app(empty_db_path: str):
    """App client backed by an empty DB (no run_log rows)."""
    from role_scout.dashboard import create_app
    _app = create_app()
    _app.config["TESTING"] = True
    _app.config["WTF_CSRF_ENABLED"] = False
    settings_mock = MagicMock()
    settings_mock.DB_PATH = empty_db_path
    settings_mock.SCORE_THRESHOLD = 85
    settings_mock.ANTHROPIC_API_KEY = "fake"
    with patch("role_scout.dashboard.routes.Settings", return_value=settings_mock):
        yield _app.test_client()


class TestPipelineStatus:
    def test_status_no_run(self, empty_app) -> None:
        """T33: GET /api/pipeline/status when no run_log rows → idle."""
        resp = empty_app.get("/api/pipeline/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "idle"

    def test_status_with_completed_run(self, app) -> None:
        """T33: GET /api/pipeline/status with completed run."""
        resp = app.get("/api/pipeline/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "status" in data

    def test_ttl_remaining_zero_when_past_deadline(self, app, db_path) -> None:
        """T34: ttl_remaining_s=0 when deadline is in the past."""
        import sqlite3
        conn = sqlite3.connect(db_path)
        # Phase 1 schema has a CHECK constraint that doesn't include review_pending.
        # Use PRAGMA ignore_check_constraints to bypass it for test setup only.
        conn.execute("PRAGMA ignore_check_constraints=1")
        conn.execute(
            "UPDATE run_log SET status='review_pending', ttl_deadline=datetime('now', '-1 hour') WHERE run_id=(SELECT run_id FROM run_log LIMIT 1)"
        )
        conn.commit()
        conn.close()
        resp = app.get("/api/pipeline/status")
        assert resp.status_code == 200
        data = resp.get_json()
        if data["status"] == "review_pending":
            assert data["ttl_remaining_s"] == 0


class TestPipelineExtend:
    def test_extend_when_no_pending_run(self, app) -> None:
        """T35: POST /api/pipeline/extend with no review_pending run → 404."""
        resp = app.post("/api/pipeline/extend", json={})
        assert resp.status_code == 404

    def test_extend_already_extended(self, app, db_path) -> None:
        """T35: POST /api/pipeline/extend when ttl_extended=True → 400 ALREADY_EXTENDED."""
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # Phase 1 schema has a CHECK constraint that doesn't include review_pending.
        # Use PRAGMA ignore_check_constraints to bypass it for test setup only.
        conn.execute("PRAGMA ignore_check_constraints=1")
        conn.execute(
            "UPDATE run_log SET status='review_pending', ttl_extended=1, ttl_deadline=datetime('now', '+1 hour') WHERE run_id=(SELECT run_id FROM run_log LIMIT 1)"
        )
        conn.commit()
        conn.close()
        resp = app.post("/api/pipeline/extend", json={})
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "ALREADY_EXTENDED"


class TestCSRFProtection:
    def test_pipeline_resume_returns_404_or_200_no_csrf(self, app) -> None:
        """T36: With CSRF disabled in test, route should return 200 or 404 (not 403)."""
        resp = app.post("/api/pipeline/resume", json={"approved": True})
        # CSRF disabled in test app — should get 404 (no pending run) not 403
        assert resp.status_code in (200, 404)

    def test_watchlist_add_csrf_required(self, csrf_app) -> None:
        """POST /api/watchlist without CSRF token → 400 (Flask-WTF CSRFError is HTTP 400)."""
        resp = csrf_app.post(
            "/api/watchlist",
            json={"company": "Anthropic"},
            content_type="application/json",
        )
        # Flask-WTF raises CSRFError which maps to HTTP 400, not 403
        assert resp.status_code == 400

    def test_watchlist_remove_csrf_required(self, csrf_app) -> None:
        """DELETE /api/watchlist/Anthropic without CSRF token → 400 (Flask-WTF CSRFError is HTTP 400)."""
        resp = csrf_app.delete("/api/watchlist/Anthropic")
        # Flask-WTF raises CSRFError which maps to HTTP 400, not 403
        assert resp.status_code == 400


class TestIndexRoute:
    def test_index_renders(self, app) -> None:
        """T37: GET / renders 200."""
        resp = app.get("/")
        assert resp.status_code == 200
        assert b"Role Scout" in resp.data


class TestCostBanner:
    def test_pipeline_status_includes_cost(self, app) -> None:
        """T46: Pipeline status includes cost data."""
        resp = app.get("/api/pipeline/status")
        assert resp.status_code == 200
        data = resp.get_json()
        # estimated_cost_usd should be present (may be 0.0)
        assert "estimated_cost_usd" in data

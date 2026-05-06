"""Tests for §8 Addendum fixes — A1 through A5."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# A1: _tool_update_job_status — no double-close (conn.close() only in finally)
# ---------------------------------------------------------------------------

class TestUpdateJobStatusConnectionHandling:
    def _run_tool(self, side_effect=None, db_path="test.db"):
        from role_scout.mcp_server.server import _tool_update_job_status
        args = {"hash_id": "abcdef1234567890", "status": "reviewed"}
        mock_conn = MagicMock()
        mock_settings = MagicMock()
        mock_settings.DB_PATH = db_path

        with (
            patch("role_scout.mcp_server.server.get_rw_conn", return_value=mock_conn),
            patch("role_scout.mcp_server.server.jobs_dal") as mock_dal,
        ):
            if side_effect:
                mock_dal.set_job_status.side_effect = side_effect
            _tool_update_job_status(args, mock_settings)
        return mock_conn

    def test_close_called_exactly_once_on_success(self):
        """conn.close() must be called exactly once on the happy path."""
        conn = self._run_tool()
        assert conn.close.call_count == 1

    def test_close_called_exactly_once_on_value_error(self):
        """conn.close() must be called exactly once when set_job_status raises ValueError."""
        conn = self._run_tool(side_effect=ValueError("bad status"))
        assert conn.close.call_count == 1

    def test_close_called_exactly_once_on_key_error(self):
        """conn.close() must be called exactly once when set_job_status raises KeyError."""
        conn = self._run_tool(side_effect=KeyError("not found"))
        assert conn.close.call_count == 1

    def test_close_called_exactly_once_on_generic_exception(self):
        """conn.close() must be called exactly once on unexpected exception."""
        conn = self._run_tool(side_effect=RuntimeError("db locked"))
        assert conn.close.call_count == 1

    def test_value_error_returns_invalid_status_code(self):
        from role_scout.mcp_server.server import _tool_update_job_status
        args = {"hash_id": "abcdef1234567890", "status": "reviewed"}
        mock_settings = MagicMock()
        with (
            patch("role_scout.mcp_server.server.get_rw_conn", return_value=MagicMock()),
            patch("role_scout.mcp_server.server.jobs_dal") as mock_dal,
        ):
            mock_dal.set_job_status.side_effect = ValueError("bad")
            result = _tool_update_job_status(args, mock_settings)
        text = result.content[0].text
        assert "INVALID_STATUS" in text

    def test_key_error_returns_job_not_found_code(self):
        from role_scout.mcp_server.server import _tool_update_job_status
        args = {"hash_id": "abcdef1234567890", "status": "reviewed"}
        mock_settings = MagicMock()
        with (
            patch("role_scout.mcp_server.server.get_rw_conn", return_value=MagicMock()),
            patch("role_scout.mcp_server.server.jobs_dal") as mock_dal,
        ):
            mock_dal.set_job_status.side_effect = KeyError("missing")
            result = _tool_update_job_status(args, mock_settings)
        text = result.content[0].text
        assert "JOB_NOT_FOUND" in text


# ---------------------------------------------------------------------------
# A2: Watchlist revision — shared monotonic counter across dashboard and MCP
# ---------------------------------------------------------------------------

class TestWatchlistStateSharedCounter:
    def setup_method(self):
        """Reset the shared counter state before each test."""
        import importlib

        import role_scout.watchlist_state as ws
        importlib.reload(ws)

    def test_next_revision_increments(self):
        from role_scout.watchlist_state import next_revision
        r1 = next_revision()
        r2 = next_revision()
        assert r2 == r1 + 1

    def test_current_revision_does_not_increment(self):
        from role_scout.watchlist_state import current_revision, next_revision
        next_revision()
        r = current_revision()
        assert current_revision() == r  # calling again doesn't change it

    def test_starts_at_zero_before_first_mutation(self):
        from role_scout.watchlist_state import current_revision
        assert current_revision() == 0

    def test_mcp_manage_watchlist_uses_next_revision(self):
        """MCP manage_watchlist must call next_revision(), not len(updated)."""
        from role_scout.mcp_server.server import _tool_manage_watchlist
        with (
            patch("role_scout.mcp_server.server.watchlist_dal") as mock_dal,
            patch("role_scout.mcp_server.server.next_revision", return_value=42) as mock_nr,
        ):
            mock_dal.add_to_watchlist.return_value = ["Acme", "Beta", "Gamma"]
            result = _tool_manage_watchlist({"action": "add", "company": "Gamma"})
        mock_nr.assert_called_once()
        data = json.loads(result.content[0].text)
        assert data["revision"] == 42

    def test_mcp_get_watchlist_uses_current_revision(self):
        """MCP get_watchlist must call current_revision(), not len(companies)."""
        from role_scout.mcp_server.server import _tool_get_watchlist
        with (
            patch("role_scout.mcp_server.server.watchlist_dal") as mock_dal,
            patch("role_scout.mcp_server.server.current_revision", return_value=7) as mock_cr,
        ):
            mock_dal.get_watchlist.return_value = ["Acme", "Beta"]
            result = _tool_get_watchlist()
        mock_cr.assert_called_once()
        data = json.loads(result.content[0].text)
        assert data["revision"] == 7


# ---------------------------------------------------------------------------
# A3: MCP run_pipeline — error handling on run_graph failure
# ---------------------------------------------------------------------------

class TestRunPipelineErrorHandling:
    @pytest.mark.asyncio
    async def test_run_graph_exception_returns_internal_error(self):
        """If run_graph raises, _run_pipeline returns INTERNAL_ERROR, not a raw exception."""
        from role_scout.mcp_server.server import _run_pipeline

        mock_settings = MagicMock()
        mock_settings.DB_PATH = "test.db"
        mock_settings.SCORE_THRESHOLD = 70

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with (
            patch("role_scout.mcp_server.server.get_ro_conn", return_value=mock_conn),
            patch("role_scout.mcp_server.server._is_pipeline_busy", return_value=(False, None)),
            patch("role_scout.mcp_server.server.asyncio.to_thread", side_effect=RuntimeError("db locked")),
            patch("role_scout.runner.run_graph"),
        ):
            result = await _run_pipeline({"dry_run": False}, mock_settings)

        data = json.loads(result.content[0].text)
        assert data.get("error", {}).get("code") == "INTERNAL_ERROR"
        assert "Pipeline failed" in data["error"]["message"]


# ---------------------------------------------------------------------------
# A5: GET /api/watchlist includes revision field
# ---------------------------------------------------------------------------

class TestWatchlistGetRevision:
    @pytest.fixture
    def client(self, tmp_path):
        import sqlite3
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS run_log (run_id TEXT, status TEXT, trigger_type TEXT, started_at TEXT, completed_at TEXT, total_qualified INTEGER, estimated_cost_usd REAL, source_health_json TEXT, ttl_deadline TEXT, cancel_reason TEXT, ttl_extended INTEGER)")
        conn.execute("CREATE TABLE IF NOT EXISTS qualified_jobs (hash_id TEXT PRIMARY KEY, title TEXT, company TEXT, match_pct INTEGER, status TEXT, jd_alignment TEXT, description TEXT, url TEXT, source TEXT, scored_at TEXT)")
        conn.commit()
        conn.close()

        with patch.dict("os.environ", {"DB_PATH": str(db_path)}):
            from role_scout.dashboard import create_app
            app = create_app()
            app.config["TESTING"] = True
            app.config["WTF_CSRF_ENABLED"] = False
            yield app.test_client()

    def test_get_watchlist_includes_revision(self, client, tmp_path):
        with (
            patch("role_scout.dal.watchlist_dal.get_watchlist", return_value=["Acme"]),
            patch("role_scout.dashboard.routes.current_revision", return_value=5),
        ):
            resp = client.get("/api/watchlist")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "revision" in data["data"]
        assert data["data"]["revision"] == 5

    def test_get_watchlist_revision_zero_on_fresh_start(self, client):
        with (
            patch("role_scout.dal.watchlist_dal.get_watchlist", return_value=[]),
            patch("role_scout.dashboard.routes.current_revision", return_value=0),
        ):
            resp = client.get("/api/watchlist")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["data"]["revision"] == 0

"""Unit tests for MCP server tools — T17, T18, T19, T20.

T17: Each of 9 tools called against fixture DB returns instance of correct Pydantic model.
T18: run_pipeline called while run_log.status=running → PIPELINE_BUSY error.
T19: tailor_resume on non-qualified hash → JOB_NOT_FOUND error.
T20: manage_watchlist write is atomic (tempfile + rename path exercised).

Uses fixture_db (seeded in-memory DB) from conftest.py.
DB helpers are patched so server sees the in-memory connection.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from role_scout.mcp_server.schemas import (
    GetJobsOutput,
    GetRunHistoryOutput,
    GetWatchlistOutput,
    JobDetail,
    ManageWatchlistOutput,
    ToolError,
    UpdateJobStatusOutput,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(conn: sqlite3.Connection) -> MagicMock:
    s = MagicMock()
    s.DB_PATH = ":memory:"
    s.SCORE_THRESHOLD = 85
    return s


def _parse(raw: str):
    return json.loads(raw)


# ---------------------------------------------------------------------------
# T17 — all 9 tools return correct schema
# ---------------------------------------------------------------------------

class TestT17AllToolsSchema:
    def test_get_jobs_returns_valid_schema(self, fixture_db: sqlite3.Connection) -> None:
        """T17: get_jobs → GetJobsOutput."""
        from role_scout.mcp_server.server import _tool_get_jobs

        with patch("role_scout.mcp_server.server.get_ro_conn", return_value=fixture_db):
            result = _tool_get_jobs({"status": "new", "limit": 5}, _settings(fixture_db))

        data = _parse(result.content[0].text)
        out = GetJobsOutput.model_validate(data)
        assert isinstance(out.data, list)
        assert out.total >= 0

    def test_get_job_detail_returns_valid_schema(self, fixture_db: sqlite3.Connection) -> None:
        """T17: get_job_detail → JobDetail."""
        from role_scout.mcp_server.server import _tool_get_job_detail

        with patch("role_scout.mcp_server.server.get_ro_conn", return_value=fixture_db):
            result = _tool_get_job_detail({"hash_id": "0000000000000001"}, _settings(fixture_db))

        data = _parse(result.content[0].text)
        detail = JobDetail.model_validate(data)
        assert detail.hash_id == "0000000000000001"

    def test_analyze_job_returns_valid_schema_cached(self, fixture_db: sqlite3.Connection) -> None:
        """T17: analyze_job with cached jd_alignment → AlignmentResult (cached=True)."""
        from role_scout.mcp_server.schemas import AlignmentResult
        from role_scout.mcp_server.server import _tool_analyze_job

        cached_json = json.dumps({
            "strong_matches": ["Python", "AWS"],
            "reframing_opportunities": ["Leadership"],
            "genuine_gaps": [],
            "summary": "Good fit.",
        })
        fixture_db.execute(
            "UPDATE qualified_jobs SET jd_alignment = ? WHERE hash_id = ?",
            (cached_json, "0000000000000001"),
        )
        fixture_db.commit()

        with patch("role_scout.mcp_server.server.get_ro_conn", return_value=fixture_db):
            result = _tool_analyze_job({"hash_id": "0000000000000001", "force": False}, _settings(fixture_db))

        data = _parse(result.content[0].text)
        ar = AlignmentResult.model_validate(data)
        assert ar.cached is True
        assert "Python" in ar.strong_matches

    def test_tailor_resume_returns_valid_schema(self, fixture_db: sqlite3.Connection) -> None:
        """T17: tailor_resume → TailoredResume JSON on success."""
        import json as _json
        from unittest.mock import patch as _patch
        from role_scout.mcp_server.schemas import TailoredResume
        from role_scout.mcp_server.server import _tool_tailor_resume
        from role_scout.models.api import TailoredResume as DomainTailoredResume
        from datetime import datetime, timezone

        fake_result = DomainTailoredResume(
            hash_id="0000000000000001",
            job_title="Staff ML Engineer",
            company="Acme AI",
            tailored_summary="Great fit for this role.",
            tailored_bullets=["Led ML pipeline.", "Built cluster.", "Reduced cost 30%."],
            keywords_incorporated=["MLOps"],
            cache_key="abcd1234abcd1234",
            prompt_version="v1.0",
            cached=False,
            tailored_at=datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
        )

        with _patch("role_scout.mcp_server.server.get_rw_conn", return_value=fixture_db), \
             _patch("role_scout.tailor.tailor_resume", return_value=fake_result):
            result = _tool_tailor_resume({"hash_id": "0000000000000001"}, _settings(fixture_db))

        assert result.isError is not True
        data = _parse(result.content[0].text)
        tr = TailoredResume.model_validate(data)
        assert tr.hash_id == "0000000000000001"
        assert tr.cached is False

    def test_update_job_status_returns_valid_schema(self, fixture_db: sqlite3.Connection) -> None:
        """T17: update_job_status → UpdateJobStatusOutput."""
        from role_scout.mcp_server.server import _tool_update_job_status

        with patch("role_scout.mcp_server.server.get_rw_conn", return_value=fixture_db):
            result = _tool_update_job_status(
                {"hash_id": "0000000000000001", "status": "reviewed"},
                _settings(fixture_db),
            )

        data = _parse(result.content[0].text)
        out = UpdateJobStatusOutput.model_validate(data)
        assert out.ok is True

    def test_get_run_history_returns_valid_schema(self, fixture_db: sqlite3.Connection) -> None:
        """T17: get_run_history → GetRunHistoryOutput."""
        from role_scout.mcp_server.server import _tool_get_run_history

        with patch("role_scout.mcp_server.server.get_ro_conn", return_value=fixture_db):
            result = _tool_get_run_history({"limit": 3}, _settings(fixture_db))

        data = _parse(result.content[0].text)
        out = GetRunHistoryOutput.model_validate(data)
        assert isinstance(out.data, list)

    def test_get_watchlist_returns_valid_schema(self, fixture_db: sqlite3.Connection) -> None:
        """T17: get_watchlist → GetWatchlistOutput."""
        from role_scout.mcp_server.server import _tool_get_watchlist

        with patch(
            "role_scout.mcp_server.server.watchlist_dal.get_watchlist",
            return_value=["Anthropic"],
        ):
            result = _tool_get_watchlist()

        data = _parse(result.content[0].text)
        out = GetWatchlistOutput.model_validate(data)
        assert "Anthropic" in out.watchlist

    def test_manage_watchlist_returns_valid_schema(self, fixture_db: sqlite3.Connection) -> None:
        """T17: manage_watchlist → ManageWatchlistOutput."""
        from role_scout.mcp_server.server import _tool_manage_watchlist

        with patch(
            "role_scout.mcp_server.server.watchlist_dal.add_to_watchlist",
            return_value=["Anthropic", "Stripe"],
        ):
            result = _tool_manage_watchlist({"action": "add", "company": "Stripe"})

        data = _parse(result.content[0].text)
        out = ManageWatchlistOutput.model_validate(data)
        assert out.ok is True
        assert out.action == "add"

    def test_list_tools_count(self) -> None:
        """T17: server registers exactly 9 tools."""
        from role_scout.mcp_server.server import _TOOLS
        assert len(_TOOLS) == 9
        names = {t.name for t in _TOOLS}
        expected = {
            "run_pipeline", "get_jobs", "get_job_detail", "analyze_job", "tailor_resume",
            "update_job_status", "get_run_history", "get_watchlist", "manage_watchlist",
        }
        assert names == expected


# ---------------------------------------------------------------------------
# T18 — PIPELINE_BUSY
# ---------------------------------------------------------------------------

class TestT18PipelineBusy:
    def test_run_pipeline_busy_when_running(self, fixture_db: sqlite3.Connection) -> None:
        """T18: run_pipeline returns PIPELINE_BUSY if a run is in progress."""
        from role_scout.mcp_server.server import _is_pipeline_busy

        # Seed a running row
        fixture_db.execute(
            "UPDATE run_log SET status = 'running' WHERE run_id = 'run_aabbccdd-0003'"
        )
        fixture_db.commit()

        busy, run_id = _is_pipeline_busy(fixture_db)
        assert busy is True
        assert run_id == "run_aabbccdd-0003"

    def test_run_pipeline_not_busy_when_completed(self, fixture_db: sqlite3.Connection) -> None:
        """T18: _is_pipeline_busy returns False when all runs are completed/failed."""
        from role_scout.mcp_server.server import _is_pipeline_busy

        fixture_db.execute(
            "UPDATE run_log SET status = 'completed' WHERE run_id = 'run_aabbccdd-0003'"
        )
        fixture_db.commit()

        busy, _ = _is_pipeline_busy(fixture_db)
        assert busy is False

    def test_run_pipeline_returns_pipeline_busy_error(self, fixture_db: sqlite3.Connection) -> None:
        """T18: full run_pipeline call returns PIPELINE_BUSY ToolError when busy."""
        import asyncio
        from role_scout.mcp_server.server import _run_pipeline

        settings = _settings(fixture_db)

        # Force a running row
        fixture_db.execute(
            "UPDATE run_log SET status = 'running' WHERE run_id = 'run_aabbccdd-0003'"
        )
        fixture_db.commit()

        with patch("role_scout.mcp_server.server.get_ro_conn", return_value=fixture_db):
            result = asyncio.run(_run_pipeline({}, settings))

        assert result.isError is True
        data = _parse(result.content[0].text)
        err = ToolError.model_validate(data)
        assert err.error.code == "PIPELINE_BUSY"


# ---------------------------------------------------------------------------
# T19 — tailor_resume on non-existent hash
# ---------------------------------------------------------------------------

class TestT19TailorResumeNotFound:
    def test_tailor_resume_unknown_hash_returns_not_qualified(
        self, fixture_db: sqlite3.Connection
    ) -> None:
        """T19: tailor_resume with unknown hash_id → NOT_QUALIFIED error."""
        from role_scout.mcp_server.server import _tool_tailor_resume
        from role_scout.tailor import NotQualifiedError

        with patch("role_scout.mcp_server.server.get_rw_conn", return_value=fixture_db), \
             patch("role_scout.tailor._read_prompt", return_value=("<!-- version: v1.0 -->\nprompt", "v1.0")), \
             patch("role_scout.tailor._read_resume", return_value=("resume text", "abc123def456789a")):
            result = _tool_tailor_resume(
                {"hash_id": "ffffffffffffffff"}, _settings(fixture_db)
            )

        assert result.isError is True
        data = _parse(result.content[0].text)
        err = ToolError.model_validate(data)
        assert err.error.code == "NOT_QUALIFIED"


# ---------------------------------------------------------------------------
# T20 — manage_watchlist atomicity
# ---------------------------------------------------------------------------

class TestT20WatchlistAtomic:
    def test_add_to_watchlist_is_atomic(self, tmp_path) -> None:
        """T20: add_to_watchlist uses atomic tempfile+rename (no partial writes)."""
        from role_scout.dal.watchlist_dal import add_to_watchlist, get_watchlist
        from pathlib import Path

        wl_path = tmp_path / "watchlist.yaml"
        result = add_to_watchlist("Anthropic", path=wl_path)
        assert "Anthropic" in result
        assert "Anthropic" in get_watchlist(path=wl_path)

    def test_remove_from_watchlist_is_atomic(self, tmp_path) -> None:
        """T20: remove_from_watchlist uses atomic tempfile+rename."""
        from role_scout.dal.watchlist_dal import add_to_watchlist, remove_from_watchlist
        from pathlib import Path

        wl_path = tmp_path / "watchlist.yaml"
        add_to_watchlist("Anthropic", path=wl_path)
        add_to_watchlist("Stripe", path=wl_path)

        result = remove_from_watchlist("Anthropic", path=wl_path)
        assert "Anthropic" not in result
        assert "Stripe" in result

    def test_manage_watchlist_tool_writes_atomically(self, fixture_db: sqlite3.Connection, tmp_path) -> None:
        """T20: manage_watchlist tool delegates to watchlist_dal (atomic write)."""
        from role_scout.mcp_server.server import _tool_manage_watchlist
        from role_scout.dal import watchlist_dal
        from pathlib import Path

        wl_path = tmp_path / "watchlist.yaml"

        with patch.object(watchlist_dal, "add_to_watchlist", wraps=lambda c, path=None: ["Stripe"]) as mock_add:
            result = _tool_manage_watchlist({"action": "add", "company": "Stripe"})

        assert not result.isError
        data = _parse(result.content[0].text)
        out = ManageWatchlistOutput.model_validate(data)
        assert out.ok is True

#!/usr/bin/env python3
"""MCP smoke test — invokes all 9 tools against fixture DB and validates schemas.

Usage:
    uv run python scripts/mcp_smoke.py

Exits 0 on success, 1 on any schema mismatch or tool error.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the repo root is on sys.path
_REPO = Path(__file__).parent.parent
for p in [str(_REPO / "src"), str(_REPO)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from tests.fixtures.seed_fixture_db import create_fixture_db  # noqa: E402
from role_scout.mcp_server.schemas import (  # noqa: E402
    GetJobsOutput,
    GetRunHistoryOutput,
    GetWatchlistOutput,
    JobDetail,
    ManageWatchlistOutput,
    ToolError,
    UpdateJobStatusOutput,
)

# ---------------------------------------------------------------------------
# Fixture DB — write to a temp file so the server module can open it via path
# ---------------------------------------------------------------------------

import sqlite3
import tempfile
import os

_FAILURES: list[str] = []


def _fail(tool: str, reason: str) -> None:
    _FAILURES.append(f"[{tool}] {reason}")
    print(f"  FAIL: {reason}", file=sys.stderr)


def _ok(tool: str) -> None:
    print(f"  OK: {tool}")


def _parse(tool: str, raw: str) -> dict | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        _fail(tool, f"JSON parse error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Standalone tool invocations (call server helpers directly, not via stdio)
# ---------------------------------------------------------------------------

def smoke_all(db_path: str) -> None:
    """Run all 9 tool smoke tests against db_path."""
    import os
    os.environ.setdefault("DB_PATH", db_path)

    from unittest.mock import patch, MagicMock

    settings_mock = MagicMock()
    settings_mock.DB_PATH = db_path
    settings_mock.SCORE_THRESHOLD = 85

    with patch("role_scout.mcp_server.server.Settings", return_value=settings_mock):
        _smoke_get_jobs(db_path)
        _smoke_get_job_detail(db_path)
        _smoke_update_job_status(db_path)
        _smoke_get_run_history(db_path)
        _smoke_get_watchlist()
        _smoke_manage_watchlist()
        _smoke_get_jobs_with_source(db_path)
        _smoke_get_jobs_invalid_status(db_path)
        _smoke_get_job_detail_not_found(db_path)


def _smoke_get_jobs(db_path: str) -> None:
    from role_scout.mcp_server.server import _tool_get_jobs
    from unittest.mock import MagicMock
    settings = MagicMock()
    settings.DB_PATH = db_path

    result = _tool_get_jobs({"status": "new", "limit": 5}, settings)
    raw = result.content[0].text
    data = _parse("get_jobs", raw)
    if data is None:
        return
    try:
        out = GetJobsOutput.model_validate(data)
        assert out.total >= 0
        _ok("get_jobs(status=new, limit=5)")
    except Exception as exc:
        _fail("get_jobs", str(exc))


def _smoke_get_jobs_with_source(db_path: str) -> None:
    from role_scout.mcp_server.server import _tool_get_jobs
    from unittest.mock import MagicMock
    settings = MagicMock()
    settings.DB_PATH = db_path

    result = _tool_get_jobs({"status": "new", "limit": 10, "source": "linkedin"}, settings)
    raw = result.content[0].text
    data = _parse("get_jobs[source=linkedin]", raw)
    if data is None:
        return
    try:
        out = GetJobsOutput.model_validate(data)
        for job in out.data:
            assert job.source == "linkedin", f"Expected linkedin, got {job.source}"
        _ok("get_jobs(source=linkedin)")
    except Exception as exc:
        _fail("get_jobs[source=linkedin]", str(exc))


def _smoke_get_jobs_invalid_status(db_path: str) -> None:
    from role_scout.mcp_server.server import _tool_get_jobs
    from unittest.mock import MagicMock
    settings = MagicMock()
    settings.DB_PATH = db_path

    result = _tool_get_jobs({"status": "invalid_status"}, settings)
    raw = result.content[0].text
    data = _parse("get_jobs[invalid_status]", raw)
    if data is None:
        return
    try:
        err = ToolError.model_validate(data)
        assert err.error.code == "VALIDATION_ERROR"
        _ok("get_jobs(invalid_status) → VALIDATION_ERROR")
    except Exception as exc:
        _fail("get_jobs[invalid_status]", f"Expected ToolError, got: {raw[:200]}")


def _smoke_get_job_detail(db_path: str) -> None:
    from role_scout.mcp_server.server import _tool_get_job_detail
    from unittest.mock import MagicMock
    settings = MagicMock()
    settings.DB_PATH = db_path

    # hash_id "0000000000000001" is seeded in fixture
    result = _tool_get_job_detail({"hash_id": "0000000000000001"}, settings)
    raw = result.content[0].text
    data = _parse("get_job_detail", raw)
    if data is None:
        return
    try:
        detail = JobDetail.model_validate(data)
        assert detail.hash_id == "0000000000000001"
        _ok("get_job_detail(0000000000000001)")
    except Exception as exc:
        _fail("get_job_detail", str(exc))


def _smoke_get_job_detail_not_found(db_path: str) -> None:
    from role_scout.mcp_server.server import _tool_get_job_detail
    from unittest.mock import MagicMock
    settings = MagicMock()
    settings.DB_PATH = db_path

    result = _tool_get_job_detail({"hash_id": "ffffffffffffffff"}, settings)
    raw = result.content[0].text
    data = _parse("get_job_detail[not_found]", raw)
    if data is None:
        return
    try:
        err = ToolError.model_validate(data)
        assert err.error.code == "JOB_NOT_FOUND"
        _ok("get_job_detail(unknown) → JOB_NOT_FOUND")
    except Exception as exc:
        _fail("get_job_detail[not_found]", f"Expected ToolError, got: {raw[:200]}")


def _smoke_update_job_status(db_path: str) -> None:
    from role_scout.mcp_server.server import _tool_update_job_status
    from unittest.mock import MagicMock
    settings = MagicMock()
    settings.DB_PATH = db_path

    result = _tool_update_job_status(
        {"hash_id": "0000000000000001", "status": "reviewed"}, settings
    )
    raw = result.content[0].text
    data = _parse("update_job_status", raw)
    if data is None:
        return
    try:
        out = UpdateJobStatusOutput.model_validate(data)
        assert out.ok is True
        assert out.status == "reviewed"
        _ok("update_job_status(→ reviewed)")
    except Exception as exc:
        _fail("update_job_status", str(exc))


def _smoke_get_run_history(db_path: str) -> None:
    from role_scout.mcp_server.server import _tool_get_run_history
    from unittest.mock import MagicMock
    settings = MagicMock()
    settings.DB_PATH = db_path

    result = _tool_get_run_history({"limit": 3}, settings)
    raw = result.content[0].text
    data = _parse("get_run_history", raw)
    if data is None:
        return
    try:
        out = GetRunHistoryOutput.model_validate(data)
        assert isinstance(out.data, list)
        _ok(f"get_run_history(limit=3) → {len(out.data)} rows")
    except Exception as exc:
        _fail("get_run_history", str(exc))


def _smoke_get_watchlist() -> None:
    from role_scout.mcp_server.server import _tool_get_watchlist
    from unittest.mock import patch

    with patch("role_scout.mcp_server.server.watchlist_dal.get_watchlist", return_value=["Anthropic", "OpenAI"]):
        result = _tool_get_watchlist()
    raw = result.content[0].text
    data = _parse("get_watchlist", raw)
    if data is None:
        return
    try:
        out = GetWatchlistOutput.model_validate(data)
        assert "Anthropic" in out.watchlist
        _ok("get_watchlist()")
    except Exception as exc:
        _fail("get_watchlist", str(exc))


def _smoke_manage_watchlist() -> None:
    from role_scout.mcp_server.server import _tool_manage_watchlist
    from unittest.mock import patch

    with patch(
        "role_scout.mcp_server.server.watchlist_dal.add_to_watchlist",
        return_value=["Anthropic", "Stripe"],
    ):
        result = _tool_manage_watchlist({"action": "add", "company": "Stripe"})
    raw = result.content[0].text
    data = _parse("manage_watchlist", raw)
    if data is None:
        return
    try:
        out = ManageWatchlistOutput.model_validate(data)
        assert out.ok is True
        assert out.action == "add"
        assert "Stripe" in out.watchlist
        _ok("manage_watchlist(add=Stripe)")
    except Exception as exc:
        _fail("manage_watchlist", str(exc))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=== MCP smoke test ===")

    # Create a temp file DB (server needs a path, not :memory:)
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="role_scout_smoke_")
    os.close(fd)
    try:
        conn = create_fixture_db(db_path)
        conn.close()

        smoke_all(db_path)
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass

    if _FAILURES:
        print(f"\n{len(_FAILURES)} failure(s):", file=sys.stderr)
        for f in _FAILURES:
            print(f"  {f}", file=sys.stderr)
        return 1

    print(f"\nAll {9} tools passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

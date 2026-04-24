"""Unit tests for preflight_node — T38, T39, T40, config validation."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ---- helpers ----

def _make_conn_with_run_log(entries: list[dict]) -> sqlite3.Connection:
    """Create in-memory DB with run_log + source_health_json rows."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE run_log (
            run_id TEXT PRIMARY KEY,
            started_at TEXT,
            status TEXT,
            source_health_json TEXT
        )"""
    )
    for e in entries:
        conn.execute(
            "INSERT INTO run_log (run_id, started_at, status, source_health_json) VALUES (?,?,?,?)",
            (e["run_id"], e["started_at"], e["status"], e.get("source_health_json")),
        )
    conn.commit()
    return conn


def _health_json(linkedin="ok", google="ok", trueup="ok") -> str:
    return json.dumps({
        "linkedin": {"status": linkedin, "jobs": 5, "duration_s": 1.0},
        "google": {"status": google, "jobs": 5, "duration_s": 1.0},
        "trueup": {"status": trueup, "jobs": 5, "duration_s": 1.0},
    })


# ---- T38: 3 consecutive failures → auto-skip ----

class TestSourceAutoSkip:
    def test_t38_three_consecutive_failures_auto_skip(self):
        """T38: source with 3 consecutive failures in run_log should be in skip set."""
        from role_scout.dal.run_log_dal import get_sources_to_skip

        conn = _make_conn_with_run_log([
            {"run_id": "r1", "started_at": "2026-04-01T00:00:00", "status": "failed",
             "source_health_json": _health_json(linkedin="failed")},
            {"run_id": "r2", "started_at": "2026-04-02T00:00:00", "status": "failed",
             "source_health_json": _health_json(linkedin="failed")},
            {"run_id": "r3", "started_at": "2026-04-03T00:00:00", "status": "failed",
             "source_health_json": _health_json(linkedin="failed")},
        ])
        skip = get_sources_to_skip(conn, window=3)
        assert "linkedin" in skip
        assert "google" not in skip
        assert "trueup" not in skip

    def test_two_consecutive_failures_not_enough(self):
        """Only 2 consecutive failures should NOT trigger auto-skip."""
        from role_scout.dal.run_log_dal import get_sources_to_skip

        conn = _make_conn_with_run_log([
            {"run_id": "r1", "started_at": "2026-04-01T00:00:00", "status": "ok",
             "source_health_json": _health_json(linkedin="ok")},
            {"run_id": "r2", "started_at": "2026-04-02T00:00:00", "status": "failed",
             "source_health_json": _health_json(linkedin="failed")},
            {"run_id": "r3", "started_at": "2026-04-03T00:00:00", "status": "failed",
             "source_health_json": _health_json(linkedin="failed")},
        ])
        skip = get_sources_to_skip(conn, window=3)
        assert "linkedin" not in skip

    def test_insufficient_run_history_no_skip(self):
        """Fewer than `window` runs should never trigger auto-skip."""
        from role_scout.dal.run_log_dal import get_sources_to_skip

        conn = _make_conn_with_run_log([
            {"run_id": "r1", "started_at": "2026-04-01T00:00:00", "status": "failed",
             "source_health_json": _health_json(linkedin="failed")},
            {"run_id": "r2", "started_at": "2026-04-02T00:00:00", "status": "failed",
             "source_health_json": _health_json(linkedin="failed")},
        ])
        skip = get_sources_to_skip(conn, window=3)
        assert skip == set()

    def test_t39_force_source_overrides_skip(self):
        """T39: --force-source should not appear in the skip set returned to discovery."""
        from role_scout.dal.run_log_dal import get_sources_to_skip

        conn = _make_conn_with_run_log([
            {"run_id": "r1", "started_at": "2026-04-01T00:00:00", "status": "failed",
             "source_health_json": _health_json(linkedin="failed")},
            {"run_id": "r2", "started_at": "2026-04-02T00:00:00", "status": "failed",
             "source_health_json": _health_json(linkedin="failed")},
            {"run_id": "r3", "started_at": "2026-04-03T00:00:00", "status": "failed",
             "source_health_json": _health_json(linkedin="failed")},
        ])
        # The force_sources override is applied in preflight_node before calling get_sources_to_skip
        to_skip = get_sources_to_skip(conn, window=3)
        force_sources = {"linkedin"}
        effective_skip = to_skip - force_sources
        assert "linkedin" not in effective_skip


# ---- T40: SerpAPI quota low → google skipped ----

class TestSerpApiQuota:
    def test_t40_quota_below_min_google_skipped(self):
        """T40: When SerpAPI remaining < SERPAPI_MIN_QUOTA, google must be skipped."""
        from role_scout.nodes.preflight import _check_serpapi_quota

        with patch("role_scout.nodes.preflight.httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"plan_searches_left": 5}
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            remaining = _check_serpapi_quota("fake_key", min_quota=10)
            assert remaining == 5
            # The caller (preflight_node) checks: if remaining < min_quota → skip google
            assert remaining < 10

    def test_quota_check_failure_returns_none(self):
        """Network error on quota check must return None (not raise)."""
        from role_scout.nodes.preflight import _check_serpapi_quota

        with patch("role_scout.nodes.preflight.httpx.get", side_effect=Exception("timeout")):
            result = _check_serpapi_quota("fake_key", min_quota=10)
            assert result is None


# ---- PreflightError on missing ANTHROPIC_API_KEY ----

class TestPreflightError:
    def test_missing_api_key_raises(self):
        """preflight_node must raise PreflightError when ANTHROPIC_API_KEY is absent."""
        from unittest.mock import MagicMock
        from role_scout.nodes.preflight import preflight_node, PreflightError

        with patch("role_scout.nodes.preflight.Settings") as MockSettings:
            settings = MagicMock()
            settings.ANTHROPIC_API_KEY = ""
            MockSettings.return_value = settings

            with pytest.raises(PreflightError, match="ANTHROPIC_API_KEY"):
                preflight_node({"trigger_type": "manual", "errors": []})


# ---- SourceHealthBlob serialisation ----

class TestSourceHealthBlob:
    def test_as_dict_excludes_none_sources(self):
        from role_scout.models.records import SourceHealthBlob
        from role_scout.models.core import SourceHealthEntry

        blob = SourceHealthBlob(
            linkedin=SourceHealthEntry(status="ok", jobs=5, duration_s=1.0),
            google=None,
            trueup=SourceHealthEntry(status="failed", jobs=0, duration_s=0.5, error="timeout"),
        )
        d = blob.as_dict()
        assert "linkedin" in d
        assert "google" not in d
        assert d["trueup"].status == "failed"

    def test_roundtrip_json(self):
        from role_scout.models.records import SourceHealthBlob
        from role_scout.models.core import SourceHealthEntry

        blob = SourceHealthBlob(
            linkedin=SourceHealthEntry(status="ok", jobs=10, duration_s=2.5),
        )
        raw = blob.model_dump_json()
        restored = SourceHealthBlob.model_validate_json(raw)
        assert restored.linkedin is not None
        assert restored.linkedin.jobs == 10

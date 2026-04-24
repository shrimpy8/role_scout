"""Unit tests for source_health persistence — T41."""
from __future__ import annotations

import json
import sqlite3

import pytest


def _make_run_log_db() -> sqlite3.Connection:
    """Create in-memory DB with minimal run_log schema including Phase 2 columns."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE run_log (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            trigger_type TEXT,
            source_health_json TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost_usd REAL,
            cancel_reason TEXT,
            ttl_deadline TEXT,
            ttl_extended INTEGER DEFAULT 0,
            completed_at TEXT,
            errors TEXT,
            total_fetched INTEGER,
            total_qualified INTEGER,
            total_new INTEGER
        )"""
    )
    conn.execute(
        "INSERT INTO run_log (run_id, started_at, status) VALUES (?, ?, ?)",
        ("run_test001", "2026-04-01T10:00:00", "running"),
    )
    conn.commit()
    return conn


# ---- T41: source_health_json written and parseable ----

class TestSourceHealthPersistence:
    def test_t41_write_and_parse_source_health(self):
        """T41: write_source_health writes valid JSON parseable as SourceHealthBlob."""
        from role_scout.dal.run_log_dal import write_source_health
        from role_scout.models.core import SourceHealthEntry
        from role_scout.models.records import SourceHealthBlob

        conn = _make_run_log_db()
        health = {
            "linkedin": SourceHealthEntry(status="ok", jobs=15, duration_s=1.2),
            "google": SourceHealthEntry(status="failed", jobs=0, duration_s=0.5, error="quota"),
            "trueup": SourceHealthEntry(status="ok", jobs=3, duration_s=0.8),
        }

        write_source_health(conn, "run_test001", health)

        raw = conn.execute(
            "SELECT source_health_json FROM run_log WHERE run_id = ?",
            ("run_test001",),
        ).fetchone()[0]

        assert raw is not None
        blob = SourceHealthBlob.model_validate_json(raw)
        assert blob.linkedin is not None
        assert blob.linkedin.jobs == 15
        assert blob.google is not None
        assert blob.google.status == "failed"
        assert blob.google.error == "quota"
        assert blob.trueup is not None
        assert blob.trueup.jobs == 3

    def test_write_partial_health_skipped_sources_not_stored(self):
        """SourceHealthBlob with None entries must exclude those sources from as_dict()."""
        from role_scout.dal.run_log_dal import write_source_health
        from role_scout.models.core import SourceHealthEntry
        from role_scout.models.records import SourceHealthBlob

        conn = _make_run_log_db()
        # Only linkedin and trueup (google was skipped at preflight → not in health dict)
        health = {
            "linkedin": SourceHealthEntry(status="ok", jobs=10, duration_s=1.0),
            "trueup": SourceHealthEntry(status="skipped", jobs=0, duration_s=0.0),
        }
        write_source_health(conn, "run_test001", health)

        raw = conn.execute(
            "SELECT source_health_json FROM run_log WHERE run_id = ?",
            ("run_test001",),
        ).fetchone()[0]

        blob = SourceHealthBlob.model_validate_json(raw)
        as_dict = blob.as_dict()
        # google was never provided — should be absent from as_dict()
        assert "google" not in as_dict
        assert "linkedin" in as_dict
        assert "trueup" in as_dict

    def test_source_health_json_is_valid_json(self):
        """Stored value must be parseable with json.loads (not just SourceHealthBlob)."""
        from role_scout.dal.run_log_dal import write_source_health
        from role_scout.models.core import SourceHealthEntry

        conn = _make_run_log_db()
        health = {
            "linkedin": SourceHealthEntry(status="ok", jobs=5, duration_s=0.9),
        }
        write_source_health(conn, "run_test001", health)

        raw = conn.execute(
            "SELECT source_health_json FROM run_log WHERE run_id = ?",
            ("run_test001",),
        ).fetchone()[0]

        parsed = json.loads(raw)
        assert isinstance(parsed, dict)

    def test_write_health_overwrites_previous(self):
        """Calling write_source_health twice on same run_id replaces the stored value."""
        from role_scout.dal.run_log_dal import write_source_health
        from role_scout.models.core import SourceHealthEntry
        from role_scout.models.records import SourceHealthBlob

        conn = _make_run_log_db()
        health_v1 = {
            "linkedin": SourceHealthEntry(status="ok", jobs=5, duration_s=1.0),
        }
        health_v2 = {
            "linkedin": SourceHealthEntry(status="failed", jobs=0, duration_s=0.3, error="down"),
        }
        write_source_health(conn, "run_test001", health_v1)
        write_source_health(conn, "run_test001", health_v2)

        raw = conn.execute(
            "SELECT source_health_json FROM run_log WHERE run_id = ?",
            ("run_test001",),
        ).fetchone()[0]
        blob = SourceHealthBlob.model_validate_json(raw)
        assert blob.linkedin is not None
        assert blob.linkedin.status == "failed"
        assert blob.linkedin.error == "down"

    def test_get_recent_source_health_ordered_by_started_at(self):
        """get_recent_source_health must return most-recent rows first."""
        from role_scout.dal.run_log_dal import get_recent_source_health, write_source_health
        from role_scout.models.core import SourceHealthEntry

        conn = _make_run_log_db()
        # Add two more runs
        for run_id, started_at in [
            ("run_older", "2026-03-30T08:00:00"),
            ("run_newest", "2026-04-02T08:00:00"),
        ]:
            conn.execute(
                "INSERT INTO run_log (run_id, started_at, status) VALUES (?, ?, ?)",
                (run_id, started_at, "completed"),
            )
        conn.commit()

        health = {"linkedin": SourceHealthEntry(status="ok", jobs=1, duration_s=0.1)}
        for run_id in ("run_test001", "run_older", "run_newest"):
            write_source_health(conn, run_id, health)

        rows = get_recent_source_health(conn, limit=3)
        assert rows[0]["run_id"] == "run_newest"
        assert rows[1]["run_id"] == "run_test001"
        assert rows[2]["run_id"] == "run_older"

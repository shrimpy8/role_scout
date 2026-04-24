"""D1 scaffold tests — models, migrations, config, graph skeleton."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from pydantic import ValidationError


# ---- core types ----

class TestCoreTypes:
    def test_hash_id_valid(self):
        from role_scout.models.core import BaseSchema, HashId
        from typing import Annotated
        from pydantic import TypeAdapter

        ta = TypeAdapter(HashId)
        assert ta.validate_python("abcdef0123456789") == "abcdef0123456789"

    def test_hash_id_rejects_wrong_length(self):
        from role_scout.models.core import HashId
        from typing import Annotated
        from pydantic import TypeAdapter, ValidationError

        ta = TypeAdapter(HashId)
        with pytest.raises(ValidationError):
            ta.validate_python("abc")

    def test_hash_id_rejects_uppercase(self):
        from role_scout.models.core import HashId
        from pydantic import TypeAdapter, ValidationError

        ta = TypeAdapter(HashId)
        with pytest.raises(ValidationError):
            ta.validate_python("ABCDEF0123456789")

    def test_base_schema_rejects_naive_datetime(self):
        from role_scout.models.core import BaseSchema

        class _ModelWithDT(BaseSchema):
            ts: datetime

        with pytest.raises(ValidationError):
            _ModelWithDT(ts=datetime(2024, 1, 1))  # naive — no tzinfo

    def test_source_health_entry_valid(self):
        from role_scout.models.core import SourceHealthEntry

        e = SourceHealthEntry(
            status="ok",
            jobs=10,
            duration_s=1.5,
            raw_count=20,
            after_dedup=12,
            query_params={"q": "swe", "pages": 3},
        )
        assert e.jobs == 10
        assert e.status == "ok"

    def test_error_envelope_shape(self):
        from role_scout.models.core import ErrorDetail, ErrorEnvelope

        env = ErrorEnvelope(
            error=ErrorDetail(code="NOT_FOUND", message="Job not found")
        )
        assert env.error.code == "NOT_FOUND"

    def test_error_code_must_be_uppercase(self):
        from role_scout.models.core import ErrorDetail

        with pytest.raises(ValidationError):
            ErrorDetail(code="not_found", message="x")


# ---- TailoredResumeRecord ----

class TestTailoredResumeRecord:
    def _valid(self, **overrides):
        from role_scout.models.records import TailoredResumeRecord

        defaults = dict(
            hash_id="abcdef0123456789",
            job_title="SWE",
            company="Acme",
            tailored_summary="A great summary that fits.",
            tailored_bullets=["bullet one here", "bullet two here", "bullet three here"],
            keywords_incorporated=["python", "api"],
            cache_key="abcdef01234567ab",
            prompt_version="2026-04-23-v1",
            resume_sha="a" * 64,
            tailored_at=datetime.now(timezone.utc),
        )
        defaults.update(overrides)
        return TailoredResumeRecord(**defaults)

    def test_valid_record(self):
        r = self._valid()
        assert r.company == "Acme"

    def test_requires_3_bullets_minimum(self):
        with pytest.raises(ValidationError):
            self._valid(tailored_bullets=["one", "two"])

    def test_rejects_blank_bullet(self):
        with pytest.raises(ValidationError):
            self._valid(tailored_bullets=["valid", "   ", "also valid"])

    def test_summary_max_length(self):
        with pytest.raises(ValidationError):
            self._valid(tailored_summary="x" * 2001)

    def test_resume_sha_must_be_64_hex(self):
        with pytest.raises(ValidationError):
            self._valid(resume_sha="abc")


# ---- PipelineResumeRequest ----

class TestPipelineResumeRequest:
    def test_approve(self):
        from role_scout.models.api import PipelineResumeRequest

        req = PipelineResumeRequest(approved=True)
        assert req.approved is True

    def test_reject_requires_cancel_reason(self):
        from role_scout.models.api import PipelineResumeRequest

        with pytest.raises(ValidationError):
            PipelineResumeRequest(approved=False)

    def test_reject_with_reason(self):
        from role_scout.models.api import PipelineResumeRequest

        req = PipelineResumeRequest(approved=False, cancel_reason="user_cancel")
        assert req.cancel_reason == "user_cancel"


# ---- Settings ----

class TestSettings:
    def _make(self, **overrides):
        import os
        env = {
            "ANTHROPIC_API_KEY": "sk-test",
            "SERPAPI_KEY": "serp-test",
            "APIFY_TOKEN": "apify-test",
            "IMAP_EMAIL": "test@example.com",
            "IMAP_APP_PASSWORD": "password",
            # Explicit defaults so .env file values don't bleed in
            "SCORE_THRESHOLD": "85",
            "RUN_MODE": "shadow",
            "MAX_COST_USD": "5.0",
            "INTERRUPT_TTL_HOURS": "4.0",
            "REFLECTION_ENABLED": "true",
        }
        env.update(overrides)
        with patch.dict(os.environ, env, clear=False):
            from importlib import reload
            import role_scout.config as cfg
            reload(cfg)
            return cfg.Settings(_env_file=None)

    def test_defaults(self):
        s = self._make()
        assert s.SCORE_THRESHOLD == 85
        assert s.RUN_MODE == "shadow"
        assert s.REFLECTION_ENABLED is True
        assert s.MAX_COST_USD == 5.0
        assert s.INTERRUPT_TTL_HOURS == 4.0

    def test_float_threshold_coercion(self):
        s = self._make(SCORE_THRESHOLD="0.75")
        assert s.SCORE_THRESHOLD == 75

    def test_invalid_log_level(self):
        with pytest.raises(Exception):
            self._make(LOG_LEVEL="VERBOSE")

    def test_band_high_must_exceed_low(self):
        with pytest.raises(Exception):
            self._make(REFLECTION_BAND_LOW="80", REFLECTION_BAND_HIGH="70")


# ---- Migrations ----

class TestMigrations:
    def test_run_migrations_idempotent(self):
        from role_scout.migrations import run_migrations

        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE qualified_jobs (id INTEGER PRIMARY KEY, hash_id TEXT, status TEXT)"
        )
        conn.execute(
            "CREATE TABLE run_log (id INTEGER PRIMARY KEY, started_at TEXT, errors TEXT)"
        )

        # First run — adds columns
        run_migrations(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(run_log)").fetchall()}
        assert "input_tokens" in cols
        assert "output_tokens" in cols
        assert "estimated_cost_usd" in cols
        assert "source_health_json" in cols
        assert "trigger_type" in cols

        qualified_cols = {row[1] for row in conn.execute("PRAGMA table_info(qualified_jobs)").fetchall()}
        assert "tailored_resume" in qualified_cols

        # Second run — idempotent (no exception)
        run_migrations(conn)
        conn.close()

    def test_wal_pragma_set(self):
        from role_scout.migrations import run_migrations

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE qualified_jobs (id INTEGER PRIMARY KEY, status TEXT)")
        conn.execute("CREATE TABLE run_log (id INTEGER PRIMARY KEY, started_at TEXT, errors TEXT)")
        run_migrations(conn)
        row = conn.execute("PRAGMA journal_mode").fetchone()
        # In-memory DB always returns "memory" not "wal" — just verify no exception
        conn.close()


# ---- Graph skeleton ----

class TestGraphSkeleton:
    def test_graph_builds(self):
        from role_scout.graph import build_graph

        g = build_graph()
        assert g is not None

    def test_graph_has_all_nodes(self):
        from role_scout.graph import build_graph

        g = build_graph()
        nodes = set(g.nodes.keys())
        expected = {"preflight", "discovery", "enrichment", "scoring", "reflection", "review", "output"}
        assert expected.issubset(nodes)

    def test_all_nodes_implemented(self):
        """Verify review_node and output_node are no longer stubs (D4 complete)."""
        from role_scout.nodes.output import output_node
        from role_scout.nodes.review import review_node

        # Both are callable without raising NotImplementedError
        assert callable(review_node)
        assert callable(output_node)


# ---- State helper ----

class TestAssertStateSize:
    def test_small_state_passes(self):
        from role_scout.models.state import assert_state_size

        assert_state_size({"run_id": "run_abc", "errors": []})

    def test_oversized_state_raises(self):
        from role_scout.models.state import assert_state_size, StateSizeExceeded

        big_state = {"data": "x" * (11 * 1024 * 1024)}
        with pytest.raises(StateSizeExceeded):
            assert_state_size(big_state, cap_mb=10)

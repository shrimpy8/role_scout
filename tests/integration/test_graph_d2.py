"""Integration tests for D2 discovery + preflight nodes through the LangGraph graph."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---- in-memory DB fixtures ----

def _make_db() -> sqlite3.Connection:
    """Minimal run_log + qualified_jobs schema for integration tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE run_log (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            trigger_type TEXT,
            source_health_json TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            estimated_cost_usd REAL DEFAULT 0,
            cancel_reason TEXT,
            ttl_deadline TEXT,
            ttl_extended INTEGER DEFAULT 0,
            completed_at TEXT,
            errors TEXT,
            total_fetched INTEGER DEFAULT 0,
            total_qualified INTEGER DEFAULT 0,
            total_new INTEGER DEFAULT 0
        )"""
    )
    conn.execute(
        """CREATE TABLE qualified_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hash_id TEXT UNIQUE NOT NULL,
            title TEXT,
            company TEXT,
            location TEXT,
            url TEXT,
            source TEXT,
            score REAL,
            status TEXT DEFAULT 'new',
            seen_at TEXT,
            tailored_resume TEXT
        )"""
    )
    conn.commit()
    return conn


def _fake_raw_jobs(n: int = 3) -> list[dict[str, Any]]:
    return [
        {
            "title": f"SWE {i}",
            "company": "Acme",
            "location": "Remote",
            "url": f"https://example.com/jobs/{i}",
            "source": "linkedin",
        }
        for i in range(n)
    ]


# ---- discovery_node integration ----

class TestDiscoveryNodeIntegration:
    """Runs discovery_node with mocked fetchers; checks state output shape."""

    def _patch_and_run(
        self,
        linkedin_result=None,
        google_result=None,
        trueup_result=None,
        skipped: set[str] | None = None,
        force_partial: bool = False,
    ) -> dict:
        from role_scout.nodes.discovery import discovery_node
        from role_scout.config import Settings

        if linkedin_result is None:
            linkedin_result = (_fake_raw_jobs(5), {"queries": ["SWE"], "location": "Remote"})
        if google_result is None:
            google_result = (_fake_raw_jobs(3), {"queries": ["SWE"], "location": "Remote"})
        if trueup_result is None:
            trueup_result = (_fake_raw_jobs(2), {"host": "imap.gmail.com"})

        profile = MagicMock()
        profile.target_roles = ["Software Engineer"]
        profile.location = "Remote"
        profile.posted_within = 7

        state = {
            "run_id": "run_integ001",
            "candidate_profile": profile,
            "skipped_sources": list(skipped or []),
            "force_partial": force_partial,
            "errors": [],
        }

        settings_mock = MagicMock(spec=Settings)
        settings_mock.APIFY_TOKEN = "tok"
        settings_mock.SERPAPI_KEY = "key"
        settings_mock.IMAP_USER = "a@b.com"
        settings_mock.IMAP_PASSWORD = "pw"
        settings_mock.IMAP_FOLDER = "INBOX"
        settings_mock.DB_PATH = ":memory:"
        settings_mock.DISCOVERY_MAX_ITEMS = 50

        conn = _make_db()

        def _fake_run_linkedin(profile, token, max_items=50):
            return linkedin_result

        def _fake_run_google(profile, key, max_results=50):
            return google_result

        def _fake_run_trueup(email, pw, folder="INBOX"):
            return trueup_result

        fake_normalized = [
            MagicMock(source="linkedin", title="SWE", company="Acme") for _ in range(5)
        ]

        with (
            patch("role_scout.nodes.discovery.Settings", return_value=settings_mock),
            patch("role_scout.nodes.discovery.run_linkedin", _fake_run_linkedin),
            patch("role_scout.nodes.discovery.run_google", _fake_run_google),
            patch("role_scout.nodes.discovery.run_trueup", _fake_run_trueup),
            patch("role_scout.nodes.discovery.normalize_jobs", return_value=fake_normalized),
            patch("role_scout.nodes.discovery.dedup_jobs", return_value=fake_normalized),
            patch("role_scout.nodes.discovery.get_rw_conn", return_value=conn),
            patch("role_scout.nodes.discovery._persist_health"),
        ):
            return discovery_node(state)

    def test_happy_path_state_shape(self):
        """discovery_node happy path: all expected keys present in returned state."""
        update = self._patch_and_run()

        assert "raw_by_source" in update
        assert "normalized_jobs" in update
        assert "new_jobs" in update
        assert "source_counts" in update
        assert "source_health" in update
        assert "errors" in update
        # No cancellation on happy path
        assert update.get("cancel_reason") is None

    def test_source_health_keys_present_for_active_sources(self):
        """source_health must contain entries for all non-skipped sources."""
        update = self._patch_and_run()
        health = update["source_health"]
        assert "linkedin" in health
        assert "google" in health
        assert "trueup" in health

    def test_skipped_source_has_skipped_health_entry(self):
        """Skipped sources must appear in source_health with status='skipped'."""
        update = self._patch_and_run(skipped={"trueup"})
        health = update["source_health"]
        assert "trueup" in health
        assert health["trueup"].status == "skipped"

    def test_circuit_breaker_on_two_source_failures(self):
        """Two source failures → cancel_reason='crippled_fetch' returned."""
        update = self._patch_and_run(
            linkedin_result=([], {"queries": []}),  # will return no error — need to mock error
        )
        # Force 2 failures by patching _gather_sources directly
        from role_scout.nodes.discovery import discovery_node
        from role_scout.config import Settings

        profile = MagicMock()
        state = {
            "run_id": "run_cb",
            "candidate_profile": profile,
            "skipped_sources": [],
            "force_partial": False,
            "errors": [],
        }
        settings_mock = MagicMock(spec=Settings)
        settings_mock.DB_PATH = ":memory:"

        failed_results = [
            ("linkedin", [], {}, 0.1, "timeout"),
            ("google", [], {}, 0.2, "quota"),
            ("trueup", [{"id": "1"}], {}, 0.3, None),
        ]

        with (
            patch("role_scout.nodes.discovery.Settings", return_value=settings_mock),
            patch(
                "role_scout.nodes.discovery._gather_sources",
                return_value=failed_results,
            ),
            patch("role_scout.nodes.discovery._persist_health"),
        ):
            cb_update = discovery_node(state)

        assert cb_update.get("cancel_reason") == "crippled_fetch"

    def test_errors_field_accumulates_fetch_errors(self):
        """Fetch errors from sources must be appended to the errors list."""
        from role_scout.nodes.discovery import discovery_node
        from role_scout.config import Settings

        profile = MagicMock()
        state = {
            "run_id": "run_errs",
            "candidate_profile": profile,
            "skipped_sources": [],
            "force_partial": True,
            "errors": ["pre_existing_error"],
        }
        settings_mock = MagicMock(spec=Settings)
        settings_mock.DB_PATH = ":memory:"

        error_results = [
            ("linkedin", [], {}, 0.1, "IMAP error"),
            ("google", [], {}, 0.2, "SerpAPI quota"),
            ("trueup", [], {}, 0.15, "SSL error"),
        ]

        with (
            patch("role_scout.nodes.discovery.Settings", return_value=settings_mock),
            patch(
                "role_scout.nodes.discovery._gather_sources",
                return_value=error_results,
            ),
            patch("role_scout.nodes.discovery.normalize_jobs", return_value=[]),
            patch("role_scout.nodes.discovery.dedup_jobs", return_value=[]),
            patch("role_scout.nodes.discovery.get_rw_conn", return_value=_make_db()),
            patch("role_scout.nodes.discovery._persist_health"),
        ):
            update = discovery_node(state)

        errors = update["errors"]
        assert "pre_existing_error" in errors
        assert any("IMAP error" in e for e in errors)
        assert any("SerpAPI quota" in e for e in errors)
        assert any("SSL error" in e for e in errors)


# ---- preflight → discovery state threading ----

class TestPreflightToDiscoveryStateThreading:
    """Verify that preflight_node output can be consumed by discovery_node."""

    def test_preflight_output_is_valid_discovery_input(self):
        """preflight state update must contain all keys discovery_node reads."""
        from role_scout.nodes.preflight import preflight_node

        conn = _make_db()

        with (
            patch.dict(
                os.environ,
                {"ANTHROPIC_API_KEY": "sk-ant-test"},
                clear=False,
            ),
            patch("role_scout.nodes.preflight.Settings") as MockSettings,
            patch("role_scout.nodes.preflight.init_db"),
            patch("role_scout.nodes.preflight.get_rw_conn", return_value=conn),
            patch("role_scout.nodes.preflight.insert_run"),
            patch("role_scout.nodes.preflight.get_sources_to_skip", return_value=set()),
            patch("role_scout.nodes.preflight._check_serpapi_quota", return_value=100),
            patch("role_scout.nodes.preflight.load_candidate_profile") as mock_profile,
            patch("role_scout.nodes.preflight._load_watchlist", return_value=[]),
        ):
            settings = MagicMock()
            settings.ANTHROPIC_API_KEY = "sk-ant-test"
            settings.SERPAPI_KEY = "key"
            settings.SERPAPI_MIN_QUOTA = 10
            settings.SOURCE_HEALTH_WINDOW = 3
            settings.DB_PATH = ":memory:"
            settings.INTERRUPT_TTL_HOURS = 4
            settings.SCORE_THRESHOLD = 70
            settings.RUN_MODE = "shadow"
            MockSettings.return_value = settings

            mock_profile.return_value = MagicMock(
                target_roles=["SWE"],
                location="Remote",
                posted_within=7,
            )

            state_in = {"trigger_type": "manual", "errors": []}
            update = preflight_node(state_in)

        # Keys discovery_node reads from state
        assert "run_id" in update
        assert "candidate_profile" in update
        assert "skipped_sources" in update
        assert "errors" in update

        # skipped_sources must be iterable (list from preflight, set expected by discovery)
        assert isinstance(update["skipped_sources"], list)

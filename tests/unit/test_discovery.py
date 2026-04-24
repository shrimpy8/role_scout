"""Unit tests for discovery_node — T2, T3, T7 concurrent fetch, circuit breaker."""
from __future__ import annotations

import asyncio
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---- helpers ----

def _make_profile() -> MagicMock:
    p = MagicMock()
    p.target_roles = ["Software Engineer"]
    p.location = "Remote"
    p.posted_within = 7
    return p


def _make_state(skipped: set[str] | None = None, force_partial: bool = False) -> dict:
    from role_scout.models.state import JobSearchState
    return {
        "run_id": "run_abc123",
        "candidate_profile": _make_profile(),
        "skipped_sources": list(skipped or []),
        "force_partial": force_partial,
        "errors": [],
    }


# ---- T2: concurrent fetch timing ----

class TestConcurrentFetch:
    def test_t2_all_sources_run_concurrently(self):
        """T2: Three fetchers must start before any completes (concurrency verified via timing)."""
        from role_scout.nodes.discovery import _gather_sources
        from role_scout.config import Settings

        call_times: list[float] = []

        def slow_linkedin(profile, token):
            call_times.append(time.monotonic())
            time.sleep(0.05)
            return [], {}

        def slow_google(profile, key):
            call_times.append(time.monotonic())
            time.sleep(0.05)
            return [], {}

        def slow_trueup(email, password):
            call_times.append(time.monotonic())
            time.sleep(0.05)
            return [], {}

        settings = MagicMock(spec=Settings)
        settings.APIFY_TOKEN = "tok"
        settings.SERPAPI_KEY = "key"
        settings.IMAP_EMAIL = "a@b.com"
        settings.IMAP_APP_PASSWORD = "pw"

        with (
            patch("role_scout.nodes.discovery.run_linkedin", slow_linkedin),
            patch("role_scout.nodes.discovery.run_google", slow_google),
            patch("role_scout.nodes.discovery.run_trueup", slow_trueup),
        ):
            t_start = time.monotonic()
            import structlog
            results = asyncio.run(_gather_sources(_make_profile(), settings, set(), structlog.get_logger()))
            elapsed = time.monotonic() - t_start

        # All three fetchers started (returned results)
        assert len(results) == 3
        # If sequential: ~0.15s; concurrent: ~0.05s + overhead
        # We allow generous headroom: must be < 0.14s
        assert elapsed < 0.14, f"Sources ran sequentially? elapsed={elapsed:.3f}s"

    def test_skipped_source_excluded(self):
        """Skipped sources must not appear in gather results."""
        from role_scout.nodes.discovery import _gather_sources
        from role_scout.config import Settings

        settings = MagicMock(spec=Settings)
        settings.APIFY_TOKEN = "tok"
        settings.SERPAPI_KEY = "key"
        settings.IMAP_EMAIL = "a@b.com"
        settings.IMAP_APP_PASSWORD = "pw"

        with (
            patch("role_scout.nodes.discovery.run_linkedin", return_value=([], {})),
            patch("role_scout.nodes.discovery.run_google", return_value=([], {})),
            patch("role_scout.nodes.discovery.run_trueup", return_value=([], {})),
        ):
            import structlog
            results = asyncio.run(
                _gather_sources(_make_profile(), settings, {"linkedin", "trueup"}, structlog.get_logger())
            )

        # Only google should run
        assert len(results) == 1
        assert results[0][0] == "google"

    def test_all_skipped_returns_empty(self):
        """If all sources are skipped, gather returns empty list — no tasks queued."""
        from role_scout.nodes.discovery import _gather_sources
        from role_scout.config import Settings

        settings = MagicMock(spec=Settings)

        with (
            patch("role_scout.nodes.discovery.run_linkedin", return_value=([], {})),
            patch("role_scout.nodes.discovery.run_google", return_value=([], {})),
            patch("role_scout.nodes.discovery.run_trueup", return_value=([], {})),
        ):
            import structlog
            results = asyncio.run(
                _gather_sources(_make_profile(), settings, {"linkedin", "google", "trueup"}, structlog.get_logger())
            )

        assert results == []


# ---- T3: IMAP per-thread connection ----

class TestImaplibConcurrency:
    def test_t3_each_trueup_call_opens_own_connection(self):
        """T3: Concurrent run_trueup calls must each open their own IMAP connection."""
        from role_scout.fetchers.trueup_wrapper import run_trueup

        open_ids: list[int] = []
        connection_count = 0

        def fake_fetch_trueup(user, password):
            nonlocal connection_count
            connection_count += 1
            open_ids.append(id(user))
            time.sleep(0.02)
            return []

        with patch("role_scout.fetchers.trueup_wrapper.fetch_trueup", fake_fetch_trueup):
            async def run_two():
                import asyncio
                results = await asyncio.gather(
                    asyncio.to_thread(run_trueup, "user@a.com", "pw"),
                    asyncio.to_thread(run_trueup, "user@b.com", "pw"),
                )
                return results

            asyncio.run(run_two())

        # Both calls executed
        assert connection_count == 2

    def test_trueup_exception_propagates(self):
        """run_trueup must re-raise exceptions from fetch_trueup."""
        from role_scout.fetchers.trueup_wrapper import run_trueup

        with patch(
            "role_scout.fetchers.trueup_wrapper.fetch_trueup",
            side_effect=ConnectionError("IMAP auth failed"),
        ):
            with pytest.raises(ConnectionError, match="IMAP auth failed"):
                run_trueup("u@example.com", "pw")


# ---- T7: partial-failure circuit breaker ----

class TestPartialFailureCircuitBreaker:
    def _run_discovery(self, results_override: list, force_partial: bool = False) -> dict:
        """Helper: run discovery_node with mocked fetcher results."""
        from role_scout.nodes.discovery import discovery_node
        from role_scout.config import Settings

        state = _make_state(force_partial=force_partial)

        settings_mock = MagicMock(spec=Settings)
        settings_mock.APIFY_TOKEN = "tok"
        settings_mock.SERPAPI_KEY = "key"
        settings_mock.IMAP_EMAIL = "a@b.com"
        settings_mock.IMAP_APP_PASSWORD = "pw"
        settings_mock.DB_PATH = ":memory:"

        with (
            patch("role_scout.nodes.discovery.Settings", return_value=settings_mock),
            patch(
                "role_scout.nodes.discovery._gather_sources",
                return_value=results_override,
            ),
            patch("role_scout.nodes.discovery.normalize_jobs", return_value=[]),
            patch("role_scout.nodes.discovery.dedup_jobs", return_value=[]),
            patch("role_scout.nodes.discovery.get_rw_conn", return_value=MagicMock()),
            patch("role_scout.nodes.discovery._persist_health"),
        ):
            return discovery_node(state)

    def test_t7_two_sources_fail_triggers_circuit_breaker(self):
        """T7: ≥2 source failures and force_partial=False → cancel_reason='crippled_fetch'."""
        results = [
            ("linkedin", [], {}, 0.1, "timeout"),
            ("google", [], {}, 0.2, "quota exceeded"),
            ("trueup", [{"id": "1"}], {}, 0.3, None),
        ]
        update = self._run_discovery(results, force_partial=False)
        assert update.get("cancel_reason") == "crippled_fetch"
        assert update["normalized_jobs"] == []
        assert update["new_jobs"] == []

    def test_one_source_failure_no_circuit_breaker(self):
        """Only 1 failure with force_partial=False must NOT trigger circuit breaker."""
        results = [
            ("linkedin", [], {}, 0.1, "timeout"),
            ("google", [{"id": "1"}], {}, 0.2, None),
            ("trueup", [{"id": "2"}], {}, 0.3, None),
        ]
        update = self._run_discovery(results, force_partial=False)
        assert update.get("cancel_reason") is None

    def test_force_partial_bypasses_circuit_breaker(self):
        """force_partial=True: even 2 failures proceed (no cancel_reason set)."""
        results = [
            ("linkedin", [], {}, 0.1, "timeout"),
            ("google", [], {}, 0.2, "quota"),
            ("trueup", [{"id": "1"}], {}, 0.3, None),
        ]
        update = self._run_discovery(results, force_partial=True)
        assert update.get("cancel_reason") is None

    def test_all_sources_skipped_no_circuit_breaker(self):
        """Empty results list (all sources skipped) → no cancellation."""
        update = self._run_discovery([], force_partial=False)
        assert update.get("cancel_reason") is None

    def test_source_health_contains_failed_entries(self):
        """Failed sources must be recorded with status='failed' in source_health."""
        results = [
            ("linkedin", [], {}, 0.5, "IMAP error"),
            ("google", [{"id": "1"}], {}, 0.3, None),
            ("trueup", [], {}, 0.1, "timeout"),
        ]
        # 2 failures → circuit breaker fires, but health is still populated before return
        update = self._run_discovery(results, force_partial=False)
        health = update.get("source_health", {})
        assert health.get("linkedin") is not None
        assert health["linkedin"].status == "failed"
        assert health["linkedin"].error == "IMAP error"
        assert health.get("trueup") is not None
        assert health["trueup"].status == "failed"

"""Tests for dedup logic in discovery_node — failure path, circuit breaker interaction."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest


def _make_normalized_job(hash_id: str, source: str = "linkedin") -> MagicMock:
    job = MagicMock()
    job.hash_id = hash_id
    job.source = source
    job.title = "SWE"
    job.company = "Acme"
    return job


def _make_discovery_state() -> dict:
    profile = MagicMock()
    profile.target_roles = ["SWE"]
    profile.location = "Remote"
    return {
        "run_id": "run_dedup001",
        "candidate_profile": profile,
        "errors": [],
        "skipped_sources": [],
    }


class TestDedup:
    def test_dedup_failure_sets_cancel_reason(self):
        """If dedup raises an exception, discovery returns cancel_reason='dedup_failed'."""
        from role_scout.nodes.discovery import discovery_node

        state = _make_discovery_state()
        jobs = [_make_normalized_job("abc" + "0" * 13)]

        with patch("role_scout.nodes.discovery.asyncio.run", return_value=[
            ("linkedin", [{"id": "1"}], {}, 0.1, None),
        ]):
            with patch("role_scout.nodes.discovery.normalize_jobs", return_value=jobs):
                with patch("role_scout.nodes.discovery.get_rw_conn", side_effect=RuntimeError("db locked")):
                    with patch("role_scout.nodes.discovery.Settings"):
                        with patch("role_scout.nodes.discovery._persist_health"):
                            with patch("role_scout.nodes.discovery.get_excluded_set", return_value=frozenset()):
                                result = discovery_node(state)

        assert result.get("cancel_reason") == "dedup_failed"
        assert result.get("new_jobs") == []
        assert any("dedup_failed" in e for e in result.get("errors", []))

    def test_dedup_error_preserved_in_errors_list(self):
        """The dedup error message is captured in the errors field."""
        from role_scout.nodes.discovery import discovery_node

        state = _make_discovery_state()

        with patch("role_scout.nodes.discovery.asyncio.run", return_value=[
            ("linkedin", [{"id": "1"}], {}, 0.1, None),
        ]):
            with patch("role_scout.nodes.discovery.normalize_jobs", return_value=[]):
                with patch("role_scout.nodes.discovery.get_rw_conn", side_effect=sqlite3.OperationalError("locked")):
                    with patch("role_scout.nodes.discovery.Settings"):
                        with patch("role_scout.nodes.discovery._persist_health"):
                            with patch("role_scout.nodes.discovery.get_excluded_set", return_value=frozenset()):
                                result = discovery_node(state)

        assert result.get("cancel_reason") == "dedup_failed"
        errors = result.get("errors", [])
        assert len(errors) >= 1

    def test_new_jobs_count_set_after_dedup(self):
        """new_jobs_count in state matches actual deduped job count."""
        from role_scout.nodes.discovery import discovery_node

        state = _make_discovery_state()
        deduped = [_make_normalized_job(f"abc{i:013x}") for i in range(3)]

        mock_conn = MagicMock()

        with patch("role_scout.nodes.discovery.asyncio.run", return_value=[
            ("linkedin", [{"id": str(i)} for i in range(5)], {}, 0.1, None),
        ]):
            with patch("role_scout.nodes.discovery.normalize_jobs", return_value=deduped):
                with patch("role_scout.nodes.discovery.dedup_jobs", return_value=deduped):
                    with patch("role_scout.nodes.discovery.get_rw_conn", return_value=mock_conn):
                        with patch("role_scout.nodes.discovery.Settings"):
                            with patch("role_scout.nodes.discovery._persist_health"):
                                with patch("role_scout.nodes.discovery.get_excluded_set", return_value=frozenset()):
                                    result = discovery_node(state)

        assert result.get("new_jobs_count") == 3

    def test_crippled_fetch_does_not_reach_dedup(self):
        """When ≥2 sources fail and force_partial=False, dedup is never called."""
        from role_scout.nodes.discovery import discovery_node

        state = _make_discovery_state()

        with patch("role_scout.nodes.discovery.asyncio.run", return_value=[
            ("linkedin", [], {}, 0.1, "linkedin error"),
            ("google", [], {}, 0.1, "google error"),
            ("trueup", [], {}, 0.1, "trueup error"),
        ]):
            with patch("role_scout.nodes.discovery.Settings"):
                with patch("role_scout.nodes.discovery._persist_health"):
                    with patch("role_scout.nodes.discovery.get_excluded_set", return_value=frozenset()):
                        with patch("role_scout.nodes.discovery.dedup_jobs") as mock_dedup:
                            result = discovery_node(state)

        mock_dedup.assert_not_called()
        assert result.get("cancel_reason") == "crippled_fetch"

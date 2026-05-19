"""Unit tests for do-not-apply filtering in discovery_node."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from role_scout.compat.models import NormalizedJob


def _make_job(company: str, title: str = "Engineer") -> NormalizedJob:
    return NormalizedJob(
        hash_id="a" * 16,
        title=title,
        company=company,
        location="Remote",
        city="Remote",
        country="US",
        source="linkedin",
        url="https://example.com",
    )


def _make_state() -> dict:
    profile = MagicMock()
    profile.target_roles = ["Engineer"]
    profile.location = "Remote"
    profile.posted_within = 7
    return {
        "run_id": "run_test01",
        "candidate_profile": profile,
        "skipped_sources": [],
        "force_partial": False,
        "errors": [],
    }


class TestDonotapplyFiltering:
    def _run_discovery(self, normalized_jobs: list[NormalizedJob], excluded: frozenset[str], deduped: list[NormalizedJob]) -> dict:
        with (
            patch("role_scout.nodes.discovery.run_linkedin", return_value=([], {})),
            patch("role_scout.nodes.discovery.run_google", return_value=([], {})),
            patch("role_scout.nodes.discovery.run_trueup", return_value=([], {})),
            patch("role_scout.nodes.discovery.normalize_jobs", return_value=normalized_jobs),
            patch("role_scout.nodes.discovery.dedup_jobs", return_value=deduped),
            patch("role_scout.nodes.discovery.get_excluded_set", return_value=excluded),
            patch("role_scout.nodes.discovery.get_rw_conn"),
            patch("role_scout.nodes.discovery._persist_health"),
        ):
            from role_scout.nodes.discovery import discovery_node
            return discovery_node(_make_state())

    def test_excluded_company_dropped_post_normalize(self) -> None:
        jobs = [_make_job("Acme"), _make_job("SafeCo")]
        excluded = frozenset({"acme"})
        result = self._run_discovery(jobs, excluded, deduped=[_make_job("SafeCo")])
        companies = [j.company for j in result["new_jobs"]]
        assert "Acme" not in companies

    def test_allowed_company_passes_through(self) -> None:
        jobs = [_make_job("SafeCo")]
        excluded = frozenset({"acme"})
        result = self._run_discovery(jobs, excluded, deduped=[_make_job("SafeCo")])
        companies = [j.company for j in result["new_jobs"]]
        assert "SafeCo" in companies

    def test_case_insensitive_match(self) -> None:
        jobs = [_make_job("ACME Corp"), _make_job("SafeCo")]
        excluded = frozenset({"acme corp"})
        result = self._run_discovery(jobs, excluded, deduped=[_make_job("SafeCo")])
        companies = [j.company for j in result["new_jobs"]]
        assert "ACME Corp" not in companies

    def test_empty_exclusion_list_passes_all(self) -> None:
        jobs = [_make_job("Acme"), _make_job("BigCorp")]
        result = self._run_discovery(jobs, frozenset(), deduped=jobs)
        assert len(result["new_jobs"]) == 2

    def test_all_excluded_returns_empty(self) -> None:
        jobs = [_make_job("Acme")]
        excluded = frozenset({"acme"})
        result = self._run_discovery(jobs, excluded, deduped=[])
        assert result["new_jobs"] == []

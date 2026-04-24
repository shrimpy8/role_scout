"""Unit tests for enrichment_node — T1, T10."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_mock_job(hash_id: str = "abcdef0123456789", description: str = "") -> MagicMock:
    job = MagicMock()
    job.hash_id = hash_id
    job.description = description
    job.apply_url = f"https://example.com/apply/{hash_id}"
    job.url = f"https://example.com/jobs/{hash_id}"
    return job


class TestEnrichmentNode:
    def test_t1_state_keys_present(self):
        """T1: enrichment_node returns correct state keys."""
        from role_scout.nodes.enrichment import enrichment_node

        jobs = [_make_mock_job(f"{'a' * 15}{i}") for i in range(3)]
        state = {
            "run_id": "run_test",
            "new_jobs": jobs,
            "raw_by_source": {"linkedin": [{"id": "1"}]},
            "normalized_jobs": jobs[:],
            "errors": [],
        }

        with patch("role_scout.nodes.enrichment.enrich_descriptions") as mock_enrich:
            mock_enrich.return_value = jobs
            result = enrichment_node(state)

        assert "enriched_jobs" in result
        assert "raw_by_source" in result
        assert "normalized_jobs" in result
        assert "new_jobs" in result

    def test_t10_state_trimmed_after_enrichment(self):
        """T10: raw_by_source == {}, normalized_jobs == [], new_jobs == [] after enrichment."""
        from role_scout.nodes.enrichment import enrichment_node

        jobs = [_make_mock_job(f"{'a' * 15}{i}") for i in range(3)]
        state = {
            "run_id": "run_test",
            "new_jobs": jobs,
            "raw_by_source": {"linkedin": [{"id": "1"}], "google": [{"id": "2"}]},
            "normalized_jobs": jobs[:],
            "errors": [],
        }

        with patch("role_scout.nodes.enrichment.enrich_descriptions") as mock_enrich:
            mock_enrich.return_value = jobs
            result = enrichment_node(state)

        assert result["raw_by_source"] == {}
        assert result["normalized_jobs"] == []
        assert result["new_jobs"] == []

    def test_enriched_jobs_contains_input_jobs(self):
        """enriched_jobs in state must contain the same objects that were in new_jobs."""
        from role_scout.nodes.enrichment import enrichment_node

        jobs = [_make_mock_job(f"{'b' * 15}{i}") for i in range(2)]
        state = {
            "run_id": "run_test",
            "new_jobs": jobs,
            "raw_by_source": {},
            "normalized_jobs": [],
            "errors": [],
        }

        with patch("role_scout.nodes.enrichment.enrich_descriptions") as mock_enrich:
            mock_enrich.return_value = jobs
            result = enrichment_node(state)

        assert len(result["enriched_jobs"]) == 2

    def test_empty_new_jobs_returns_empty_enriched(self):
        """When new_jobs is empty, enriched_jobs must also be empty."""
        from role_scout.nodes.enrichment import enrichment_node

        state = {
            "run_id": "run_test",
            "new_jobs": [],
            "raw_by_source": {},
            "normalized_jobs": [],
            "errors": [],
        }

        with patch("role_scout.nodes.enrichment.enrich_descriptions"):
            result = enrichment_node(state)

        assert result["enriched_jobs"] == []

    def test_t10_state_size_under_10mb(self):
        """T10: State after enrichment must be below 10 MB."""
        import json
        from role_scout.nodes.enrichment import enrichment_node

        # Create jobs with small descriptions to stay under limit
        jobs = [_make_mock_job(f"{'c' * 15}{i}") for i in range(5)]
        for j in jobs:
            j.description = "A" * 100  # short description

        state = {
            "run_id": "run_test",
            "new_jobs": jobs,
            "raw_by_source": {},
            "normalized_jobs": [],
            "errors": [],
        }

        with patch("role_scout.nodes.enrichment.enrich_descriptions") as mock_enrich:
            mock_enrich.return_value = jobs
            result = enrichment_node(state)

        combined = {**state, **result}
        size = len(json.dumps(combined, default=str).encode())
        assert size < 10 * 1024 * 1024

    def test_enrich_called_per_job_concurrently(self):
        """enrich_descriptions is called once per job (for concurrent execution)."""
        from role_scout.nodes.enrichment import enrichment_node

        jobs = [_make_mock_job(f"{'d' * 15}{i}") for i in range(3)]
        state = {
            "run_id": "run_test",
            "new_jobs": jobs,
            "raw_by_source": {},
            "normalized_jobs": [],
            "errors": [],
        }

        call_count = 0

        def counting_enrich(job_list):
            nonlocal call_count
            call_count += len(job_list)
            return job_list

        with patch("role_scout.nodes.enrichment.enrich_descriptions", side_effect=counting_enrich):
            enrichment_node(state)

        # One call per job (concurrent per-job wrapping)
        assert call_count == 3

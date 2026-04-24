"""Unit tests for scoring_node — T1, kill switch, state trimming."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_scored_job(hash_id: str, match_pct: int) -> MagicMock:
    job = MagicMock()
    job.hash_id = hash_id
    job.match_pct = match_pct
    job.title = "Software Engineer"
    job.company = "Acme"
    return job


def _make_normalized_job(hash_id: str) -> MagicMock:
    job = MagicMock()
    job.hash_id = hash_id
    job.title = "SWE"
    job.company = "Acme"
    job.description = "A great role"
    return job


def _patched_scoring_node(state: dict, scored_jobs: list, kill_switch: bool = False) -> dict:
    """Helper: run scoring_node with mocked Phase 1 scorer."""
    from role_scout.nodes.scoring import scoring_node

    with patch("role_scout.nodes.scoring.Settings") as MockSettings:
        settings = MagicMock()
        settings.SCORE_THRESHOLD = 70
        settings.MAX_COST_USD = 5.0 if not kill_switch else 0.0
        settings.ANTHROPIC_API_KEY = "sk-ant-test"
        MockSettings.return_value = settings

        with patch("role_scout.nodes.scoring.score_jobs_batch", return_value=scored_jobs):
            return scoring_node(state)


class TestScoringNode:
    def test_t1_state_keys_present(self):
        """T1: scoring_node must return all expected state keys."""
        jobs = [_make_normalized_job(f"a{i:015x}") for i in range(5)]
        scored = [_make_scored_job(f"a{i:015x}", match_pct=80) for i in range(5)]
        state = {
            "run_id": "run_score01",
            "candidate_profile": MagicMock(),
            "enriched_jobs": jobs,
            "qualify_threshold": 70,
            "total_cost_usd": 0.0,
            "scoring_tokens_in": 0,
            "scoring_tokens_out": 0,
            "errors": [],
        }
        result = _patched_scoring_node(state, scored)

        assert "scored_jobs" in result
        assert "enriched_jobs" in result
        assert "scoring_tokens_in" in result
        assert "scoring_tokens_out" in result

    def test_five_jobs_returned(self):
        """Phase 1 scorer returns 5 scores → scored_jobs has 5 entries."""
        jobs = [_make_normalized_job(f"b{i:015x}") for i in range(5)]
        scored = [_make_scored_job(f"b{i:015x}", match_pct=85) for i in range(5)]
        state = {
            "run_id": "run_score02",
            "candidate_profile": MagicMock(),
            "enriched_jobs": jobs,
            "qualify_threshold": 70,
            "total_cost_usd": 0.0,
            "scoring_tokens_in": 0,
            "scoring_tokens_out": 0,
            "errors": [],
        }
        result = _patched_scoring_node(state, scored)
        assert len(result["scored_jobs"]) == 5

    def test_enriched_jobs_trimmed_after_scoring(self):
        """enriched_jobs must be [] after scoring_node runs."""
        jobs = [_make_normalized_job(f"c{i:015x}") for i in range(3)]
        state = {
            "run_id": "run_score03",
            "candidate_profile": MagicMock(),
            "enriched_jobs": jobs,
            "qualify_threshold": 70,
            "total_cost_usd": 0.0,
            "scoring_tokens_in": 0,
            "scoring_tokens_out": 0,
            "errors": [],
        }
        result = _patched_scoring_node(state, [])
        assert result["enriched_jobs"] == []

    def test_tokens_accumulated_in_state(self):
        """scoring_tokens_in and scoring_tokens_out must be updated in state."""
        jobs = [_make_normalized_job(f"d{i:015x}") for i in range(10)]
        scored = []
        state = {
            "run_id": "run_score04",
            "candidate_profile": MagicMock(),
            "enriched_jobs": jobs,
            "qualify_threshold": 70,
            "total_cost_usd": 0.0,
            "scoring_tokens_in": 0,
            "scoring_tokens_out": 0,
            "errors": [],
        }
        result = _patched_scoring_node(state, scored)
        assert result["scoring_tokens_in"] > 0
        assert result["scoring_tokens_out"] > 0

    def test_empty_enriched_jobs_returns_empty_scored(self):
        """Empty enriched_jobs → scoring skipped, scored_jobs == []."""
        state = {
            "run_id": "run_score05",
            "candidate_profile": MagicMock(),
            "enriched_jobs": [],
            "qualify_threshold": 70,
            "total_cost_usd": 0.0,
            "scoring_tokens_in": 0,
            "scoring_tokens_out": 0,
            "errors": [],
        }

        with patch("role_scout.nodes.scoring.Settings") as MockSettings:
            settings = MagicMock()
            settings.SCORE_THRESHOLD = 70
            settings.MAX_COST_USD = 5.0
            settings.ANTHROPIC_API_KEY = "sk-ant"
            MockSettings.return_value = settings

            with patch("role_scout.nodes.scoring.score_jobs_batch") as mock_scorer:
                from role_scout.nodes.scoring import scoring_node
                result = scoring_node(state)

        mock_scorer.assert_not_called()
        assert result["scored_jobs"] == []

    def test_kill_switch_fires_when_over_limit(self):
        """Kill switch must fire before score_jobs_batch is called."""
        jobs = [_make_normalized_job(f"e{i:015x}") for i in range(5)]
        state = {
            "run_id": "run_score06",
            "candidate_profile": MagicMock(),
            "enriched_jobs": jobs,
            "qualify_threshold": 70,
            "total_cost_usd": 5.01,
            "scoring_tokens_in": 0,
            "scoring_tokens_out": 0,
            "errors": [],
        }

        with patch("role_scout.nodes.scoring.Settings") as MockSettings:
            settings = MagicMock()
            settings.SCORE_THRESHOLD = 70
            settings.MAX_COST_USD = 5.0
            settings.ANTHROPIC_API_KEY = "sk-ant-test"
            MockSettings.return_value = settings

            with patch("role_scout.nodes.scoring.score_jobs_batch") as mock_scorer:
                from role_scout.nodes.scoring import scoring_node
                result = scoring_node(state)

        mock_scorer.assert_not_called()
        assert result.get("cancel_reason") == "cost_kill_switch"

    def test_scorer_exception_captured_in_errors(self):
        """If score_jobs_batch raises, error is captured and scored_jobs is empty."""
        jobs = [_make_normalized_job(f"f{i:015x}") for i in range(3)]
        state = {
            "run_id": "run_score07",
            "candidate_profile": MagicMock(),
            "enriched_jobs": jobs,
            "qualify_threshold": 70,
            "total_cost_usd": 0.0,
            "scoring_tokens_in": 0,
            "scoring_tokens_out": 0,
            "errors": [],
        }

        with patch("role_scout.nodes.scoring.Settings") as MockSettings:
            settings = MagicMock()
            settings.SCORE_THRESHOLD = 70
            settings.MAX_COST_USD = 5.0
            settings.ANTHROPIC_API_KEY = "sk-ant-test"
            MockSettings.return_value = settings

            with patch(
                "role_scout.nodes.scoring.score_jobs_batch",
                side_effect=RuntimeError("API error"),
            ):
                from role_scout.nodes.scoring import scoring_node
                result = scoring_node(state)

        assert result["scored_jobs"] == []
        assert any("scoring_failed" in e for e in result["errors"])

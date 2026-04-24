"""Unit tests for reflection_node — T12, T13, T14, T15."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _make_scored_job(
    hash_id: str,
    match_pct: int,
    salary_visible: bool = True,
    comp_score: int | None = None,
) -> MagicMock:
    job = MagicMock()
    job.hash_id = hash_id
    job.match_pct = match_pct
    job.salary_visible = salary_visible
    job.comp_score = comp_score if comp_score is not None else (5 if not salary_visible else 7)
    job.title = "Software Engineer"
    job.company = "Acme"
    job.description = "Great role"
    job.role_fit = 8
    job.domain_fit = 7
    job.level_fit = 8
    job.location_fit = 9

    def _copy(update=None):
        copy = MagicMock()
        copy.hash_id = hash_id
        copy.match_pct = update.get("match_pct", match_pct) if update else match_pct
        copy.comp_score = update.get("comp_score", job.comp_score) if update else job.comp_score
        copy.salary_visible = salary_visible
        copy.title = "Software Engineer"
        copy.company = "Acme"
        return copy

    job.model_copy = _copy
    return job


def _run_reflection(scored_jobs, response_json: dict | None = None, enabled: bool = True) -> dict:
    """Helper: run reflection_node with mocked Claude and Settings."""
    from role_scout.nodes.reflection import reflection_node

    resp_text = json.dumps(response_json) if response_json else '{"changed": false}'

    profile = MagicMock()
    profile.model_dump.return_value = {"name": "Test", "target_roles": ["SWE"]}

    state = {
        "run_id": "run_refl01",
        "candidate_profile": profile,
        "scored_jobs": scored_jobs,
        "reflection_tokens_in": 0,
        "reflection_tokens_out": 0,
        "total_cost_usd": 0.0,
        "scoring_tokens_in": 0,
        "scoring_tokens_out": 0,
        "errors": [],
    }

    with patch("role_scout.nodes.reflection.Settings") as MockSettings:
        settings = MagicMock()
        settings.REFLECTION_ENABLED = enabled
        settings.REFLECTION_BAND_LOW = 70
        settings.REFLECTION_BAND_HIGH = 89
        settings.MAX_COST_USD = 5.0
        settings.ANTHROPIC_API_KEY = "sk-ant-test"
        MockSettings.return_value = settings

        with patch(
            "role_scout.nodes.reflection.call_claude",
            return_value=(resp_text, 1000, 200),
        ):
            return reflection_node(state)


class TestReflectionNode:
    def test_t14_high_score_not_sent_to_claude(self):
        """T14: Jobs with match_pct=95 (above band) must NOT be sent to Claude."""
        jobs = [_make_scored_job("a" * 16, match_pct=95)]

        from role_scout.nodes.reflection import reflection_node

        profile = MagicMock()
        profile.model_dump.return_value = {}
        state = {
            "run_id": "run_t14",
            "candidate_profile": profile,
            "scored_jobs": jobs,
            "reflection_tokens_in": 0,
            "reflection_tokens_out": 0,
            "total_cost_usd": 0.0,
            "scoring_tokens_in": 0,
            "scoring_tokens_out": 0,
            "errors": [],
        }

        with patch("role_scout.nodes.reflection.Settings") as MockSettings:
            settings = MagicMock()
            settings.REFLECTION_ENABLED = True
            settings.REFLECTION_BAND_LOW = 70
            settings.REFLECTION_BAND_HIGH = 89
            settings.MAX_COST_USD = 5.0
            settings.ANTHROPIC_API_KEY = "sk-ant-test"
            MockSettings.return_value = settings

            with patch("role_scout.nodes.reflection.call_claude") as mock_claude:
                reflection_node(state)

        mock_claude.assert_not_called()

    def test_t14_low_score_not_sent_to_claude(self):
        """T14: Jobs with match_pct=60 (below band) must NOT be sent to Claude."""
        jobs = [_make_scored_job("b" * 16, match_pct=60)]

        from role_scout.nodes.reflection import reflection_node

        profile = MagicMock()
        profile.model_dump.return_value = {}
        state = {
            "run_id": "run_t14b",
            "candidate_profile": profile,
            "scored_jobs": jobs,
            "reflection_tokens_in": 0,
            "reflection_tokens_out": 0,
            "total_cost_usd": 0.0,
            "scoring_tokens_in": 0,
            "scoring_tokens_out": 0,
            "errors": [],
        }

        with patch("role_scout.nodes.reflection.Settings") as MockSettings:
            settings = MagicMock()
            settings.REFLECTION_ENABLED = True
            settings.REFLECTION_BAND_LOW = 70
            settings.REFLECTION_BAND_HIGH = 89
            settings.MAX_COST_USD = 5.0
            settings.ANTHROPIC_API_KEY = "sk-ant-test"
            MockSettings.return_value = settings

            with patch("role_scout.nodes.reflection.call_claude") as mock_claude:
                reflection_node(state)

        mock_claude.assert_not_called()

    def test_t13_salary_not_visible_comp_corrected(self):
        """T13: salary_visible=False, comp_score=0 → Claude returns comp_score=5."""
        jobs = [
            _make_scored_job("c" * 16, match_pct=78, salary_visible=False, comp_score=0)
        ]
        response = {
            "revised_score": 82,
            "revised_subscores": {
                "role_fit": 8,
                "domain_fit": 7,
                "comp_score": 5,
                "level_fit": 8,
                "location_fit": 9,
            },
            "reasoning": "comp_score corrected from 0 to 5 (salary not visible)",
            "changed": True,
        }
        result = _run_reflection(jobs, response)
        updated = result.get("scored_jobs", [])
        assert len(updated) == 1
        assert updated[0].comp_score == 5
        assert result.get("reflection_applied_count") == 1

    def test_t15_malformed_json_keeps_original_score(self):
        """T15: Claude returns malformed JSON → original score preserved, reflection_applied=False."""
        jobs = [_make_scored_job("d" * 16, match_pct=75)]

        from role_scout.nodes.reflection import reflection_node

        profile = MagicMock()
        profile.model_dump.return_value = {}
        state = {
            "run_id": "run_t15",
            "candidate_profile": profile,
            "scored_jobs": jobs,
            "reflection_tokens_in": 0,
            "reflection_tokens_out": 0,
            "total_cost_usd": 0.0,
            "scoring_tokens_in": 0,
            "scoring_tokens_out": 0,
            "errors": [],
        }

        with patch("role_scout.nodes.reflection.Settings") as MockSettings:
            settings = MagicMock()
            settings.REFLECTION_ENABLED = True
            settings.REFLECTION_BAND_LOW = 70
            settings.REFLECTION_BAND_HIGH = 89
            settings.MAX_COST_USD = 5.0
            settings.ANTHROPIC_API_KEY = "sk-ant-test"
            MockSettings.return_value = settings

            with patch(
                "role_scout.nodes.reflection.call_claude",
                return_value=("this is not json at all {{{broken", 500, 100),
            ):
                result = reflection_node(state)

        updated = result.get("scored_jobs", [])
        assert len(updated) == 1
        assert updated[0].match_pct == 75  # unchanged
        assert result.get("reflection_applied_count") == 0

    def test_t12_reflection_cost_tracked(self):
        """T12: reflection_tokens_in and reflection_tokens_out accumulated in state."""
        jobs = [_make_scored_job(f"e{i:015x}", match_pct=78) for i in range(3)]
        result = _run_reflection(jobs, {"changed": False})
        assert result.get("reflection_tokens_in", 0) > 0
        assert result.get("reflection_tokens_out", 0) > 0

    def test_borderline_job_sent_to_claude(self):
        """Job in 70-89 band must be sent to Claude for reflection."""
        jobs = [_make_scored_job("f" * 16, match_pct=80)]

        from role_scout.nodes.reflection import reflection_node

        profile = MagicMock()
        profile.model_dump.return_value = {}
        state = {
            "run_id": "run_border",
            "candidate_profile": profile,
            "scored_jobs": jobs,
            "reflection_tokens_in": 0,
            "reflection_tokens_out": 0,
            "total_cost_usd": 0.0,
            "scoring_tokens_in": 0,
            "scoring_tokens_out": 0,
            "errors": [],
        }

        with patch("role_scout.nodes.reflection.Settings") as MockSettings:
            settings = MagicMock()
            settings.REFLECTION_ENABLED = True
            settings.REFLECTION_BAND_LOW = 70
            settings.REFLECTION_BAND_HIGH = 89
            settings.MAX_COST_USD = 5.0
            settings.ANTHROPIC_API_KEY = "sk-ant-test"
            MockSettings.return_value = settings

            with patch(
                "role_scout.nodes.reflection.call_claude",
                return_value=('{"changed": false}', 500, 100),
            ) as mock_claude:
                reflection_node(state)

        mock_claude.assert_called_once()

    def test_reflection_disabled_returns_empty(self):
        """When REFLECTION_ENABLED=False, reflection_node returns immediately."""
        jobs = [_make_scored_job("g" * 16, match_pct=80)]
        result = _run_reflection(jobs, enabled=False)
        assert result == {}

    def test_claude_error_captured_in_errors(self):
        """Call failure must be captured in errors list; job score unchanged."""
        jobs = [_make_scored_job("h" * 16, match_pct=80)]

        from role_scout.nodes.reflection import reflection_node

        profile = MagicMock()
        profile.model_dump.return_value = {}
        state = {
            "run_id": "run_err",
            "candidate_profile": profile,
            "scored_jobs": jobs,
            "reflection_tokens_in": 0,
            "reflection_tokens_out": 0,
            "total_cost_usd": 0.0,
            "scoring_tokens_in": 0,
            "scoring_tokens_out": 0,
            "errors": [],
        }

        with patch("role_scout.nodes.reflection.Settings") as MockSettings:
            settings = MagicMock()
            settings.REFLECTION_ENABLED = True
            settings.REFLECTION_BAND_LOW = 70
            settings.REFLECTION_BAND_HIGH = 89
            settings.MAX_COST_USD = 5.0
            settings.ANTHROPIC_API_KEY = "sk-ant-test"
            MockSettings.return_value = settings

            with patch(
                "role_scout.nodes.reflection.call_claude",
                side_effect=Exception("Claude unavailable"),
            ):
                result = reflection_node(state)

        assert result.get("reflection_applied_count") == 0
        assert any("reflection_failed" in e for e in result.get("errors", []))

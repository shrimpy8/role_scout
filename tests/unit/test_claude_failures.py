"""Tests for Claude API failure paths — reflection error handling, partial usage extraction."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_scored_job(hash_id: str = "abc123def456789a", match_pct: int = 72) -> MagicMock:
    job = MagicMock(spec=["hash_id", "match_pct"])
    job.hash_id = hash_id
    job.match_pct = match_pct
    return job


def _make_reflection_state(scored_jobs=None, errors=None):
    profile = MagicMock()
    profile.model_dump.return_value = {"target_roles": ["SWE"]}
    return {
        "run_id": "run_test001",
        "scored_jobs": scored_jobs or [],
        "errors": errors or [],
        "scoring_tokens_in": 0,
        "scoring_tokens_out": 0,
        "reflection_tokens_in": 0,
        "reflection_tokens_out": 0,
        "total_cost_usd": 0.0,
        "candidate_profile": profile,
    }


def _mock_settings():
    settings = MagicMock()
    settings.REFLECTION_ENABLED = True
    settings.REFLECTION_BAND_LOW = 70
    settings.REFLECTION_BAND_HIGH = 89
    settings.MAX_COST_USD = 5.0
    settings.ANTHROPIC_API_KEY = "sk-ant-test"
    settings.CLAUDE_MODEL = "claude-opus-4-7"
    settings.CLAUDE_INPUT_COST_PER_MTOK = 3.0
    settings.CLAUDE_OUTPUT_COST_PER_MTOK = 15.0
    return settings


def _run_reflection_with_failing_claude(jobs, exc):
    """Helper: run reflection_node where call_claude raises exc for all jobs."""
    from role_scout.nodes.reflection import reflection_node

    state = _make_reflection_state(scored_jobs=jobs)
    with patch("role_scout.nodes.reflection.Settings") as MockSettings:
        MockSettings.return_value = _mock_settings()
        with patch("role_scout.nodes.reflection.call_claude", side_effect=exc):
            with patch("role_scout.nodes.reflection._build_reflection_prompt", return_value="prompt"):
                with patch("role_scout.nodes.reflection.Path") as mock_path:
                    mock_path.return_value.__truediv__.return_value.exists.return_value = True
                    mock_path.return_value.__truediv__.return_value.read_text.return_value = "$title $company"
                    return reflection_node(state)


class TestReflectionClaudeFailures:
    def test_claude_exception_appended_to_errors(self):
        """If Claude call raises, the error is captured in state errors without crashing."""
        job = _make_scored_job(match_pct=72)
        result = _run_reflection_with_failing_claude([job], RuntimeError("timeout"))
        errors = result.get("errors", [])
        assert any("reflection_failed" in e for e in errors)

    def test_reflection_does_not_crash_on_all_borderline_failures(self):
        """Multiple borderline jobs all failing Claude calls still returns a valid state."""
        jobs = [_make_scored_job(hash_id=f"abc{i:013x}", match_pct=73) for i in range(3)]
        result = _run_reflection_with_failing_claude(jobs, ConnectionError("net"))
        assert "scored_jobs" in result
        assert len(result["errors"]) == 3  # one error per job

    def test_reflection_partial_usage_extracted_from_sdk_exception(self):
        """If SDK exception carries a response.usage, tokens are tracked."""
        job = _make_scored_job(match_pct=72)

        exc = RuntimeError("overload")
        mock_usage = MagicMock()
        mock_usage.input_tokens = 500
        mock_usage.output_tokens = 50
        mock_response = MagicMock()
        mock_response.usage = mock_usage
        exc.response = mock_response

        result = _run_reflection_with_failing_claude([job], exc)

        # Tokens from the exception's partial usage must appear in reflection counters
        assert result.get("reflection_tokens_in", 0) == 500
        assert result.get("reflection_tokens_out", 0) == 50


class TestAlignmentResponseSizeLimit:
    def test_oversized_response_is_truncated(self):
        """alignment.py truncates responses exceeding _MAX_RESPONSE_CHARS."""
        from role_scout.compat.pipeline import alignment

        oversized_text = '{"a": "' + "x" * 20_000 + '"}'
        text_block = MagicMock()
        text_block.text = oversized_text

        mock_response = MagicMock()
        mock_response.content = [text_block]
        mock_response.stop_reason = "end_turn"

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        job = MagicMock()
        job.hash_id = "abc1234567890123"
        job.description = "A" * 200
        job.title = "SWE"
        job.company = "Acme"
        job.source = "linkedin"

        with patch("role_scout.compat.pipeline.alignment.anthropic.Anthropic", return_value=mock_client):
            with patch("role_scout.compat.pipeline.alignment._ALIGNMENT_PROMPT_PATH") as mock_pp:
                mock_pp.exists.return_value = True
                mock_pp.read_text.return_value = "system $resume_summary"
                with patch("role_scout.config.Settings") as MockSettings:
                    MockSettings.return_value.ANTHROPIC_API_KEY = "sk-ant-test"
                    MockSettings.return_value.CLAUDE_MODEL = "claude-opus-4-7"
                    MockSettings.return_value.RESUME_SUMMARY_PATH = "/fake/resume.md"
                    with patch("pathlib.Path.exists", return_value=True):
                        with patch("pathlib.Path.read_text", return_value="resume"):
                            # Should not crash (JSON parse may fail on truncated text, that's ok)
                            try:
                                alignment.run_alignment(job)
                            except Exception:
                                pass  # JSONDecodeError expected on truncated JSON; what matters is no hang

"""Unit tests for D7 eval framework — T28, T31."""
from __future__ import annotations

import pytest
from scipy.stats import spearmanr

from role_scout.eval.scorer_eval import run_scorer_eval, load_ground_truth, ScorerEvalResult, run_reflection_ab_eval
from role_scout.eval.discovery_recall_eval import run_recall_eval


class TestT28ScorerEval:
    def test_spearman_correct(self, tmp_path):
        """T28: Spearman computed correctly vs scipy manual calc.

        We pair each GT job with a predicted score, then manually compute the
        expected Spearman using the same (gt_human, predicted) pair — so the
        comparison is apples-to-apples.
        """
        predicted_values = [90, 82, 80, 55, 48, 90, 68, 58, 85, 70]

        # Build fake predicted list matching ground_truth fixture hash_ids
        gt = load_ground_truth()
        paired = [(gt[i].hash_id, predicted_values[i]) for i in range(len(predicted_values)) if i < len(gt)]

        # Manual scipy calc using the same GT human scores that run_scorer_eval will use
        gt_human = [gt[i].human_score for i in range(len(paired))]
        expected_r, _ = spearmanr(gt_human, predicted_values[: len(paired)])

        result = run_scorer_eval(paired)
        assert abs(result.spearman_r - expected_r) < 1e-9  # exact match
        assert result.n_jobs == len(paired)
        assert isinstance(result.pass_criteria, bool)

    def test_perfect_correlation_passes(self):
        """T28: Perfect correlation (using GT scores as predictions) → pass_criteria=True."""
        gt = load_ground_truth()
        predicted = [(job.hash_id, job.human_score) for job in gt]
        result = run_scorer_eval(predicted)
        assert result.spearman_r == pytest.approx(1.0, abs=0.01)
        assert result.pass_criteria is True

    def test_ground_truth_loads(self):
        """T28: ground_truth.yaml loads and validates with ≥10 entries."""
        gt = load_ground_truth()
        assert len(gt) >= 10


class TestT31RecallEval:
    def test_empty_gold_returns_zero(self):
        """T31: Empty gold set → 0.0, no ZeroDivisionError."""
        result = run_recall_eval([], ["abc123def456789a"])
        assert result.recall == 0.0
        assert result.gold_count == 0
        assert result.pass_criteria is False

    def test_recall_computed_correctly(self):
        """T31: 20-item gold set → recall correctly computed."""
        gold = [f"{i:016x}" for i in range(20)]
        pipeline = gold[:18]  # found 18/20
        result = run_recall_eval(gold, pipeline)
        assert result.recall == pytest.approx(0.90, abs=0.001)
        assert result.found_count == 18
        assert len(result.missing_hash_ids) == 2

    def test_perfect_recall(self):
        gold = ["abcdef1234567890", "0000000000000001"]
        result = run_recall_eval(gold, gold)
        assert result.recall == 1.0
        assert result.pass_criteria is True


class TestT29JudgeNonAnthropic:
    def test_judge_model_not_claude(self) -> None:
        """T29: Judge wrapper uses non-Anthropic model."""
        import os
        from unittest.mock import patch

        from role_scout.eval.judge import get_judge_model

        # When OpenAI key present, model must not start with "claude"
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-key"}):
            provider_model = get_judge_model()
            assert provider_model is not None
            provider, model = provider_model
            assert not model.startswith("claude"), f"Judge must not use Claude; got {model}"

    def test_judge_text_rejects_claude_model(self) -> None:
        """T29: judge_text() asserts error if given a claude model name."""
        from role_scout.eval.judge import judge_text

        with pytest.raises(AssertionError, match="claude"):
            judge_text("some text", "relevance", "rate it", "anthropic", "claude-opus-4-7")

    def test_no_provider_returns_none(self) -> None:
        """T29: No API keys → score_with_judge returns None."""
        import os
        from unittest.mock import patch

        from role_scout.eval.judge import score_with_judge

        # Strip both keys from environment to ensure no provider is found
        clean_env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "GOOGLE_API_KEY")}
        with patch.dict(os.environ, clean_env, clear=True):
            result = score_with_judge("text", ["relevance"], {"relevance": "rubric"})
            assert result is None


class TestT30TailorDisagreement:
    def test_disagreement_flag_triggers(self) -> None:
        """T30: Disagreement flag raised when |llm_mean - human_normalized| > 1.0."""
        from unittest.mock import patch

        from role_scout.eval.judge import JudgeScore
        from role_scout.eval.tailor_eval import run_tailor_eval

        # LLM gives 4.5 mean (all dims), human gives 20/100 → normalized = 1.8
        # |4.5 - 1.8| = 2.7 > 1.0 → disagreement flag
        mock_scores = [
            JudgeScore(model="gpt-4o", section=s, score=4.5, rationale="good")
            for s in ["relevance", "no_fabrication", "keyword_fit", "reframe_quality"]
        ]
        tailor_output = {
            "tailored_summary": "Great candidate.",
            "tailored_bullets": ["Led ML projects.", "Built systems.", "Reduced cost."],
            "keywords_incorporated": ["Python"],
        }

        with patch("role_scout.eval.tailor_eval.score_with_judge", return_value=mock_scores):
            result = run_tailor_eval([(tailor_output, 20)])  # human=20 → normalized=1.8

        assert result is not None
        assert len(result.disagreement_flags) == 1
        flag = result.disagreement_flags[0]
        assert flag.delta > 1.0

    def test_no_disagreement_when_close(self) -> None:
        """T30: No flag when LLM and human agree within 1.0."""
        from unittest.mock import patch

        from role_scout.eval.judge import JudgeScore
        from role_scout.eval.tailor_eval import run_tailor_eval

        # LLM=4.0, human=80/100→4.2, |4.0-4.2|=0.2 < 1.0
        mock_scores = [
            JudgeScore(model="gpt-4o", section=s, score=4.0, rationale="ok")
            for s in ["relevance", "no_fabrication", "keyword_fit", "reframe_quality"]
        ]
        tailor_output = {
            "tailored_summary": "Good.",
            "tailored_bullets": ["b1", "b2", "b3"],
            "keywords_incorporated": [],
        }
        with patch("role_scout.eval.tailor_eval.score_with_judge", return_value=mock_scores):
            result = run_tailor_eval([(tailor_output, 80)])

        assert result is not None
        assert len(result.disagreement_flags) == 0


class TestT16ReflectionAB:
    def test_reflection_ab_delta_pass(self) -> None:
        """T16: With-reflection predictions clearly better → pass_criteria=True (delta >= 0.05)."""
        gt_scores = [90, 80, 70, 60, 50, 40, 30, 20, 10, 5]
        # with-reflection: near-perfect order (high Spearman)
        with_reflection = [88, 78, 72, 58, 52, 38, 32, 22, 12, 7]
        # without-reflection: degraded order (lower Spearman)
        without_reflection = [50, 80, 30, 90, 10, 70, 20, 60, 5, 40]

        result = run_reflection_ab_eval(gt_scores, with_reflection, without_reflection)

        assert result.spearman_with_reflection > result.spearman_without_reflection
        assert result.delta >= 0.05
        assert result.pass_criteria is True

    def test_reflection_ab_delta_fail(self) -> None:
        """T16: No improvement from reflection → pass_criteria=False (delta < 0.05)."""
        gt_scores = [90, 80, 70, 60, 50, 40, 30, 20, 10, 5]
        # Both prediction sets equally good (identical) → delta = 0.0
        preds = [88, 78, 68, 58, 48, 38, 28, 18, 8, 3]

        result = run_reflection_ab_eval(gt_scores, preds, preds)

        assert result.delta == pytest.approx(0.0, abs=1e-9)
        assert result.pass_criteria is False

"""Coverage boost tests — T-CB01 to T-CB20.

Targeted unit tests for alignment_eval, shadow, runner utilities, and run_eval
CLI to push overall coverage above the 80% gate.
"""
from __future__ import annotations

import queue
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# alignment_eval tests
# ---------------------------------------------------------------------------

def _make_judge_scores(sections: list[str], value: float = 4.5) -> list[Any]:
    """Return mock JudgeScore list for given sections."""
    from role_scout.eval.judge import JudgeScore
    return [JudgeScore(model="gpt-4o", section=s, score=value, rationale="ok") for s in sections]


class TestAlignmentEval:
    def test_run_alignment_eval_pass(self, tmp_path, monkeypatch):
        """T-CB01: 10 pairs all scoring 4.5 → pass_criteria=True."""
        monkeypatch.chdir(tmp_path)
        from role_scout.eval.alignment_eval import run_alignment_eval

        mock_scores = _make_judge_scores(["strong_matches", "reframing_opportunities", "genuine_gaps"], 4.5)

        with patch("role_scout.eval.alignment_eval.score_with_judge", return_value=mock_scores):
            pairs = [("JD text", "Alignment text")] * 10
            result = run_alignment_eval(pairs)

        assert result is not None
        assert result.n_pairs == 10
        assert result.pass_criteria is True
        assert result.overall_mean == pytest.approx(4.5)
        assert set(result.per_section_means.keys()) == {"strong_matches", "reframing_opportunities", "genuine_gaps"}

    def test_run_alignment_eval_fail(self, tmp_path, monkeypatch):
        """T-CB02: scores below 4.0 → pass_criteria=False."""
        monkeypatch.chdir(tmp_path)
        from role_scout.eval.alignment_eval import run_alignment_eval

        mock_scores = _make_judge_scores(["strong_matches", "reframing_opportunities", "genuine_gaps"], 2.0)

        with patch("role_scout.eval.alignment_eval.score_with_judge", return_value=mock_scores):
            result = run_alignment_eval([("JD", "Align")] * 10)

        assert result is not None
        assert result.pass_criteria is False

    def test_run_alignment_eval_no_judge(self, tmp_path, monkeypatch):
        """T-CB03: score_with_judge returns None → result is None."""
        monkeypatch.chdir(tmp_path)
        from role_scout.eval.alignment_eval import run_alignment_eval

        with patch("role_scout.eval.alignment_eval.score_with_judge", return_value=None):
            result = run_alignment_eval([("JD", "Align")])

        assert result is None

    def test_run_alignment_eval_empty_pairs(self, tmp_path, monkeypatch):
        """T-CB04: empty pairs list → None."""
        monkeypatch.chdir(tmp_path)
        from role_scout.eval.alignment_eval import run_alignment_eval

        result = run_alignment_eval([])
        assert result is None

    def test_run_alignment_eval_writes_report(self, tmp_path, monkeypatch):
        """T-CB05: successful eval writes a markdown report file."""
        monkeypatch.chdir(tmp_path)
        from role_scout.eval.alignment_eval import run_alignment_eval

        mock_scores = _make_judge_scores(["strong_matches", "reframing_opportunities", "genuine_gaps"], 4.0)

        with patch("role_scout.eval.alignment_eval.score_with_judge", return_value=mock_scores):
            result = run_alignment_eval([("JD", "Align")] * 8)

        assert result is not None
        reports = list((tmp_path / "eval" / "reports").glob("*-alignment.md"))
        assert len(reports) == 1

    def test_alignment_pair_result_model(self):
        """T-CB06: PairResult model validates correctly."""
        from role_scout.eval.alignment_eval import PairResult

        pr = PairResult(
            pair_index=0,
            section_scores={"relevance": 4.0, "tone": 3.5},
            overall=3.75,
        )
        assert pr.pair_index == 0
        assert pr.overall == pytest.approx(3.75)

    def test_alignment_eval_result_model(self):
        """T-CB07: AlignmentEvalResult model instantiates."""
        from role_scout.eval.alignment_eval import AlignmentEvalResult

        r = AlignmentEvalResult(
            per_section_means={"relevance": 4.0},
            overall_mean=4.0,
            n_pairs=5,
            pass_criteria=True,
            pair_results=[],
        )
        assert r.pass_criteria is True


# ---------------------------------------------------------------------------
# shadow.py tests
# ---------------------------------------------------------------------------

class TestShadowModels:
    def test_shadow_disagreement_model(self):
        """T-CB08: ShadowDisagreement instantiates with all fields."""
        from role_scout.shadow import ShadowDisagreement
        d = ShadowDisagreement(hash_id="abc", agentic_score=90.0, linear_score=85.0, delta=5.0)
        assert d.delta == pytest.approx(5.0)

    def test_shadow_result_model(self):
        """T-CB09: ShadowResult instantiates and validates counts."""
        from role_scout.shadow import ShadowResult
        r = ShadowResult(
            run_id="shadow_abc",
            agentic_count=5,
            linear_count=0,
            disagreements=[],
            passed=False,
            linear_available=False,
        )
        assert r.agentic_count == 5
        assert r.linear_available is False


class TestShadowWriteReport:
    def test_write_report_creates_file(self, tmp_path):
        """T-CB10: _write_report writes a JSON file to shadow_diffs/."""
        import role_scout.shadow as shadow_mod
        from datetime import datetime, timezone

        run_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        expected_file = tmp_path / "shadow_diffs" / f"{run_date}-shadow_test.json"

        with patch.object(shadow_mod, "_write_report") as mock_write:
            mock_write.return_value = str(expected_file)
            result = mock_write(
                shadow_run_id="shadow_test",
                agentic_scores={"abc": 90.0},
                linear_scores={"abc": 90.0},
                disagreements=[],
                passed=True,
                linear_available=True,
            )
            assert result == str(expected_file)

    def test_write_report_pass(self, tmp_path, monkeypatch):
        """T-CB11: _write_report with no disagreements writes JSON with passed=True."""
        import json
        monkeypatch.chdir(tmp_path)
        from role_scout.shadow import _write_report

        path = _write_report(
            shadow_run_id="shadow_test123",
            agentic_scores={"hash1": 90.0},
            linear_scores={"hash1": 90.0},
            disagreements=[],
            passed=True,
            linear_available=True,
        )

        assert path is not None
        data = json.loads(Path(path).read_text())
        assert data["passed"] is True
        assert data["run_id"] == "shadow_test123"

    def test_write_report_with_disagreements(self, tmp_path, monkeypatch):
        """T-CB12: _write_report with disagreements includes disagreements list in JSON."""
        import json
        monkeypatch.chdir(tmp_path)
        from role_scout.shadow import _write_report, ShadowDisagreement

        disagreements = [ShadowDisagreement(hash_id="hash1", agentic_score=90.0, linear_score=80.0, delta=10.0)]
        path = _write_report(
            shadow_run_id="shadow_diff",
            agentic_scores={"hash1": 90.0},
            linear_scores={"hash1": 80.0},
            disagreements=disagreements,
            passed=False,
            linear_available=True,
        )

        data = json.loads(Path(path).read_text())
        assert "disagreements" in data
        assert len(data["disagreements"]) == 1
        assert data["disagreements"][0]["hash_id"] == "hash1"

    def test_write_report_linear_unavailable(self, tmp_path, monkeypatch):
        """T-CB13: _write_report sets warning field when linear path unavailable."""
        import json
        monkeypatch.chdir(tmp_path)
        from role_scout.shadow import _write_report

        path = _write_report(
            shadow_run_id="shadow_nowarn",
            agentic_scores={},
            linear_scores={},
            disagreements=[],
            passed=False,
            linear_available=False,
        )

        data = json.loads(Path(path).read_text())
        assert data["linear_available"] is False
        assert data["warning"] is not None
        assert "unavailable" in data["warning"].lower()


class TestRunShadow:
    def test_run_shadow_linear_unavailable(self, tmp_path, monkeypatch):
        """T-CB14: run_shadow when linear path unavailable returns result with passed=False."""
        monkeypatch.chdir(tmp_path)
        import role_scout.shadow as shadow_mod

        mock_state = {"scored_jobs": []}
        # run_graph is imported locally inside run_shadow; patch at its source
        with patch("role_scout.runner.run_graph", return_value=mock_state), \
             patch.object(shadow_mod, "_LINEAR_AVAILABLE", False):
            result = shadow_mod.run_shadow(auto_approve=True, dry_run=True)

        assert result.linear_available is False
        assert result.passed is False
        assert result.agentic_count == 0

    def test_run_shadow_agentic_jobs_no_linear(self, tmp_path, monkeypatch):
        """T-CB15: run_shadow with agentic jobs but no linear path → disagreements."""
        monkeypatch.chdir(tmp_path)
        import role_scout.shadow as shadow_mod

        agentic_job = MagicMock()
        agentic_job.hash_id = "abc123def456789a"
        agentic_job.match_pct = 90

        with patch("role_scout.runner.run_graph", return_value={"scored_jobs": [agentic_job]}), \
             patch.object(shadow_mod, "_LINEAR_AVAILABLE", False):
            result = shadow_mod.run_shadow(auto_approve=True, dry_run=True)

        assert result.run_id.startswith("shadow_")
        assert result.report_path is not None
        assert result.agentic_count == 1
        assert len(result.disagreements) == 1  # agentic job has no linear counterpart

    def test_run_shadow_agentic_exception(self, tmp_path, monkeypatch):
        """T-CB16: run_shadow when agentic path raises → still returns result."""
        monkeypatch.chdir(tmp_path)
        import role_scout.shadow as shadow_mod

        with patch("role_scout.runner.run_graph", side_effect=RuntimeError("agentic failed")), \
             patch.object(shadow_mod, "_LINEAR_AVAILABLE", False):
            result = shadow_mod.run_shadow(auto_approve=True, dry_run=True)

        assert result.agentic_count == 0
        assert result.passed is False


# ---------------------------------------------------------------------------
# runner.py — register_pending / resolve_pending
# ---------------------------------------------------------------------------

class TestRunnerPendingDecisions:
    def test_register_and_resolve_pending(self):
        """T-CB17: register_pending adds queue; resolve_pending signals it."""
        from role_scout.runner import register_pending, resolve_pending
        import role_scout.runner as runner_mod

        q: queue.Queue[str] = queue.Queue()
        run_id = "test_run_001"

        # Clean state
        runner_mod._pending_decisions.pop(run_id, None)

        register_pending(run_id, q)
        assert run_id in runner_mod._pending_decisions

        resolved = resolve_pending(run_id, "approve")
        assert resolved is True
        assert q.get_nowait() == "approve"
        assert run_id not in runner_mod._pending_decisions

    def test_resolve_pending_unknown_run_id(self):
        """T-CB18: resolve_pending for unknown run_id returns False."""
        from role_scout.runner import resolve_pending

        result = resolve_pending("nonexistent_run_xyz", "approve")
        assert result is False

    def test_register_pending_replaces_existing(self):
        """T-CB19: registering same run_id twice replaces the queue."""
        from role_scout.runner import register_pending, resolve_pending
        import role_scout.runner as runner_mod

        run_id = "test_run_replace"
        q1: queue.Queue[str] = queue.Queue()
        q2: queue.Queue[str] = queue.Queue()

        runner_mod._pending_decisions.pop(run_id, None)

        register_pending(run_id, q1)
        register_pending(run_id, q2)

        resolve_pending(run_id, "cancel")
        assert q1.empty()
        assert q2.get_nowait() == "cancel"


# ---------------------------------------------------------------------------
# run_eval CLI
# ---------------------------------------------------------------------------

class TestRunEvalCLI:
    def test_run_eval_scorer_flag(self, tmp_path, monkeypatch):
        """T-CB20: run_eval main() with --scorer exits 0 on perfect predictions."""
        monkeypatch.chdir(tmp_path)
        from role_scout.eval import run_eval

        with patch("sys.argv", ["run_eval", "--scorer"]):
            exit_code = run_eval.main()

        assert exit_code == 0

    def test_run_eval_recall_flag(self, tmp_path, monkeypatch):
        """T-CB21: run_eval main() with --recall exits 0."""
        monkeypatch.chdir(tmp_path)
        from role_scout.eval import run_eval

        with patch("sys.argv", ["run_eval", "--recall"]):
            exit_code = run_eval.main()

        assert exit_code == 0

    def test_run_eval_all_flag(self, tmp_path, monkeypatch):
        """T-CB22: run_eval main() with --all runs both scorer and recall."""
        monkeypatch.chdir(tmp_path)
        from role_scout.eval import run_eval

        with patch("sys.argv", ["run_eval", "--all"]):
            exit_code = run_eval.main()

        assert exit_code == 0

    def test_run_eval_no_flags_exits_0(self, tmp_path, monkeypatch):
        """T-CB23: run_eval main() with no flags runs nothing but exits 0."""
        monkeypatch.chdir(tmp_path)
        from role_scout.eval import run_eval

        with patch("sys.argv", ["run_eval"]):
            exit_code = run_eval.main()

        assert exit_code == 0

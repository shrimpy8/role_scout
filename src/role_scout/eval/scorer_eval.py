"""Scorer evaluation: Spearman correlation + agreement % against ground truth."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import structlog
import yaml
from pydantic import BaseModel
from scipy.stats import spearmanr

from role_scout.eval.ground_truth_schema import GroundTruthJob

log = structlog.get_logger()

_GROUND_TRUTH_PATH = Path(__file__).parent / "ground_truth.yaml"
_REPORTS_DIR = Path("eval/reports")


class PerJobDelta(BaseModel):
    hash_id: str
    human_score: int
    predicted_score: int
    delta: int


class ScorerEvalResult(BaseModel):
    spearman_r: float
    spearman_p: float
    agreement_pct: float  # % within 10 points (0–100 scale)
    n_jobs: int
    pass_criteria: bool  # spearman_r >= 0.80 AND agreement_pct >= 80.0
    per_job_deltas: list[PerJobDelta]


class ReflectionABResult(BaseModel):
    spearman_with_reflection: float
    spearman_without_reflection: float
    delta: float
    pass_criteria: bool  # delta >= 0.05


def run_reflection_ab_eval(
    gt_scores: list[int],
    with_reflection_preds: list[int],
    without_reflection_preds: list[int],
) -> ReflectionABResult:
    """Compute the Spearman delta between reflection-enabled and reflection-disabled predictions.

    This function only computes the delta — callers are responsible for running the
    scoring pipeline twice (once with reflection enabled, once disabled) and providing
    both prediction lists aligned to the same ground truth scores.

    Args:
        gt_scores: Ground truth human scores (aligned to both prediction lists).
        with_reflection_preds: Predicted scores from the reflection-enabled pipeline.
        without_reflection_preds: Predicted scores from the reflection-disabled pipeline.

    Returns:
        ReflectionABResult with Spearman r for each condition, delta, and pass/fail.
    """
    r_with, _ = spearmanr(gt_scores, with_reflection_preds)
    r_without, _ = spearmanr(gt_scores, without_reflection_preds)
    delta = float(r_with) - float(r_without)
    return ReflectionABResult(
        spearman_with_reflection=round(float(r_with), 4),
        spearman_without_reflection=round(float(r_without), 4),
        delta=round(delta, 4),
        pass_criteria=delta >= 0.05,
    )


def load_ground_truth() -> list[GroundTruthJob]:
    """Load and validate ground truth YAML."""
    with open(_GROUND_TRUTH_PATH) as f:
        data = yaml.safe_load(f)
    return [GroundTruthJob.model_validate(item) for item in data]


def run_scorer_eval(predicted: list[tuple[str, int]]) -> ScorerEvalResult:
    """
    Args:
        predicted: list of (hash_id, predicted_score) tuples
    Returns ScorerEvalResult with Spearman r, agreement %, pass/fail.
    """
    gt = load_ground_truth()
    gt_map = {job.hash_id: job.human_score for job in gt}

    human_scores = []
    pred_scores = []
    deltas = []

    for hash_id, pred in predicted:
        if hash_id not in gt_map:
            continue
        h = gt_map[hash_id]
        human_scores.append(h)
        pred_scores.append(pred)
        deltas.append(PerJobDelta(hash_id=hash_id, human_score=h, predicted_score=pred, delta=abs(h - pred)))

    if len(human_scores) < 2:
        raise ValueError(f"Need ≥2 matched jobs for Spearman; got {len(human_scores)}")

    r, p = spearmanr(human_scores, pred_scores)
    agreement = sum(1 for d in deltas if d.delta <= 10) / len(deltas) * 100

    result = ScorerEvalResult(
        spearman_r=float(r),
        spearman_p=float(p),
        agreement_pct=round(agreement, 1),
        n_jobs=len(deltas),
        pass_criteria=float(r) >= 0.80 and agreement >= 80.0,
        per_job_deltas=deltas,
    )
    _write_report(result)
    return result


def _write_report(result: ScorerEvalResult) -> None:
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    path = _REPORTS_DIR / f"{date}-scorer.md"
    lines = [
        f"# Scorer Eval — {date}",
        "",
        "| Metric | Value | Pass |",
        "|--------|-------|------|",
        f"| Spearman r | {result.spearman_r:.3f} | {'✓' if result.spearman_r >= 0.80 else '✗'} |",
        f"| Agreement % | {result.agreement_pct:.1f}% | {'✓' if result.agreement_pct >= 80.0 else '✗'} |",
        f"| n_jobs | {result.n_jobs} | — |",
        "",
        "## Per-Job Deltas",
    ]
    for d in result.per_job_deltas:
        lines.append(f"- {d.hash_id}: human={d.human_score}, pred={d.predicted_score}, delta={d.delta}")
    path.write_text("\n".join(lines))
    log.info("scorer_eval.report_written", path=str(path))

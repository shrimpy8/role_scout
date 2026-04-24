"""Discovery recall evaluation: what fraction of gold-set jobs the pipeline found."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog
from pydantic import BaseModel

log = structlog.get_logger()
_REPORTS_DIR = Path("eval/reports")


class RecallEvalResult(BaseModel):
    recall: float
    gold_count: int
    found_count: int
    missing_hash_ids: list[str]
    pass_criteria: bool  # recall >= 0.90


def run_recall_eval(gold_set: list[str], pipeline_output: list[str]) -> RecallEvalResult:
    """
    Args:
        gold_set: hash_ids of hand-bookmarked relevant jobs
        pipeline_output: hash_ids from pipeline's qualified_jobs
    Returns RecallEvalResult. Returns recall=0.0 on empty gold_set (no ZeroDivisionError).
    """
    if not gold_set:
        result = RecallEvalResult(recall=0.0, gold_count=0, found_count=0, missing_hash_ids=[], pass_criteria=False)
        _write_report(result)
        return result

    gold = set(gold_set)
    found = gold & set(pipeline_output)
    missing = sorted(gold - found)
    recall = len(found) / len(gold)

    result = RecallEvalResult(
        recall=round(recall, 4),
        gold_count=len(gold),
        found_count=len(found),
        missing_hash_ids=missing,
        pass_criteria=recall >= 0.90,
    )
    _write_report(result)
    return result


def _write_report(result: RecallEvalResult) -> None:
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = _REPORTS_DIR / f"{date}-recall.md"
    lines = [
        f"# Discovery Recall Eval — {date}",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Recall | {result.recall:.1%} |",
        f"| Gold count | {result.gold_count} |",
        f"| Found | {result.found_count} |",
        f"| Pass (≥90%) | {'✓' if result.pass_criteria else '✗'} |",
    ]
    if result.missing_hash_ids:
        lines.extend(["", "## Missing", *[f"- {h}" for h in result.missing_hash_ids]])
    path.write_text("\n".join(lines))

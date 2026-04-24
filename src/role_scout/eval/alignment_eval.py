"""Alignment evaluation: cross-model LLM judge rates resume alignment quality."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import structlog
from pydantic import BaseModel

from role_scout.eval.judge import score_with_judge

log = structlog.get_logger()
_REPORTS_DIR = Path("eval/reports")

_ALIGNMENT_SECTIONS = ["strong_matches", "reframing_opportunities", "genuine_gaps"]
_ALIGNMENT_RUBRICS = {
    "strong_matches": "How well does the alignment identify genuine strengths matching the job requirements? 1=no real matches identified, 5=all key matches correctly surfaced.",
    "reframing_opportunities": "Are reframing opportunities identified (experiences that apply but need reframing)? 1=none identified, 5=all relevant reframings surfaced and actionable.",
    "genuine_gaps": "Are genuine skill/experience gaps accurately identified (not fabricated)? 1=fabricated gaps or missed real ones, 5=accurate honest gap assessment.",
}


class PairResult(BaseModel):
    pair_index: int
    section_scores: dict[str, float]
    overall: float


class AlignmentEvalResult(BaseModel):
    per_section_means: dict[str, float]
    overall_mean: float
    n_pairs: int
    pass_criteria: bool  # mean >= 4.0 on >= 8/10 pairs
    pair_results: list[PairResult]


def run_alignment_eval(pairs: list[tuple[str, str]]) -> AlignmentEvalResult | None:
    """Run alignment eval over a list of (job_description, alignment_output) pairs.

    Args:
        pairs: list of (job_description, alignment_output) tuples

    Returns:
        AlignmentEvalResult or None if no judge provider available.
    """
    pair_results = []

    for i, (jd, alignment) in enumerate(pairs):
        text = f"JD: {jd}\n\nAlignment: {alignment}"
        scores = score_with_judge(text, _ALIGNMENT_SECTIONS, _ALIGNMENT_RUBRICS)
        if scores is None:
            log.warning("alignment_eval.skipped", reason="no judge provider")
            return None
        section_scores = {s.section: s.score for s in scores}
        overall = sum(section_scores.values()) / len(section_scores)
        pair_results.append(PairResult(pair_index=i, section_scores=section_scores, overall=overall))

    if not pair_results:
        return None

    section_means = {}
    for section in _ALIGNMENT_SECTIONS:
        section_means[section] = sum(r.section_scores.get(section, 0) for r in pair_results) / len(pair_results)
    overall_mean = sum(r.overall for r in pair_results) / len(pair_results)
    pairs_passing = sum(1 for r in pair_results if r.overall >= 4.0)

    result = AlignmentEvalResult(
        per_section_means={k: round(v, 2) for k, v in section_means.items()},
        overall_mean=round(overall_mean, 2),
        n_pairs=len(pair_results),
        pass_criteria=overall_mean >= 4.0 and pairs_passing >= min(8, len(pair_results)),
        pair_results=pair_results,
    )
    _write_report(result)
    return result


def _write_report(result: AlignmentEvalResult) -> None:
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    path = _REPORTS_DIR / f"{date}-alignment.md"
    lines = [
        f"# Alignment Eval — {date}",
        "",
        "| Section | Mean Score |",
        "|---------|-----------|",
    ]
    for section, mean in result.per_section_means.items():
        lines.append(f"| {section} | {mean:.2f} |")
    lines += [
        "",
        f"**Overall mean**: {result.overall_mean:.2f} | **Pass**: {'✓' if result.pass_criteria else '✗'}",
    ]
    path.write_text("\n".join(lines))

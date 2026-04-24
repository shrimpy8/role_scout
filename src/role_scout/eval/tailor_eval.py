"""Tailor evaluation: LLM judge rates tailored resume quality; flags disagreements."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import structlog
from pydantic import BaseModel, model_validator

from role_scout.eval.judge import score_with_judge

log = structlog.get_logger()
_REPORTS_DIR = Path("eval/reports")

_TAILOR_SECTIONS = ["relevance", "no_fabrication", "keyword_fit", "reframe_quality"]
_TAILOR_RUBRICS = {
    "relevance": "Does the tailored summary/bullets directly address the JD requirements? 1=generic, 5=highly targeted.",
    "no_fabrication": "Do all claims reflect real experience (no invented achievements)? 1=fabrications present, 5=100% grounded.",
    "keyword_fit": "How well are job-specific keywords incorporated naturally? 1=keywords absent or forced, 5=all key terms incorporated naturally.",
    "reframe_quality": "How well are experiences reframed for this specific role without fabrication? 1=generic/no reframing, 5=excellent role-specific reframing of genuine experiences.",
}


class DisagreementFlag(BaseModel):
    pair_index: int
    llm_mean: float
    human_score_normalized: float  # human 0-100 → normalized 1-5
    delta: float


class TailorEvalResult(BaseModel):
    per_dim_means: dict[str, float]
    overall_mean: float
    n_pairs: int
    disagreement_flags: list[DisagreementFlag]
    pass_criteria: bool
    human_spot_check_mean: float | None = None

    @model_validator(mode="after")
    def _apply_human_spot_check(self) -> TailorEvalResult:
        """If human_spot_check_mean is provided, pass requires both LLM mean and human mean >= 4.0."""
        if self.human_spot_check_mean is not None:
            self.pass_criteria = self.overall_mean >= 4.0 and self.human_spot_check_mean >= 4.0
        return self


def run_tailor_eval(pairs: list[tuple[dict, int]]) -> TailorEvalResult | None:
    """Run tailor eval over (tailor_output_dict, human_score_0_to_100) pairs.

    Args:
        pairs: list of (tailor_output_dict, human_score_0_to_100) tuples

    Returns:
        TailorEvalResult or None if no judge provider.
    """
    dim_scores: dict[str, list[float]] = {s: [] for s in _TAILOR_SECTIONS}
    disagreements = []
    n = 0

    for i, (tailor_output, human_score) in enumerate(pairs):
        text = (
            f"Summary: {tailor_output.get('tailored_summary', '')}\n"
            f"Bullets: {'; '.join(tailor_output.get('tailored_bullets', []))}\n"
            f"Keywords: {', '.join(tailor_output.get('keywords_incorporated', []))}"
        )
        scores = score_with_judge(text, _TAILOR_SECTIONS, _TAILOR_RUBRICS)
        if scores is None:
            log.warning("tailor_eval.skipped", reason="no judge provider")
            return None

        for s in scores:
            dim_scores[s.section].append(s.score)

        llm_mean = sum(s.score for s in scores) / len(scores)
        human_normalized = 1.0 + (human_score / 100.0) * 4.0  # map 0-100 → 1-5
        delta = abs(llm_mean - human_normalized)

        if delta > 1.0:
            disagreements.append(
                DisagreementFlag(
                    pair_index=i,
                    llm_mean=round(llm_mean, 2),
                    human_score_normalized=round(human_normalized, 2),
                    delta=round(delta, 2),
                )
            )
        n += 1

    if n == 0:
        return None

    per_dim_means = {dim: round(sum(v) / len(v), 2) if v else 0.0 for dim, v in dim_scores.items()}
    overall_mean = sum(per_dim_means.values()) / len(per_dim_means)

    result = TailorEvalResult(
        per_dim_means=per_dim_means,
        overall_mean=round(overall_mean, 2),
        n_pairs=n,
        disagreement_flags=disagreements,
        pass_criteria=overall_mean >= 4.0,
    )
    _write_report(result)
    return result


def _write_report(result: TailorEvalResult) -> None:
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    path = _REPORTS_DIR / f"{date}-tailor.md"
    lines = [
        f"# Tailor Eval — {date}",
        "",
        "| Dimension | Mean |",
        "|-----------|------|",
    ]
    for dim, mean in result.per_dim_means.items():
        lines.append(f"| {dim} | {mean:.2f} |")
    lines += ["", f"**Overall**: {result.overall_mean:.2f} | **Pass**: {'✓' if result.pass_criteria else '✗'}"]
    if result.disagreement_flags:
        lines += ["", "## Disagreements (|llm - human| > 1.0)"]
        for d in result.disagreement_flags:
            lines.append(f"- Pair {d.pair_index}: LLM={d.llm_mean:.2f}, Human={d.human_score_normalized:.2f}, Δ={d.delta:.2f}")
    path.write_text("\n".join(lines))

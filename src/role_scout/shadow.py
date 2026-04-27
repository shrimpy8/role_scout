"""Shadow mode — runs both linear (Phase 1) and agentic (Phase 2) orchestrators on
the same input, diffs scored_jobs, and writes a shadow report.

Activated when RUN_MODE=shadow. Both paths share the same discovery output
(jobs fetched once), diverge at scoring. Linear path calls Phase 1 runner;
agentic path calls the LangGraph graph.

Architecture
------------
The shadow harness is a thin wrapper that:

1. Executes the agentic path (``role_scout.runner.run_graph``) to obtain
   ``scored_jobs`` from the LangGraph pipeline.
2. Attempts to execute the linear path (``jobsearch.pipeline.run_linear_pipeline``
   from Phase 1, auto_jobsearch/). If Phase 1 is not importable it logs a WARNING
   and records ``linear_count=0`` so the report is still written.
3. Compares ``scored_jobs`` between the two paths on ``(hash_id, match_pct)``.
   Disagreements are recorded when ``abs(agentic_score - linear_score) > 2``
   (per TECH-DESIGN §8.2), or when a job is present in only one path.
4. Writes a JSON report to ``shadow_diffs/YYYY-MM-DD-<run_id>.json``.
5. Returns a ``ShadowResult`` with counts and disagreements for programmatic use.

Promotion criteria (when to flip RUN_MODE default to "agentic")
---------------------------------------------------------------
- Zero disagreements in ``scored_jobs`` across 6 real fetches (3 per week over
  2 consecutive weeks).
- All 10 eval gates pass (see DEVELOPMENT_TODOS.md §Shadow Period Protocol).
- Coverage ≥ 80% (``pytest --cov``).

If any unexplained diff appears, stop the shadow period and investigate before
continuing.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Optional Phase 1 import
# ---------------------------------------------------------------------------

_LINEAR_AVAILABLE = False  # Phase 1 linear pipeline removed in Option B migration

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class ShadowDisagreement(BaseModel):
    """A single scoring disagreement between the two paths."""

    hash_id: str
    agentic_score: float | None = None
    linear_score: float | None = None
    delta: float | None = None


class ShadowResult(BaseModel):
    """Structured output from a shadow run."""

    run_id: str
    agentic_count: int = Field(ge=0)
    linear_count: int = Field(ge=0)
    disagreements: list[ShadowDisagreement] = Field(default_factory=list)
    passed: bool
    linear_available: bool
    report_path: str | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_shadow(
    *,
    auto_approve: bool = False,
    dry_run: bool = False,
    force_partial: bool = False,
    trigger_type: str = "manual",
    db_path: str | None = None,
) -> ShadowResult:
    """Execute shadow mode: run both pipelines, diff scored_jobs, write report.

    Parameters mirror :func:`role_scout.runner.run_graph` so the caller can
    substitute ``run_shadow`` transparently when ``RUN_MODE=shadow``.

    Returns a :class:`ShadowResult` Pydantic model. Always returns even if the
    linear path is unavailable (``linear_available=False``, ``passed=False``).
    """
    shadow_run_id = f"shadow_{uuid.uuid4().hex[:16]}"
    run_log = log.bind(shadow_run_id=shadow_run_id)
    run_log.info("shadow_run_start")

    # ------------------------------------------------------------------
    # 1. Agentic path
    # ------------------------------------------------------------------
    agentic_state: dict[str, Any] = {}
    try:
        from role_scout.runner import run_graph

        agentic_state = run_graph(
            auto_approve=auto_approve,
            dry_run=dry_run,
            force_partial=force_partial,
            trigger_type=trigger_type,
            db_path=db_path,
        )
        run_log.info("shadow_agentic_complete")
    except Exception:
        run_log.exception("shadow_agentic_failed")

    agentic_jobs = agentic_state.get("scored_jobs", [])
    agentic_scores: dict[str, float] = {
        str(getattr(j, "hash_id", "")): float(getattr(j, "match_pct", 0))
        for j in agentic_jobs
        if getattr(j, "hash_id", None)
    }

    # ------------------------------------------------------------------
    # 2. Linear path (Phase 1)
    # ------------------------------------------------------------------
    linear_scores: dict[str, float] = {}

    if not _LINEAR_AVAILABLE:
        run_log.warning(
            "shadow_linear_unavailable",
            reason="jobsearch.pipeline not importable; reporting agentic results only",
        )
    else:
        try:
            linear_result = run_linear_pipeline(
                dry_run=dry_run,
                db_path=db_path,
            )
            linear_jobs = linear_result.get("scored_jobs", []) if isinstance(linear_result, dict) else []
            linear_scores = {
                str(getattr(j, "hash_id", "")): float(getattr(j, "match_pct", 0))
                for j in linear_jobs
                if getattr(j, "hash_id", None)
            }
            run_log.info("shadow_linear_complete", linear_count=len(linear_scores))
        except Exception:
            run_log.exception("shadow_linear_failed")

    # ------------------------------------------------------------------
    # 3. Diff
    # ------------------------------------------------------------------
    all_hash_ids = set(agentic_scores) | set(linear_scores)
    disagreements: list[ShadowDisagreement] = []

    for hash_id in sorted(all_hash_ids):
        a_score = agentic_scores.get(hash_id)
        l_score = linear_scores.get(hash_id)

        # Flag when a job is present in only one path (delta is effectively
        # infinite) or when the numeric delta exceeds 2 pts (TECH-DESIGN §8.2).
        delta: float | None = None
        if a_score is not None and l_score is not None:
            delta = round(a_score - l_score, 2)
            if abs(delta) <= 2:
                continue  # within tolerance — not a disagreement
        disagreements.append(
            ShadowDisagreement(
                hash_id=hash_id,
                agentic_score=a_score,
                linear_score=l_score,
                delta=delta,
            )
        )

    passed = _LINEAR_AVAILABLE and len(disagreements) == 0

    run_log.info(
        "shadow_diff_complete",
        agentic_count=len(agentic_scores),
        linear_count=len(linear_scores),
        disagreement_count=len(disagreements),
        passed=passed,
    )

    # ------------------------------------------------------------------
    # 4. Write report
    # ------------------------------------------------------------------
    report_path = _write_report(
        shadow_run_id=shadow_run_id,
        agentic_scores=agentic_scores,
        linear_scores=linear_scores,
        disagreements=disagreements,
        passed=passed,
        linear_available=_LINEAR_AVAILABLE,
    )

    return ShadowResult(
        run_id=shadow_run_id,
        agentic_count=len(agentic_scores),
        linear_count=len(linear_scores),
        disagreements=disagreements,
        passed=passed,
        linear_available=_LINEAR_AVAILABLE,
        report_path=report_path,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _write_report(
    *,
    shadow_run_id: str,
    agentic_scores: dict[str, float],
    linear_scores: dict[str, float],
    disagreements: list[ShadowDisagreement],
    passed: bool,
    linear_available: bool,
) -> str | None:
    """Write a JSON shadow report to shadow_diffs/YYYY-MM-DD-<run_id>.json.

    Returns the path written, or None if the directory cannot be created.
    """
    import json

    diffs_dir = Path("shadow_diffs")
    try:
        diffs_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.exception("shadow_report_dir_failed", diffs_dir=str(diffs_dir))
        return None

    run_date = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    report_file = diffs_dir / f"{run_date}-{shadow_run_id}.json"

    report = {
        "run_id": shadow_run_id,
        "date": run_date,
        "passed": passed,
        "linear_available": linear_available,
        "agentic_count": len(agentic_scores),
        "linear_count": len(linear_scores),
        "disagreements": [
            {
                "hash_id": d.hash_id,
                "agentic_score": d.agentic_score,
                "linear_score": d.linear_score,
                "delta": d.delta,
            }
            for d in disagreements
        ],
        "warning": "Linear path unavailable — shadow comparison skipped" if not linear_available else None,
    }

    try:
        report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
        log.info("shadow_report_written", path=str(report_file))
        return str(report_file)
    except OSError:
        log.exception("shadow_report_write_failed", path=str(report_file))
        return None

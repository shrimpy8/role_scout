"""scoring_node — wraps Phase 1 score_jobs_batch with cost kill-switch and state trimming."""
from __future__ import annotations

from typing import Any

import structlog
from jobsearch.models import CandidateProfile, NormalizedJob, ScoredJob
from jobsearch.pipeline.scorer import score_jobs_batch

from role_scout.config import Settings
from role_scout.cost import CostKillSwitchError, check_cost_kill_switch, compute_cost
from role_scout.models.state import JobSearchState, assert_state_size

log = structlog.get_logger()

_BATCH_TOKEN_ESTIMATE = 8_000
_TOKENS_PER_JOB_ESTIMATE = 500


def scoring_node(state: JobSearchState) -> dict[str, Any]:
    """Score enriched jobs via Phase 1 score_jobs_batch; trim enriched_jobs from state.

    Kill-switch: raises CostKillSwitchError if accumulated cost has already exceeded
    MAX_COST_USD before scoring begins — prevents runaway spend.

    State trimming (per TECH-DESIGN §3.2):
    - enriched_jobs → [] after this node
    Token accumulation: scoring_tokens_in / scoring_tokens_out updated in state.
    """
    settings = Settings()
    run_id: str = state.get("run_id", "run_unknown")
    bound_log = log.bind(correlation_id=run_id, run_id=run_id, node_name="scoring")

    enriched_jobs: list[NormalizedJob] = list(state.get("enriched_jobs", []))
    profile: CandidateProfile = state["candidate_profile"]
    qualify_threshold: int = int(state.get("qualify_threshold", settings.SCORE_THRESHOLD))
    errors: list[str] = list(state.get("errors", []))

    accumulated_input: int = int(state.get("scoring_tokens_in", 0))
    accumulated_output: int = int(state.get("scoring_tokens_out", 0))
    current_cost: float = float(state.get("total_cost_usd", 0.0))

    bound_log.info("scoring_started", job_count=len(enriched_jobs), threshold=qualify_threshold)

    if not enriched_jobs:
        bound_log.info("scoring_skipped", reason="no_enriched_jobs")
        return {
            "scored_jobs": [],
            "enriched_jobs": [],
            "scoring_tokens_in": accumulated_input,
            "scoring_tokens_out": accumulated_output,
        }

    # Pre-scoring kill-switch check
    try:
        check_cost_kill_switch(current_cost, settings.MAX_COST_USD)
    except CostKillSwitchError:
        bound_log.error(
            "scoring_aborted_cost_kill_switch",
            accumulated_cost=current_cost,
            max_cost=settings.MAX_COST_USD,
        )
        errors.append(f"cost_kill_switch: ${current_cost:.4f} >= ${settings.MAX_COST_USD:.2f}")
        return {
            "scored_jobs": [],
            "enriched_jobs": [],
            "cancel_reason": "cost_kill_switch",
            "scoring_tokens_in": accumulated_input,
            "scoring_tokens_out": accumulated_output,
            "total_cost_usd": current_cost,
            "errors": errors,
        }

    try:
        scored_jobs: list[ScoredJob] = score_jobs_batch(
            jobs=enriched_jobs,
            candidate_profile=profile,
            api_key=settings.ANTHROPIC_API_KEY,
            qualify_threshold=qualify_threshold,
            run_id=run_id,
        )
    except Exception as exc:
        bound_log.exception("scoring_failed")
        errors.append(f"scoring_failed: {exc}")
        scored_jobs = []

    # Estimate token usage from Phase 1 scorer (Phase 1 doesn't expose usage counters;
    # use a conservative per-job estimate until Phase 2 scorer replaces this).
    # ~8k input + ~500 output per 10-job batch.
    n_batches = max(1, (len(enriched_jobs) + 9) // 10)
    est_input = n_batches * _BATCH_TOKEN_ESTIMATE
    est_output = n_batches * _TOKENS_PER_JOB_ESTIMATE

    new_input_total = accumulated_input + est_input
    new_output_total = accumulated_output + est_output
    new_cost = compute_cost(new_input_total, new_output_total)

    bound_log.info(
        "scoring_complete",
        scored=len(scored_jobs),
        estimated_input_tokens=est_input,
        estimated_cost_usd=round(new_cost, 4),
    )

    state_update: dict[str, Any] = {
        "scored_jobs": scored_jobs,
        "enriched_jobs": [],
        "scoring_tokens_in": new_input_total,
        "scoring_tokens_out": new_output_total,
        "total_cost_usd": new_cost,
        "errors": errors,
    }

    assert_state_size({**state, **state_update})
    return state_update

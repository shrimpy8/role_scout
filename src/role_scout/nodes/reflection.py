"""reflection_node — second-pass Claude review for borderline-scored jobs (70–89%)."""
from __future__ import annotations

import json
from pathlib import Path
from string import Template
from typing import Any

import structlog
from role_scout.compat.models import ScoredJob

from role_scout.claude_client import call_claude
from role_scout.config import Settings
from role_scout.cost import compute_cost_from_settings
from role_scout.models.state import JobSearchState, assert_state_size

log = structlog.get_logger()

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "scoring_reflection_system.md"


def _load_reflection_prompt() -> str:
    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(f"Reflection prompt not found at {_PROMPT_PATH}")
    return _PROMPT_PATH.read_text()


def _build_reflection_prompt(
    prompt_template: str,
    job: ScoredJob,
    profile_json: str,
) -> str:
    """Interpolate job + profile into the reflection system prompt."""
    job_dict = {
        "hash_id": job.hash_id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "work_model": getattr(job, "work_model", None),
        "comp_range": getattr(job, "comp_range", None),
        "salary_visible": getattr(job, "salary_visible", False),
        "description": (job.description or "")[:1000],
    }
    subscores = {
        "role_fit": getattr(job, "role_fit", None),
        "domain_fit": getattr(job, "domain_fit", None),
        "comp_score": getattr(job, "comp_score", None),
        "level_fit": getattr(job, "level_fit", None),
        "location_fit": getattr(job, "location_fit", None),
    }
    return Template(prompt_template).safe_substitute(
        original_score_json=json.dumps(job.match_pct),
        subscores_json=json.dumps(subscores, default=str),
        job_json=json.dumps(job_dict, default=str),
        candidate_profile_json=profile_json,
    )


def _apply_reflection_result(job: ScoredJob, raw_response: str) -> tuple[ScoredJob, bool]:
    """Parse Claude's reflection JSON and return (updated_job, was_changed).

    On any parse failure: returns original job unchanged.
    """
    try:
        text = raw_response.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("No JSON object found")
        data = json.loads(text[start : end + 1])

        changed: bool = bool(data.get("changed", False))
        if not changed:
            return job, False

        revised_score: int = int(data["revised_score"])
        revised_subscores: dict[str, Any] = data.get("revised_subscores", {})

        updates: dict[str, Any] = {"match_pct": revised_score}
        for field in ("role_fit", "domain_fit", "comp_score", "level_fit", "location_fit"):
            if field in revised_subscores:
                updates[field] = int(revised_subscores[field])

        return job.model_copy(update=updates), True

    except Exception as exc:
        log.warning("reflection_parse_failed", error=str(exc), response_excerpt=raw_response[:200])
        return job, False


def reflection_node(state: JobSearchState) -> dict[str, Any]:
    """Re-score borderline jobs (REFLECTION_BAND_LOW–REFLECTION_BAND_HIGH) via Claude.

    Jobs outside the reflection band are passed through unchanged. On any Claude
    error or JSON parse failure, the original score is preserved and
    reflection_applied is set to False for that job.
    """
    settings = Settings()
    run_id: str = state.get("run_id", "run_unknown")
    bound_log = log.bind(correlation_id=run_id, run_id=run_id, node_name="reflection")

    if not settings.REFLECTION_ENABLED:
        bound_log.info("reflection_disabled")
        return {}

    scored_jobs: list[ScoredJob] = list(state.get("scored_jobs", []))
    profile = state.get("candidate_profile")
    errors: list[str] = list(state.get("errors", []))

    reflection_in: int = int(state.get("reflection_tokens_in", 0))
    reflection_out: int = int(state.get("reflection_tokens_out", 0))
    current_cost: float = float(state.get("total_cost_usd", 0.0))

    band_low = settings.REFLECTION_BAND_LOW
    band_high = settings.REFLECTION_BAND_HIGH

    borderline = [j for j in scored_jobs if band_low <= j.match_pct <= band_high]
    bound_log.info(
        "reflection_started",
        total_scored=len(scored_jobs),
        borderline=len(borderline),
        band=f"{band_low}–{band_high}",
    )

    if not borderline:
        bound_log.info("reflection_no_borderline_jobs")
        return {
            "reflection_tokens_in": reflection_in,
            "reflection_tokens_out": reflection_out,
            "reflection_applied_count": 0,
        }

    try:
        prompt_template = _load_reflection_prompt()
    except FileNotFoundError as exc:
        bound_log.error("reflection_prompt_missing", error=str(exc))
        errors.append(str(exc))
        return {"errors": errors}

    profile_json = json.dumps(
        profile.model_dump() if hasattr(profile, "model_dump") else dict(profile or {})
    )

    updated_jobs: list[ScoredJob] = list(scored_jobs)  # copy for mutation
    applied_count = 0

    for job in borderline:
        system_prompt = _build_reflection_prompt(prompt_template, job, profile_json)
        try:
            text, in_tok, out_tok = call_claude(
                system=system_prompt,
                user="Review this job score for consistency.",
                api_key=settings.ANTHROPIC_API_KEY,
                model=settings.CLAUDE_MODEL,
                accumulated_cost=current_cost,
                max_cost=settings.MAX_COST_USD,
                input_cost_per_mtok=settings.CLAUDE_INPUT_COST_PER_MTOK,
                output_cost_per_mtok=settings.CLAUDE_OUTPUT_COST_PER_MTOK,
            )
        except Exception as exc:
            bound_log.warning(
                "reflection_claude_call_failed",
                hash_id=job.hash_id,
                error=str(exc),
            )
            errors.append(f"reflection_failed({job.hash_id}): {exc}")
            continue

        call_cost = compute_cost_from_settings(in_tok, out_tok, settings)
        reflection_in += in_tok
        reflection_out += out_tok
        current_cost += call_cost

        revised_job, was_changed = _apply_reflection_result(job, text)

        if was_changed:
            idx = next(
                (i for i, j in enumerate(updated_jobs) if j.hash_id == job.hash_id), None
            )
            if idx is not None:
                updated_jobs[idx] = revised_job
            applied_count += 1
            bound_log.info(
                "reflection_applied",
                hash_id=job.hash_id,
                original_score=job.match_pct,
                revised_score=revised_job.match_pct,
            )
        else:
            bound_log.debug("reflection_unchanged", hash_id=job.hash_id)

    reflection_cost = compute_cost_from_settings(reflection_in, reflection_out, settings)
    bound_log.info(
        "reflection_complete",
        applied=applied_count,
        reflection_cost_usd=round(reflection_cost, 4),
    )

    state_update: dict[str, Any] = {
        "scored_jobs": updated_jobs,
        "reflection_tokens_in": reflection_in,
        "reflection_tokens_out": reflection_out,
        "reflection_applied_count": applied_count,
        "total_cost_usd": current_cost,
        "errors": errors,
    }

    assert_state_size({**state, **state_update})
    return state_update

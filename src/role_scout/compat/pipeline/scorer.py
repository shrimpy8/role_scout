"""score_jobs_batch(): batch-score NormalizedJobs via Claude API; return qualified ScoredJobs."""

import json
import math
import re
from pathlib import Path
from string import Template
from typing import Any

import anthropic
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from role_scout.compat.logging import get_logger
from role_scout.compat.models import CandidateProfile, NormalizedJob, ScoredJob, ScoreResult

logger = get_logger(__name__)

# Path relative to this file: compat/pipeline/scorer.py → role_scout/prompts/scoring_system.md
_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "scoring_system.md"
_MAX_TOKENS = 4096
_DESCRIPTION_MAX_CHARS = 2000
_NON_PROFILE_TOKENS = {"jobs_json", "n", "comp_min_k_minus_1"}


def _load_prompt_template() -> str:
    """Load scoring prompt template; raise FileNotFoundError if missing."""
    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(f"Scoring prompt not found at {_PROMPT_PATH}")
    return _PROMPT_PATH.read_text()


def _validate_prompt_template(template: str, profile_keys: set[str]) -> None:
    """Assert all $placeholder tokens in template resolve against profile keys."""
    tokens = set(re.findall(r"\$([a-zA-Z_]\w*)", template))
    missing = tokens - profile_keys - _NON_PROFILE_TOKENS
    if missing:
        raise ValueError(f"Scoring prompt has unresolvable placeholders: {missing}")


def _build_system_prompt(
    template: str,
    profile: CandidateProfile | dict[str, Any],
    n: int,
    jobs_json: str = "",
) -> str:
    """Interpolate candidate profile fields into the prompt template via string.Template."""
    p: dict[str, Any] = profile.model_dump() if isinstance(profile, CandidateProfile) else profile
    comp_min_k = p.get("comp_min_k", 175)
    return Template(template).safe_substitute(
        name=p.get("name", "Candidate"),
        target_roles=", ".join(p.get("target_roles", [])),
        seniority_level=p.get("seniority_level", "Senior"),
        preferred_domains=", ".join(p.get("preferred_domains", [])),
        location=p.get("location", "San Francisco Bay Area"),
        remote_ok=str(p.get("remote_ok", True)),
        target_stages=", ".join(p.get("target_stages", [])),
        comp_min_k=comp_min_k,
        comp_min_k_minus_1=comp_min_k - 1,
        skills=", ".join(p.get("skills", [])),
        must_have_keywords=", ".join(p.get("must_have_keywords", [])),
        anti_keywords=", ".join(p.get("anti_keywords", [])),
        n=n,
        jobs_json=jobs_json,
    )


def _job_to_scoring_dict(job: NormalizedJob) -> dict:
    """Produce the condensed job representation sent to Claude."""
    return {
        "hash_id": job.hash_id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "work_model": job.work_model,
        "company_stage": job.company_stage,
        "comp_range": job.comp_range,
        "salary_visible": job.salary_visible,
        "description": (job.description or "")[:_DESCRIPTION_MAX_CHARS],
    }


@retry(
    retry=retry_if_exception_type(anthropic.APIStatusError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    reraise=True,
)
def _call_claude(client: anthropic.Anthropic, system: str, user: str, model: str = "claude-sonnet-4-6") -> str:
    """Call Claude API; tenacity retries on rate-limit / server errors."""
    response = client.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text


def _parse_batch(raw_text: str, batch: list[NormalizedJob]) -> list[ScoreResult]:
    """Parse Claude's JSON array response into ScoreResult objects."""
    text = raw_text.strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        logger.error("score_parse_no_array", response_excerpt=text[:200])
        return []
    text = text[start : end + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.error(
            "score_parse_error",
            response_excerpt=raw_text[:200],
            batch_hashes=[j.hash_id for j in batch],
        )
        return []

    if not isinstance(data, list):
        logger.error("score_parse_not_array", response_excerpt=raw_text[:200])
        return []

    results: list[ScoreResult] = []
    hash_set = {j.hash_id for j in batch}

    for item in data:
        if not isinstance(item, dict):
            continue
        hash_id = item.get("hash_id", "")
        if hash_id not in hash_set:
            logger.warning("score_unknown_hash_id", hash_id=hash_id)
            continue
        try:
            result = ScoreResult(**item)
            matching_job = next((j for j in batch if j.hash_id == hash_id), None)
            if matching_job and not matching_job.salary_visible and result.comp_score != 5:
                logger.warning(
                    "score_comp_score_corrected",
                    hash_id=hash_id,
                    original=result.comp_score,
                )
                result = result.model_copy(update={"comp_score": 5})
            results.append(result)
        except ValidationError:
            logger.exception("score_validation_error", hash_id=hash_id)

    return results


def score_jobs_batch(
    jobs: list[NormalizedJob],
    candidate_profile: CandidateProfile | dict[str, Any],
    api_key: str,
    batch_size: int = 10,
    qualify_threshold: int | None = None,
    run_id: str | None = None,
    model: str = "claude-sonnet-4-6",
) -> list[ScoredJob]:
    """Score jobs in batches via Claude; return ScoredJobs >= qualify_threshold."""
    if qualify_threshold is None:
        qualify_threshold = 85

    if not jobs:
        return []

    template = _load_prompt_template()
    p_keys = set(
        (candidate_profile.model_dump() if isinstance(candidate_profile, CandidateProfile) else candidate_profile).keys()
    )
    _validate_prompt_template(template, p_keys)
    client = anthropic.Anthropic(api_key=api_key)
    qualified: list[ScoredJob] = []
    n_batches = math.ceil(len(jobs) / batch_size)

    for batch_num in range(n_batches):
        batch = jobs[batch_num * batch_size : (batch_num + 1) * batch_size]
        actual_n = len(batch)

        jobs_payload = [_job_to_scoring_dict(j) for j in batch]
        jobs_json = json.dumps(jobs_payload, ensure_ascii=False)
        system = _build_system_prompt(template, candidate_profile, actual_n, jobs_json)

        logger.info(
            "score_batch_start",
            batch_num=batch_num + 1,
            n_batches=n_batches,
            batch_size=actual_n,
            run_id=run_id,
        )

        try:
            raw_text = _call_claude(client, system, "Score the jobs listed in the system prompt.", model=model)
        except Exception:
            logger.exception(
                "score_batch_failed",
                batch_num=batch_num + 1,
                jobs_lost=actual_n,
            )
            continue

        score_results = _parse_batch(raw_text, batch)

        score_map = {sr.hash_id: sr for sr in score_results}
        batch_qualified = 0

        for job in batch:
            score = score_map.get(job.hash_id)
            if score is None:
                logger.warning("score_missing_result", hash_id=job.hash_id)
                continue
            if score.match_pct < qualify_threshold:
                logger.debug(
                    "score_below_threshold",
                    hash_id=job.hash_id,
                    match_pct=score.match_pct,
                    threshold=qualify_threshold,
                )
                continue
            scored_job = ScoredJob.from_normalized_and_score(job, score, run_id=run_id)
            qualified.append(scored_job)
            batch_qualified += 1

        logger.info(
            "score_batch_complete",
            batch_num=batch_num + 1,
            qualified=batch_qualified,
            run_id=run_id,
        )

    logger.info(
        "score_run_complete",
        total_scored=len(jobs),
        total_qualified=len(qualified),
        threshold=qualify_threshold,
        run_id=run_id,
    )
    return qualified

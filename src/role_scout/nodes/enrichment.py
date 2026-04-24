"""enrichment_node — concurrent JD fetch + state trimming."""
from __future__ import annotations

import asyncio
from typing import Any

import structlog
from jobsearch.models import NormalizedJob
from jobsearch.pipeline.enrich import enrich_descriptions

from role_scout.models.state import JobSearchState, assert_state_size

log = structlog.get_logger()

_MIN_DESCRIPTION_LENGTH = 200


async def _enrich_concurrently(jobs: list[NormalizedJob]) -> None:
    """Fetch full JD text for each job concurrently via asyncio.to_thread.

    enrich_descriptions([job]) is called once per job — each call is an independent
    HTTP fetch so concurrent execution is safe and significantly faster than serial.
    """
    tasks = [asyncio.to_thread(enrich_descriptions, [job]) for job in jobs]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            log.warning(
                "enrich_job_failed",
                hash_id=getattr(jobs[i], "hash_id", "unknown"),
                error=str(result),
            )


def enrichment_node(state: JobSearchState) -> dict[str, Any]:
    """Fetch full JD text for each new_job concurrently; trim raw_by_source and normalized_jobs.

    State trimming (per TECH-DESIGN §3.2):
    - raw_by_source → {} after this node
    - normalized_jobs → [] after this node
    - new_jobs → [] after this node
    Trimming reduces state size before the scoring node loads enriched descriptions.
    """
    run_id = state.get("run_id", "run_unknown")
    bound_log = log.bind(correlation_id=run_id, run_id=run_id, node_name="enrichment")

    new_jobs: list[NormalizedJob] = list(state.get("new_jobs", []))
    errors: list[str] = list(state.get("errors", []))

    bound_log.info("enrichment_started", job_count=len(new_jobs))

    if new_jobs:
        asyncio.run(_enrich_concurrently(new_jobs))

    enriched_count = sum(
        1 for j in new_jobs if j.description and len(j.description) > _MIN_DESCRIPTION_LENGTH
    )
    bound_log.info("enrichment_complete", total=len(new_jobs), enriched=enriched_count)

    state_update: dict[str, Any] = {
        "enriched_jobs": new_jobs,
        "raw_by_source": {},
        "normalized_jobs": [],
        "new_jobs": [],
    }

    if errors:
        state_update["errors"] = errors

    assert_state_size({**state, **state_update})
    return state_update

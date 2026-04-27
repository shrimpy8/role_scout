"""Thin wrapper around Phase 1 fetch_google_jobs for use in asyncio.to_thread."""
from __future__ import annotations

import time
from typing import Any

import structlog
from role_scout.compat.fetchers.google_jobs import fetch_google_jobs
from role_scout.compat.models import CandidateProfile

log = structlog.get_logger()


def run_google(
    profile: CandidateProfile,
    serpapi_key: str,
    max_results: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Synchronous Google Jobs fetch. Returns (raw_jobs, query_params).

    Called via asyncio.to_thread — must NOT share state with other threads.
    """
    query_params = {
        "queries": profile.target_roles,
        "location": profile.location,
        "max_results": max_results,
        "posted_within": profile.posted_within,
    }
    t0 = time.monotonic()
    try:
        raw = fetch_google_jobs(
            api_key=serpapi_key,
            queries=profile.target_roles,
            location=profile.location,
            max_results=max_results,
            posted_within=profile.posted_within,
        )
        duration = time.monotonic() - t0
        log.info("google_fetch_done", count=len(raw), duration_s=round(duration, 2))
        return raw, query_params
    except Exception as exc:
        duration = time.monotonic() - t0
        log.exception("google_fetch_failed", duration_s=round(duration, 2))
        raise

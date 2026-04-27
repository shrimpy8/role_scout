"""Thin wrapper around Phase 1 fetch_linkedin for use in asyncio.to_thread."""
from __future__ import annotations

import time
from typing import Any

import structlog
from role_scout.compat.fetchers.linkedin import fetch_linkedin
from role_scout.compat.models import CandidateProfile

log = structlog.get_logger()


def run_linkedin(
    profile: CandidateProfile,
    apify_token: str,
    max_items: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Synchronous LinkedIn fetch. Returns (raw_jobs, query_params).

    Called via asyncio.to_thread in discovery_node — must NOT share state with
    other threads. No module-level mutable state used.
    """
    query_params = {
        "queries": profile.target_roles,
        "location": profile.location,
        "max_items": max_items,
        "posted_within": profile.posted_within,
    }
    t0 = time.monotonic()
    try:
        raw = fetch_linkedin(
            token=apify_token,
            queries=profile.target_roles,
            location=profile.location,
            max_items=max_items,
            posted_within=profile.posted_within,
        )
        duration = time.monotonic() - t0
        log.info("linkedin_fetch_done", count=len(raw), duration_s=round(duration, 2))
        return raw, query_params
    except Exception as exc:
        duration = time.monotonic() - t0
        log.exception("linkedin_fetch_failed", duration_s=round(duration, 2))
        raise

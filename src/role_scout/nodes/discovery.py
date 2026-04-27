"""discovery_node — parallel async fetch from 3 sources, dedup, source-health assembly."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from jobsearch.models import CandidateProfile, NormalizedJob
from jobsearch.pipeline.dedup import dedup_jobs
from jobsearch.pipeline.normalize import normalize_jobs

from role_scout.config import Settings
from role_scout.dal.run_log_dal import write_source_health
from role_scout.db import get_rw_conn
from role_scout.fetchers.google_wrapper import run_google
from role_scout.fetchers.linkedin_wrapper import run_linkedin
from role_scout.fetchers.trueup_wrapper import run_trueup
from role_scout.models.core import SourceHealthEntry
from role_scout.models.state import JobSearchState, assert_state_size

log = structlog.get_logger()

# Phase 1 source names → Phase 2 SourceName
_P1_TO_P2: dict[str, str] = {"google_jobs": "google"}


def _p2_source(p1_name: str) -> str:
    """Map Phase 1 source identifier to Phase 2 SourceName."""
    return _P1_TO_P2.get(p1_name, p1_name)


async def _fetch_one(
    source: str,
    fn: Any,
    *args: Any,
) -> tuple[str, list[dict[str, Any]], dict[str, Any], float, str | None]:
    """Run a sync fetcher in a thread. Returns (source, raw, query_params, duration_s, error)."""
    t0 = time.monotonic()
    try:
        raw, query_params = await asyncio.to_thread(fn, *args)
        return source, raw, query_params, time.monotonic() - t0, None
    except Exception as exc:
        return source, [], {}, time.monotonic() - t0, str(exc)


async def _gather_sources(
    profile: CandidateProfile,
    settings: Settings,
    skipped_sources: set[str],
    bound_log: structlog.BoundLogger,
) -> list[tuple[str, list[dict[str, Any]], dict[str, Any], float, str | None]]:
    """Launch all non-skipped fetchers concurrently and return results."""
    tasks = []

    if "linkedin" not in skipped_sources:
        tasks.append(_fetch_one("linkedin", run_linkedin, profile, settings.APIFY_TOKEN, settings.DISCOVERY_MAX_ITEMS))
    else:
        bound_log.warning("source_skipped", source="linkedin")

    if "google" not in skipped_sources:
        tasks.append(_fetch_one("google", run_google, profile, settings.SERPAPI_KEY, settings.DISCOVERY_MAX_ITEMS))
    else:
        bound_log.warning("source_skipped", source="google")

    if "trueup" not in skipped_sources:
        tasks.append(
            _fetch_one("trueup", run_trueup, settings.IMAP_EMAIL, settings.IMAP_APP_PASSWORD)
        )
    else:
        bound_log.warning("source_skipped", source="trueup")

    if not tasks:
        return []

    return list(await asyncio.gather(*tasks))


def discovery_node(state: JobSearchState) -> dict[str, Any]:
    """Fetch jobs from all active sources concurrently, dedup, and assemble health stats.

    Partial-failure circuit breaker: if ≥ 2 sources fail and force_partial is False,
    sets cancel_reason='crippled_fetch' to short-circuit the graph.
    """
    settings = Settings()
    run_id: str = state.get("run_id", "run_unknown")
    profile: CandidateProfile = state["candidate_profile"]
    skipped_sources: set[str] = set(state.get("skipped_sources", []))
    force_partial: bool = bool(state.get("force_partial", False))
    errors: list[str] = list(state.get("errors", []))

    bound_log = log.bind(correlation_id=run_id, run_id=run_id, node_name="discovery")
    bound_log.info("discovery_started")

    # --- Concurrent fetch ---
    results = asyncio.run(_gather_sources(profile, settings, skipped_sources, bound_log))

    # Seed health with skipped-source entries
    source_health: dict[str, SourceHealthEntry] = {
        src: SourceHealthEntry(status="skipped", jobs=0, duration_s=0.0)
        for src in skipped_sources
    }

    raw_by_source: dict[str, list[dict[str, Any]]] = {}
    source_counts: dict[str, int] = {}
    fetch_errors: list[str] = []

    for source, raw, query_params, duration_s, error in results:
        p2_src = _p2_source(source)
        raw_by_source[p2_src] = raw

        if error:
            fetch_errors.append(f"{source}: {error}")
            source_health[p2_src] = SourceHealthEntry(
                status="failed",
                jobs=0,
                duration_s=round(duration_s, 2),
                error=error,
                query_params={k: str(v) for k, v in query_params.items()},
            )
            bound_log.error("source_fetch_failed", source=source, error=error)
        else:
            source_counts[p2_src] = len(raw)
            source_health[p2_src] = SourceHealthEntry(
                status="ok",
                jobs=len(raw),
                duration_s=round(duration_s, 2),
                raw_count=len(raw),
                query_params={k: str(v) for k, v in query_params.items()},
            )
            bound_log.info("source_fetch_ok", source=source, count=len(raw))

    errors.extend(fetch_errors)

    # --- Partial-failure circuit breaker ---
    active_count = len(results)
    failed_count = len(fetch_errors)

    if active_count > 0 and failed_count >= 2 and not force_partial:
        bound_log.error(
            "discovery_crippled",
            failed=failed_count,
            active=active_count,
        )
        _persist_health(settings, run_id, source_health, bound_log)
        return {
            "raw_by_source": {},
            "normalized_jobs": [],
            "new_jobs": [],
            "source_counts": source_counts,
            "source_health": source_health,
            "errors": errors,
            "cancel_reason": "crippled_fetch",
            "human_approved": False,
        }

    if failed_count >= 2 and force_partial:
        bound_log.warning("discovery_partial_forced", failed=failed_count)

    # --- Normalize ---
    all_normalized: list[NormalizedJob] = []
    for p2_src, raw in raw_by_source.items():
        if not raw:
            continue
        p1_src = "google_jobs" if p2_src == "google" else p2_src
        normalized = normalize_jobs(raw, p1_src)
        all_normalized.extend(normalized)

    bound_log.info("normalized_total", count=len(all_normalized))

    # --- Dedup ---
    try:
        conn = get_rw_conn(settings.DB_PATH)
        new_jobs = dedup_jobs(conn, all_normalized)
        conn.commit()
        conn.close()
    except Exception as exc:
        bound_log.exception("dedup_failed")
        errors.append(f"dedup_failed: {exc}")
        new_jobs = all_normalized

    # Update after_dedup counts per source in health entries
    after_dedup_by_source: dict[str, int] = {}
    for job in new_jobs:
        p2_src = _p2_source(job.source)
        after_dedup_by_source[p2_src] = after_dedup_by_source.get(p2_src, 0) + 1

    for p2_src, entry in source_health.items():
        if entry.status == "ok":
            source_health[p2_src] = SourceHealthEntry(
                status=entry.status,
                jobs=entry.jobs,
                duration_s=entry.duration_s,
                error=entry.error,
                raw_count=entry.raw_count,
                after_dedup=after_dedup_by_source.get(p2_src, 0),
                query_params=entry.query_params,
            )

    source_counts = {src: after_dedup_by_source.get(src, 0) for src in raw_by_source}
    bound_log.info("dedup_complete", new_count=len(new_jobs))

    # --- Persist source health ---
    _persist_health(settings, run_id, source_health, bound_log)

    state_update: dict[str, Any] = {
        "raw_by_source": raw_by_source,
        "normalized_jobs": all_normalized,
        "new_jobs": new_jobs,
        "source_counts": source_counts,
        "source_health": source_health,
        "errors": errors,
    }

    assert_state_size({**state, **state_update})
    bound_log.info("discovery_complete", new_jobs=len(new_jobs))
    return state_update


def _persist_health(
    settings: Settings,
    run_id: str,
    health: dict[str, SourceHealthEntry],
    bound_log: structlog.BoundLogger,
) -> None:
    """Write source_health to run_log — best-effort, never raises."""
    try:
        conn = get_rw_conn(settings.DB_PATH)
        write_source_health(conn, run_id, health)  # type: ignore[arg-type]
        conn.close()
    except Exception:
        bound_log.exception("source_health_persist_failed")

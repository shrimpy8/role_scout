"""preflight_node — validates config, inserts run_log, checks SerpAPI quota, builds skip list."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from jobsearch.db.connection import get_db
from jobsearch.models import load_candidate_profile

from role_scout.config import Settings
from role_scout.dal.run_log_dal import get_sources_to_skip, insert_run, update_run
from role_scout.db import get_rw_conn, init_db
from role_scout.models.state import JobSearchState

log = structlog.get_logger()


class PreflightError(RuntimeError):
    """Raised when a required precondition for the pipeline is not met."""


def _check_serpapi_quota(api_key: str, min_quota: int) -> int | None:
    """Return remaining SerpAPI quota for this month, or None on failure.

    Does not raise — quota failure is a warning, not a blocker.
    """
    try:
        resp = httpx.get(
            "https://serpapi.com/account",
            params={"api_key": api_key},
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
        total: int = data.get("plan_searches_left", 9999)
        return total
    except Exception as exc:
        log.warning("serpapi_quota_check_failed", error=str(exc))
        return None


def _load_watchlist(watchlist_path: str) -> list[str]:
    """Load watchlist from YAML. Returns empty list if file missing."""
    import yaml  # imported lazily to keep startup fast
    from pathlib import Path

    p = Path(watchlist_path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text()) or {}
    return [str(c) for c in data.get("companies", [])]


def preflight_node(state: JobSearchState) -> dict[str, Any]:
    """Validate environment, load profile/watchlist, insert run_log row, check sources.

    Returns a partial state dict that preflight contributes.
    Raises PreflightError on unrecoverable failures (missing API key, DB error).
    """
    settings = Settings()

    # --- Validate required secrets ---
    if not settings.ANTHROPIC_API_KEY:
        raise PreflightError("ANTHROPIC_API_KEY not set")

    # --- Build run identity ---
    run_id = f"run_{uuid.uuid4().hex}"
    trigger_type = state.get("trigger_type", "manual")
    started_at = datetime.now(timezone.utc)

    bound_log = log.bind(correlation_id=run_id, run_id=run_id, trigger_type=trigger_type, node_name="preflight")
    bound_log.info("preflight_started")

    # --- DB init + run insert ---
    try:
        init_db(settings.DB_PATH)
        conn = get_rw_conn(settings.DB_PATH)
        insert_run(conn, run_id=run_id, trigger_type=trigger_type, started_at=started_at)
    except Exception as exc:
        raise PreflightError(f"DB initialisation failed: {exc}") from exc

    # --- Load candidate profile ---
    try:
        candidate_profile = load_candidate_profile(str(settings.CANDIDATE_PROFILE_PATH))
    except Exception as exc:
        _fail_run(conn, run_id, str(exc))
        raise PreflightError(f"Cannot load candidate_profile.yaml: {exc}") from exc

    # --- Load watchlist ---
    watchlist = _load_watchlist(str(settings.WATCHLIST_PATH))

    # --- Source auto-skip (3 consecutive failures) ---
    force_sources: set[str] = set(state.get("force_sources", []))  # type: ignore[arg-type]
    to_skip = get_sources_to_skip(conn, window=settings.SOURCE_HEALTH_WINDOW) - force_sources
    if to_skip:
        bound_log.warning("sources_auto_skipped", sources=sorted(to_skip))

    # --- SerpAPI quota check ---
    errors: list[str] = list(state.get("errors", []))
    skipped_sources = set(to_skip)

    if "google" not in skipped_sources:
        remaining = _check_serpapi_quota(settings.SERPAPI_KEY, settings.SERPAPI_MIN_QUOTA)
        if remaining is not None and remaining < settings.SERPAPI_MIN_QUOTA:
            bound_log.warning(
                "serpapi_quota_low",
                remaining=remaining,
                min_quota=settings.SERPAPI_MIN_QUOTA,
            )
            errors.append(f"serpapi_quota_low (remaining={remaining})")
            skipped_sources.add("google")

    # --- TTL deadline ---
    from datetime import timedelta
    ttl_deadline = started_at + timedelta(hours=settings.INTERRUPT_TTL_HOURS)

    conn.close()

    bound_log.info("preflight_complete", skipped_sources=sorted(skipped_sources))

    return {
        "run_id": run_id,
        "started_at": started_at,
        "trigger_type": trigger_type,
        "candidate_profile": candidate_profile,
        "watchlist": watchlist,
        "qualify_threshold": settings.SCORE_THRESHOLD,
        "run_mode": settings.RUN_MODE,
        "skipped_sources": sorted(skipped_sources),
        "ttl_deadline": ttl_deadline,
        "ttl_extended": False,
        "errors": errors,
        "source_counts": {},
        "source_health": {},
    }


def _fail_run(conn: Any, run_id: str, error: str) -> None:
    """Mark the run as failed immediately (best-effort, never raises)."""
    import json as _json
    try:
        update_run(conn, run_id, status="failed", errors=[error])
    except Exception:
        log.exception("preflight._fail_run.db_update_failed", run_id=run_id)
        raise

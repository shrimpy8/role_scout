"""Phase 2 DAL for qualified_jobs — wraps Phase 1 with validation and Pydantic models."""
from __future__ import annotations

import sqlite3

import structlog
from role_scout.compat.db.qualified_jobs import (
    get_job_by_hash_id as _p1_get_job_by_hash_id,
    get_qualified_jobs as _p1_get_qualified_jobs,
    update_job_status as _p1_update_job_status,
)
from role_scout.compat.models import ScoredJob
from pydantic import Field

from role_scout.models.core import BaseSchema

log = structlog.get_logger()

_VALID_STATUSES: frozenset[str] = frozenset({"new", "reviewed", "applied", "rejected"})
_VALID_STATUSES_WITH_ALL: frozenset[str] = _VALID_STATUSES | {"all"}
_VALID_SORT_COLS: frozenset[str] = frozenset(
    {"match_pct", "company", "title", "city", "work_model", "company_stage", "status", "scored_at"}
)
_VALID_DIRECTIONS: frozenset[str] = frozenset({"asc", "desc"})
_LIMIT_CAP: int = 50


class JobSummary(BaseSchema):
    """Lightweight projection of a qualified_jobs row for list endpoints.

    All fields are sourced directly from the ScoredJob returned by Phase 1.
    ``source`` preserves the Phase 1 value (e.g. ``"google_jobs"``).
    """

    hash_id: str
    title: str
    company: str
    location: str
    work_model: str
    match_pct: int = Field(ge=0, le=100)
    status: str
    source: str
    comp_range: str | None = None
    salary_visible: bool
    is_watchlist: bool
    posted_date: str | None = None


def _scored_job_to_summary(job: ScoredJob) -> JobSummary:
    return JobSummary(
        hash_id=job.hash_id,
        title=job.title,
        company=job.company,
        location=job.location,
        work_model=job.work_model,
        match_pct=job.match_pct,
        status=job.status,
        source=job.source,
        comp_range=job.comp_range,
        salary_visible=job.salary_visible,
        is_watchlist=job.is_watchlist,
        posted_date=job.posted_date,
    )


def get_jobs(
    conn: sqlite3.Connection,
    *,
    status: str = "new",
    limit: int = 20,
    sort: str = "match_pct",
    direction: str = "desc",
) -> list[JobSummary]:
    """Return paginated qualified jobs as JobSummary list.

    Delegates to Phase 1 get_qualified_jobs(). Validates status is in
    ("new", "reviewed", "applied", "rejected", "all"). Caps limit at 50.

    Args:
        conn: Open SQLite connection.
        status: Filter by job status, or ``"all"`` for no filter.
        limit: Max rows to return; capped at 50.
        sort: Column to sort by; falls back to ``"match_pct"`` if invalid.
        direction: ``"asc"`` or ``"desc"``; falls back to ``"desc"`` if invalid.

    Returns:
        List of JobSummary models ordered by the requested sort column.

    Raises:
        ValueError: If ``status`` is not a recognised value.
    """
    if status not in _VALID_STATUSES_WITH_ALL:
        raise ValueError(
            f"Invalid status {status!r}. Must be one of: "
            + ", ".join(sorted(_VALID_STATUSES_WITH_ALL))
        )

    effective_limit = min(limit, _LIMIT_CAP)
    effective_sort = sort if sort in _VALID_SORT_COLS else "match_pct"
    effective_dir = direction if direction in _VALID_DIRECTIONS else "desc"

    log.debug(
        "jobs_dal.get_jobs",
        status=status,
        limit=effective_limit,
        sort=effective_sort,
        direction=effective_dir,
    )

    jobs = _p1_get_qualified_jobs(
        conn,
        status=status,
        limit=effective_limit,
        sort=effective_sort,
        dir=effective_dir,
    )
    return [_scored_job_to_summary(j) for j in jobs]


def get_job_detail(conn: sqlite3.Connection, hash_id: str) -> ScoredJob | None:
    """Return full ScoredJob by hash_id, or None if not found.

    Delegates to Phase 1 get_job_by_hash_id().

    Args:
        conn: Open SQLite connection.
        hash_id: 16-character hex job identifier.

    Returns:
        A ScoredJob instance, or None if the hash_id is not in the DB.
    """
    log.debug("jobs_dal.get_job_detail", hash_id=hash_id)
    return _p1_get_job_by_hash_id(conn, hash_id)


def set_job_status(conn: sqlite3.Connection, hash_id: str, status: str) -> str:
    """Update job status. Returns old status.

    Validates status before any DB write. Raises KeyError if hash_id not found.

    Args:
        conn: Open SQLite connection.
        hash_id: 16-character hex job identifier.
        status: New status; must be one of new / reviewed / applied / rejected.

    Returns:
        The previous status string.

    Raises:
        ValueError: If ``status`` is not a valid transition target.
        KeyError: If no job with ``hash_id`` exists in the DB.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(
            f"Invalid status {status!r}. Must be one of: "
            + ", ".join(sorted(_VALID_STATUSES))
        )

    old_status = _p1_update_job_status(conn, hash_id, status)
    if old_status is None:
        raise KeyError(f"Job not found: {hash_id!r}")

    conn.commit()
    log.info("jobs_dal.set_job_status", hash_id=hash_id, old=old_status, new=status)
    return old_status

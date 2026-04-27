"""qualified_jobs table operations: insert, update status, query."""

import json
import sqlite3
from datetime import datetime
from typing import Any

from role_scout.compat.models import ScoredJob

_VALID_SORT_COLS = {"match_pct", "company", "title", "city", "work_model", "company_stage", "status", "scored_at"}
_VALID_DIRS = {"asc", "desc"}
_SELECT_COLS = (
    "hash_id, title, company, location, city, country, work_model, url, apply_url, source, "
    "posted_date, comp_range, salary_visible, company_stage, is_watchlist, "
    "match_pct, seniority_score, domain_score, location_score, stage_score, "
    "comp_score, reasoning, key_requirements, red_flags, domain_alignment, "
    "seniority_match, location_fit, company_stage_fit, jd_alignment, description, "
    "description_snippet, company_size, domain_tags, status, jd_filename, "
    "jd_downloaded, scored_at, fetched_at, run_id"
)


def _row_to_scored_job(row: sqlite3.Row) -> ScoredJob:
    """Convert a DB row to a ScoredJob model."""
    d = dict(row)
    d["salary_visible"] = bool(d["salary_visible"])
    d["is_watchlist"] = bool(d["is_watchlist"])
    d["jd_downloaded"] = bool(d["jd_downloaded"])
    d["key_requirements"] = json.loads(d.get("key_requirements") or "[]")
    d["red_flags"] = json.loads(d.get("red_flags") or "[]")
    d["domain_tags"] = json.loads(d.get("domain_tags") or "[]")
    return ScoredJob(**d)


def insert_qualified_job(conn: sqlite3.Connection, job: ScoredJob) -> None:
    """Insert a qualified job; skip silently if hash_id already exists."""
    conn.execute(
        """
        INSERT OR IGNORE INTO qualified_jobs (
            hash_id, title, company, location, city, country, work_model, url, apply_url, source,
            posted_date, comp_range, salary_visible, company_stage, is_watchlist,
            match_pct, seniority_score, domain_score, location_score, stage_score,
            comp_score, reasoning, key_requirements, red_flags, domain_alignment,
            seniority_match, location_fit, company_stage_fit, jd_alignment,
            description, description_snippet, company_size, domain_tags,
            status, jd_filename, jd_downloaded, scored_at, fetched_at, run_id
        ) VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            job.hash_id, job.title, job.company, job.location, job.city, job.country,
            job.work_model, job.url, job.apply_url, job.source, job.posted_date, job.comp_range,
            int(job.salary_visible), job.company_stage, int(job.is_watchlist),
            job.match_pct, job.seniority_score, job.domain_score, job.location_score,
            job.stage_score, job.comp_score, job.reasoning,
            json.dumps(job.key_requirements), json.dumps(job.red_flags),
            job.domain_alignment, job.seniority_match, job.location_fit,
            job.company_stage_fit, job.jd_alignment,
            job.description, job.description_snippet, job.company_size,
            json.dumps(job.domain_tags),
            job.status, job.jd_filename,
            int(job.jd_downloaded),
            job.scored_at.isoformat() if job.scored_at else datetime.utcnow().isoformat(),
            job.fetched_at.isoformat() if job.fetched_at else None,
            job.run_id,
        ),
    )


def update_job_status(conn: sqlite3.Connection, hash_id: str, status: str) -> str | None:
    """Update the status of a job. Returns old status if found, None if not found."""
    row = conn.execute(
        "SELECT status FROM qualified_jobs WHERE hash_id = ?", (hash_id,)
    ).fetchone()
    if row is None:
        return None
    old_status = row["status"]
    conn.execute(
        "UPDATE qualified_jobs SET status = ? WHERE hash_id = ?",
        (status, hash_id),
    )
    return old_status


def update_jd_filename(conn: sqlite3.Connection, hash_id: str, filename: str) -> None:
    """Update jd_filename for a qualified job after successful export."""
    conn.execute(
        "UPDATE qualified_jobs SET jd_filename = ? WHERE hash_id = ?",
        (filename, hash_id),
    )


def get_qualified_jobs(
    conn: sqlite3.Connection,
    status: str = "all",
    run_id: str | None = None,
    limit: int = 50,
    sort: str = "match_pct",
    dir: str = "desc",
    source: str | None = None,
) -> list[ScoredJob]:
    """Return qualified jobs with optional status, source, sort, and limit filters."""
    if sort not in _VALID_SORT_COLS:
        sort = "match_pct"
    if dir not in _VALID_DIRS:
        dir = "desc"

    conditions: list[str] = []
    params: list[Any] = []

    if status == "history":
        conditions.append("status IN ('applied','rejected')")
    elif status != "all":
        conditions.append("status = ?")
        params.append(status)

    if run_id:
        conditions.append("run_id = ?")
        params.append(run_id)

    if source:
        conditions.append("source = ?")
        params.append(source)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT {_SELECT_COLS} FROM qualified_jobs {where} ORDER BY {sort} {dir} LIMIT ?"  # noqa: S608
    params.append(min(limit, 200))

    rows = conn.execute(query, params).fetchall()
    return [_row_to_scored_job(r) for r in rows]


def get_job_by_hash_id(conn: sqlite3.Connection, hash_id: str) -> ScoredJob | None:
    """Return a single ScoredJob by hash_id, or None if not found."""
    row = conn.execute(
        f"SELECT {_SELECT_COLS} FROM qualified_jobs WHERE hash_id = ?", (hash_id,)  # noqa: S608
    ).fetchone()
    return _row_to_scored_job(row) if row else None


def update_jd_alignment(conn: sqlite3.Connection, hash_id: str, alignment: str) -> None:
    """Persist on-demand alignment analysis text for a job."""
    conn.execute(
        "UPDATE qualified_jobs SET jd_alignment = ? WHERE hash_id = ?",
        (alignment, hash_id),
    )


def get_job_count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    """Return counts of jobs grouped by status."""
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM qualified_jobs GROUP BY status"
    ).fetchall()
    counts = {"new": 0, "reviewed": 0, "applied": 0, "rejected": 0}
    for row in rows:
        counts[row["status"]] = row["cnt"]
    counts["total"] = sum(counts.values())
    return counts


def get_job_count_by_source(conn: sqlite3.Connection) -> dict[str, int]:
    """Return counts of jobs grouped by source."""
    rows = conn.execute(
        "SELECT source, COUNT(*) as cnt FROM qualified_jobs GROUP BY source"
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["source"]] = row["cnt"]
    return counts

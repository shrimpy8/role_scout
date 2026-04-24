"""DAL for the tailored_resume column in qualified_jobs."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import structlog

log = structlog.get_logger()


def get_cached_tailor(conn: sqlite3.Connection, hash_id: str) -> dict | None:
    """Return the cached tailored_resume JSON dict for hash_id, or None."""
    row = conn.execute(
        "SELECT tailored_resume FROM qualified_jobs WHERE hash_id = ?",
        (hash_id,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        log.warning("tailor_dal.get_cached_tailor.parse_error", hash_id=hash_id)
        return None


def write_tailor(conn: sqlite3.Connection, hash_id: str, data: dict) -> None:
    """Persist tailor result JSON to qualified_jobs.tailored_resume."""
    conn.execute(
        "UPDATE qualified_jobs SET tailored_resume = ? WHERE hash_id = ?",
        (json.dumps(data, ensure_ascii=False), hash_id),
    )
    conn.commit()
    log.info("tailor_dal.write_tailor", hash_id=hash_id)

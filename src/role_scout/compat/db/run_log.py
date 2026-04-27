"""run_log table operations: insert, update, and query run records."""

import json
import sqlite3
from datetime import datetime
from typing import Any

from role_scout.compat.models import RunLog


def insert_run_log(conn: sqlite3.Connection, run: RunLog) -> str:
    """Insert a new run_log row. Returns the run_id."""
    conn.execute(
        """
        INSERT INTO run_log (
            run_id, started_at, completed_at, status, trigger_type,
            source_linkedin, source_google_jobs, source_wellfound, source_trueup,
            total_fetched, total_new, total_qualified, watchlist_hits, errors
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run.run_id,
            run.started_at.isoformat(),
            run.completed_at.isoformat() if run.completed_at else None,
            run.status, run.trigger_type,
            run.source_linkedin, run.source_google_jobs,
            run.source_wellfound, run.source_trueup,
            run.total_fetched, run.total_new, run.total_qualified,
            json.dumps(run.watchlist_hits), json.dumps(run.errors),
        ),
    )
    return run.run_id


def update_run_log(conn: sqlite3.Connection, run_id: str, **fields: Any) -> None:
    """Update arbitrary fields on a run_log row."""
    if not fields:
        return
    for k, v in fields.items():
        if isinstance(v, (dict, list)):
            fields[k] = json.dumps(v)
        elif isinstance(v, datetime):
            fields[k] = v.isoformat()

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [run_id]
    conn.execute(f"UPDATE run_log SET {set_clause} WHERE run_id = ?", values)  # noqa: S608


def get_run_logs(conn: sqlite3.Connection, limit: int = 10) -> list[RunLog]:
    """Return the last N pipeline runs ordered by start time descending."""
    rows = conn.execute(
        "SELECT * FROM run_log ORDER BY started_at DESC LIMIT ?",
        (min(limit, 50),),
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["watchlist_hits"] = json.loads(d.get("watchlist_hits") or "{}")
        d["errors"] = json.loads(d.get("errors") or "[]")
        result.append(RunLog(**d))
    return result

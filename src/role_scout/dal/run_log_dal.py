"""Phase 2 DAL for run_log table — inserts, updates, and source-health queries."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import structlog
from role_scout.compat.db.run_log import insert_run_log as _p1_insert_run_log
from role_scout.compat.models import RunLog as P1RunLog

from role_scout.models.core import CancelReason, RunStatus, SourceHealthEntry, SourceName, TriggerType
from role_scout.models.records import RunLogRow, SourceHealthBlob

log = structlog.get_logger()


def insert_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    trigger_type: TriggerType,
    started_at: datetime,
) -> None:
    """Insert a new run_log row via Phase 1 DAL, then update Phase 2 columns."""
    p1_run = P1RunLog(
        run_id=run_id,
        started_at=started_at,
        status="running",
        trigger_type=trigger_type,
    )
    _p1_insert_run_log(conn, p1_run)
    conn.execute(
        "UPDATE run_log SET trigger_type = ? WHERE run_id = ?",
        (trigger_type, run_id),
    )
    conn.commit()
    log.info("run_inserted", run_id=run_id, trigger_type=trigger_type)


def update_run(conn: sqlite3.Connection, run_id: str, **fields: Any) -> None:
    """Update arbitrary Phase 2 columns on an existing run_log row."""
    if not fields:
        return
    serialised: dict[str, Any] = {}
    for k, v in fields.items():
        if isinstance(v, (dict, list)):
            serialised[k] = json.dumps(v)
        elif isinstance(v, datetime):
            serialised[k] = v.isoformat()
        elif isinstance(v, bool):
            serialised[k] = int(v)
        else:
            serialised[k] = v

    set_clause = ", ".join(f"{k} = ?" for k in serialised)
    values = list(serialised.values()) + [run_id]
    conn.execute(f"UPDATE run_log SET {set_clause} WHERE run_id = ?", values)  # noqa: S608
    conn.commit()


def set_run_status(
    conn: sqlite3.Connection,
    run_id: str,
    status: RunStatus,
    cancel_reason: CancelReason | None = None,
    completed_at: datetime | None = None,
) -> None:
    """Convenience: update status + cancel_reason + completed_at atomically."""
    fields: dict[str, Any] = {"status": status}
    if cancel_reason is not None:
        fields["cancel_reason"] = cancel_reason
    if completed_at is not None:
        fields["completed_at"] = completed_at
    update_run(conn, run_id, **fields)


def write_source_health(
    conn: sqlite3.Connection,
    run_id: str,
    health: dict[SourceName, SourceHealthEntry],
) -> None:
    """Serialise source_health dict and write to run_log.source_health_json."""
    blob = SourceHealthBlob(
        linkedin=health.get("linkedin"),
        google=health.get("google"),
        trueup=health.get("trueup"),
    )
    conn.execute(
        "UPDATE run_log SET source_health_json = ? WHERE run_id = ?",
        (blob.model_dump_json(), run_id),
    )
    conn.commit()


def get_recent_source_health(
    conn: sqlite3.Connection, limit: int = 3
) -> list[dict[str, Any]]:
    """Return the `limit` most recent run_log rows that have source_health_json set."""
    rows = conn.execute(
        """
        SELECT run_id, started_at, status, source_health_json
        FROM run_log
        WHERE source_health_json IS NOT NULL
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_sources_to_skip(conn: sqlite3.Connection, window: int = 3) -> set[str]:
    """Return source names that failed in ALL of the last `window` runs.

    A source is auto-skipped when its status == 'failed' in every one of the
    most recent `window` run_log rows that recorded source_health_json.
    """
    recent = get_recent_source_health(conn, limit=window)
    if len(recent) < window:
        return set()

    candidates: set[str] = {"linkedin", "google", "trueup"}
    for row in recent:
        raw = row.get("source_health_json")
        if not raw:
            candidates.clear()
            break
        try:
            blob = SourceHealthBlob.model_validate_json(raw)
            health = blob.as_dict()
        except Exception:
            candidates.clear()
            break

        ok_sources = {name for name, entry in health.items() if entry.status != "failed"}
        candidates -= ok_sources

    return candidates


def get_run_logs(
    conn: sqlite3.Connection, limit: int = 20, offset: int = 0
) -> tuple[list[RunLogRow], int]:
    """Return paginated run_log rows with total count for /api/runs."""
    total: int = conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0]
    rows = conn.execute(
        """
        SELECT run_id, status, trigger_type, started_at, completed_at,
               COALESCE(input_tokens, 0)        AS input_tokens,
               COALESCE(output_tokens, 0)       AS output_tokens,
               COALESCE(estimated_cost_usd, 0)  AS estimated_cost_usd,
               COALESCE(total_fetched, 0)       AS fetched_count,
               COALESCE(total_qualified, 0)     AS qualified_count,
               COALESCE(total_new, 0)           AS exported_count,
               source_health_json,
               errors,
               cancel_reason,
               ttl_deadline,
               COALESCE(ttl_extended, 0)        AS ttl_extended
        FROM run_log
        ORDER BY started_at DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()

    result: list[RunLogRow] = []
    for r in rows:
        d = dict(r)
        # Deserialise JSON columns
        raw_health = d.pop("source_health_json", None)
        health: dict[SourceName, SourceHealthEntry] = {}
        if raw_health:
            try:
                blob = SourceHealthBlob.model_validate_json(raw_health)
                health = blob.as_dict()
            except Exception:
                pass

        raw_errors = d.get("errors") or "[]"
        try:
            errors_list: list[str] = json.loads(raw_errors)
        except Exception:
            errors_list = []

        utc = timezone.utc
        result.append(
            RunLogRow(
                run_id=d["run_id"],
                status=d["status"] or "running",
                trigger_type=d.get("trigger_type") or "manual",
                started_at=datetime.fromisoformat(d["started_at"]).replace(tzinfo=utc),
                completed_at=(
                    datetime.fromisoformat(d["completed_at"]).replace(tzinfo=utc)
                    if d.get("completed_at")
                    else None
                ),
                input_tokens=int(d["input_tokens"]),
                output_tokens=int(d["output_tokens"]),
                estimated_cost_usd=float(d["estimated_cost_usd"]),
                fetched_count=int(d["fetched_count"]),
                qualified_count=int(d["qualified_count"]),
                exported_count=int(d["exported_count"]),
                source_health=health,
                errors=errors_list,
                cancel_reason=d.get("cancel_reason"),
                ttl_deadline=(
                    datetime.fromisoformat(d["ttl_deadline"]).replace(tzinfo=utc)
                    if d.get("ttl_deadline")
                    else None
                ),
                ttl_extended=bool(d.get("ttl_extended", 0)),
            )
        )
    return result, total

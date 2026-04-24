"""output_node — final node in the Phase 2 LangGraph pipeline.

Runs regardless of whether the human approved or cancelled.  On the approved
path it persists qualified jobs, updates seen-hashes, exports JD text files,
and writes the completed run summary.  On the cancelled path it records the
cancellation status only.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from jobsearch.db.qualified_jobs import insert_qualified_job
from jobsearch.db.seen_hashes import upsert_seen_hash
from jobsearch.models import ScoredJob

from role_scout.config import Settings
from role_scout.dal.run_log_dal import set_run_status, update_run, write_source_health
from role_scout.db import get_rw_conn
from role_scout.models.state import JobSearchState, assert_state_size

log = structlog.get_logger()


def output_node(state: JobSearchState) -> dict[str, Any]:
    """Persist results (approved) or record cancellation, then finalise the run log.

    Always runs as the terminal node of the graph.  Returns a partial state
    update containing ``exported_count`` and the accumulated ``errors`` list.
    """
    settings = Settings()
    run_id: str = state.get("run_id", "run_unknown")
    bound_log = log.bind(correlation_id=run_id, run_id=run_id, node_name="output")

    human_approved: bool = bool(state.get("human_approved", False))
    cancel_reason: str | None = state.get("cancel_reason")
    scored_jobs: list[ScoredJob] = list(state.get("scored_jobs", []))
    qualify_threshold: int = int(state.get("qualify_threshold", settings.SCORE_THRESHOLD))
    errors: list[str] = list(state.get("errors", []))

    scoring_tokens_in: int = int(state.get("scoring_tokens_in", 0))
    scoring_tokens_out: int = int(state.get("scoring_tokens_out", 0))
    reflection_tokens_in: int = int(state.get("reflection_tokens_in", 0))
    reflection_tokens_out: int = int(state.get("reflection_tokens_out", 0))
    total_cost_usd: float = float(state.get("total_cost_usd", 0.0))

    bound_log.info("output_started", human_approved=human_approved)

    exported_count: int = 0
    conn: sqlite3.Connection | None = None

    try:
        conn = get_rw_conn(settings.DB_PATH)

        if human_approved:
            exported_count = _run_approved_path(
                conn=conn,
                state=state,
                run_id=run_id,
                scored_jobs=scored_jobs,
                qualify_threshold=qualify_threshold,
                scoring_tokens_in=scoring_tokens_in,
                scoring_tokens_out=scoring_tokens_out,
                reflection_tokens_in=reflection_tokens_in,
                reflection_tokens_out=reflection_tokens_out,
                total_cost_usd=total_cost_usd,
                errors=errors,
                settings=settings,
                bound_log=bound_log,
            )
        else:
            _run_cancelled_path(
                conn=conn,
                run_id=run_id,
                cancel_reason=cancel_reason,
                errors=errors,
                bound_log=bound_log,
            )

    except Exception as exc:
        bound_log.exception("output_node_db_error")
        errors.append(f"output_node_db_error: {exc}")
    finally:
        if conn is not None:
            conn.close()

    bound_log.info("output_complete", exported=exported_count, approved=human_approved)

    state_update: dict[str, Any] = {
        "exported_count": exported_count,
        "errors": errors,
    }
    assert_state_size({**state, **state_update})
    return state_update


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _run_approved_path(
    *,
    conn: sqlite3.Connection,
    state: JobSearchState,
    run_id: str,
    scored_jobs: list[ScoredJob],
    qualify_threshold: int,
    scoring_tokens_in: int,
    scoring_tokens_out: int,
    reflection_tokens_in: int,
    reflection_tokens_out: int,
    total_cost_usd: float,
    errors: list[str],
    settings: Settings,
    bound_log: Any,
) -> int:
    """Insert qualified jobs, export JD files, and write the completed run log row.

    Returns the count of jobs that were exported.
    """
    qualified: list[ScoredJob] = [j for j in scored_jobs if j.match_pct >= qualify_threshold]

    bound_log.info(
        "output_qualified_jobs",
        total_scored=len(scored_jobs),
        qualified=len(qualified),
        threshold=qualify_threshold,
    )

    # --- Persist jobs and seen-hashes ---
    for job in qualified:
        try:
            insert_qualified_job(conn, job)
            upsert_seen_hash(conn, job.hash_id, source=job.source, title=job.title, company=job.company)
        except Exception as exc:
            bound_log.exception("output_insert_job_failed", hash_id=job.hash_id)
            errors.append(f"insert_job_failed:{job.hash_id}: {exc}")

    conn.commit()

    # --- Export JD text files ---
    jd_dir = Path(settings.DB_PATH).parent / "jds"
    jd_dir.mkdir(parents=True, exist_ok=True)

    for job in qualified:
        if not job.description:
            continue
        jd_path = jd_dir / f"{job.hash_id}.txt"
        try:
            jd_path.write_text(job.description, encoding="utf-8")
        except OSError as exc:
            bound_log.exception("output_jd_write_failed", hash_id=job.hash_id, path=str(jd_path))
            errors.append(f"jd_write_failed:{job.hash_id}: {exc}")

    # --- Update run log ---
    total_input = scoring_tokens_in + reflection_tokens_in
    total_output = scoring_tokens_out + reflection_tokens_out

    update_run(
        conn,
        run_id,
        status="completed",
        completed_at=datetime.now(UTC),
        total_qualified=len(qualified),
        total_new=len(qualified),
        input_tokens=total_input,
        output_tokens=total_output,
        estimated_cost_usd=total_cost_usd,
        errors=errors,
    )

    write_source_health(conn, run_id, state.get("source_health", {}))
    conn.commit()

    return len(qualified)


def _run_cancelled_path(
    *,
    conn: sqlite3.Connection,
    run_id: str,
    cancel_reason: str | None,
    errors: list[str],
    bound_log: Any,
) -> None:
    """Record cancellation status in the run log.  No job data is persisted."""
    run_status = "cancelled_ttl" if cancel_reason == "ttl_expired" else "cancelled"

    bound_log.info("output_cancelled", run_status=run_status, cancel_reason=cancel_reason)

    try:
        set_run_status(
            conn,
            run_id,
            run_status,
            cancel_reason=cancel_reason,
            completed_at=datetime.now(UTC),
        )
        conn.commit()
    except Exception as exc:
        bound_log.exception("output_set_run_status_failed")
        errors.append(f"set_run_status_failed: {exc}")

"""Agentic pipeline runner — wraps the LangGraph graph with HiTL interrupt handling.

Handles three interrupt resolution paths:
  1. Auto-approve: trigger_type in ("scheduled", "mcp") or --auto-approve flag
  2. Interactive: prompt user via stdin; enforces TTL timeout via threading
  3. TTL expiry: returns "ttl_expired" decision if user doesn't respond in time
"""
from __future__ import annotations

import queue
import queue as _queue_module
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from langgraph.types import Command

from role_scout.config import Settings
from role_scout.db import get_rw_conn
from role_scout.dal.run_log_dal import set_run_status
from role_scout.graph import build_graph
from role_scout.ttl import compute_ttl_deadline

log = structlog.get_logger()

_AUTO_APPROVE_TRIGGERS: frozenset[str] = frozenset({"mcp", "scheduled"})

# Maps run_id → Queue used by Flask route to signal HiTL decision
_pending_decisions: dict[str, _queue_module.Queue[str]] = {}


def register_pending(run_id: str, q: _queue_module.Queue[str]) -> None:
    """Register a pending HiTL decision queue (called by run_graph before waiting)."""
    _pending_decisions[run_id] = q


def resolve_pending(run_id: str, decision: str) -> bool:
    """Signal a decision from Flask route. Returns True if a pending run was found."""
    q = _pending_decisions.pop(run_id, None)
    if q is None:
        return False
    q.put(decision)
    return True


def run_graph(
    *,
    auto_approve: bool = False,
    dry_run: bool = False,
    force_partial: bool = False,
    trigger_type: str = "manual",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Execute the agentic pipeline graph end-to-end.

    Handles the review interrupt if raised (manual/dry_run triggers):
      - auto_approve=True or trigger in auto-approve set → immediately send "approve"
      - interactive → prompt user; TTL timer cancels with "ttl_expired" on expiry

    Returns the final LangGraph state values dict.
    """
    settings = Settings()
    effective_db_path = db_path or str(settings.DB_PATH)
    effective_trigger: str = "dry_run" if dry_run else trigger_type

    graph = build_graph()
    thread_id = str(uuid.uuid4())
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

    initial_state: dict[str, Any] = {
        "trigger_type": effective_trigger,
        "run_mode": "agentic",
        "force_partial": force_partial,
    }

    run_log = log.bind(trigger_type=effective_trigger, auto_approve=auto_approve)
    run_log.info("graph_run_start")

    # --- Stream graph until interrupt() or natural completion ---
    for _ in graph.stream(initial_state, config, stream_mode="values"):
        pass

    current = graph.get_state(config)

    # --- Graph completed without interrupt (auto-approve triggers, or empty run) ---
    if not current.next:
        run_log.info("graph_run_complete_no_interrupt")
        return dict(current.values)

    # --- Graph paused at review interrupt ---
    run_id: str = current.values.get("run_id", "run_unknown")
    qualified_count = sum(
        1
        for j in current.values.get("scored_jobs", [])
        if j.match_pct >= current.values.get("qualify_threshold", settings.SCORE_THRESHOLD)
    )

    # Update DB to review_pending
    _set_db_review_pending(effective_db_path, run_id, settings.INTERRUPT_TTL_HOURS)

    should_auto_approve = auto_approve or effective_trigger in _AUTO_APPROVE_TRIGGERS
    if should_auto_approve:
        decision = "approve"
        run_log.info("review_auto_approved_runner", run_id=run_id, qualified_count=qualified_count)
    else:
        decision = _interactive_decision(
            qualified_count=qualified_count,
            run_id=run_id,
            ttl_hours=settings.INTERRUPT_TTL_HOURS,
        )

    run_log.info("review_decision_runner", run_id=run_id, decision=decision)

    # --- Resume graph with decision ---
    for _ in graph.stream(Command(resume=decision), config, stream_mode="values"):
        pass

    run_log.info("graph_run_complete")
    return dict(graph.get_state(config).values)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _set_db_review_pending(db_path: str, run_id: str, ttl_hours: float) -> None:
    """Mark the run as review_pending with a TTL deadline in the DB."""
    try:
        conn = get_rw_conn(db_path)
        ttl_deadline = compute_ttl_deadline(ttl_hours)
        set_run_status(conn, run_id, "review_pending")
        from role_scout.dal.run_log_dal import update_run
        update_run(conn, run_id, ttl_deadline=ttl_deadline)
        conn.close()
    except Exception:
        log.warning("runner_db_review_pending_failed", run_id=run_id)


def _prompt_user(qualified_count: int, run_id: str) -> str:
    """Print job summary to stdout and read user decision from stdin.

    Returns 'approve' or 'cancel'. Never raises — returns 'cancel' on EOFError.
    """
    separator = "=" * 60
    print(f"\n{separator}")
    print(f"Run ID      : {run_id}")
    print(f"Qualified   : {qualified_count} job(s) above threshold")
    print(f"Actions     : approve / cancel")
    print(separator)
    while True:
        try:
            choice = input("Decision [approve/cancel]: ").strip().lower()
        except EOFError:
            return "cancel"
        if choice in ("approve", "cancel"):
            return choice
        print("Please type 'approve' or 'cancel'.")


def _interactive_decision(
    *,
    qualified_count: int,
    run_id: str,
    ttl_hours: float,
) -> str:
    """Collect a human decision via stdin with a TTL-enforced timeout.

    Spawns a daemon thread for stdin reading; returns 'ttl_expired' if the
    user does not respond within ``ttl_hours * 3600`` seconds.

    Also registers the queue via register_pending() so that the Flask
    /api/pipeline/resume route can signal the decision without stdin.
    """
    ttl_seconds = ttl_hours * 3600
    result_q: queue.Queue[str] = queue.Queue()

    def _reader() -> None:
        result_q.put(_prompt_user(qualified_count, run_id))

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()

    # Register so the Flask route can signal a decision via resolve_pending()
    register_pending(run_id, result_q)

    try:
        return result_q.get(timeout=ttl_seconds)
    except queue.Empty:
        log.warning(
            "review_ttl_expired_runner",
            run_id=run_id,
            ttl_hours=ttl_hours,
        )
        return "ttl_expired"

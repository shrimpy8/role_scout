"""review_node — Human-in-the-Loop checkpoint between scoring and output."""
from __future__ import annotations

from typing import Any

import structlog
from jobsearch.models import ScoredJob
from langgraph.types import interrupt

from role_scout.models.state import JobSearchState

log = structlog.get_logger()

_AUTO_APPROVE_TRIGGERS: frozenset[str] = frozenset({"mcp", "scheduled"})


def review_node(state: JobSearchState) -> dict[str, Any]:
    """Pause for human approval or auto-approve for non-interactive trigger types.

    For ``mcp`` and ``scheduled`` runs the node auto-approves immediately without
    calling ``interrupt()``.  For all other trigger types (``manual``, ``dry_run``)
    execution is suspended via ``interrupt()``; the caller resumes by passing a
    ``Command(resume=decision)`` where *decision* is one of:

    * ``"approve"``      → ``human_approved=True``
    * ``"ttl_expired"``  → ``human_approved=False, cancel_reason="ttl_expired"``
    * anything else      → ``human_approved=False, cancel_reason="user_cancel"``
    """
    run_id: str = state.get("run_id", "run_unknown")
    bound_log = log.bind(correlation_id=run_id, run_id=run_id, node_name="review")

    trigger_type: str = state.get("trigger_type", "manual")
    scored_jobs: list[ScoredJob] = list(state.get("scored_jobs", []))
    qualify_threshold: int = int(state.get("qualify_threshold", 85))

    qualified_count: int = sum(
        1 for job in scored_jobs if job.match_pct >= qualify_threshold
    )

    if trigger_type in _AUTO_APPROVE_TRIGGERS:
        bound_log.info(
            "review_auto_approved",
            trigger_type=trigger_type,
            qualified_count=qualified_count,
        )
        return {"human_approved": True, "cancel_reason": None}

    bound_log.info("review_interrupt", qualified_count=qualified_count)

    decision: str = interrupt({"run_id": run_id, "qualified_count": qualified_count})

    if decision == "approve":
        human_approved = True
        cancel_reason: str | None = None
    elif decision == "ttl_expired":
        human_approved = False
        cancel_reason = "ttl_expired"
    else:
        human_approved = False
        cancel_reason = "user_cancel"

    bound_log.info(
        "review_decision",
        human_approved=human_approved,
        cancel_reason=cancel_reason,
    )

    return {"human_approved": human_approved, "cancel_reason": cancel_reason}

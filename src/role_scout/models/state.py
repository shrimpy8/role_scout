"""LangGraph state schema. Passed between nodes; serialized by MemorySaver."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import TypedDict

from role_scout.compat.models import CandidateProfile, NormalizedJob, ScoredJob

from role_scout.models.core import (
    CancelReason,
    RunId,
    RunMode,
    SourceHealthEntry,
    SourceName,
    TriggerType,
)


class JobSearchState(TypedDict, total=False):
    """LangGraph state shared across all nodes. Keys are optional by default (total=False)."""
    # Immutable, set in preflight
    run_id: RunId
    trigger_type: TriggerType
    started_at: datetime
    candidate_profile: CandidateProfile
    watchlist: list[str]
    qualify_threshold: int
    run_mode: RunMode

    # Discovery outputs — trimmed to {} / [] after enrichment_node
    raw_by_source: dict[SourceName, list[dict]]  # type: ignore[type-arg]
    normalized_jobs: list[NormalizedJob]
    new_jobs: list[NormalizedJob]
    source_counts: dict[SourceName, int]
    source_health: dict[SourceName, SourceHealthEntry]

    # Enrichment — trimmed to [] after scoring_node
    enriched_jobs: list[NormalizedJob]

    # Scoring + reflection
    watchlist_hits: dict[str, int]
    scored_jobs: list[ScoredJob]
    scoring_tokens_in: int
    scoring_tokens_out: int
    reflection_tokens_in: int
    reflection_tokens_out: int
    reflection_applied_count: int

    # Review
    human_approved: bool
    cancel_reason: CancelReason | None
    ttl_deadline: datetime
    ttl_extended: bool

    # Output
    exported_count: int
    total_cost_usd: float

    # Accumulated
    errors: list[str]


class StateSizeExceeded(RuntimeError):
    """Raised when the serialised LangGraph state exceeds the configured cap."""


def assert_state_size(state: JobSearchState, cap_mb: int = 10) -> None:
    """Raise StateSizeExceeded if serialized state exceeds cap_mb."""
    size = sys.getsizeof(json.dumps(state, default=str).encode())
    if size > cap_mb * 1024 * 1024:
        raise StateSizeExceeded(f"State size {size} bytes exceeds {cap_mb} MB cap")

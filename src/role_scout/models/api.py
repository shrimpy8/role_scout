"""Pydantic models for Flask API request bodies and response envelopes."""
from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import Field

from role_scout.models.core import (
    BaseSchema,
    CompanyName,
    HashId,
    Meta,
    RunId,
    RunStatus,
    SourceHealthEntry,
    SourceName,
    TriggerType,
)
from role_scout.models.records import RunLogRow, TailoredResumeRecord

T = TypeVar("T")


class DataEnvelope(BaseSchema, Generic[T]):
    """Uniform 2xx envelope for all endpoints except /api/pipeline/status."""

    data: T
    meta: Meta


# ---- /api/pipeline/status ----

class TopMatch(BaseSchema):
    hash_id: HashId
    company: str
    title: str
    match_pct: int = Field(ge=0, le=100)


class PipelineStatusResponse(BaseSchema):
    """Raw response (no data wrapper) — optimized for 5s polling."""

    run_active: bool
    run_id: RunId | None = None
    status: RunStatus | None = None
    trigger_type: TriggerType | None = None
    started_at: datetime | None = None
    ttl_deadline: datetime | None = None
    ttl_extended: bool = False
    fetched_count: int | None = Field(default=None, ge=0)
    new_count: int | None = Field(default=None, ge=0)
    qualified_count: int | None = Field(default=None, ge=0)
    cost_so_far_usd: float | None = Field(default=None, ge=0)
    top_matches: list[TopMatch] = Field(default_factory=list, max_length=5)
    watchlist_hits: dict[str, int] = Field(default_factory=dict)
    source_health: dict[SourceName, SourceHealthEntry] = Field(default_factory=dict)
    watchlist_revision: int = Field(ge=0)


# ---- /api/pipeline/resume ----

class PipelineResumeRequest(BaseSchema):
    approved: bool
    cancel_reason: Literal["user_cancel"] | None = None

    def model_post_init(self, __ctx: object) -> None:
        if not self.approved and self.cancel_reason is None:
            raise ValueError("cancel_reason required when approved=false")


class PipelineResumeData(BaseSchema):
    run_id: RunId
    next_status: Literal["running", "cancelled"]


# ---- /api/pipeline/extend ----

class PipelineExtendData(BaseSchema):
    ttl_deadline: datetime
    ttl_extended: Literal[True]


# ---- /api/tailor/{hash_id} ----

class TailorRequest(BaseSchema):
    force: bool = False


class TailoredResume(BaseSchema):
    """Returned by tailor_resume() and serialised by the Flask tailor route."""

    hash_id: HashId
    job_title: str
    company: str
    tailored_summary: str
    tailored_bullets: list[str]
    keywords_incorporated: list[str]
    cache_key: str
    prompt_version: str
    cached: bool
    tailored_at: datetime


# Kept for backwards-compatibility — callers should use TailoredResume directly.
TailorResponseBody = TailoredResume


# ---- /api/watchlist ----

class WatchlistAddRequest(BaseSchema):
    company: CompanyName


class WatchlistResponseBody(BaseSchema):
    watchlist: list[str]
    revision: int = Field(ge=0)


# ---- /api/runs ----

class RunsPagination(BaseSchema):
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    has_more: bool


class RunsListResponse(BaseSchema):
    data: list[RunLogRow]
    pagination: RunsPagination

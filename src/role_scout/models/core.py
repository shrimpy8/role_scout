"""Shared value objects and type aliases used across Phase 2."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# ---- Primitive types ----

HashId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{16}$")]
RunId = Annotated[str, StringConstraints(pattern=r"^run_[a-f0-9\-]+$")]
RequestId = Annotated[str, StringConstraints(pattern=r"^req_[a-f0-9]{16}$")]
CompanyName = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"^[^\n\r]+$")]
JobStatus = Literal["new", "reviewed", "applied", "rejected"]
SourceName = Literal["linkedin", "google", "trueup"]
RunStatus = Literal["running", "review_pending", "completed", "failed", "cancelled", "cancelled_ttl"]
TriggerType = Literal["manual", "scheduled", "mcp", "dry_run"]
RunMode = Literal["linear", "agentic", "shadow"]
CancelReason = Literal["user_cancel", "ttl_expired", "crippled_fetch", "cost_kill_switch"]


class BaseSchema(BaseModel):
    """Base class for all Phase 2 Pydantic models. Strict, timezone-aware."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def _reject_naive_datetimes(cls, v: object) -> object:
        if isinstance(v, datetime) and v.tzinfo is None:
            raise ValueError("Naive datetimes are not permitted; use UTC (tzinfo=timezone.utc).")
        return v


# ---- Shared value objects ----

class SourceHealthEntry(BaseSchema):
    """Per-source fetch result recorded in run_log.source_health_json."""

    status: Literal["ok", "failed", "skipped", "quota_low"]
    jobs: int = Field(ge=0)
    duration_s: float = Field(ge=0)
    error: str | None = None
    raw_count: int | None = Field(default=None, ge=0)
    after_dedup: int | None = Field(default=None, ge=0)
    query_params: dict[str, str | int] | None = None


class ErrorDetail(BaseSchema):
    """Body of the `error` key in all API error responses."""

    code: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$")]
    message: str
    details: list[dict[str, str]] = Field(default_factory=list)
    request_id: RequestId | None = None
    correlation_id: RunId | None = None


class ErrorEnvelope(BaseSchema):
    """Top-level error response shape for all API endpoints."""

    error: ErrorDetail


class Meta(BaseSchema):
    """Request metadata included in all 2xx DataEnvelope responses."""

    request_id: RequestId
    correlation_id: RunId | None = None

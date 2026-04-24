"""Pydantic models mirroring SQLite rows + JSON blob shapes stored in TEXT columns."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from role_scout.models.core import (
    BaseSchema,
    CancelReason,
    HashId,
    JobStatus,
    RunId,
    RunStatus,
    SourceHealthEntry,
    SourceName,
    TriggerType,
)


# -------- JSON blobs stored in TEXT columns --------

class TailoredResumeRecord(BaseSchema):
    """Shape stored in qualified_jobs.tailored_resume (TEXT column, JSON-encoded)."""

    hash_id: HashId
    job_title: str
    company: str
    tailored_summary: Annotated[str, StringConstraints(max_length=2000)]
    tailored_bullets: list[Annotated[str, StringConstraints(max_length=400)]] = Field(
        min_length=3, max_length=10
    )
    keywords_incorporated: list[Annotated[str, StringConstraints(max_length=80)]]
    cache_key: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{16}$")]
    prompt_version: str
    resume_sha: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    tailored_at: datetime

    @field_validator("tailored_bullets")
    @classmethod
    def _bullets_non_blank(cls, v: list[str]) -> list[str]:
        if any(not b.strip() for b in v):
            raise ValueError("bullets must not be blank")
        return v


class SourceHealthBlob(BaseSchema):
    """Shape stored in run_log.source_health_json."""

    linkedin: SourceHealthEntry | None = None
    google: SourceHealthEntry | None = None
    trueup: SourceHealthEntry | None = None

    def as_dict(self) -> dict[SourceName, SourceHealthEntry]:
        return {
            k: v
            for k, v in {
                "linkedin": self.linkedin,
                "google": self.google,
                "trueup": self.trueup,
            }.items()
            if v is not None
        }


# -------- DB row projections --------

class QualifiedJobRow(BaseSchema):
    """qualified_jobs row, typed. tailored_resume is the parsed JSON blob or None."""

    hash_id: HashId
    company: str
    title: str
    location: str | None
    source: SourceName
    url: str
    apply_url: str | None
    description: str
    salary_visible: bool
    work_model: str | None
    company_stage: str | None
    match_pct: int = Field(ge=0, le=100)
    subscores: dict[str, int]
    reflection_applied: bool
    status: JobStatus
    watchlist: bool
    discovered_at: datetime
    jd_filename: str | None
    tailored_resume: TailoredResumeRecord | None = None


class RunLogRow(BaseSchema):
    """run_log row, typed. source_health is the parsed JSON blob."""

    run_id: RunId
    status: RunStatus
    trigger_type: TriggerType
    started_at: datetime
    completed_at: datetime | None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    fetched_count: int = Field(ge=0)
    qualified_count: int = Field(ge=0)
    exported_count: int = Field(ge=0)
    source_health: dict[SourceName, SourceHealthEntry] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    cancel_reason: CancelReason | None = None
    ttl_deadline: datetime | None = None
    ttl_extended: bool = False

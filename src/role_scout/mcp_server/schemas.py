"""Pydantic v2 models for all 9 MCP tool inputs, outputs, and the shared error envelope."""
from __future__ import annotations
from datetime import datetime
from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field, StringConstraints, field_validator

HashId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{16}$")]
CompanyName = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"^[^\n\r]+$")]
JobStatus = Literal["new", "reviewed", "applied", "rejected"]
RunStatus = Literal["running", "review_pending", "completed", "failed", "cancelled", "cancelled_ttl"]
SourceName = Literal["linkedin", "google", "trueup"]


# ---------------------------------------------------------------------------
# Shared error envelope
# ---------------------------------------------------------------------------

class ErrorDetail(BaseModel):
    code: Literal[
        "PIPELINE_BUSY", "JOB_NOT_FOUND", "NOT_QUALIFIED", "TAILOR_PARSE_ERROR",
        "VALIDATION_ERROR", "CLAUDE_API_ERROR", "WATCHLIST_WRITE_ERROR", "INVALID_STATUS",
        "DB_ERROR", "INTERNAL_ERROR",
    ]
    message: str
    details: list[dict[str, str]] = Field(default_factory=list)


class ToolError(BaseModel):
    """Uniform error envelope returned by every tool on failure."""
    error: ErrorDetail


# ---------------------------------------------------------------------------
# Shared value objects
# ---------------------------------------------------------------------------

class SourceHealthEntry(BaseModel):
    status: Literal["ok", "failed", "skipped", "quota_low"]
    jobs: int = Field(ge=0)
    duration_s: float = Field(ge=0)
    error: str | None = None


class JobSummary(BaseModel):
    """Compact row for list displays."""
    hash_id: HashId
    company: str
    title: str
    location: str | None = None
    source: SourceName
    match_pct: int = Field(ge=0, le=100)
    status: JobStatus
    watchlist: bool
    discovered_at: datetime
    has_tailored_resume: bool


class JobDetail(BaseModel):
    """Full job record with JD text."""
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
    match_pct: int
    subscores: dict[str, int]
    reflection_applied: bool
    status: JobStatus
    watchlist: bool
    discovered_at: datetime


class AlignmentResult(BaseModel):
    hash_id: HashId
    strong_matches: list[str]
    reframing_opportunities: list[str]
    genuine_gaps: list[str]
    summary: str
    analyzed_at: datetime
    cached: bool


class TailoredResume(BaseModel):
    hash_id: HashId
    job_title: str
    company: str
    tailored_summary: Annotated[str, StringConstraints(max_length=2000)]
    tailored_bullets: list[Annotated[str, StringConstraints(max_length=400)]] = Field(min_length=3, max_length=10)
    keywords_incorporated: list[Annotated[str, StringConstraints(max_length=80)]]
    cache_key: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{16}$")]
    prompt_version: str
    tailored_at: datetime
    cached: bool


class RunLogEntry(BaseModel):
    run_id: str
    status: RunStatus
    trigger_type: Literal["manual", "scheduled", "mcp", "dry_run"]
    started_at: datetime
    completed_at: datetime | None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    fetched_count: int = Field(ge=0)
    qualified_count: int = Field(ge=0)
    exported_count: int = Field(ge=0)
    source_health: dict[SourceName, SourceHealthEntry]
    errors: list[str]
    cancel_reason: str | None


# ---------------------------------------------------------------------------
# Tool 1: run_pipeline
# ---------------------------------------------------------------------------

class RunPipelineInput(BaseModel):
    dry_run: bool = False


class RunPipelineOutput(BaseModel):
    run_id: str
    status: RunStatus
    exported_count: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    fetched_count: int = Field(ge=0)
    qualified_count: int = Field(ge=0)
    source_health: dict[SourceName, SourceHealthEntry]


RunPipelineResponse = Union[RunPipelineOutput, ToolError]


# ---------------------------------------------------------------------------
# Tool 2: get_jobs
# ---------------------------------------------------------------------------

class GetJobsInput(BaseModel):
    status: JobStatus = "new"
    limit: int = Field(default=10, ge=1, le=100)
    source: SourceName | None = None


class GetJobsOutput(BaseModel):
    data: list[JobSummary]
    total: int


GetJobsResponse = Union[GetJobsOutput, ToolError]


# ---------------------------------------------------------------------------
# Tool 3: get_job_detail
# ---------------------------------------------------------------------------

class GetJobDetailInput(BaseModel):
    hash_id: HashId


GetJobDetailResponse = Union[JobDetail, ToolError]


# ---------------------------------------------------------------------------
# Tool 4: analyze_job
# ---------------------------------------------------------------------------

class AnalyzeJobInput(BaseModel):
    hash_id: HashId
    force: bool = False


AnalyzeJobResponse = Union[AlignmentResult, ToolError]


# ---------------------------------------------------------------------------
# Tool 5: tailor_resume
# ---------------------------------------------------------------------------

class TailorResumeInput(BaseModel):
    hash_id: HashId
    force: bool = False


TailorResumeResponse = Union[TailoredResume, ToolError]


# ---------------------------------------------------------------------------
# Tool 6: update_job_status
# ---------------------------------------------------------------------------

class UpdateJobStatusInput(BaseModel):
    hash_id: HashId
    status: JobStatus


class UpdateJobStatusOutput(BaseModel):
    ok: Literal[True]
    hash_id: HashId
    status: JobStatus


UpdateJobStatusResponse = Union[UpdateJobStatusOutput, ToolError]


# ---------------------------------------------------------------------------
# Tool 7: get_run_history
# ---------------------------------------------------------------------------

class GetRunHistoryInput(BaseModel):
    limit: int = Field(default=5, ge=1, le=50)


class GetRunHistoryOutput(BaseModel):
    data: list[RunLogEntry]


GetRunHistoryResponse = Union[GetRunHistoryOutput, ToolError]


# ---------------------------------------------------------------------------
# Tool 8: get_watchlist
# ---------------------------------------------------------------------------

class GetWatchlistInput(BaseModel):
    pass  # no arguments


class GetWatchlistOutput(BaseModel):
    watchlist: list[str]
    revision: int = Field(ge=0)


GetWatchlistResponse = Union[GetWatchlistOutput, ToolError]


# ---------------------------------------------------------------------------
# Tool 9: manage_watchlist
# ---------------------------------------------------------------------------

class ManageWatchlistInput(BaseModel):
    action: Literal["add", "remove"]
    company: CompanyName


class ManageWatchlistOutput(BaseModel):
    ok: Literal[True]
    action: Literal["add", "remove"]
    company: str
    watchlist: list[str]
    revision: int


ManageWatchlistResponse = Union[ManageWatchlistOutput, ToolError]


# ---------------------------------------------------------------------------
# Registry (for server.py iteration)
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, tuple[type[BaseModel], type]] = {
    "run_pipeline":       (RunPipelineInput,       RunPipelineResponse),
    "get_jobs":           (GetJobsInput,           GetJobsResponse),
    "get_job_detail":     (GetJobDetailInput,      GetJobDetailResponse),
    "analyze_job":        (AnalyzeJobInput,        AnalyzeJobResponse),
    "tailor_resume":      (TailorResumeInput,      TailorResumeResponse),
    "update_job_status":  (UpdateJobStatusInput,   UpdateJobStatusResponse),
    "get_run_history":    (GetRunHistoryInput,     GetRunHistoryResponse),
    "get_watchlist":      (GetWatchlistInput,      GetWatchlistResponse),
    "manage_watchlist":   (ManageWatchlistInput,   ManageWatchlistResponse),
}

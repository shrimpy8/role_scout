# MCP-SCHEMAS: Role Scout Phase 2 MCP Server Tools

| Field | Value |
|-------|-------|
| Parent | [PRD-CORE.md](./PRD-CORE.md) |
| Related | [SPEC.md §4](./SPEC.md#4-f3-mcp-server) · [TECH-DESIGN.md](./TECH-DESIGN.md) · [DATA-MODEL.md](./DATA-MODEL.md) |
| Version | 1.0 |
| Owner | [project-owner] |
| Status | Approved |
| Updated | 2026-04-23 |

> Per-tool contracts for the 9-tool MCP server exposed to Claude Code over stdio. Each tool: JSON Schema (for MCP transport), Pydantic v2 model (drop-in for `role_scout/mcp_server/schemas.py`), and example invocation.

---

## 1. Overview

- **Transport:** stdio (no network)
- **SDK:** `mcp==1.0.x` (exact minor version pinned; patch allowed) — see SPEC §4.6
- **Server entry:** `role_scout/mcp_server/server.py` (~150 LOC)
- **Registration:** `.claude.json` → see SPEC §4.5

### 1.1 Tool inventory

| # | Tool | Side effects | Calls into |
|---|------|--------------|------------|
| 1 | `run_pipeline` | Writes run_log, qualified_jobs, JD files | LangGraph with `auto_approve=True` |
| 2 | `get_jobs` | none | Phase 1 DAL `get_qualified_jobs()` |
| 3 | `get_job_detail` | none | Phase 1 DAL `get_job_by_hash_id()` |
| 4 | `analyze_job` | Writes alignment cache | Phase 1 `run_alignment()` |
| 5 | `tailor_resume` | Writes `qualified_jobs.tailored_resume` | F4 `tailor_resume()` |
| 6 | `update_job_status` | Writes `qualified_jobs.status` | Phase 1 DAL `update_job_status()` |
| 7 | `get_run_history` | none | Phase 1 DAL `get_run_logs()` |
| 8 | `get_watchlist` | none | Reads `config/watchlist.yaml` |
| 9 | `manage_watchlist` | Writes `config/watchlist.yaml` (atomic rename) | Same |

### 1.2 Error contract

Tools return structured error objects (not raised exceptions, so Claude Code can reason about them):

```python
class ToolError(BaseModel):
    error: ErrorDetail

class ErrorDetail(BaseModel):
    code: Literal[
        "PIPELINE_BUSY", "JOB_NOT_FOUND", "NOT_QUALIFIED", "VALIDATION_ERROR",
        "CLAUDE_API_ERROR", "WATCHLIST_WRITE_ERROR", "INVALID_STATUS",
        "DB_ERROR", "INTERNAL_ERROR"
    ]
    message: str
    details: list[dict[str, str]] = []
```

Every tool's output schema is `Union[<Success>, ToolError]`.

### 1.3 Single-writer lock

When `run_log.status ∈ {running, review_pending}`, `run_pipeline` immediately returns `PIPELINE_BUSY` with the active `run_id`. All other tools are DB-read or quick-write; no lock required.

### 1.4 Input hygiene

Every tool validates inputs through its Pydantic model before doing any work. Constraints (regex, enum, length) are enforced at the schema layer; invalid calls return `VALIDATION_ERROR` without touching DB or Claude.

---

## 2. Pydantic schemas (full source of `schemas.py`)

```python
"""
role_scout/mcp_server/schemas.py

Pydantic v2 models for all 9 MCP tools.
Drop-in ready; imported by role_scout/mcp_server/server.py.
"""
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
        "PIPELINE_BUSY", "JOB_NOT_FOUND", "NOT_QUALIFIED", "VALIDATION_ERROR",
        "CLAUDE_API_ERROR", "WATCHLIST_WRITE_ERROR", "INVALID_STATUS",
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
```

---

## 3. Per-tool JSON Schemas (MCP wire format)

MCP transports schemas as JSONSchema. Pydantic v2 auto-generates these via `Model.model_json_schema()`. The canonical forms are documented below; `server.py` MUST register exactly these.

### 3.1 `run_pipeline`

```json
{
  "name": "run_pipeline",
  "description": "Run the full Role Scout pipeline (fetch → score → export). Always auto-approves the HiTL interrupt because MCP tools are request/response and cannot wait for human input. Returns PIPELINE_BUSY if another run is in progress.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "dry_run": { "type": "boolean", "default": false, "description": "If true, runs fetch/score but does not persist qualified_jobs or export JDs." }
    },
    "additionalProperties": false
  }
}
```

### 3.2 `get_jobs`

```json
{
  "name": "get_jobs",
  "description": "List qualified jobs (match_pct >= threshold) filtered by status and optional source. Newest first.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "status": { "type": "string", "enum": ["new", "reviewed", "applied", "rejected"], "default": "new" },
      "limit":  { "type": "integer", "minimum": 1, "maximum": 100, "default": 10 },
      "source": { "type": ["string", "null"], "enum": ["linkedin", "google", "trueup", null] }
    },
    "additionalProperties": false
  }
}
```

### 3.3 `get_job_detail`

```json
{
  "name": "get_job_detail",
  "description": "Fetch the full record for one job including JD text and subscores.",
  "inputSchema": {
    "type": "object",
    "required": ["hash_id"],
    "properties": {
      "hash_id": { "type": "string", "pattern": "^[a-f0-9]{16}$" }
    },
    "additionalProperties": false
  }
}
```

### 3.4 `analyze_job`

```json
{
  "name": "analyze_job",
  "description": "Run (or return cached) JD-vs-resume alignment analysis. Returns strong_matches, reframing_opportunities, genuine_gaps.",
  "inputSchema": {
    "type": "object",
    "required": ["hash_id"],
    "properties": {
      "hash_id": { "type": "string", "pattern": "^[a-f0-9]{16}$" },
      "force":   { "type": "boolean", "default": false }
    },
    "additionalProperties": false
  }
}
```

### 3.5 `tailor_resume`

```json
{
  "name": "tailor_resume",
  "description": "Generate (or return cached) tailored resume content for a qualified job. Cache invalidates if resume_summary.md changes or prompt_version bumps.",
  "inputSchema": {
    "type": "object",
    "required": ["hash_id"],
    "properties": {
      "hash_id": { "type": "string", "pattern": "^[a-f0-9]{16}$" },
      "force":   { "type": "boolean", "default": false }
    },
    "additionalProperties": false
  }
}
```

### 3.6 `update_job_status`

```json
{
  "name": "update_job_status",
  "description": "Update a job's review status (new → reviewed → applied → rejected).",
  "inputSchema": {
    "type": "object",
    "required": ["hash_id", "status"],
    "properties": {
      "hash_id": { "type": "string", "pattern": "^[a-f0-9]{16}$" },
      "status":  { "type": "string", "enum": ["new", "reviewed", "applied", "rejected"] }
    },
    "additionalProperties": false
  }
}
```

### 3.7 `get_run_history`

```json
{
  "name": "get_run_history",
  "description": "Return the most recent N run_log entries with cost, source health, and status.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "limit": { "type": "integer", "minimum": 1, "maximum": 50, "default": 5 }
    },
    "additionalProperties": false
  }
}
```

### 3.8 `get_watchlist`

```json
{
  "name": "get_watchlist",
  "description": "Return the current watchlist (list of company names).",
  "inputSchema": { "type": "object", "properties": {}, "additionalProperties": false }
}
```

### 3.9 `manage_watchlist`

```json
{
  "name": "manage_watchlist",
  "description": "Add or remove a company from the watchlist. Idempotent by content.",
  "inputSchema": {
    "type": "object",
    "required": ["action", "company"],
    "properties": {
      "action":  { "type": "string", "enum": ["add", "remove"] },
      "company": { "type": "string", "minLength": 1, "maxLength": 100, "pattern": "^[^\\n\\r]+$" }
    },
    "additionalProperties": false
  }
}
```

---

## 4. Example Invocations (Claude Code conversational flow)

### 4.1 "Show my top 5 new jobs from LinkedIn"

**Claude Code → server** (`tools/call`):
```json
{ "name": "get_jobs", "arguments": { "status": "new", "limit": 5, "source": "linkedin" } }
```

**Server → Claude Code**:
```json
{
  "data": [
    { "hash_id": "a1b2c3d4e5f60718", "company": "WorkOS", "title": "Senior PM",
      "source": "linkedin", "match_pct": 88, "status": "new", "watchlist": false,
      "discovered_at": "2026-04-22T08:00:00Z", "has_tailored_resume": false }
  ],
  "total": 1
}
```

### 4.2 "Run the pipeline"

**Claude Code** → `{ "name": "run_pipeline", "arguments": {} }`

**Server** (success):
```json
{
  "run_id": "run_a1b2c3d4",
  "status": "completed",
  "exported_count": 12,
  "estimated_cost_usd": 0.84,
  "duration_s": 147.3,
  "fetched_count": 108,
  "qualified_count": 12,
  "source_health": {
    "linkedin": { "status": "ok", "jobs": 42, "duration_s": 18.2, "error": null },
    "google":   { "status": "ok", "jobs": 38, "duration_s": 12.7, "error": null },
    "trueup":   { "status": "ok", "jobs": 28, "duration_s": 3.1,  "error": null }
  }
}
```

**Server** (busy):
```json
{ "error": { "code": "PIPELINE_BUSY", "message": "Another run is active (run_id=run_a1b2c3d4, status=review_pending).", "details": [] } }
```

### 4.3 "Tailor my resume for the WorkOS role"

Typical two-step pattern Claude Code performs:

1. `get_jobs` → pick hash_id of the WorkOS job
2. `tailor_resume { hash_id: "a1b2c3d4e5f60718" }` → returns `TailoredResume`

If user then edits `resume_summary.md` and asks again:
3. Cache key changes (new resume_sha) → fresh Claude call, `cached=false`

### 4.4 "Mark Anthropic as applied"

Claude Code performs:

1. `get_jobs { status: "new" }` → find Anthropic's hash_id
2. `update_job_status { hash_id: "...", status: "applied" }`

Response:
```json
{ "ok": true, "hash_id": "b2c3d4e5f607a1b2", "status": "applied" }
```

### 4.5 "What was the cost of the last 3 runs?"

`get_run_history { limit: 3 }` → pipeline shows totals for each.

### 4.6 "Add Anthropic to my watchlist"

`manage_watchlist { action: "add", company: "Anthropic" }` →
```json
{ "ok": true, "action": "add", "company": "Anthropic",
  "watchlist": ["Anthropic", "OpenAI", "Stripe", "WorkOS"], "revision": 13 }
```

---

## 5. Server Registration & Discovery

```python
# role_scout/mcp_server/server.py  (excerpt)
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from role_scout.mcp_server.schemas import TOOL_REGISTRY
from role_scout.mcp_server.handlers import HANDLERS  # maps name → async callable

server = Server("role_scout")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name=name,
            description=TOOL_DESCRIPTIONS[name],
            inputSchema=input_model.model_json_schema(),
        )
        for name, (input_model, _) in TOOL_REGISTRY.items()
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name not in TOOL_REGISTRY:
        return _error("VALIDATION_ERROR", f"Unknown tool: {name}")
    input_model, _ = TOOL_REGISTRY[name]
    try:
        args = input_model.model_validate(arguments)
    except ValidationError as e:
        return _error("VALIDATION_ERROR", str(e), details=_format_errors(e))
    return await HANDLERS[name](args)


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
```

Invoked by `run.py --mcp`.

---

## 6. Smoke Test Contract

`scripts/mcp_smoke.py` exercises all 9 tools against a fixture DB. MUST pass before bumping `mcp` SDK version (SPEC §4.6).

Fixture:
- 3 qualified jobs, 1 with `status=new`, 1 `reviewed`, 1 `applied`
- One run_log row (completed)
- `watchlist.yaml` with 2 companies

Assertions (one per tool) covered by tests T17, T18, T19, T20, T21.

---

## 7. AI-Agent Consumption Checklist

| Requirement | Status |
|-------------|--------|
| All tools have typed inputs (Pydantic v2) + JSON Schema (MCP transport) | ✓ |
| Enums (`JobStatus`, `SourceName`, `RunStatus`) defined once, reused | ✓ |
| `hash_id` regex enforced at input layer for 4 tools that accept it | ✓ |
| Every tool returns `Union[<Success>, ToolError]` with stable error codes | ✓ |
| `run_pipeline` concurrency safety (PIPELINE_BUSY early-return) documented | ✓ |
| No tool hangs — `run_pipeline` auto-approves interrupt | ✓ (ADR-8) |
| MCP SDK version pinned exactly | ✓ (SPEC §4.6) |
| Smoke test covers all 9 tools | ✓ (§6) |

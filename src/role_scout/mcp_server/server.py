"""Role Scout MCP server — 9 tools over stdio transport.

Entry point: run_server() (called from run.py --mcp).

Tools:
  1. run_pipeline       — Execute full pipeline (auto-approve)
  2. get_jobs           — List qualified jobs
  3. get_job_detail     — Full job record
  4. analyze_job        — JD-vs-resume alignment (Phase 1 run_alignment)
  5. tailor_resume      — Tailored resume content (stub; D6)
  6. update_job_status  — Update job review status
  7. get_run_history    — Recent run_log entries
  8. get_watchlist      — List watchlist companies
  9. manage_watchlist   — Add / remove watchlist company
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from role_scout.config import Settings
from role_scout.dal import jobs_dal, watchlist_dal
from role_scout.dal.run_log_dal import get_run_logs as _get_run_logs
from role_scout.db import get_ro_conn, get_rw_conn
from role_scout.mcp_server.schemas import (
    AlignmentResult,
    AnalyzeJobInput,
    ErrorDetail,
    GetJobDetailInput,
    GetJobsInput,
    GetJobsOutput,
    GetRunHistoryInput,
    GetRunHistoryOutput,
    GetWatchlistOutput,
    JobDetail,
    JobSummary,
    ManageWatchlistInput,
    ManageWatchlistOutput,
    RunLogEntry,
    RunPipelineInput,
    RunPipelineOutput,
    SourceHealthEntry,
    TailorResumeInput,
    ToolError,
    UpdateJobStatusInput,
    UpdateJobStatusOutput,
)

log = structlog.get_logger()

server = Server("role-scout")

# ---------------------------------------------------------------------------
# Tool descriptors (JSON Schemas from MCP-SCHEMAS.md §3)
# ---------------------------------------------------------------------------

_TOOLS: list[Tool] = [
    Tool(
        name="run_pipeline",
        description=(
            "Run the full Role Scout pipeline (fetch → score → export). "
            "Always auto-approves the HiTL interrupt because MCP tools are "
            "request/response and cannot wait for human input. "
            "Returns PIPELINE_BUSY if another run is in progress."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dry_run": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "If true, runs fetch/score but does not persist "
                        "qualified_jobs or export JDs."
                    ),
                },
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_jobs",
        description="List qualified jobs (match_pct >= threshold) filtered by status and optional source. Newest first.",
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["new", "reviewed", "applied", "rejected"],
                    "default": "new",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                "source": {
                    "type": ["string", "null"],
                    "enum": ["linkedin", "google", "trueup", None],
                },
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_job_detail",
        description="Fetch the full record for one job including JD text and subscores.",
        inputSchema={
            "type": "object",
            "required": ["hash_id"],
            "properties": {
                "hash_id": {"type": "string", "pattern": "^[a-f0-9]{16}$"},
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="analyze_job",
        description="Run (or return cached) JD-vs-resume alignment analysis. Returns strong_matches, reframing_opportunities, genuine_gaps.",
        inputSchema={
            "type": "object",
            "required": ["hash_id"],
            "properties": {
                "hash_id": {"type": "string", "pattern": "^[a-f0-9]{16}$"},
                "force": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="tailor_resume",
        description="Generate (or return cached) tailored resume content for a qualified job.",
        inputSchema={
            "type": "object",
            "required": ["hash_id"],
            "properties": {
                "hash_id": {"type": "string", "pattern": "^[a-f0-9]{16}$"},
                "force": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="update_job_status",
        description="Update a job's review status (new → reviewed → applied → rejected).",
        inputSchema={
            "type": "object",
            "required": ["hash_id", "status"],
            "properties": {
                "hash_id": {"type": "string", "pattern": "^[a-f0-9]{16}$"},
                "status": {
                    "type": "string",
                    "enum": ["new", "reviewed", "applied", "rejected"],
                },
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_run_history",
        description="Return the most recent N run_log entries with cost, source health, and status.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_watchlist",
        description="Return the current watchlist (list of company names).",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    Tool(
        name="manage_watchlist",
        description="Add or remove a company from the watchlist. Idempotent by content.",
        inputSchema={
            "type": "object",
            "required": ["action", "company"],
            "properties": {
                "action": {"type": "string", "enum": ["add", "remove"]},
                "company": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 100,
                    "pattern": r"^[^\n\r]+$",
                },
            },
            "additionalProperties": False,
        },
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(payload: Any) -> CallToolResult:
    """Wrap a Pydantic model (or dict) as a successful tool result."""
    if hasattr(payload, "model_dump_json"):
        text = payload.model_dump_json()
    else:
        text = json.dumps(payload)
    return CallToolResult(content=[TextContent(type="text", text=text)])


def _err(code: str, message: str, details: list[dict[str, str]] | None = None) -> CallToolResult:
    err = ToolError(error=ErrorDetail(code=code, message=message, details=details or []))
    return CallToolResult(content=[TextContent(type="text", text=err.model_dump_json())], isError=True)


def _is_pipeline_busy(conn: Any) -> tuple[bool, str]:
    """Return (True, run_id) if another run is active."""
    row = conn.execute(
        "SELECT run_id FROM run_log WHERE status IN ('running','review_pending') LIMIT 1"
    ).fetchone()
    if row:
        return True, row[0]
    return False, ""


# ---------------------------------------------------------------------------
# MCP handlers
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[Tool]:
    return _TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    try:
        return await _dispatch(name, arguments)
    except Exception:
        log.exception("mcp_tool_unhandled", tool=name)
        return _err("INTERNAL_ERROR", f"Unhandled error in tool '{name}'")


async def _dispatch(name: str, arguments: dict[str, Any]) -> CallToolResult:
    settings = Settings()

    if name == "run_pipeline":
        return await _run_pipeline(arguments, settings)
    if name == "get_jobs":
        return _tool_get_jobs(arguments, settings)
    if name == "get_job_detail":
        return _tool_get_job_detail(arguments, settings)
    if name == "analyze_job":
        return _tool_analyze_job(arguments, settings)
    if name == "tailor_resume":
        return _tool_tailor_resume(arguments, settings)
    if name == "update_job_status":
        return _tool_update_job_status(arguments, settings)
    if name == "get_run_history":
        return _tool_get_run_history(arguments, settings)
    if name == "get_watchlist":
        return _tool_get_watchlist()
    if name == "manage_watchlist":
        return _tool_manage_watchlist(arguments)
    return _err("VALIDATION_ERROR", f"Unknown tool: {name!r}")


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def _run_pipeline(arguments: dict[str, Any], settings: Settings) -> CallToolResult:
    inp = RunPipelineInput.model_validate(arguments)
    conn = get_ro_conn(settings.DB_PATH)
    try:
        busy, active_run_id = _is_pipeline_busy(conn)
    finally:
        conn.close()

    if busy:
        return _err(
            "PIPELINE_BUSY",
            f"Another run is active (run_id={active_run_id}, status=running).",
        )

    from role_scout.runner import run_graph

    t0 = time.monotonic()
    # Run in thread to avoid blocking the MCP event loop
    state = await asyncio.to_thread(
        run_graph,
        trigger_type="mcp",
        auto_approve=True,
        dry_run=inp.dry_run,
    )
    duration_s = round(time.monotonic() - t0, 2)

    health_raw: dict = state.get("source_health", {})
    source_health = {
        src: SourceHealthEntry(
            status=e.status if hasattr(e, "status") else e.get("status", "ok"),
            jobs=e.jobs if hasattr(e, "jobs") else e.get("jobs", 0),
            duration_s=e.duration_s if hasattr(e, "duration_s") else e.get("duration_s", 0.0),
            error=e.error if hasattr(e, "error") else e.get("error"),
        )
        for src, e in health_raw.items()
        if src in {"linkedin", "google", "trueup"}
    }

    out = RunPipelineOutput(
        run_id=state.get("run_id", ""),
        status=state.get("run_status", "completed"),
        exported_count=int(state.get("exported_count", 0)),
        estimated_cost_usd=float(state.get("total_cost_usd", 0.0)),
        duration_s=duration_s,
        fetched_count=sum(state.get("source_counts", {}).values()),
        qualified_count=sum(
            1 for j in state.get("scored_jobs", [])
            if j.match_pct >= settings.SCORE_THRESHOLD
        ),
        source_health=source_health,
    )
    return _ok(out)


def _tool_get_jobs(arguments: dict[str, Any], settings: Settings) -> CallToolResult:
    try:
        inp = GetJobsInput.model_validate(arguments)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    conn = get_ro_conn(settings.DB_PATH)
    try:
        # Map MCP source name to Phase 1 source name (google → google_jobs)
        p1_source = "google_jobs" if inp.source == "google" else inp.source

        from role_scout.compat.db.qualified_jobs import get_qualified_jobs as _p1_get
        jobs = _p1_get(conn, status=inp.status, limit=inp.limit, source=p1_source)
    finally:
        conn.close()

    summaries = [
        JobSummary(
            hash_id=j.hash_id,
            company=j.company,
            title=j.title,
            location=j.location,
            source=("google" if j.source == "google_jobs" else j.source),  # type: ignore[arg-type]
            match_pct=j.match_pct,
            status=j.status,  # type: ignore[arg-type]
            watchlist=j.is_watchlist,
            discovered_at=j.scored_at if j.scored_at.tzinfo else j.scored_at.replace(tzinfo=timezone.utc),
            has_tailored_resume=bool(getattr(j, "tailored_resume", None)),
        )
        for j in jobs
    ]
    return _ok(GetJobsOutput(data=summaries, total=len(summaries)))


def _tool_get_job_detail(arguments: dict[str, Any], settings: Settings) -> CallToolResult:
    try:
        inp = GetJobDetailInput.model_validate(arguments)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    conn = get_ro_conn(settings.DB_PATH)
    try:
        job = jobs_dal.get_job_detail(conn, inp.hash_id)
    finally:
        conn.close()

    if job is None:
        return _err("JOB_NOT_FOUND", f"No job found with hash_id={inp.hash_id!r}")

    discovered_at = job.scored_at if job.scored_at.tzinfo else job.scored_at.replace(tzinfo=timezone.utc)
    detail = JobDetail(
        hash_id=job.hash_id,
        company=job.company,
        title=job.title,
        location=job.location,
        source=("google" if job.source == "google_jobs" else job.source),  # type: ignore[arg-type]
        url=job.url or "",
        apply_url=getattr(job, "apply_url", None),
        description=job.description or "",
        salary_visible=job.salary_visible,
        work_model=job.work_model,
        company_stage=getattr(job, "company_stage", None),
        match_pct=job.match_pct,
        subscores={
            "seniority": getattr(job, "seniority_score", 0) or 0,
            "domain": getattr(job, "domain_score", 0) or 0,
            "location": getattr(job, "location_score", 0) or 0,
            "stage": getattr(job, "stage_score", 0) or 0,
            "comp": getattr(job, "comp_score", 0) or 0,
        },
        reflection_applied=False,
        status=job.status,  # type: ignore[arg-type]
        watchlist=job.is_watchlist,
        discovered_at=discovered_at,
    )
    return _ok(detail)


def _tool_analyze_job(arguments: dict[str, Any], settings: Settings) -> CallToolResult:
    try:
        inp = AnalyzeJobInput.model_validate(arguments)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    conn = get_ro_conn(settings.DB_PATH)
    try:
        job = jobs_dal.get_job_detail(conn, inp.hash_id)
    finally:
        conn.close()

    if job is None:
        return _err("JOB_NOT_FOUND", f"No job found with hash_id={inp.hash_id!r}")

    # Return cached alignment if present and not force-refresh
    cached_json: str | None = getattr(job, "jd_alignment", None)
    if cached_json and not inp.force:
        try:
            cached = json.loads(cached_json)
            result = AlignmentResult(
                hash_id=inp.hash_id,
                strong_matches=cached.get("strong_matches", []),
                reframing_opportunities=cached.get("reframing_opportunities", []),
                genuine_gaps=cached.get("genuine_gaps", []),
                summary=cached.get("summary", ""),
                analyzed_at=datetime.now(timezone.utc),
                cached=True,
            )
            return _ok(result)
        except Exception:
            pass  # fall through to fresh analysis

    try:
        from role_scout.compat.pipeline.alignment import run_alignment
        raw_json = run_alignment(job)
        parsed = json.loads(raw_json)
    except ValueError as exc:
        return _err("JOB_NOT_FOUND", str(exc))
    except Exception as exc:
        log.exception("analyze_job_failed", hash_id=inp.hash_id)
        return _err("CLAUDE_API_ERROR", f"Alignment analysis failed: {exc}")

    result = AlignmentResult(
        hash_id=inp.hash_id,
        strong_matches=parsed.get("strong_matches", []),
        reframing_opportunities=parsed.get("reframing_opportunities", []),
        genuine_gaps=parsed.get("genuine_gaps", []),
        summary=parsed.get("summary", ""),
        analyzed_at=datetime.now(timezone.utc),
        cached=False,
    )

    # Persist to DB
    try:
        rw_conn = get_rw_conn(settings.DB_PATH)
        from role_scout.compat.db.qualified_jobs import update_jd_alignment
        update_jd_alignment(rw_conn, inp.hash_id, raw_json)
        rw_conn.commit()
        rw_conn.close()
    except Exception:
        log.exception("analyze_job_persist_failed", hash_id=inp.hash_id)

    return _ok(result)


def _tool_tailor_resume(arguments: dict[str, Any], settings: Settings) -> CallToolResult:
    try:
        inp = TailorResumeInput.model_validate(arguments)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    from role_scout.tailor import NotQualifiedError, TailorParseError, tailor_resume

    conn = get_rw_conn(settings.DB_PATH)
    try:
        result = tailor_resume(
            conn,
            inp.hash_id,
            qualify_threshold=settings.SCORE_THRESHOLD,
            force=inp.force,
            api_key=settings.ANTHROPIC_API_KEY,
            model=settings.CLAUDE_MODEL,
            max_cost=settings.MAX_COST_USD,
            input_cost_per_mtok=settings.CLAUDE_INPUT_COST_PER_MTOK,
            output_cost_per_mtok=settings.CLAUDE_OUTPUT_COST_PER_MTOK,
        )
    except NotQualifiedError as exc:
        conn.close()
        return _err("NOT_QUALIFIED", str(exc))
    except TailorParseError as exc:
        conn.close()
        return _err("TAILOR_PARSE_ERROR", str(exc))
    except Exception as exc:
        conn.close()
        log.exception("tailor_resume_failed", hash_id=inp.hash_id)
        return _err("INTERNAL_ERROR", str(exc))
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return _ok(result.model_dump(mode="json"))


def _tool_update_job_status(arguments: dict[str, Any], settings: Settings) -> CallToolResult:
    try:
        inp = UpdateJobStatusInput.model_validate(arguments)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    conn = get_rw_conn(settings.DB_PATH)
    try:
        jobs_dal.set_job_status(conn, inp.hash_id, inp.status)
    except ValueError as exc:
        conn.close()
        return _err("INVALID_STATUS", str(exc))
    except KeyError:
        conn.close()
        return _err("JOB_NOT_FOUND", f"No job found with hash_id={inp.hash_id!r}")
    except Exception as exc:
        conn.close()
        log.exception("update_job_status_failed", hash_id=inp.hash_id)
        return _err("DB_ERROR", str(exc))
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return _ok(UpdateJobStatusOutput(ok=True, hash_id=inp.hash_id, status=inp.status))


def _tool_get_run_history(arguments: dict[str, Any], settings: Settings) -> CallToolResult:
    try:
        inp = GetRunHistoryInput.model_validate(arguments)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    conn = get_ro_conn(settings.DB_PATH)
    try:
        rows, _ = _get_run_logs(conn, limit=inp.limit)
    finally:
        conn.close()

    entries = [
        RunLogEntry(
            run_id=r.run_id,
            status=r.status,  # type: ignore[arg-type]
            trigger_type=r.trigger_type,  # type: ignore[arg-type]
            started_at=r.started_at,
            completed_at=r.completed_at,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            estimated_cost_usd=r.estimated_cost_usd,
            fetched_count=r.fetched_count,
            qualified_count=r.qualified_count,
            exported_count=r.exported_count,
            source_health={
                src: SourceHealthEntry(
                    status=e.status,
                    jobs=e.jobs,
                    duration_s=e.duration_s,
                    error=e.error,
                )
                for src, e in r.source_health.items()
                if src in {"linkedin", "google", "trueup"}
            },
            errors=r.errors,
            cancel_reason=r.cancel_reason,  # type: ignore[arg-type]
        )
        for r in rows
    ]
    return _ok(GetRunHistoryOutput(data=entries))


def _tool_get_watchlist() -> CallToolResult:
    companies = watchlist_dal.get_watchlist()
    return _ok(GetWatchlistOutput(watchlist=companies, revision=len(companies)))


def _tool_manage_watchlist(arguments: dict[str, Any]) -> CallToolResult:
    try:
        inp = ManageWatchlistInput.model_validate(arguments)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    try:
        if inp.action == "add":
            updated = watchlist_dal.add_to_watchlist(inp.company)
        else:
            updated = watchlist_dal.remove_from_watchlist(inp.company)
    except Exception as exc:
        log.exception("manage_watchlist_failed", action=inp.action, company=inp.company)
        return _err("WATCHLIST_WRITE_ERROR", str(exc))

    from role_scout.mcp_server.schemas import ManageWatchlistOutput
    return _ok(ManageWatchlistOutput(
        ok=True,
        action=inp.action,
        company=inp.company,
        watchlist=updated,
        revision=len(updated),
    ))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def run_server() -> None:
    """Start the MCP server on stdio (blocking)."""
    asyncio.run(_main())

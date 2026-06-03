"""Phase 2 Flask routes — tailor, pipeline status, watchlist CRUD, HiTL resume/extend, index.

All routes bind to 127.0.0.1 (enforced in run.py --serve, not here).
All write routes require CSRF token (Flask-WTF).
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import re
import sqlite3
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any

import structlog
from flask import Blueprint, Response, current_app, g, jsonify, render_template, request, send_file, send_from_directory

from role_scout.compat.db.qualified_jobs import (
    get_job_by_hash_id,
    get_job_count_by_source,
    get_job_count_by_status,
    get_qualified_jobs,
    insert_qualified_job,
    update_jd_alignment,
    update_job_status,
)
from role_scout.compat.db.seen_hashes import upsert_seen_hash
from role_scout.compat.models import ScoredJob as _ScoredJobModel
from pydantic import ValidationError as _PydanticValidationError
from role_scout.config import Settings
from role_scout.dal import donotapply_dal, watchlist_dal
from role_scout.dal.run_log_dal import get_run_logs
from role_scout.db import get_ro_conn, get_rw_conn, ro_conn, rw_conn
from role_scout.watchlist_state import current_revision, next_revision

log = structlog.get_logger()

_TOP_MATCHES_LIMIT = 3
_PAGINATION_MAX_LIMIT = 50
_PAGINATION_DEFAULT_LIMIT = 10
_JOBS_LISTING_LIMIT = 200
_MAX_COMPANY_NAME_LENGTH = 100
_VALID_STATUSES = {"new", "reviewed", "applied", "rejected", "not_a_fit", "not_available"}
_VALID_SORT_COLS = {"match_pct", "company", "title", "city", "work_model", "company_stage", "status", "scored_at", "posted_date"}
_VALID_DIRS = {"asc", "desc"}

_HASH_ID_RE = re.compile(r"^[a-f0-9]{16}$")
_RELATIVE_DATE_RE = re.compile(r"(\d+)\s+(hour|hours|day|days|week|weeks|month|months)\s+ago", re.IGNORECASE)


def _parse_days_since_posted(raw: str, today: Any) -> int | None:
    """Return days since posting from ISO date or relative string, or None."""
    if not raw:
        return None
    # ISO date / datetime (e.g. "2026-04-27")
    try:
        from datetime import date as _date
        posted = datetime.fromisoformat(raw).date()
        return (today - posted).days
    except (ValueError, TypeError):
        pass
    # Relative strings: "N days ago", "N hours ago", "N weeks ago", etc.
    m = _RELATIVE_DATE_RE.search(raw)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        if "hour" in unit:
            return 0
        if "day" in unit:
            return n
        if "week" in unit:
            return n * 7
        if "month" in unit:
            return n * 30
    if re.search(r"\byesterday\b", raw, re.IGNORECASE):
        return 1
    if re.search(r"\btoday\b|\bjust now\b|\ban? hour", raw, re.IGNORECASE):
        return 0
    return None


def _get_settings() -> Settings:
    """Return the app-scoped Settings instance (initialised once at startup)."""
    return current_app.config["RS_SETTINGS"]


def _validate_hash_id(hash_id: str):
    """Return None if valid, or a (response, status_code) tuple if invalid."""
    if not _HASH_ID_RE.match(hash_id):
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": "hash_id must be 16 hex chars", "details": []}}), 422
    return None


def jsonify_ok(data: dict[str, Any], **meta: Any):
    """Wrap data in the standard {data, meta} API response envelope (API-SPEC §1.5)."""
    return jsonify({
        "data": data,
        "meta": {"request_id": getattr(g, "request_id", None), **meta},
    })


def jsonify_error(
    code: str,
    message: str,
    status: int = 400,
    details: list[Any] | None = None,
) -> tuple[Any, int]:
    """Return a standard error envelope with request_id (API-SPEC §1.5).

    Always includes ``g.request_id`` so callers can correlate errors with server logs.
    """
    return jsonify({
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
        },
        "meta": {"request_id": getattr(g, "request_id", None)},
    }), status


bp = Blueprint("role_scout", __name__, url_prefix="")


# ---------------------------------------------------------------------------
# POST /api/tailor/<hash_id>
# ---------------------------------------------------------------------------

@bp.route("/api/tailor/<hash_id>", methods=["POST"])
def tailor_route(hash_id: str):
    """Generate (or return cached) tailored resume content.

    Request body (JSON, optional):
        force (bool): If true, bypass cache.

    Returns 200 + TailoredResume JSON on success.
    Returns 400 NOT_QUALIFIED if job below threshold or not found.
    Returns 404 JOB_NOT_FOUND if hash_id is not in DB.
    Returns 422 VALIDATION_ERROR for bad hash_id format.
    Returns 500 CLAUDE_API_ERROR on upstream failures.
    """
    err = _validate_hash_id(hash_id)
    if err:
        return err
    corr_id = str(uuid.uuid4())
    bound_log = log.bind(correlation_id=corr_id, hash_id=hash_id)

    body = request.get_json(silent=True) or {}
    force = bool(body.get("force", False))

    settings = _get_settings()
    try:
        from role_scout.tailor import NotQualifiedError, TailorParseError, tailor_resume  # deferred: circular import
        with rw_conn(settings.DB_PATH) as conn:
            result = tailor_resume(
                conn,
                hash_id,
                qualify_threshold=settings.SCORE_THRESHOLD,
                force=force,
                api_key=settings.ANTHROPIC_API_KEY,
                model=settings.CLAUDE_MODEL,
                max_cost=settings.MAX_COST_USD,
                input_cost_per_mtok=settings.CLAUDE_INPUT_COST_PER_MTOK,
                output_cost_per_mtok=settings.CLAUDE_OUTPUT_COST_PER_MTOK,
                correlation_id=corr_id,
            )
    except NotQualifiedError as exc:
        bound_log.warning("tailor_route.not_qualified", reason=str(exc))
        return jsonify({"error": {"code": "NOT_QUALIFIED", "message": "Job does not meet the qualification threshold for tailoring", "details": []}}), 400
    except TailorParseError as exc:
        bound_log.error("tailor_route.parse_error", reason=str(exc))
        return jsonify({"error": {"code": "CLAUDE_API_ERROR", "message": "Claude returned an unparseable response — please try again", "details": []}}), 500
    except Exception:
        bound_log.exception("tailor_route.error")
        return jsonify({"error": {"code": "CLAUDE_API_ERROR", "message": "Tailoring failed due to an unexpected error. Please try again — if this keeps happening, check your ANTHROPIC_API_KEY in .env.", "details": []}}), 500

    bound_log.info("tailor_route.ok", cached=result.cached)
    return jsonify(result.model_dump(mode="json")), 200


# ---------------------------------------------------------------------------
# POST /api/status/<hash_id>
# ---------------------------------------------------------------------------

@bp.route("/api/status/<hash_id>", methods=["POST"])
def status_update(hash_id: str):
    """Inline status update for a qualified job.

    Body: {"status": "reviewed" | "applied" | "rejected" | "new"}
    Returns 200 + {hash_id, status, updated} on success.
    Returns 400 on invalid status, 404 if job not found.
    """
    err = _validate_hash_id(hash_id)
    if err:
        return err

    body = request.get_json(silent=True) or {}
    new_status = body.get("status", "").strip()
    if new_status not in _VALID_STATUSES:
        return jsonify({"error": {"code": "INVALID_STATUS", "message": f"status must be one of: {', '.join(sorted(_VALID_STATUSES))}", "details": []}}), 400

    settings = _get_settings()
    try:
        with rw_conn(settings.DB_PATH) as conn:
            old_status = update_job_status(conn, hash_id, new_status)
            conn.commit()
    except Exception:
        log.exception("status_update.error", hash_id=hash_id)
        return jsonify({"error": {"code": "DB_ERROR", "message": "Failed to update status", "details": []}}), 500

    if old_status is None:
        return jsonify({"error": {"code": "NOT_FOUND", "message": "Job not found", "details": []}}), 404

    log.info("status_update.ok", hash_id=hash_id, old_status=old_status, new_status=new_status)
    return jsonify_ok({"hash_id": hash_id, "status": new_status, "updated": True}), 200


# ---------------------------------------------------------------------------
# POST /api/alignment/<hash_id>
# ---------------------------------------------------------------------------

@bp.route("/api/alignment/<hash_id>", methods=["POST"])
def alignment_run(hash_id: str):
    """Run (or return cached) JD alignment analysis via Claude.

    Body: {"force": false}  — set true to bypass cache.
    Returns 200 + {hash_id, jd_alignment (JSON string), cached} on success.
    Returns 404 if job not found, 422 if no description or resume missing.
    Returns 500 on Claude error.
    """
    err = _validate_hash_id(hash_id)
    if err:
        return err

    body = request.get_json(silent=True) or {}
    force = bool(body.get("force", False))

    settings = _get_settings()

    with ro_conn(settings.DB_PATH) as conn:
        job = get_job_by_hash_id(conn, hash_id)

    if job is None:
        return jsonify({"error": {"code": "NOT_FOUND", "message": "Job not found", "details": []}}), 404

    if not force and job.jd_alignment:
        try:
            parsed_cached = json.loads(job.jd_alignment)
        except (json.JSONDecodeError, TypeError):
            log.warning("alignment_route.cached_corrupt", hash_id=hash_id, raw=job.jd_alignment[:200])
            parsed_cached = {}
        return jsonify_ok({"hash_id": hash_id, "cached": True, **parsed_cached}), 200

    if not job.description:
        return jsonify({"error": {"code": "NO_DESCRIPTION", "message": "No job description available to analyze", "details": []}}), 422

    resume_path = settings.RESUME_SUMMARY_PATH
    if not resume_path.exists():
        return jsonify({"error": {"code": "RESUME_MISSING", "message": "config/resume_summary.md not found — place your resume summary there", "details": []}}), 422

    prompt_path = Path(__file__).parent.parent / "prompts" / "alignment_system.md"
    if not prompt_path.exists():
        return jsonify({"error": {"code": "PROMPT_MISSING", "message": "alignment_system.md prompt not found", "details": []}}), 500

    try:
        resume_text = resume_path.read_text(encoding="utf-8")
        prompt_template = prompt_path.read_text(encoding="utf-8")
    except OSError:
        log.exception("alignment_run.file_read_error", hash_id=hash_id)
        return jsonify({"error": {"code": "FILE_ERROR", "message": "Could not read required files", "details": []}}), 500

    # System prompt contains only static instructions; untrusted JD/resume data
    # is passed as XML-delimited content in the user message to prevent prompt injection.
    system_prompt = Template(prompt_template).safe_substitute(
        title=job.title,
        company=job.company,
        source=job.source,
    )

    user_message = (
        "Analyze this job for alignment with the candidate's resume.\n\n"
        "<resume_summary>\n"
        f"{resume_text}\n"
        "</resume_summary>\n\n"
        "<job_description>\n"
        f"{(job.description or '')[:2000]}\n"
        "</job_description>"
    )

    try:
        from role_scout.claude_client import call_claude  # deferred: circular import
        text, _in_tok, _out_tok = call_claude(
            system=system_prompt,
            user=user_message,
            api_key=settings.ANTHROPIC_API_KEY,
            model=settings.CLAUDE_MODEL,
            accumulated_cost=0.0,
            max_cost=settings.MAX_COST_USD,
            input_cost_per_mtok=settings.CLAUDE_INPUT_COST_PER_MTOK,
            output_cost_per_mtok=settings.CLAUDE_OUTPUT_COST_PER_MTOK,
        )
    except Exception:
        log.exception("alignment_run.claude_error", hash_id=hash_id)
        return jsonify({"error": {"code": "CLAUDE_ERROR", "message": "Claude API call failed — check your ANTHROPIC_API_KEY", "details": []}}), 500

    # Validate we got JSON back
    try:
        raw = text.strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("No JSON object in Claude response")
        alignment_json = raw[start:end + 1]
        json.loads(alignment_json)  # validate parseable
    except (ValueError, json.JSONDecodeError) as exc:
        log.error("alignment_run.parse_error", hash_id=hash_id, error=str(exc))
        return jsonify({"error": {"code": "PARSE_ERROR", "message": "Claude returned non-JSON — try again", "details": []}}), 500

    try:
        with rw_conn(settings.DB_PATH) as conn:
            update_jd_alignment(conn, hash_id, alignment_json)
            conn.commit()
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        log.exception("alignment_run.db_write_error", hash_id=hash_id)
        # Return result even if cache write fails

    log.info("alignment_run.ok", hash_id=hash_id)
    parsed_result = json.loads(alignment_json)
    return jsonify_ok({"hash_id": hash_id, "cached": False, **parsed_result}), 200


# ---------------------------------------------------------------------------
# GET /api/jd/download/<hash_id>  — download JD with friendly filename
# ---------------------------------------------------------------------------

_FILENAME_UNSAFE_RE = re.compile(r"[^\w\-]")


def _safe_filename_part(value: str) -> str:
    """Replace whitespace with underscores, strip non-word chars."""
    return _FILENAME_UNSAFE_RE.sub("_", value.replace(" ", "_")).strip("_") or "unknown"


@bp.route("/api/jd/download/<hash_id>", methods=["GET"])
def jd_download_by_hash(hash_id: str):
    """Download a stored JD as Company_RoleName_JD.txt."""
    err = _validate_hash_id(hash_id)
    if err:
        return err

    with ro_conn(_get_settings().DB_PATH) as conn:
        job = get_job_by_hash_id(conn, hash_id)

    if job is None:
        return jsonify({"error": {"code": "NOT_FOUND", "message": "Job not found", "details": []}}), 404

    dl_name = f"{_safe_filename_part(job.company)}_{_safe_filename_part(job.title)}_JD.txt"
    log.info("jd_download_by_hash", hash_id=hash_id, dl_name=dl_name)

    # Prefer file on disk if written; fall back to description stored in DB
    jd_content: str | None = None
    if job.jd_filename:
        settings = _get_settings()
        jd_dir = (Path(settings.DB_PATH).parent / "jds").resolve()
        requested = (jd_dir / job.jd_filename).resolve()
        if str(requested).startswith(str(jd_dir) + "/") and requested.exists():
            jd_content = requested.read_text(encoding="utf-8", errors="replace")

    if jd_content is None:
        if not job.description:
            return jsonify({"error": {"code": "NOT_FOUND", "message": "No JD stored for this job", "details": []}}), 404
        jd_content = job.description

    url_header = f"Job Posting URL: {job.url}\n{'=' * 60}\n\n" if job.url else ""
    combined = url_header + jd_content

    return send_file(
        io.BytesIO(combined.encode("utf-8")),
        mimetype="text/plain",
        as_attachment=True,
        download_name=dl_name,
    )


# ---------------------------------------------------------------------------
# GET /jds/<filename>
# ---------------------------------------------------------------------------

@bp.route("/jds/<path:filename>", methods=["GET"])
def jd_download(filename: str):
    """Download a JD text file by name.

    Path traversal protected: rejects '..' and absolute paths.
    """
    settings = _get_settings()
    jd_dir = (Path(settings.DB_PATH).parent / "jds").resolve()
    requested = (jd_dir / filename).resolve()
    if not str(requested).startswith(str(jd_dir) + "/"):
        return jsonify({"error": {"code": "INVALID_PATH", "message": "Invalid filename", "details": []}}), 400
    if not requested.exists():
        return jsonify({"error": {"code": "NOT_FOUND", "message": "JD file not found", "details": []}}), 404

    return send_from_directory(str(jd_dir), filename, as_attachment=True, mimetype="text/plain")


# ---------------------------------------------------------------------------
# GET /api/jd/download-reviewed-zip  — bulk ZIP of all reviewed JDs
# ---------------------------------------------------------------------------

def _reviewed_zip_entry_name(job: Any) -> str:
    """Build a safe per-job filename for use inside the ZIP."""
    company = _safe_filename_part(job.company or "Unknown")[:30]
    title = _safe_filename_part(job.title or "Unknown")[:40]
    return f"{company}_{title}_{job.hash_id[:8]}.txt"


def _build_reviewed_zip_manifest(
    included: list[tuple[Any, str]],
    missing: list[Any],
    generated_at: str,
) -> str:
    total = len(included) + len(missing)
    lines = [
        "Role Scout — Reviewed JDs",
        f"Generated : {generated_at}",
        f"Total reviewed : {total}  |  Included in ZIP : {len(included)}  |  Not available : {len(missing)}",
        "",
    ]
    if included:
        lines += [f"INCLUDED ({len(included)})", "=" * 60]
        for i, (job, fname) in enumerate(included, 1):
            comp_range = job.comp_range or "—"
            city = job.city or job.location or "—"
            lines += [
                f" {i:3d}. {fname}",
                f"       Company  : {job.company}",
                f"       Role     : {job.title}",
                f"       Location : {city}  |  Model : {job.work_model}  |  Match : {job.match_pct}%  |  Comp : {comp_range}",
                f"       URL      : {job.url or '—'}",
                "",
            ]
    if missing:
        lines += [f"NOT AVAILABLE ({len(missing)})", "=" * 60]
        for i, job in enumerate(missing, 1):
            city = job.city or job.location or "—"
            lines += [
                f" {i:3d}. {job.company}  |  {job.title}  |  {city}  |  {job.work_model}  |  {job.match_pct}%",
                f"       URL    : {job.url or '—'}",
                f"       Reason : No JD text stored",
                "",
            ]
    return "\n".join(lines)


@bp.route("/api/jd/download-reviewed-zip", methods=["GET"])
def jd_download_reviewed_zip():
    """Build and stream a ZIP of all reviewed jobs' JDs.

    Each file is Company_Role_hashid8.txt.  A manifest.txt summarises what
    was included and what was unavailable.  No CSRF required (read-only GET).
    """
    settings = _get_settings()
    try:
        with ro_conn(settings.DB_PATH) as conn:
            jobs = get_qualified_jobs(conn, status="reviewed", limit=200)
    except Exception:
        log.exception("jd_zip_db_error")
        return jsonify({"error": {"code": "DB_ERROR", "message": "Failed to load reviewed jobs", "details": []}}), 500

    if not jobs:
        return jsonify({"error": {"code": "NO_REVIEWED_JOBS", "message": "No reviewed jobs found", "details": []}}), 404

    jd_dir = (Path(settings.DB_PATH).parent / "jds").resolve()
    included: list[tuple[Any, str]] = []
    missing: list[Any] = []

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for job in jobs:
            content: str | None = None
            entry_name = _reviewed_zip_entry_name(job)

            if job.jd_filename:
                candidate = (jd_dir / job.jd_filename).resolve()
                if str(candidate).startswith(str(jd_dir) + "/") and candidate.is_file():
                    try:
                        content = candidate.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        pass

            if content is None and job.description:
                content = job.description

            if content:
                url_header = f"Job Posting URL: {job.url}\n{'=' * 60}\n\n" if job.url else ""
                zf.writestr(entry_name, url_header + content)
                included.append((job, entry_name))
            else:
                missing.append(job)

        generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        zf.writestr("manifest.txt", _build_reviewed_zip_manifest(included, missing, generated_at))

    log.info("jd_zip_built", included=len(included), missing=len(missing))
    buf.seek(0)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"reviewed_jds_{today}.zip",
    )


# ---------------------------------------------------------------------------
# GET /api/pipeline/status
# ---------------------------------------------------------------------------

@bp.route("/api/pipeline/status", methods=["GET"])
def pipeline_status():
    """Return current pipeline run status for 5s polling by the HiTL banner.

    Returns the latest run_log row fields:
        run_id, status, qualified_count, top_3_matches, ttl_remaining_s,
        watchlist_hits, watchlist_revision, source_health, estimated_cost_usd.
    """
    settings = _get_settings()
    with ro_conn(settings.DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT run_id, status, trigger_type, started_at, completed_at,
                   COALESCE(total_qualified, 0) AS qualified_count,
                   COALESCE(estimated_cost_usd, 0.0) AS estimated_cost_usd,
                   source_health_json, ttl_deadline, cancel_reason,
                   COALESCE(ttl_extended, 0) AS ttl_extended
            FROM run_log
            ORDER BY started_at DESC LIMIT 1
            """,
        ).fetchone()

        if row is None:
            return jsonify({"status": "idle", "run_id": None}), 200

        top3_rows = conn.execute(
            "SELECT title, company, match_pct FROM qualified_jobs ORDER BY match_pct DESC LIMIT ?",
            (_TOP_MATCHES_LIMIT,),
        ).fetchall()

    d = dict(row)
    ttl_remaining_s = None
    if d.get("ttl_deadline") and d.get("status") == "review_pending":
        try:
            deadline = datetime.fromisoformat(d["ttl_deadline"]).replace(tzinfo=UTC)
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            ttl_remaining_s = max(0, int(remaining))
        except (ValueError, TypeError, KeyError):
            log.warning("pipeline_status.ttl_parse_failed", raw=d.get("ttl_deadline"))

    top3_matches = [
        {"title": r["title"], "company": r["company"], "match_pct": r["match_pct"]}
        for r in top3_rows
    ]

    source_health: list[dict[str, Any]] = []
    if d.get("source_health_json"):
        try:
            raw = json.loads(d["source_health_json"])
            for name, entry in raw.items():
                if entry is not None:
                    source_health.append({"name": name, **entry})
        except (json.JSONDecodeError, AttributeError):
            log.warning("pipeline_status.source_health_parse_failed", raw=d["source_health_json"][:200])

    resp = jsonify({
        "run_id": d["run_id"],
        "status": d["status"],
        "qualified_count": d["qualified_count"],
        "estimated_cost_usd": d["estimated_cost_usd"],
        "ttl_remaining_s": ttl_remaining_s,
        "ttl_extended": bool(d.get("ttl_extended")),
        "cancel_reason": d.get("cancel_reason"),
        "watchlist_revision": current_revision(),
        "top_3_matches": top3_matches,
        "source_health": source_health,
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp, 200


# ---------------------------------------------------------------------------
# GET /api/watchlist
# ---------------------------------------------------------------------------

@bp.route("/api/watchlist", methods=["GET"])
def watchlist_get():
    """Return the current watchlist."""
    try:
        current = watchlist_dal.get_watchlist()
    except OSError:
        log.exception("watchlist_get.error")
        return jsonify({"error": {"code": "WATCHLIST_READ_ERROR", "message": "Failed to read watchlist", "details": []}}), 500
    return jsonify_ok({"watchlist": current, "revision": current_revision()}), 200


# ---------------------------------------------------------------------------
# POST /api/watchlist
# ---------------------------------------------------------------------------

@bp.route("/api/watchlist", methods=["POST"])
def watchlist_add():
    """Add a company to the watchlist."""
    body = request.get_json(silent=True) or {}
    company = body.get("company", "").strip()
    if not company or len(company) > _MAX_COMPANY_NAME_LENGTH or "\n" in company or "\r" in company:
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": f"company must be 1–{_MAX_COMPANY_NAME_LENGTH} chars with no newlines", "details": []}}), 422

    try:
        updated = watchlist_dal.add_to_watchlist(company)
    except OSError:
        log.exception("watchlist_add.error", company=company)
        return jsonify({"error": {"code": "WATCHLIST_WRITE_ERROR", "message": "Failed to update watchlist", "details": []}}), 500

    return jsonify_ok({"watchlist": updated, "revision": next_revision()}), 200


# ---------------------------------------------------------------------------
# DELETE /api/watchlist/<company>
# ---------------------------------------------------------------------------

@bp.route("/api/watchlist/<company>", methods=["DELETE"])
def watchlist_remove(company: str):
    """Remove a company from the watchlist. Returns 404 if company is not present."""
    try:
        current = watchlist_dal.get_watchlist()
    except OSError:
        log.exception("watchlist_remove.read_error", company=company)
        return jsonify({"error": {"code": "WATCHLIST_READ_ERROR", "message": "Failed to read watchlist", "details": []}}), 500

    if company not in current:
        return jsonify({"error": {"code": "NOT_FOUND", "message": f"{company!r} is not in the watchlist", "details": []}}), 404

    try:
        updated = watchlist_dal.remove_from_watchlist(company)
    except OSError:
        log.exception("watchlist_remove.write_error", company=company)
        return jsonify({"error": {"code": "WATCHLIST_WRITE_ERROR", "message": "Failed to update watchlist", "details": []}}), 500

    return jsonify_ok({"watchlist": updated, "revision": next_revision()}), 200


# ---------------------------------------------------------------------------
# GET /api/donotapply
# ---------------------------------------------------------------------------

@bp.route("/api/donotapply", methods=["GET"])
def donotapply_get():
    """Return YAML-managed and env-locked do-not-apply lists separately."""
    try:
        current = donotapply_dal.get_donotapply()
    except OSError:
        log.exception("donotapply_get.error")
        return jsonify({"error": {"code": "DONOTAPPLY_READ_ERROR", "message": "Failed to read do-not-apply list", "details": []}}), 500
    locked = donotapply_dal.get_locked_list(_get_settings().DONOTAPPLY_COMPANIES)
    return jsonify_ok({"donotapply": current, "locked": locked}), 200


# ---------------------------------------------------------------------------
# POST /api/donotapply
# ---------------------------------------------------------------------------

@bp.route("/api/donotapply", methods=["POST"])
def donotapply_add():
    """Add a company to the do-not-apply list."""
    body = request.get_json(silent=True) or {}
    company = body.get("company", "").strip()
    if not company or len(company) > _MAX_COMPANY_NAME_LENGTH or "\n" in company or "\r" in company:
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": f"company must be 1–{_MAX_COMPANY_NAME_LENGTH} chars with no newlines", "details": []}}), 422

    try:
        updated = donotapply_dal.add_to_donotapply(company)
    except OSError:
        log.exception("donotapply_add.error", company=company)
        return jsonify({"error": {"code": "DONOTAPPLY_WRITE_ERROR", "message": "Failed to update do-not-apply list", "details": []}}), 500

    return jsonify_ok({"donotapply": updated}), 200


# ---------------------------------------------------------------------------
# DELETE /api/donotapply/<company>
# ---------------------------------------------------------------------------

@bp.route("/api/donotapply/<company>", methods=["DELETE"])
def donotapply_remove(company: str):
    """Remove a company from the do-not-apply list."""
    try:
        current = donotapply_dal.get_donotapply()
    except OSError:
        log.exception("donotapply_remove.read_error", company=company)
        return jsonify({"error": {"code": "DONOTAPPLY_READ_ERROR", "message": "Failed to read do-not-apply list", "details": []}}), 500

    if company not in current:
        return jsonify({"error": {"code": "NOT_FOUND", "message": f"{company!r} is not in the do-not-apply list", "details": []}}), 404

    try:
        updated = donotapply_dal.remove_from_donotapply(company)
    except OSError:
        log.exception("donotapply_remove.write_error", company=company)
        return jsonify({"error": {"code": "DONOTAPPLY_WRITE_ERROR", "message": "Failed to update do-not-apply list", "details": []}}), 500

    return jsonify_ok({"donotapply": updated}), 200


# ---------------------------------------------------------------------------
# GET /api/runs
# ---------------------------------------------------------------------------

@bp.route("/api/runs", methods=["GET"])
def runs_list():
    """Paginated run_log listing."""
    try:
        limit = min(int(request.args.get("limit", _PAGINATION_DEFAULT_LIMIT)), _PAGINATION_MAX_LIMIT)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": "limit/offset must be integers", "details": []}}), 422

    settings = _get_settings()
    try:
        with ro_conn(settings.DB_PATH) as conn:
            rows, total = get_run_logs(conn, limit=limit, offset=offset)
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        log.exception("runs_list.db_error")
        return jsonify({"error": {"code": "DB_ERROR", "message": "Failed to load run history", "details": []}}), 500

    return jsonify({
        "data": [r.model_dump(mode="json") for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }), 200


# ---------------------------------------------------------------------------
# POST /api/pipeline/resume
# ---------------------------------------------------------------------------

@bp.route("/api/pipeline/resume", methods=["POST"])
def pipeline_resume():
    """Resume or cancel the currently waiting HiTL graph interrupt."""
    body = request.get_json(silent=True) or {}
    approved = bool(body.get("approved", False))
    decision = "approve" if approved else "user_cancel"

    settings = _get_settings()
    with ro_conn(settings.DB_PATH) as conn:
        row = conn.execute(
            "SELECT run_id FROM run_log WHERE status = 'review_pending' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

    if row is None:
        return jsonify({"error": {"code": "NO_PENDING_RUN", "message": "No run awaiting review", "details": []}}), 404

    run_id = row["run_id"]
    from role_scout.runner import resolve_pending  # deferred: circular import
    resolved = resolve_pending(run_id, decision)

    if not resolved:
        log.info("pipeline_resume.signaled_via_file", run_id=run_id)

    return jsonify_ok({"status": "resumed", "run_id": run_id, "approved": approved}), 200


# ---------------------------------------------------------------------------
# POST /api/pipeline/extend
# ---------------------------------------------------------------------------

@bp.route("/api/pipeline/extend", methods=["POST"])
def pipeline_extend():
    """Extend the TTL of the current review_pending run by 2 hours."""
    settings = _get_settings()
    with rw_conn(settings.DB_PATH) as conn:
        row = conn.execute(
            "SELECT run_id, ttl_extended FROM run_log WHERE status = 'review_pending' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

        if row is None:
            return jsonify({"error": {"code": "NO_PENDING_RUN", "message": "No run awaiting review", "details": []}}), 404

        if row["ttl_extended"]:
            return jsonify({"error": {"code": "ALREADY_EXTENDED", "message": "TTL already extended once for this run", "details": []}}), 400

        extension = int(settings.TTL_EXTENSION_SECONDS)
        conn.execute(
            "UPDATE run_log SET ttl_deadline = datetime(ttl_deadline, ? || ' seconds'), ttl_extended = 1 WHERE run_id = ?",
            (f"+{extension}", row["run_id"]),
        )
        conn.commit()
        return jsonify_ok({"status": "extended", "run_id": row["run_id"], "extended_by_seconds": settings.TTL_EXTENSION_SECONDS}), 200


# ---------------------------------------------------------------------------
# GET /debug/runs
# ---------------------------------------------------------------------------

@bp.route("/debug/runs", methods=["GET"])
def debug_runs():
    """Render the debug run history page."""
    return render_template("debug_runs.html")


# ---------------------------------------------------------------------------
# GET /debug/basic — Fallback basic dashboard
# ---------------------------------------------------------------------------

@bp.route("/debug/basic", methods=["GET"])
def basic_dashboard():
    """Fallback basic dashboard (pre-revamp)."""
    settings = _get_settings()
    with ro_conn(settings.DB_PATH) as conn:
        rows = conn.execute(
            "SELECT hash_id, title, company, location, match_pct, status FROM qualified_jobs ORDER BY match_pct DESC LIMIT 100"
        ).fetchall()
        jobs = [dict(r) for r in rows]
        threshold = settings.SCORE_THRESHOLD

    return render_template("basic.html", jobs=jobs, threshold=threshold)


# ---------------------------------------------------------------------------
# GET / — Dashboard index (full revamp)
# ---------------------------------------------------------------------------

@bp.route("/", methods=["GET"])
def index():
    """Full-featured dashboard index page."""
    settings = _get_settings()

    # Parse and validate query params
    active_status = request.args.get("status", "new")
    if active_status not in {"new", "reviewed", "applied", "rejected", "not_a_fit", "not_available", "history", "all"}:
        active_status = "new"

    active_source: str | None = request.args.get("source")
    if active_source not in {None, "linkedin", "google_jobs", "trueup", "manual"}:
        active_source = None

    active_sort = request.args.get("sort", "match_pct")
    if active_sort not in _VALID_SORT_COLS:
        active_sort = "match_pct"

    active_dir = request.args.get("dir", "desc")
    if active_dir not in _VALID_DIRS:
        active_dir = "desc"

    with ro_conn(settings.DB_PATH) as conn:
        # posted_date has mixed-format strings; Python post-sort handles it.
        # Pass scored_at to SQL so the initial fetch order is stable.
        sql_sort = "scored_at" if active_sort == "posted_date" else active_sort
        jobs_raw = get_qualified_jobs(
            conn,
            status=active_status,
            source=active_source,
            sort=sql_sort,
            dir=active_dir,
            limit=_JOBS_LISTING_LIMIT,
        )
        total_counts = get_job_count_by_status(conn)
        source_counts = get_job_count_by_source(conn)

        # Last 5 pipeline runs for the run strip
        run_rows = conn.execute(
            """
            SELECT started_at, total_fetched, total_new, total_qualified,
                   source_linkedin, source_google_jobs, source_trueup,
                   watchlist_hits, errors
            FROM run_log
            ORDER BY started_at DESC LIMIT 5
            """
        ).fetchall()

    run_history = []
    for r in run_rows:
        d = dict(r)
        try:
            d["watchlist_hits"] = json.loads(d.get("watchlist_hits") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["watchlist_hits"] = {}
        try:
            d["errors"] = json.loads(d.get("errors") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["errors"] = []
        # Parse started_at for display
        try:
            dt = datetime.fromisoformat(d["started_at"])
            d["started_at_display"] = dt.strftime("%b %-d, %-I:%M %p")
        except (ValueError, TypeError):
            d["started_at_display"] = d.get("started_at", "")
        run_history.append(d)

    today = datetime.now(UTC).date()

    # Convert ScoredJob objects to dicts for template
    jobs: list[dict[str, Any]] = []
    for job in jobs_raw:
        jd = job.model_dump(mode="json")
        # jd_alignment may be a JSON string — parse it so template can access keys
        if jd.get("jd_alignment"):
            try:
                jd["jd_alignment_parsed"] = json.loads(jd["jd_alignment"])
            except (json.JSONDecodeError, TypeError):
                jd["jd_alignment_parsed"] = None
        else:
            jd["jd_alignment_parsed"] = None
        # Compute days since posting — handles ISO dates and "N days/hours ago" strings
        jd["days_since_posted"] = _parse_days_since_posted(str(raw_date := jd.get("posted_date") or ""), today)
        jobs.append(jd)

    # posted_date is stored in mixed formats (ISO + relative strings) so SQL sort is
    # unreliable — sort by the computed days_since_posted in Python after building the list.
    # NULLs always go last. asc = smallest days first (most recent). desc = largest days first (oldest).
    if active_sort == "posted_date":
        if active_dir == "asc":
            jobs.sort(key=lambda j: (j["days_since_posted"] is None, j["days_since_posted"] or 0))
        else:
            jobs.sort(key=lambda j: (j["days_since_posted"] is None, -(j["days_since_posted"] or 0)))

    # Add "all active" count
    total_counts["all"] = total_counts.get("new", 0) + total_counts.get("reviewed", 0)
    total_counts["history"] = (
        total_counts.get("applied", 0) + total_counts.get("rejected", 0)
        + total_counts.get("not_a_fit", 0) + total_counts.get("not_available", 0)
    )

    watchlist = watchlist_dal.get_watchlist()
    donotapply = donotapply_dal.get_donotapply()
    locked = donotapply_dal.get_locked_list(settings.DONOTAPPLY_COMPANIES)

    return render_template(
        "index.html",
        jobs=jobs,
        threshold=settings.SCORE_THRESHOLD,
        active_status=active_status,
        active_source=active_source,
        active_sort=active_sort,
        active_dir=active_dir,
        total_counts=total_counts,
        source_counts=source_counts,
        run_history=run_history,
        watchlist=[c.lower() for c in watchlist],  # lowercased for Jinja star check
        watchlist_initial=watchlist,               # original casing for JS sidebar panel
        donotapply_initial=donotapply,
        locked_initial=locked,
    )


# ---------------------------------------------------------------------------
# Ingest helpers
# ---------------------------------------------------------------------------

_MAX_INGEST_URLS = 20
_MAX_MANUAL_TEXT_CHARS = 50_000
# Only external HTTPS URLs — blocks http:// and anything without a dot in host
_URL_RE = re.compile(r"^https://[^/\s]+\.[^/\s]", re.IGNORECASE)

# Private/loopback IP prefixes — block to prevent SSRF against local services.
# This dashboard is single-user local, but defence-in-depth applies.
_PRIVATE_PREFIXES = (
    "https://127.", "https://localhost", "https://0.",
    "https://10.", "https://192.168.",
    "https://172.16.", "https://172.17.", "https://172.18.", "https://172.19.",
    "https://172.20.", "https://172.21.", "https://172.22.", "https://172.23.",
    "https://172.24.", "https://172.25.", "https://172.26.", "https://172.27.",
    "https://172.28.", "https://172.29.", "https://172.30.", "https://172.31.",
    "https://169.254.",   # link-local / AWS metadata
    "https://[::1]",      # IPv6 loopback
    "https://[fc", "https://[fd",  # IPv6 private ULA
)


def _ingest_feature_disabled():
    """Return 404 response if MANUAL_INGEST_ENABLED is false, else None."""
    if not _get_settings().MANUAL_INGEST_ENABLED:
        return jsonify({"error": {"code": "FEATURE_DISABLED", "message": "Manual ingestion is disabled (set MANUAL_INGEST_ENABLED=true in .env)", "details": []}}), 404
    return None


def _validate_ingest_url(url: str) -> str | None:
    """Return an error string if the URL is invalid or targets a private host, else None."""
    url = url.strip()
    if not _URL_RE.match(url):
        return f"Invalid URL (must start with https://): {url[:120]}"
    lower = url.lower()
    if any(lower.startswith(p) for p in _PRIVATE_PREFIXES):
        return f"URL targets a private or loopback address: {url[:120]}"
    return None


def _sign_job(job: dict[str, Any]) -> dict[str, Any]:
    """Attach an HMAC-SHA256 signature to *job* so the confirm endpoint can verify integrity.

    The signature covers the canonical JSON (sorted keys, no _sig field) and is
    computed with the Flask ``SECRET_KEY``.  Returns a shallow copy with ``_sig`` added.
    """
    job_copy = {k: v for k, v in job.items() if k != "_sig"}
    payload = json.dumps(job_copy, sort_keys=True, separators=(",", ":"))
    secret = current_app.config.get("SECRET_KEY", "")
    sig = hmac.new(
        secret.encode() if isinstance(secret, str) else secret,
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {**job_copy, "_sig": sig}


def _verify_job_sig(job: dict[str, Any]) -> bool:
    """Return True if the HMAC signature on *job* is valid, False otherwise."""
    sig = job.get("_sig", "")
    job_without_sig = {k: v for k, v in job.items() if k != "_sig"}
    payload = json.dumps(job_without_sig, sort_keys=True, separators=(",", ":"))
    secret = current_app.config.get("SECRET_KEY", "")
    expected = hmac.new(
        secret.encode() if isinstance(secret, str) else secret,
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


# ---------------------------------------------------------------------------
# GET /ingest — Manual job ingestion page
# ---------------------------------------------------------------------------

@bp.route("/ingest", methods=["GET"])
def ingest_page():
    """Serve the manual job ingestion page (gated by MANUAL_INGEST_ENABLED)."""
    blocked = _ingest_feature_disabled()
    if blocked:
        return blocked
    return render_template("ingest.html")


# ---------------------------------------------------------------------------
# POST /api/ingest/analyze
# ---------------------------------------------------------------------------

@bp.route("/api/ingest/analyze", methods=["POST"])
def ingest_analyze():
    """Fetch, extract, and score a list of JD URLs.

    Body: {"urls": [...], "manual_texts": {"url": "pasted JD text"}}
    Returns list of AnalysisResult dicts.
    """
    blocked = _ingest_feature_disabled()
    if blocked:
        return blocked

    body = request.get_json(silent=True) or {}
    urls = body.get("urls", [])
    manual_texts = body.get("manual_texts", {})

    if not isinstance(urls, list) or not (1 <= len(urls) <= _MAX_INGEST_URLS):
        return jsonify_error("VALIDATION_ERROR", f"urls must be a list of 1–{_MAX_INGEST_URLS} items", 422)

    url_errors = [_validate_ingest_url(u) for u in urls if isinstance(u, str)]
    url_errors = [e for e in url_errors if e]
    if url_errors or any(not isinstance(u, str) for u in urls):
        return jsonify_error("VALIDATION_ERROR", "One or more URLs are invalid", 422, details=url_errors[:5])

    if not isinstance(manual_texts, dict):
        return jsonify_error("VALIDATION_ERROR", "manual_texts must be an object", 422)

    cleaned_urls = [u.strip() for u in urls]
    cleaned_texts: dict[str, str] = {}
    for url, text in manual_texts.items():
        if not isinstance(text, str):
            continue
        if len(text) > _MAX_MANUAL_TEXT_CHARS:
            return jsonify_error("VALIDATION_ERROR", f"manual_texts values must be <= {_MAX_MANUAL_TEXT_CHARS} chars", 422)
        cleaned_texts[url.strip()] = text.strip()

    settings = _get_settings()
    log.info("ingest_analyze.start", url_count=len(cleaned_urls), manual_text_count=len(cleaned_texts))
    try:
        from role_scout.compat.models import load_candidate_profile
        from role_scout.ingest.extractor import analyze_urls
        profile = load_candidate_profile(str(settings.CANDIDATE_PROFILE_PATH))
        results = analyze_urls(
            urls=cleaned_urls,
            manual_texts=cleaned_texts,
            candidate_profile=profile,
            api_key=settings.ANTHROPIC_API_KEY,
            model=settings.CLAUDE_MODEL,
            db_path=str(settings.DB_PATH),
            score_threshold=0,
            max_cost=settings.MAX_COST_USD,
        )
    except FileNotFoundError:
        log.exception("ingest_analyze.profile_missing")
        return jsonify_error("CONFIG_ERROR", "Candidate profile not found — ensure config/candidate_profile.yaml exists", 500)
    except Exception:
        log.exception("ingest_analyze.error")
        return jsonify_error("INGEST_ERROR", "Analysis failed — check server logs for details", 500)

    ready = sum(1 for r in results if r.status == "ready")
    log.info("ingest_analyze.done", total=len(results), ready=ready)

    # Sign each scored_job dict so the confirm endpoint can verify payload integrity.
    serialised = []
    for r in results:
        d = r.to_dict()
        if d.get("scored_job") is not None:
            d["scored_job"] = _sign_job(d["scored_job"])
        serialised.append(d)

    return jsonify_ok({"results": serialised}), 200


# ---------------------------------------------------------------------------
# POST /api/ingest/confirm
# ---------------------------------------------------------------------------

@bp.route("/api/ingest/confirm", methods=["POST"])
def ingest_confirm():
    """Write confirmed jobs to qualified_jobs + seen_hashes.

    Body: {"jobs": [...ScoredJob-compatible dicts with source='manual'...]}
    Returns: {"ingested": N, "skipped": M}
    """
    blocked = _ingest_feature_disabled()
    if blocked:
        return blocked

    settings = _get_settings()
    body = request.get_json(silent=True) or {}
    jobs_raw = body.get("jobs", [])

    if not isinstance(jobs_raw, list) or not (1 <= len(jobs_raw) <= _MAX_INGEST_URLS):
        return jsonify_error("VALIDATION_ERROR", f"jobs must be a list of 1–{_MAX_INGEST_URLS} items", 422)

    validated_jobs: list[_ScoredJobModel] = []
    for i, raw in enumerate(jobs_raw):
        if not isinstance(raw, dict):
            return jsonify_error("VALIDATION_ERROR", f"jobs[{i}] must be an object", 422)

        # Verify HMAC signature before trusting any field values.
        if not _verify_job_sig(raw):
            log.warning("ingest_confirm.tampered_payload", index=i)
            return jsonify_error("TAMPERED_PAYLOAD", f"jobs[{i}] signature verification failed — payload may have been tampered", 422)

        # Strip the signature before model validation — it is not part of ScoredJob schema.
        raw_clean = {k: v for k, v in raw.items() if k != "_sig"}

        hash_id = raw_clean.get("hash_id", "")
        if not _HASH_ID_RE.match(str(hash_id)):
            return jsonify_error("VALIDATION_ERROR", f"jobs[{i}].hash_id must be 16 hex chars", 422)
        if raw_clean.get("source") != "manual":
            return jsonify_error("VALIDATION_ERROR", f"jobs[{i}].source must be 'manual'", 422)
        try:
            validated_jobs.append(_ScoredJobModel.model_validate(raw_clean))
        except _PydanticValidationError:
            return jsonify_error("VALIDATION_ERROR", f"jobs[{i}] failed schema validation", 422)

    log.info("ingest_confirm.start", job_count=len(validated_jobs))
    ingested = 0
    skipped = 0
    try:
        with rw_conn(str(settings.DB_PATH)) as conn:
            for job in validated_jobs:
                existing = conn.execute(
                    "SELECT hash_id FROM qualified_jobs WHERE hash_id = ?", (job.hash_id,)
                ).fetchone()
                if existing:
                    skipped += 1
                    continue
                insert_qualified_job(conn, job)
                upsert_seen_hash(conn, job.hash_id, "manual", job.title, job.company)
                ingested += 1
            conn.commit()
    except Exception:
        log.exception("ingest_confirm.db_error")
        return jsonify_error("DB_ERROR", "Failed to write jobs to database", 500)

    log.info("ingest_confirm.ok", ingested=ingested, skipped=skipped)
    return jsonify_ok({"ingested": ingested, "skipped": skipped}), 200

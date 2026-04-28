"""Phase 2 Flask routes — tailor, pipeline status, watchlist CRUD, HiTL resume/extend, index.

All routes bind to 127.0.0.1 (enforced in run.py --serve, not here).
All write routes require CSRF token (Flask-WTF).
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from flask import Blueprint, jsonify, request, send_from_directory

from role_scout.config import Settings
from role_scout.dal import watchlist_dal
from role_scout.db import get_ro_conn, get_rw_conn, ro_conn, rw_conn

log = structlog.get_logger()

_TOP_MATCHES_LIMIT = 3
_PAGINATION_MAX_LIMIT = 50
_PAGINATION_DEFAULT_LIMIT = 10
_JOBS_LISTING_LIMIT = 200
_MAX_COMPANY_NAME_LENGTH = 100
_VALID_STATUSES = {"new", "reviewed", "applied", "rejected"}
_VALID_SORT_COLS = {"match_pct", "company", "title", "city", "work_model", "company_stage", "status", "scored_at"}
_VALID_DIRS = {"asc", "desc"}

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
    import re
    corr_id = str(uuid.uuid4())
    bound_log = log.bind(correlation_id=corr_id, hash_id=hash_id)

    if not re.match(r"^[a-f0-9]{16}$", hash_id):
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": "hash_id must be 16 hex chars", "details": []}}), 422

    body = request.get_json(silent=True) or {}
    force = bool(body.get("force", False))

    settings = Settings()
    try:
        from role_scout.tailor import NotQualifiedError, TailorParseError, tailor_resume
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
        return jsonify({"error": {"code": "NOT_QUALIFIED", "message": str(exc), "details": []}}), 400
    except TailorParseError as exc:
        bound_log.error("tailor_route.parse_error", reason=str(exc))
        return jsonify({"error": {"code": "CLAUDE_API_ERROR", "message": str(exc), "details": []}}), 500
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
    import re
    if not re.match(r"^[a-f0-9]{16}$", hash_id):
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": "hash_id must be 16 hex chars", "details": []}}), 422

    body = request.get_json(silent=True) or {}
    new_status = body.get("status", "").strip()
    if new_status not in _VALID_STATUSES:
        return jsonify({"error": {"code": "INVALID_STATUS", "message": f"status must be one of: {', '.join(sorted(_VALID_STATUSES))}", "details": []}}), 400

    settings = Settings()
    try:
        from role_scout.compat.db.qualified_jobs import update_job_status
        with rw_conn(settings.DB_PATH) as conn:
            old_status = update_job_status(conn, hash_id, new_status)
            conn.commit()
    except Exception:
        log.exception("status_update.error", hash_id=hash_id)
        return jsonify({"error": {"code": "DB_ERROR", "message": "Failed to update status", "details": []}}), 500

    if old_status is None:
        return jsonify({"error": {"code": "NOT_FOUND", "message": "Job not found", "details": []}}), 404

    log.info("status_update.ok", hash_id=hash_id, old_status=old_status, new_status=new_status)
    return jsonify({"data": {"hash_id": hash_id, "status": new_status, "updated": True}}), 200


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
    import re
    from pathlib import Path as _Path
    from string import Template

    if not re.match(r"^[a-f0-9]{16}$", hash_id):
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": "hash_id must be 16 hex chars", "details": []}}), 422

    body = request.get_json(silent=True) or {}
    force = bool(body.get("force", False))

    settings = Settings()
    from role_scout.compat.db.qualified_jobs import get_job_by_hash_id, update_jd_alignment

    with ro_conn(settings.DB_PATH) as conn:
        job = get_job_by_hash_id(conn, hash_id)

    if job is None:
        return jsonify({"error": {"code": "NOT_FOUND", "message": "Job not found", "details": []}}), 404

    if not force and job.jd_alignment:
        return jsonify({"data": {"hash_id": hash_id, "jd_alignment": job.jd_alignment, "cached": True}}), 200

    if not job.description:
        return jsonify({"error": {"code": "NO_DESCRIPTION", "message": "No job description available to analyze", "details": []}}), 422

    resume_path = _Path("config/resume_summary.md")
    if not resume_path.exists():
        return jsonify({"error": {"code": "RESUME_MISSING", "message": "config/resume_summary.md not found — place your resume summary there", "details": []}}), 422

    prompt_path = _Path(__file__).parent.parent / "prompts" / "alignment_system.md"
    if not prompt_path.exists():
        return jsonify({"error": {"code": "PROMPT_MISSING", "message": "alignment_system.md prompt not found", "details": []}}), 500

    try:
        resume_text = resume_path.read_text(encoding="utf-8")
        prompt_template = prompt_path.read_text(encoding="utf-8")
    except OSError:
        log.exception("alignment_run.file_read_error", hash_id=hash_id)
        return jsonify({"error": {"code": "FILE_ERROR", "message": "Could not read required files", "details": []}}), 500

    system_prompt = Template(prompt_template).safe_substitute(
        resume_summary=resume_text,
        title=job.title,
        company=job.company,
        source=job.source,
        description=(job.description or "")[:2000],
    )

    try:
        from role_scout.claude_client import call_claude
        text, _in_tok, _out_tok = call_claude(
            system=system_prompt,
            user="Analyze this job for alignment with the candidate's resume.",
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
    except Exception:
        log.exception("alignment_run.db_write_error", hash_id=hash_id)
        # Return result even if cache write fails

    log.info("alignment_run.ok", hash_id=hash_id)
    return jsonify({"data": {"hash_id": hash_id, "jd_alignment": alignment_json, "cached": False}}), 200


# ---------------------------------------------------------------------------
# GET /jds/<filename>
# ---------------------------------------------------------------------------

@bp.route("/jds/<path:filename>", methods=["GET"])
def jd_download(filename: str):
    """Download a JD text file by name.

    Path traversal protected: rejects '..' and absolute paths.
    """
    if ".." in filename or filename.startswith("/"):
        return jsonify({"error": {"code": "INVALID_PATH", "message": "Invalid filename", "details": []}}), 400

    settings = Settings()
    jd_dir = Path(settings.DB_PATH).parent / "jds"
    file_path = jd_dir / filename
    if not file_path.exists():
        return jsonify({"error": {"code": "NOT_FOUND", "message": "JD file not found", "details": []}}), 404

    return send_from_directory(str(jd_dir), filename, as_attachment=True, mimetype="text/plain")


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
    settings = Settings()
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
            f"SELECT title, company, match_pct FROM qualified_jobs ORDER BY match_pct DESC LIMIT {_TOP_MATCHES_LIMIT}"
        ).fetchall()

    d = dict(row)
    ttl_remaining_s = None
    if d.get("ttl_deadline") and d.get("status") == "review_pending":
        try:
            deadline = datetime.fromisoformat(d["ttl_deadline"]).replace(tzinfo=UTC)
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            ttl_remaining_s = max(0, int(remaining))
        except Exception:
            pass

    watchlist = watchlist_dal.get_watchlist()

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
            pass

    resp = jsonify({
        "run_id": d["run_id"],
        "status": d["status"],
        "qualified_count": d["qualified_count"],
        "estimated_cost_usd": d["estimated_cost_usd"],
        "ttl_remaining_s": ttl_remaining_s,
        "ttl_extended": bool(d.get("ttl_extended")),
        "cancel_reason": d.get("cancel_reason"),
        "watchlist_revision": len(watchlist),
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
    except Exception:
        log.exception("watchlist_get.error")
        return jsonify({"error": {"code": "WATCHLIST_READ_ERROR", "message": "Failed to read watchlist", "details": []}}), 500
    return jsonify({"watchlist": current}), 200


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
    except Exception as exc:
        log.exception("watchlist_add.error", company=company)
        return jsonify({"error": {"code": "WATCHLIST_WRITE_ERROR", "message": str(exc), "details": []}}), 500

    return jsonify({"watchlist": updated, "revision": len(updated)}), 200


# ---------------------------------------------------------------------------
# DELETE /api/watchlist/<company>
# ---------------------------------------------------------------------------

@bp.route("/api/watchlist/<company>", methods=["DELETE"])
def watchlist_remove(company: str):
    """Remove a company from the watchlist."""
    try:
        updated = watchlist_dal.remove_from_watchlist(company)
    except Exception as exc:
        log.exception("watchlist_remove.error", company=company)
        return jsonify({"error": {"code": "WATCHLIST_WRITE_ERROR", "message": str(exc), "details": []}}), 500

    return jsonify({"watchlist": updated, "revision": len(updated)}), 200


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

    settings = Settings()
    with ro_conn(settings.DB_PATH) as conn:
        from role_scout.dal.run_log_dal import get_run_logs
        rows, total = get_run_logs(conn, limit=limit, offset=offset)

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

    settings = Settings()
    with ro_conn(settings.DB_PATH) as conn:
        row = conn.execute(
            "SELECT run_id FROM run_log WHERE status = 'review_pending' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

    if row is None:
        return jsonify({"error": {"code": "NO_PENDING_RUN", "message": "No run awaiting review", "details": []}}), 404

    run_id = row["run_id"]
    from role_scout.runner import resolve_pending
    resolved = resolve_pending(run_id, decision)

    if not resolved:
        log.info("pipeline_resume.signaled_via_file", run_id=run_id)

    return jsonify({"status": "resumed", "run_id": run_id, "approved": approved}), 200


# ---------------------------------------------------------------------------
# POST /api/pipeline/extend
# ---------------------------------------------------------------------------

@bp.route("/api/pipeline/extend", methods=["POST"])
def pipeline_extend():
    """Extend the TTL of the current review_pending run by 2 hours."""
    settings = Settings()
    with rw_conn(settings.DB_PATH) as conn:
        row = conn.execute(
            "SELECT run_id, ttl_extended FROM run_log WHERE status = 'review_pending' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

        if row is None:
            return jsonify({"error": {"code": "NO_PENDING_RUN", "message": "No run awaiting review", "details": []}}), 404

        if row["ttl_extended"]:
            return jsonify({"error": {"code": "ALREADY_EXTENDED", "message": "TTL already extended once for this run", "details": []}}), 400

        conn.execute(
            f"UPDATE run_log SET ttl_deadline = datetime(ttl_deadline, '+{settings.TTL_EXTENSION_SECONDS} seconds'), ttl_extended = 1 WHERE run_id = ?",
            (row["run_id"],),
        )
        conn.commit()
        return jsonify({"status": "extended", "run_id": row["run_id"], "extended_by_seconds": settings.TTL_EXTENSION_SECONDS}), 200


# ---------------------------------------------------------------------------
# GET /debug/runs
# ---------------------------------------------------------------------------

@bp.route("/debug/runs", methods=["GET"])
def debug_runs():
    """Render the debug run history page."""
    from flask import render_template
    return render_template("debug_runs.html")


# ---------------------------------------------------------------------------
# GET /debug/basic — Fallback basic dashboard
# ---------------------------------------------------------------------------

@bp.route("/debug/basic", methods=["GET"])
def basic_dashboard():
    """Fallback basic dashboard (pre-revamp)."""
    from flask import render_template
    settings = Settings()
    with ro_conn(settings.DB_PATH) as conn:
        rows = conn.execute(
            f"SELECT hash_id, title, company, location, match_pct, status FROM qualified_jobs ORDER BY match_pct DESC LIMIT 100"
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
    from flask import render_template
    from role_scout.compat.db.qualified_jobs import (
        get_qualified_jobs,
        get_job_count_by_status,
        get_job_count_by_source,
    )

    settings = Settings()

    # Parse and validate query params
    active_status = request.args.get("status", "new")
    if active_status not in {"new", "reviewed", "applied", "rejected", "history", "all"}:
        active_status = "new"

    active_source: str | None = request.args.get("source")
    if active_source not in {None, "linkedin", "google_jobs", "trueup"}:
        active_source = None

    active_sort = request.args.get("sort", "match_pct")
    if active_sort not in _VALID_SORT_COLS:
        active_sort = "match_pct"

    active_dir = request.args.get("dir", "desc")
    if active_dir not in _VALID_DIRS:
        active_dir = "desc"

    with ro_conn(settings.DB_PATH) as conn:
        jobs_raw = get_qualified_jobs(
            conn,
            status=active_status,
            source=active_source,
            sort=active_sort,
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
        except Exception:
            d["started_at_display"] = d.get("started_at", "")
        run_history.append(d)

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
        jobs.append(jd)

    # Add "all active" count
    total_counts["all"] = total_counts.get("new", 0) + total_counts.get("reviewed", 0)
    total_counts["history"] = total_counts.get("applied", 0) + total_counts.get("rejected", 0)

    watchlist = watchlist_dal.get_watchlist()

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
    )

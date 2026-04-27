"""Phase 2 Flask routes — tailor, pipeline status, watchlist CRUD, HiTL resume/extend, index.

All routes bind to 127.0.0.1 (enforced in run.py --serve, not here).
All write routes require CSRF token (Flask-WTF).
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from flask import Blueprint, jsonify, request

from role_scout.config import Settings
from role_scout.dal import watchlist_dal
from role_scout.db import get_ro_conn, get_rw_conn, ro_conn, rw_conn

log = structlog.get_logger()

_TOP_MATCHES_LIMIT = 3
_PAGINATION_MAX_LIMIT = 50
_PAGINATION_DEFAULT_LIMIT = 10
_JOBS_LISTING_LIMIT = 100
_MAX_COMPANY_NAME_LENGTH = 100

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
            # raw is a dict keyed by source name; flatten to list with name included
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
# POST /api/watchlist
# ---------------------------------------------------------------------------

@bp.route("/api/watchlist", methods=["POST"])
def watchlist_add():
    """Add a company to the watchlist.

    Request body: {"company": "Anthropic"}
    Returns 200 + updated watchlist on success.
    Returns 422 VALIDATION_ERROR on bad input.
    Returns 403 on missing CSRF.
    """
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
    """Remove a company from the watchlist.

    Returns 200 + updated watchlist (idempotent if company not present).
    """
    try:
        updated = watchlist_dal.remove_from_watchlist(company)
    except Exception as exc:
        log.exception("watchlist_remove.error", company=company)
        return jsonify({"error": {"code": "WATCHLIST_WRITE_ERROR", "message": str(exc), "details": []}}), 500

    return jsonify({"watchlist": updated, "revision": len(updated)}), 200


# ---------------------------------------------------------------------------
# GET /api/runs  (debug listing)
# ---------------------------------------------------------------------------

@bp.route("/api/runs", methods=["GET"])
def runs_list():
    """Paginated run_log listing for debug/dashboard use.

    Query params: limit (default 10, max 50), offset (default 0).
    """
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
    """Resume or cancel the currently waiting HiTL graph interrupt.

    Body: {"approved": true|false}
    Returns 200 on success, 404 if no pending run.
    """
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
    from role_scout.runner import resolve_pending  # deferred to avoid eager Settings() at import
    resolved = resolve_pending(run_id, decision)

    if not resolved:
        log.info("pipeline_resume.signaled_via_file", run_id=run_id)

    return jsonify({"status": "resumed", "run_id": run_id, "approved": approved}), 200


# ---------------------------------------------------------------------------
# POST /api/pipeline/extend
# ---------------------------------------------------------------------------

@bp.route("/api/pipeline/extend", methods=["POST"])
def pipeline_extend():
    """Extend the TTL of the current review_pending run by 2 hours.

    Returns 400 ALREADY_EXTENDED if ttl_extended is already True.
    Returns 404 if no run is in review_pending.
    """
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
# GET /debug/runs — Debug run history HTML page
# ---------------------------------------------------------------------------

@bp.route("/debug/runs", methods=["GET"])
def debug_runs():
    """Render the debug run history page."""
    from flask import render_template
    return render_template("debug_runs.html")


# ---------------------------------------------------------------------------
# GET / — Dashboard index
# ---------------------------------------------------------------------------

@bp.route("/", methods=["GET"])
def index():
    """Dashboard index page showing qualified jobs."""
    from flask import render_template
    settings = Settings()
    with ro_conn(settings.DB_PATH) as conn:
        rows = conn.execute(
            f"SELECT hash_id, title, company, location, match_pct, status FROM qualified_jobs ORDER BY match_pct DESC LIMIT {_JOBS_LISTING_LIMIT}"
        ).fetchall()
        jobs = [dict(r) for r in rows]
        threshold = settings.SCORE_THRESHOLD

    return render_template(
        "index.html",
        jobs=jobs,
        threshold=threshold,
    )

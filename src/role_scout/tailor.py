"""Resume tailoring — one-shot Claude call with SHA-based cache.

Cache key: sha256(resume_sha + "|" + prompt_version + "|" + hash_id)[:16]
- resume_sha: sha256(resume_summary.md content)[:16]
- prompt_version: first-line <!-- version: X --> of tailor prompt
- Invalidates when resume or prompt version changes, not just the job.

Raises:
    NotQualifiedError: Job is below qualify_threshold.
    TailorParseError: Claude returned malformed JSON.
    CostKillSwitchError: Accumulated cost exceeds limit.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from jobsearch.db.qualified_jobs import get_job_by_hash_id
from role_scout.claude_client import call_claude
from role_scout.config import Settings
from role_scout.dal.tailor_dal import get_cached_tailor, write_tailor
from role_scout.models.api import TailoredResume

log = structlog.get_logger()

_PROMPT_PATH = Path(__file__).parent / "prompts" / "resume_tailor_system.md"
_PROMPT_VERSION_RE = re.compile(r"<!--\s*version:\s*(\S+)\s*-->")

_MIN_TAILORED_BULLETS = 3
_TAILOR_MAX_TOKENS = 2048
_SUMMARY_MAX_CHARS = 2000
_BULLETS_MAX_CHARS = 400
_KEYWORDS_MAX_COUNT = 10
_KEYWORD_MAX_CHARS = 80


class NotQualifiedError(ValueError):
    """Raised when hash_id is below qualify_threshold."""


class TailorParseError(ValueError):
    """Raised when Claude returns malformed JSON for the tailor response."""


def _read_prompt() -> tuple[str, str]:
    """Return (prompt_text, version_string) from the tailor system prompt."""
    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(f"Tailor prompt missing: {_PROMPT_PATH}")
    text = _PROMPT_PATH.read_text(encoding="utf-8")
    first_line = text.splitlines()[0] if text.splitlines() else ""
    match = _PROMPT_VERSION_RE.search(first_line)
    version = match.group(1) if match else "v0.0"
    return text, version


def _read_resume(resume_path: Path | None = None) -> tuple[str, str]:
    """Return (resume_text, sha256[:16]) from config/resume_summary.md (or override path)."""
    path = resume_path or Settings().RESUME_SUMMARY_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Resume summary missing: {path}. "
            "Create config/resume_summary.md (or set RESUME_SUMMARY_PATH) with your stripped resume content."
        )
    text = path.read_text(encoding="utf-8").strip()
    sha = hashlib.sha256(text.encode()).hexdigest()[:16]
    return text, sha


def _make_cache_key(resume_sha: str, prompt_version: str, hash_id: str) -> str:
    """Compute the 16-hex cache key for this (resume, prompt, job) triple."""
    raw = f"{resume_sha}|{prompt_version}|{hash_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _validate_response(raw_json: str, hash_id: str) -> dict[str, Any]:
    """Parse and validate Claude's JSON response.

    Raises TailorParseError with no DB write if malformed.
    """
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise TailorParseError(f"Claude returned invalid JSON for {hash_id}: {exc}") from exc

    required = {"tailored_summary", "tailored_bullets", "keywords_incorporated"}
    missing = required - set(parsed.keys())
    if missing:
        raise TailorParseError(
            f"Claude response missing required fields {missing} for {hash_id}"
        )

    if not isinstance(parsed["tailored_bullets"], list) or len(parsed["tailored_bullets"]) < _MIN_TAILORED_BULLETS:
        raise TailorParseError(
            f"tailored_bullets must be a list of ≥ {_MIN_TAILORED_BULLETS} items for {hash_id}; "
            f"got {parsed.get('tailored_bullets')!r}"
        )

    return parsed


def _check_job_qualified(conn: sqlite3.Connection, hash_id: str, qualify_threshold: int) -> Any:
    """Fetch the job row and assert it meets the qualify threshold.

    Args:
        conn: Open SQLite connection.
        hash_id: 16-char hex job identifier.
        qualify_threshold: Minimum match_pct required.

    Returns:
        The job object returned by get_job_by_hash_id.

    Raises:
        NotQualifiedError: Job not found or match_pct below threshold.
    """
    job = get_job_by_hash_id(conn, hash_id)
    if job is None:
        raise NotQualifiedError(f"Job not found: {hash_id!r}")
    if job.match_pct < qualify_threshold:
        raise NotQualifiedError(
            f"Job {hash_id} has match_pct={job.match_pct} < threshold={qualify_threshold}"
        )
    return job


def _build_cache_key(hash_id: str, prompt_version: str, resume_text: str) -> str:
    """Compute the 16-hex cache key for this (resume, prompt, job) triple.

    Delegates to _read_resume for the resume SHA and _make_cache_key for hashing,
    keeping this function's signature consistent with the task spec while re-using
    the existing helpers.

    Args:
        hash_id: 16-char hex job identifier.
        prompt_version: Version string extracted from the system prompt header.
        resume_text: Full resume text (used to derive the SHA).

    Returns:
        16-character hex cache key string.
    """
    resume_sha = hashlib.sha256(resume_text.encode()).hexdigest()[:16]
    return _make_cache_key(resume_sha, prompt_version, hash_id)


def _parse_and_validate_tailor_response(raw: str, hash_id: str) -> dict[str, Any]:
    """Parse Claude's raw text response and validate required fields.

    Args:
        raw: Raw string from Claude (JSON, possibly fence-wrapped).
        hash_id: Job identifier, used only for error messages.

    Returns:
        Parsed dict with at minimum: tailored_summary, tailored_bullets,
        keywords_incorporated.

    Raises:
        TailorParseError: If JSON is invalid or required fields are missing/malformed.
    """
    # Strip markdown code fences if Claude wrapped JSON
    clean = raw.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.MULTILINE)
        clean = re.sub(r"\s*```$", "", clean, flags=re.MULTILINE)
        clean = clean.strip()
    return _validate_response(clean, hash_id)


def tailor_resume(
    conn: sqlite3.Connection,
    hash_id: str,
    *,
    qualify_threshold: int = 85,
    force: bool = False,
    api_key: str,
    accumulated_cost: float = 0.0,
    max_cost: float = 5.0,
    correlation_id: str | None = None,
) -> TailoredResume:
    """Generate (or return cached) tailored resume content for a qualified job.

    Args:
        conn: Open SQLite connection (read/write).
        hash_id: 16-char hex job identifier.
        qualify_threshold: Minimum match_pct to be considered qualified.
        force: If True, bypass cache and call Claude even if a cached result exists.
        api_key: Anthropic API key.
        accumulated_cost: Running cost before this call (for kill-switch).
        max_cost: Maximum allowed total cost.
        correlation_id: Propagated request ID for log correlation.

    Returns:
        dict with keys: hash_id, job_title, company, tailored_summary,
        tailored_bullets, keywords_incorporated, cache_key, prompt_version,
        tailored_at (ISO 8601), cached (bool).

    Raises:
        NotQualifiedError: Job match_pct < qualify_threshold or not in DB.
        TailorParseError: Claude response not parseable / missing required fields.
        CostKillSwitchError: Cost limit exceeded before Claude call.
        FileNotFoundError: resume_summary.md or prompt file missing.
    """
    corr_id = correlation_id or str(uuid.uuid4())
    bound_log = log.bind(correlation_id=corr_id, hash_id=hash_id, node_name="tailor")
    bound_log.info("tailor_resume.start", force=force)

    # --- Fetch job ---
    job = _check_job_qualified(conn, hash_id, qualify_threshold)

    # --- Load prompt + resume ---
    prompt_text, prompt_version = _read_prompt()
    resume_text, _resume_sha = _read_resume()
    cache_key = _build_cache_key(hash_id, prompt_version, resume_text)

    bound_log.debug("tailor_resume.cache_key", cache_key=cache_key, prompt_version=prompt_version)

    # --- Cache check ---
    if not force:
        cached = get_cached_tailor(conn, hash_id)
        if cached and cached.get("cache_key") == cache_key:
            bound_log.info("tailor_resume.cache_hit", cache_key=cache_key)
            return TailoredResume(**{**cached, "cached": True})
        bound_log.info("tailor_resume.cache_miss", cache_key=cache_key)

    # --- Build Claude prompt ---
    user_message = (
        f"Job Title: {job.title}\n"
        f"Company: {job.company}\n\n"
        f"JOB DESCRIPTION:\n{job.description or ''}\n\n"
        f"CANDIDATE RESUME SUMMARY:\n{resume_text}\n"
    )
    system_prompt = prompt_text.replace("{job_title}", job.title or "").replace(
        "{company}", job.company or ""
    ).replace("{job_description}", job.description or "").replace(
        "{resume_summary}", resume_text
    ).replace("{prompt_version}", prompt_version)

    # --- Claude call ---
    raw_text, input_tokens, output_tokens = call_claude(
        system=system_prompt,
        user=user_message,
        api_key=api_key,
        max_tokens=_TAILOR_MAX_TOKENS,
        accumulated_cost=accumulated_cost,
        max_cost=max_cost,
    )
    bound_log.info(
        "tailor_resume.claude_ok",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    parsed = _parse_and_validate_tailor_response(raw_text, hash_id)

    # --- Build result ---
    now = datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "hash_id": hash_id,
        "job_title": job.title,
        "company": job.company,
        "tailored_summary": str(parsed["tailored_summary"])[:_SUMMARY_MAX_CHARS],
        "tailored_bullets": [str(b)[:_BULLETS_MAX_CHARS] for b in parsed["tailored_bullets"]][:_KEYWORDS_MAX_COUNT],
        "keywords_incorporated": [str(k)[:_KEYWORD_MAX_CHARS] for k in parsed.get("keywords_incorporated", [])],
        "cache_key": cache_key,
        "prompt_version": prompt_version,
        "tailored_at": now.isoformat(),
        "cached": False,
    }

    # --- Persist (only on success — no partial writes on parse error) ---
    write_tailor(conn, hash_id, result)
    bound_log.info("tailor_resume.done", cache_key=cache_key)
    return TailoredResume(**result)

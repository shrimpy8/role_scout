"""Claude-based metadata extraction and analysis orchestration for manual job ingestion."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, Field, field_validator

from role_scout.compat.db.seen_hashes import is_new_job
from role_scout.compat.logging import get_logger
from role_scout.compat.models import CandidateProfile, NormalizedJob, ScoredJob
from role_scout.compat.pipeline.scorer import score_jobs_batch
from role_scout.cost import CostKillSwitchError, check_cost_kill_switch
from role_scout.db import ro_conn
from role_scout.ingest.fetcher import fetch_url

logger = get_logger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "ingest_extraction.md"
_MAX_CONTENT_CHARS = 4000


# ---------------------------------------------------------------------------
# Pydantic schema for Claude's extraction response
# ---------------------------------------------------------------------------

class _ExtractionResponse(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    location: str = Field(default="Unknown", max_length=200)
    work_model: str = Field(default="unknown")
    comp_range: str | None = Field(default=None, max_length=100)
    description: str = Field(default="", max_length=2000)
    confidence_pct: int = Field(ge=0, le=100)

    @field_validator("work_model")
    @classmethod
    def _normalise_work_model(cls, v: str) -> str:
        v = v.lower().strip()
        if v in {"remote", "hybrid", "onsite", "on-site", "in-office"}:
            return "onsite" if v in {"onsite", "on-site", "in-office"} else v
        return "unknown"

    @field_validator("company", "title", mode="before")
    @classmethod
    def _strip_str(cls, v: object) -> str:
        return str(v).strip()


@dataclass
class ExtractedMetadata:
    company: str
    title: str
    location: str
    work_model: str
    description: str
    comp_range: str | None
    confidence_pct: int


@dataclass
class ExistingJobInfo:
    """Details of a job already present in qualified_jobs for dedup display."""
    hash_id: str
    company: str
    title: str
    source: str
    status: str
    match_pct: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "hash_id": self.hash_id,
            "company": self.company,
            "title": self.title,
            "source": self.source,
            "status": self.status,
            "match_pct": self.match_pct,
        }


@dataclass
class AnalysisResult:
    url: str
    status: Literal["ready", "thin", "failed"]
    confidence_pct: int = 0
    already_in_db: bool = False
    existing_job: ExistingJobInfo | None = field(default=None)
    scored_job: ScoredJob | None = field(default=None)
    error_msg: str | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "url": self.url,
            "status": self.status,
            "confidence_pct": self.confidence_pct,
            "already_in_db": self.already_in_db,
            "existing_job": self.existing_job.to_dict() if self.existing_job else None,
            "error_msg": self.error_msg,
            "scored_job": None,
        }
        if self.scored_job is not None:
            d["scored_job"] = self.scored_job.model_dump(mode="json")
        return d


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _load_prompt_template() -> str:
    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(f"Extraction prompt not found: {_PROMPT_PATH}")
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _parse_extraction_response(raw: str) -> _ExtractionResponse:
    """Extract the first JSON object from Claude's response and validate it."""
    text = raw.strip()
    # Find the outermost JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in Claude response")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON in Claude response: {exc}") from exc
    return _ExtractionResponse.model_validate(data)


def extract_metadata(
    raw_text: str,
    url: str,
    api_key: str,
    model: str,
) -> ExtractedMetadata:
    """Call Claude to extract structured job metadata from raw JD text.

    The raw_text is injected inside <job_posting> XML tags in the prompt,
    providing structural isolation against prompt injection in scraped content.
    """
    from role_scout.claude_client import CLAUDE_TIMEOUT_S  # deferred to avoid circular import

    template = _load_prompt_template()
    # Truncate and inject inside the XML boundary — adversarial content is structurally isolated.
    # str.replace() is used intentionally: re.sub() would interpret backreferences in the
    # replacement string (e.g. \1, \g<name>) if the JD text contains them.
    safe_text = raw_text[:_MAX_CONTENT_CHARS]
    prompt = template.replace("{raw_text}", safe_text)

    client = anthropic.Anthropic(api_key=api_key, timeout=CLAUDE_TIMEOUT_S)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_response = response.content[0].text
    except anthropic.APIError as exc:
        logger.exception("ingest_extract_api_error", url=url[:80])
        raise

    logger.debug("ingest_extract_raw", url=url[:80], response_excerpt=raw_response[:200])

    try:
        parsed = _parse_extraction_response(raw_response)
    except Exception:
        logger.exception("ingest_extract_parse_error", url=url[:80])
        raise

    return ExtractedMetadata(
        company=parsed.company,
        title=parsed.title,
        location=parsed.location,
        work_model=parsed.work_model,
        description=parsed.description,
        comp_range=parsed.comp_range,
        confidence_pct=parsed.confidence_pct,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def analyze_urls(
    urls: list[str],
    manual_texts: dict[str, str],
    candidate_profile: CandidateProfile | dict[str, Any],
    api_key: str,
    model: str,
    db_path: str,
    score_threshold: int = 0,
    max_cost: float = 5.0,
) -> list[AnalysisResult]:
    """Fetch, extract metadata, and score a list of JD URLs.

    manual_texts: mapping of url → pasted JD text (for thin/failed URLs).
    score_threshold: set to 0 so all scored jobs are returned regardless of match_pct.
    max_cost: budget cap in USD; each URL checks the kill-switch before calling Claude.
    """
    results: list[AnalysisResult] = []
    accumulated_cost: float = 0.0

    for url in urls:
        logger.info("ingest_analyze_url", url=url[:80])

        # --- Determine text source ---
        if url in manual_texts and manual_texts[url].strip():
            raw_text = manual_texts[url].strip()
            fetch_status = "ok"
        else:
            fetch_result = fetch_url(url)  # importable at module level for mocking
            raw_text = fetch_result.raw_text
            fetch_status = fetch_result.status

            if fetch_status == "failed":
                results.append(AnalysisResult(
                    url=url,
                    status="failed",
                    error_msg=fetch_result.error or "Fetch failed",
                ))
                continue

            if fetch_status == "thin":
                results.append(AnalysisResult(url=url, status="thin"))
                continue

        # --- Extract metadata ---
        try:
            check_cost_kill_switch(accumulated_cost, max_cost)
        except CostKillSwitchError:
            logger.warning("ingest_cost_kill_switch_extraction", url=url[:80], accumulated_cost=accumulated_cost)
            results.append(AnalysisResult(
                url=url,
                status="failed",
                error_msg="cost_kill_switch",
            ))
            continue

        try:
            meta = extract_metadata(raw_text, url, api_key, model)
            # Approximate extraction cost: 1024 output + ~2× content input tokens, with 1.5× safety margin.
            from role_scout.cost import compute_cost
            estimated_chars = len(raw_text[:_MAX_CONTENT_CHARS])
            est_input_tokens = int(estimated_chars / 4 * 1.5)
            accumulated_cost += compute_cost(est_input_tokens, 1024)
        except Exception:
            logger.exception("ingest_extract_failed", url=url[:80])
            results.append(AnalysisResult(
                url=url,
                status="failed",
                error_msg="Metadata extraction failed",
            ))
            continue

        # --- Build NormalizedJob ---
        norm_job = NormalizedJob(
            title=meta.title,
            company=meta.company,
            location=meta.location,
            city=_parse_city(meta.location),
            work_model=meta.work_model,
            url=url,
            source="manual",
            description=meta.description,
            comp_range=meta.comp_range,
            salary_visible=bool(meta.comp_range),
            fetched_at=datetime.now(UTC),
        )

        # --- Dedup check ---
        # Primary: check qualified_jobs to get full context (status, source, match_pct).
        # Fallback: check seen_hashes for jobs that were discovered but scored below threshold.
        existing_job: ExistingJobInfo | None = None
        already_in_db = False
        try:
            with ro_conn(db_path) as conn:
                row = conn.execute(
                    "SELECT hash_id, company, title, source, status, match_pct "
                    "FROM qualified_jobs WHERE hash_id = ?",
                    (norm_job.hash_id,),
                ).fetchone()
                if row:
                    already_in_db = True
                    existing_job = ExistingJobInfo(
                        hash_id=row["hash_id"],
                        company=row["company"],
                        title=row["title"],
                        source=row["source"],
                        status=row["status"],
                        match_pct=row["match_pct"],
                    )
                else:
                    # Not in qualified_jobs — check seen_hashes (below-threshold or expired)
                    already_in_db = not is_new_job(conn, norm_job.hash_id)
        except sqlite3.Error:
            logger.exception("ingest_dedup_check_error", hash_id=norm_job.hash_id)

        # --- Score ---
        try:
            check_cost_kill_switch(accumulated_cost, max_cost)
        except CostKillSwitchError:
            logger.warning("ingest_cost_kill_switch_scoring", url=url[:80], accumulated_cost=accumulated_cost)
            results.append(AnalysisResult(
                url=url,
                status="failed",
                error_msg="cost_kill_switch",
            ))
            continue

        try:
            scored_jobs = score_jobs_batch(
                [norm_job],
                candidate_profile,
                api_key,
                batch_size=1,
                qualify_threshold=score_threshold,
                run_id=None,
                model=model,
                accumulated_cost=accumulated_cost,
                max_cost=max_cost,
            )
        except Exception:
            logger.exception("ingest_score_failed", url=url[:80])
            results.append(AnalysisResult(
                url=url,
                status="failed",
                error_msg="Scoring failed",
            ))
            continue

        if not scored_jobs:
            # Score came back empty (Claude error) — create a minimal ScoredJob with 0 score
            logger.warning("ingest_score_empty", url=url[:80])
            results.append(AnalysisResult(
                url=url,
                status="failed",
                error_msg="Scorer returned no result",
            ))
            continue

        results.append(AnalysisResult(
            url=url,
            status="ready",
            confidence_pct=meta.confidence_pct,
            already_in_db=already_in_db,
            existing_job=existing_job,
            scored_job=scored_jobs[0],
        ))

    return results


def _parse_city(location: str) -> str:
    """Extract city portion from 'City, State' or 'City, Country' location strings."""
    if not location or location.lower() in {"remote", "unknown"}:
        return location
    parts = [p.strip() for p in location.split(",")]
    return parts[0] if parts else location

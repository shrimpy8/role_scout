"""Pydantic data models for the job search pipeline."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator


def _compute_hash_id(company: str, title: str, city: str, description: str = "") -> str:
    """SHA256[:16] of normalised company+title+city+description[:100] — stable dedup key."""
    raw = (
        f"{company.lower().strip()}"
        f"{title.lower().strip()}"
        f"{city.lower().strip()}"
        f"{description[:100]}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class NormalizedJob(BaseModel):
    """Cleaned job record produced by normalize_jobs() from any source."""

    title: str
    company: str
    location: str
    city: str = Field(default="")
    country: str = Field(default="")
    work_model: str = Field(default="unknown", description="remote | hybrid | onsite | unknown")
    url: str
    source: Literal["linkedin", "google_jobs", "trueup"]
    posted_date: str | None = None
    description: str | None = None
    comp_range: str | None = None
    salary_visible: bool = Field(
        default=False,
        description="True if comp is listed AND >= comp_min_k threshold ($175K)",
    )
    company_stage: str | None = None
    company_size: str | None = None
    domain_tags: list[str] = Field(default_factory=list)
    is_watchlist: bool = False
    apply_url: str | None = None
    fetched_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("work_model")
    @classmethod
    def normalise_work_model(cls, v: str) -> str:
        v = v.lower().strip()
        if v in {"remote", "hybrid", "onsite", "on-site", "in-office"}:
            return "onsite" if v in {"onsite", "on-site", "in-office"} else v
        return "unknown"

    @computed_field
    @property
    def hash_id(self) -> str:
        """SHA256[:16] of company+title+city+description[:100] — stable dedup key."""
        return _compute_hash_id(self.company, self.title, self.city, self.description or "")

    @model_validator(mode="after")
    def validate_salary_visible_invariant(self) -> NormalizedJob:
        if self.salary_visible and not self.comp_range:
            raise ValueError("salary_visible cannot be True when comp_range is None")
        return self


class ScoreResult(BaseModel):
    """Claude's scoring output for a single job."""

    hash_id: str

    seniority_score: int = Field(ge=0, le=30)
    domain_score: int = Field(ge=0, le=25)
    location_score: int = Field(ge=0, le=20)
    stage_score: int = Field(ge=0, le=15)
    comp_score: int = Field(ge=0, le=10, description="5 = neutral (salary not listed)")

    reasoning: str
    key_requirements: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)

    domain_alignment: str | None = None
    seniority_match: str | None = None
    location_fit: str | None = None
    company_stage_fit: str | None = None
    jd_alignment: str | None = None

    @computed_field
    @property
    def match_pct(self) -> int:
        """Weighted sum of all sub-scores, expressed as 0-100."""
        return (
            self.seniority_score
            + self.domain_score
            + self.location_score
            + self.stage_score
            + self.comp_score
        )


class ScoredJob(BaseModel):
    """Merged NormalizedJob + ScoreResult — written to qualified_jobs table."""

    # Identity
    hash_id: str
    title: str
    company: str
    location: str
    city: str = ""
    country: str = ""
    work_model: str = "unknown"
    url: str
    source: Literal["linkedin", "google_jobs", "trueup"]
    posted_date: str | None = None
    description: str | None = None
    description_snippet: str | None = None
    comp_range: str | None = None
    salary_visible: bool = False
    company_stage: str | None = None
    company_size: str | None = None
    domain_tags: list[str] = Field(default_factory=list)
    is_watchlist: bool = False
    apply_url: str | None = None

    # Scores
    match_pct: int = Field(ge=0, le=100)
    seniority_score: int | None = None
    domain_score: int | None = None
    location_score: int | None = None
    stage_score: int | None = None
    comp_score: int | None = None

    # Reasoning
    reasoning: str
    key_requirements: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    domain_alignment: str | None = None
    seniority_match: str | None = None
    location_fit: str | None = None
    company_stage_fit: str | None = None
    jd_alignment: str | None = None

    @field_validator("location_fit", "company_stage_fit", mode="before")
    @classmethod
    def _coerce_to_str(cls, v: object) -> str | None:
        if v is None:
            return None
        return str(v)

    # Workflow
    status: str = Field(default="new", description="new | reviewed | applied | rejected")
    jd_filename: str | None = None
    jd_downloaded: bool = False

    # Timestamps
    scored_at: datetime = Field(default_factory=datetime.utcnow)
    fetched_at: datetime | None = None
    run_id: str | None = None

    @classmethod
    def from_normalized_and_score(
        cls, job: NormalizedJob, score: ScoreResult, run_id: str | None = None
    ) -> ScoredJob:
        """Merge a NormalizedJob and ScoreResult into a ScoredJob."""
        return cls(
            hash_id=job.hash_id,
            title=job.title,
            company=job.company,
            location=job.location,
            city=job.city,
            country=job.country,
            work_model=job.work_model,
            url=job.url,
            source=job.source,
            posted_date=job.posted_date,
            description=job.description,
            description_snippet=(job.description or "")[:300] or None,
            comp_range=job.comp_range,
            salary_visible=job.salary_visible,
            company_stage=job.company_stage,
            company_size=job.company_size,
            domain_tags=job.domain_tags,
            is_watchlist=job.is_watchlist,
            apply_url=job.apply_url,
            match_pct=score.match_pct,
            seniority_score=score.seniority_score,
            domain_score=score.domain_score,
            location_score=score.location_score,
            stage_score=score.stage_score,
            comp_score=score.comp_score,
            reasoning=score.reasoning,
            key_requirements=score.key_requirements,
            red_flags=score.red_flags,
            domain_alignment=score.domain_alignment,
            seniority_match=score.seniority_match,
            location_fit=score.location_fit,
            company_stage_fit=score.company_stage_fit,
            jd_alignment=score.jd_alignment,
            fetched_at=job.fetched_at,
            run_id=run_id,
        )


class CandidateProfile(BaseModel):
    """Typed candidate profile loaded from candidate_profile.yaml."""

    name: str
    target_roles: list[str]
    seniority_level: str
    preferred_domains: list[str]
    location: str
    remote_ok: bool
    target_stages: list[str]
    comp_min_k: int
    skills: list[str]
    must_have_keywords: list[str] = Field(default_factory=list)
    anti_keywords: list[str] = Field(default_factory=list)
    posted_within: str = Field(default="month", description="Recency filter: '24h' | 'week' | 'month'")
    max_per_source: int = Field(default=50, ge=1, le=200, description="Max results per source per run")


def load_candidate_profile(path: str) -> CandidateProfile:
    """Load and validate candidate_profile.yaml. Raises on missing or invalid fields."""
    import yaml  # noqa: PLC0415

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return CandidateProfile.model_validate(data)


class RunLog(BaseModel):
    """Pipeline run record written to run_log table."""

    run_id: str
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    status: str = Field(default="running", description="running | completed | failed")
    trigger_type: str = Field(default="manual", description="scheduled | manual | dry_run")

    source_linkedin: int = 0
    source_google_jobs: int = 0
    source_wellfound: int = 0
    source_trueup: int = 0

    total_fetched: int = 0
    total_new: int = 0
    total_qualified: int = 0

    watchlist_hits: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)

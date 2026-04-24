"""Pydantic v2 schema for the ground truth eval dataset."""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field


class HumanSubscores(BaseModel):
    """Subscores matching the Phase 1 scorer rubric (all 0–10)."""

    comp_score: Annotated[int, Field(ge=0, le=10)]
    role_fit: Annotated[int, Field(ge=0, le=10)]
    remote_score: Annotated[int, Field(ge=0, le=10)]
    domain_score: Annotated[int, Field(ge=0, le=10)]
    seniority_score: Annotated[int, Field(ge=0, le=10)]


class GroundTruthJob(BaseModel):
    """One ground truth entry from the eval dataset.

    Each entry pairs a job description with a human-assigned score and metadata
    used to evaluate the pipeline's scoring accuracy.
    """

    hash_id: Annotated[str, Field(pattern=r"^[a-f0-9]{16}$", description="16-char hex job identifier")]
    jd_text: Annotated[str, Field(min_length=1, description="Job description text")]
    human_score: Annotated[int, Field(ge=0, le=100, description="Human-assigned composite score 0–100")]
    human_subscores: HumanSubscores
    human_rationale: Annotated[str, Field(min_length=1, description="1–2 sentence justification for score")]
    edge_case_tag: str | None = Field(
        default=None,
        description="Edge case category: no_comp | remote | watchlist | reject | staff_principal | non_sf | None",
    )

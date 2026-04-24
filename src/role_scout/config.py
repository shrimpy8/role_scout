"""Application settings loaded from environment / .env via pydantic-settings."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, loaded from environment variables and `.env`.

    All API keys are read from environment; non-sensitive tuning params have defaults.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ---- Required API keys ----
    ANTHROPIC_API_KEY: str
    SERPAPI_KEY: str
    APIFY_TOKEN: str
    IMAP_EMAIL: str
    IMAP_APP_PASSWORD: str

    # ---- Optional eval keys (only required when running evals) ----
    OPENAI_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None

    # ---- Pipeline tuning ----
    # Coerced from float to int to accept legacy Phase 1 .env values like "0.69"
    SCORE_THRESHOLD: int = Field(default=85, ge=0, le=100)
    REFLECTION_ENABLED: bool = True
    REFLECTION_BAND_LOW: int = Field(default=70, ge=0, le=100)
    REFLECTION_BAND_HIGH: int = Field(default=89, ge=0, le=100)

    @field_validator("SCORE_THRESHOLD", mode="before")
    @classmethod
    def _coerce_threshold(cls, v: object) -> int:
        """Phase 1 .env used 0–1 floats; Phase 2 uses 0–100 ints. Auto-upgrade."""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return v  # type: ignore[return-value]
        if 0 < f < 1:
            return round(f * 100)
        return round(f)
    RUN_MODE: Literal["linear", "agentic", "shadow"] = "shadow"
    MAX_COST_USD: float = Field(default=5.00, ge=0)
    INTERRUPT_TTL_HOURS: float = Field(default=4.0, ge=0.5, le=24.0)

    # ---- Observability ----
    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: str | None = None
    LANGSMITH_PROJECT: str = "role_scout"
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str | None = None

    # ---- Storage ----
    DB_PATH: Path = Path("../auto_jobsearch/output/jobsearch.db")
    RESUME_SUMMARY_PATH: Path = Path("config/resume_summary.md")

    # ---- Source health ----
    SERPAPI_MIN_QUOTA: int = Field(default=10, ge=1)
    SOURCE_HEALTH_WINDOW: int = Field(default=3, ge=1, le=20)

    @field_validator("REFLECTION_BAND_HIGH")
    @classmethod
    def _band_high_gt_low(cls, v: int, info: object) -> int:
        low = getattr(info, "data", {}).get("REFLECTION_BAND_LOW", 70)
        if v <= low:
            raise ValueError(
                f"REFLECTION_BAND_HIGH ({v}) must be greater than REFLECTION_BAND_LOW ({low})"
            )
        return v

    @field_validator("LOG_LEVEL")
    @classmethod
    def _valid_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid:
            raise ValueError(f"LOG_LEVEL must be one of {valid}")
        return v.upper()


def get_settings() -> Settings:
    """Return a Settings instance (reads .env once; call at module init)."""
    return Settings()  # type: ignore[call-arg]

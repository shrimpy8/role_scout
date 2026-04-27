"""Cost tracking for Claude API calls — per-run accumulation and kill-switch."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from role_scout.config import Settings

# Fallback pricing used when callers don't pass Settings (e.g. tests, CLI tools).
# Production callers should always pass settings to pick up .env overrides.
_FALLBACK_INPUT_COST_PER_MTOK: float = 3.0
_FALLBACK_OUTPUT_COST_PER_MTOK: float = 15.0


class CostKillSwitchError(RuntimeError):
    """Raised before a Claude call when accumulated cost has exceeded MAX_COST_USD."""


def compute_cost(
    input_tokens: int,
    output_tokens: int,
    *,
    input_cost_per_mtok: float = _FALLBACK_INPUT_COST_PER_MTOK,
    output_cost_per_mtok: float = _FALLBACK_OUTPUT_COST_PER_MTOK,
) -> float:
    """Return USD cost for the given token counts using the supplied per-MTok rates."""
    return (input_tokens * input_cost_per_mtok + output_tokens * output_cost_per_mtok) / 1_000_000


def compute_cost_from_settings(input_tokens: int, output_tokens: int, settings: "Settings") -> float:
    """Convenience wrapper that reads pricing from Settings — use this in all production nodes."""
    return compute_cost(
        input_tokens,
        output_tokens,
        input_cost_per_mtok=settings.CLAUDE_INPUT_COST_PER_MTOK,
        output_cost_per_mtok=settings.CLAUDE_OUTPUT_COST_PER_MTOK,
    )


def check_cost_kill_switch(accumulated_cost: float, max_cost: float) -> None:
    """Raise CostKillSwitchError if accumulated_cost >= max_cost.

    Called before every Claude API call to prevent runaway spend.
    """
    if accumulated_cost >= max_cost:
        raise CostKillSwitchError(
            f"Cost kill switch: accumulated ${accumulated_cost:.4f} >= cap ${max_cost:.2f}"
        )

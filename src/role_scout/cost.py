"""Cost tracking for Claude API calls — per-run accumulation and kill-switch."""
from __future__ import annotations

# Pricing for claude-sonnet-4-6 ($ per million tokens)
INPUT_COST_PER_MTOK: float = 3.0
OUTPUT_COST_PER_MTOK: float = 15.0


class CostKillSwitchError(RuntimeError):
    """Raised before a Claude call when accumulated cost has exceeded MAX_COST_USD."""


def compute_cost(input_tokens: int, output_tokens: int) -> float:
    """Return USD cost for the given token counts.

    Formula: (input_tokens * 3.0 + output_tokens * 15.0) / 1_000_000
    Matches claude-sonnet-4-6 pricing as of 2026-04.
    """
    return (input_tokens * INPUT_COST_PER_MTOK + output_tokens * OUTPUT_COST_PER_MTOK) / 1_000_000


def check_cost_kill_switch(accumulated_cost: float, max_cost: float) -> None:
    """Raise CostKillSwitchError if accumulated_cost >= max_cost.

    Called before every Claude API call to prevent runaway spend.
    """
    if accumulated_cost >= max_cost:
        raise CostKillSwitchError(
            f"Cost kill switch: accumulated ${accumulated_cost:.4f} >= cap ${max_cost:.2f}"
        )

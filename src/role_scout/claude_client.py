"""Phase 2 Claude API wrapper — token tracking + cost kill-switch enforcement."""
from __future__ import annotations

import anthropic
import structlog

from role_scout.cost import CostKillSwitchError, check_cost_kill_switch, compute_cost

log = structlog.get_logger()

CLAUDE_TIMEOUT_S: float = 120.0
_DEFAULT_MAX_TOKENS = 4096


def call_claude(
    system: str,
    user: str,
    api_key: str,
    *,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    accumulated_cost: float = 0.0,
    max_cost: float = 5.0,
    input_cost_per_mtok: float = 3.0,
    output_cost_per_mtok: float = 15.0,
) -> tuple[str, int, int]:
    """Call Claude and return (text, input_tokens, output_tokens).

    Raises CostKillSwitchError before the API call if accumulated_cost >= max_cost.
    The caller is responsible for adding the returned token counts to state.
    """
    check_cost_kill_switch(accumulated_cost, max_cost)

    client = anthropic.Anthropic(api_key=api_key, timeout=CLAUDE_TIMEOUT_S)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    input_tokens: int = response.usage.input_tokens
    output_tokens: int = response.usage.output_tokens
    call_cost = compute_cost(
        input_tokens,
        output_tokens,
        input_cost_per_mtok=input_cost_per_mtok,
        output_cost_per_mtok=output_cost_per_mtok,
    )

    log.info(
        "claude_call_complete",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        call_cost_usd=round(call_cost, 6),
        accumulated_cost_usd=round(accumulated_cost + call_cost, 6),
    )

    return response.content[0].text, input_tokens, output_tokens

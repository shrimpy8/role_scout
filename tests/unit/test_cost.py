"""Unit tests for cost.py — T44, T45, compute_cost, kill-switch."""
from __future__ import annotations

import pytest


class TestComputeCost:
    def test_t44_known_token_count(self):
        """T44: 100k input + 50k output → $1.05 (spec example)."""
        from role_scout.cost import compute_cost

        cost = compute_cost(100_000, 50_000)
        # (100_000 * 3.0 + 50_000 * 15.0) / 1_000_000 = (300_000 + 750_000) / 1_000_000 = 1.05
        assert abs(cost - 1.05) < 1e-9

    def test_zero_tokens_zero_cost(self):
        from role_scout.cost import compute_cost
        assert compute_cost(0, 0) == 0.0

    def test_input_only(self):
        from role_scout.cost import compute_cost
        # 1M input tokens → $3.00
        assert abs(compute_cost(1_000_000, 0) - 3.0) < 1e-9

    def test_output_only(self):
        from role_scout.cost import compute_cost
        # 1M output tokens → $15.00
        assert abs(compute_cost(0, 1_000_000) - 15.0) < 1e-9

    def test_small_token_count(self):
        from role_scout.cost import compute_cost
        # 1000 input + 500 output → (3.0 + 7.5) / 1000 = $0.0105
        cost = compute_cost(1_000, 500)
        expected = (1_000 * 3.0 + 500 * 15.0) / 1_000_000
        assert abs(cost - expected) < 1e-12


class TestKillSwitch:
    def test_t45_kill_switch_fires_at_limit(self):
        """T45: accumulated cost >= max_cost raises CostKillSwitchError."""
        from role_scout.cost import CostKillSwitchError, check_cost_kill_switch

        with pytest.raises(CostKillSwitchError, match="kill switch"):
            check_cost_kill_switch(accumulated_cost=5.0, max_cost=5.0)

    def test_kill_switch_fires_above_limit(self):
        from role_scout.cost import CostKillSwitchError, check_cost_kill_switch

        with pytest.raises(CostKillSwitchError):
            check_cost_kill_switch(accumulated_cost=5.01, max_cost=5.0)

    def test_kill_switch_passes_below_limit(self):
        from role_scout.cost import check_cost_kill_switch

        check_cost_kill_switch(accumulated_cost=4.99, max_cost=5.0)  # must not raise

    def test_kill_switch_zero_cost(self):
        from role_scout.cost import check_cost_kill_switch

        check_cost_kill_switch(accumulated_cost=0.0, max_cost=5.0)  # must not raise

    def test_scoring_node_fires_kill_switch(self):
        """T45: scoring_node with accumulated_cost >= MAX_COST_USD returns cancel_reason."""
        from unittest.mock import MagicMock, patch
        from role_scout.nodes.scoring import scoring_node

        profile = MagicMock()
        state = {
            "run_id": "run_test",
            "candidate_profile": profile,
            "enriched_jobs": [MagicMock() for _ in range(5)],
            "qualify_threshold": 70,
            "total_cost_usd": 5.01,  # already over limit
            "scoring_tokens_in": 0,
            "scoring_tokens_out": 0,
            "errors": [],
        }

        with patch("role_scout.nodes.scoring.Settings") as MockSettings:
            settings = MagicMock()
            settings.SCORE_THRESHOLD = 70
            settings.MAX_COST_USD = 5.0
            settings.ANTHROPIC_API_KEY = "sk-ant-test"
            MockSettings.return_value = settings

            with patch("role_scout.nodes.scoring.score_jobs_batch") as mock_scorer:
                result = scoring_node(state)

        # Kill switch should have fired — scorer should NOT have been called
        mock_scorer.assert_not_called()
        assert result.get("cancel_reason") == "cost_kill_switch"
        assert result["scored_jobs"] == []

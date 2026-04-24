"""Unit tests for review_node — T5, T9 (HiTL approve/cancel/auto-approve)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_scored_job(match_pct: int, hash_id: str = "a" * 16) -> MagicMock:
    job = MagicMock()
    job.match_pct = match_pct
    job.hash_id = hash_id
    return job


def _run_review(
    trigger_type: str = "manual",
    scored_jobs: list | None = None,
    qualify_threshold: int = 85,
    interrupt_return: str = "approve",
) -> dict:
    from role_scout.nodes.review import review_node

    state = {
        "run_id": "run_aabbccdd-test0",
        "trigger_type": trigger_type,
        "scored_jobs": scored_jobs or [],
        "qualify_threshold": qualify_threshold,
    }

    with patch("role_scout.nodes.review.interrupt", return_value=interrupt_return) as mock_interrupt:
        result = review_node(state)

    return result, mock_interrupt


class TestReviewNodeAutoApprove:
    def test_t9_mcp_auto_approves_no_interrupt(self):
        """T9: trigger_type='mcp' → human_approved=True, interrupt() never called."""
        result, mock_interrupt = _run_review(trigger_type="mcp")

        assert result["human_approved"] is True
        assert result["cancel_reason"] is None
        mock_interrupt.assert_not_called()

    def test_t9_scheduled_auto_approves_no_interrupt(self):
        """T9: trigger_type='scheduled' → human_approved=True, interrupt() never called."""
        result, mock_interrupt = _run_review(trigger_type="scheduled")

        assert result["human_approved"] is True
        assert result["cancel_reason"] is None
        mock_interrupt.assert_not_called()

    def test_auto_approve_with_qualified_jobs(self):
        jobs = [_make_scored_job(90), _make_scored_job(80)]
        result, mock_interrupt = _run_review(trigger_type="scheduled", scored_jobs=jobs)

        assert result["human_approved"] is True
        mock_interrupt.assert_not_called()


class TestReviewNodeInteractive:
    def test_t5_manual_approve(self):
        """T5: trigger_type='manual' + decision='approve' → human_approved=True."""
        result, mock_interrupt = _run_review(trigger_type="manual", interrupt_return="approve")

        assert result["human_approved"] is True
        assert result["cancel_reason"] is None
        mock_interrupt.assert_called_once()

    def test_t5_manual_cancel(self):
        """T5: trigger_type='manual' + decision='cancel' → human_approved=False, cancel_reason='user_cancel'."""
        result, mock_interrupt = _run_review(trigger_type="manual", interrupt_return="cancel")

        assert result["human_approved"] is False
        assert result["cancel_reason"] == "user_cancel"
        mock_interrupt.assert_called_once()

    def test_ttl_expired_sets_ttl_reason(self):
        """decision='ttl_expired' → cancel_reason='ttl_expired'."""
        result, mock_interrupt = _run_review(trigger_type="manual", interrupt_return="ttl_expired")

        assert result["human_approved"] is False
        assert result["cancel_reason"] == "ttl_expired"

    def test_dry_run_triggers_interrupt(self):
        """dry_run trigger type goes through interactive path (not auto-approve)."""
        result, mock_interrupt = _run_review(trigger_type="dry_run", interrupt_return="approve")

        mock_interrupt.assert_called_once()

    def test_interrupt_payload_contains_run_id(self):
        """interrupt() receives dict with run_id."""
        _, mock_interrupt = _run_review(trigger_type="manual")

        call_args = mock_interrupt.call_args[0][0]
        assert "run_id" in call_args
        assert call_args["run_id"] == "run_aabbccdd-test0"

    def test_qualified_count_computed_from_threshold(self):
        """qualified_count in interrupt payload reflects threshold filtering."""
        jobs = [
            _make_scored_job(95),  # above 85
            _make_scored_job(90),  # above 85
            _make_scored_job(70),  # below 85
        ]
        _, mock_interrupt = _run_review(
            trigger_type="manual",
            scored_jobs=jobs,
            qualify_threshold=85,
        )

        payload = mock_interrupt.call_args[0][0]
        assert payload["qualified_count"] == 2

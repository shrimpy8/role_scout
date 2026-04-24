"""Unit tests for output_node — T4 (approve path), T5 (cancel path), cancel_reason paths."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest


def _make_job(hash_id: str, match_pct: int, description: str | None = None) -> MagicMock:
    job = MagicMock()
    job.hash_id = hash_id
    job.match_pct = match_pct
    job.title = "Software Engineer"
    job.company = "Acme"
    job.source = "linkedin"
    job.description = description
    return job


def _run_output(
    human_approved: bool = True,
    cancel_reason: str | None = None,
    scored_jobs: list | None = None,
    qualify_threshold: int = 85,
    errors: list | None = None,
) -> dict:
    from role_scout.nodes.output import output_node

    state = {
        "run_id": "run_aabbccdd-out01",
        "human_approved": human_approved,
        "cancel_reason": cancel_reason,
        "scored_jobs": scored_jobs or [],
        "qualify_threshold": qualify_threshold,
        "scoring_tokens_in": 1000,
        "scoring_tokens_out": 200,
        "reflection_tokens_in": 500,
        "reflection_tokens_out": 100,
        "total_cost_usd": 0.05,
        "source_health": {},
        "errors": errors or [],
    }

    mock_conn = MagicMock(spec=sqlite3.Connection)

    with patch("role_scout.nodes.output.Settings") as MockSettings, \
         patch("role_scout.nodes.output.get_rw_conn", return_value=mock_conn), \
         patch("role_scout.nodes.output.insert_qualified_job") as mock_insert, \
         patch("role_scout.nodes.output.upsert_seen_hash") as mock_upsert, \
         patch("role_scout.nodes.output.update_run") as mock_update_run, \
         patch("role_scout.nodes.output.set_run_status") as mock_set_status, \
         patch("role_scout.nodes.output.write_source_health"), \
         patch("pathlib.Path.mkdir"), \
         patch("pathlib.Path.write_text"):

        settings = MagicMock()
        settings.SCORE_THRESHOLD = 85
        settings.DB_PATH = "/tmp/test.db"
        MockSettings.return_value = settings

        result = output_node(state)

    return result, mock_insert, mock_upsert, mock_update_run, mock_set_status


class TestOutputNodeApprovedPath:
    def test_t4_qualified_jobs_inserted(self):
        """T4: human_approved=True → qualified jobs inserted into DB."""
        jobs = [
            _make_job("a" * 16, 95),
            _make_job("b" * 16, 90),
            _make_job("c" * 16, 70),  # below threshold=85 → not inserted
        ]
        result, mock_insert, mock_upsert, _, _ = _run_output(
            human_approved=True, scored_jobs=jobs, qualify_threshold=85
        )

        assert result["exported_count"] == 2
        assert mock_insert.call_count == 2
        assert mock_upsert.call_count == 2

    def test_t4_run_log_status_completed(self):
        """T4: approved run → update_run called with status=completed."""
        jobs = [_make_job("a" * 16, 90)]
        _, _, _, mock_update_run, mock_set_status = _run_output(human_approved=True, scored_jobs=jobs)

        assert mock_update_run.call_count == 1
        call_kwargs = mock_update_run.call_args[1]
        assert call_kwargs["status"] == "completed"
        mock_set_status.assert_not_called()

    def test_t4_tokens_accumulated(self):
        """T4: token totals (scoring + reflection) passed to update_run."""
        _, _, _, mock_update_run, _ = _run_output(human_approved=True)

        kwargs = mock_update_run.call_args[1]
        assert kwargs["input_tokens"] == 1500   # 1000 + 500
        assert kwargs["output_tokens"] == 300   # 200 + 100

    def test_no_jobs_exported_when_all_below_threshold(self):
        jobs = [_make_job("a" * 16, 70), _make_job("b" * 16, 60)]
        result, mock_insert, _, _, _ = _run_output(
            human_approved=True, scored_jobs=jobs, qualify_threshold=85
        )
        assert result["exported_count"] == 0
        mock_insert.assert_not_called()


class TestOutputNodeCancelledPath:
    def test_t5_cancel_zero_job_writes(self):
        """T5: human_approved=False → no insert_qualified_job calls."""
        jobs = [_make_job("a" * 16, 90), _make_job("b" * 16, 95)]
        result, mock_insert, mock_upsert, mock_update_run, _ = _run_output(
            human_approved=False, cancel_reason="user_cancel", scored_jobs=jobs
        )

        assert result["exported_count"] == 0
        mock_insert.assert_not_called()
        mock_upsert.assert_not_called()
        mock_update_run.assert_not_called()

    def test_user_cancel_sets_cancelled_status(self):
        """cancel_reason='user_cancel' → set_run_status called with 'cancelled'."""
        _, _, _, _, mock_set_status = _run_output(
            human_approved=False, cancel_reason="user_cancel"
        )
        call_kwargs = mock_set_status.call_args[1]
        assert mock_set_status.call_args[0][2] == "cancelled"

    def test_ttl_expired_sets_cancelled_ttl_status(self):
        """T6 (partial): cancel_reason='ttl_expired' → set_run_status 'cancelled_ttl'."""
        _, _, _, _, mock_set_status = _run_output(
            human_approved=False, cancel_reason="ttl_expired"
        )
        assert mock_set_status.call_args[0][2] == "cancelled_ttl"

    def test_crippled_fetch_sets_cancelled_status(self):
        _, _, _, _, mock_set_status = _run_output(
            human_approved=False, cancel_reason="crippled_fetch"
        )
        assert mock_set_status.call_args[0][2] == "cancelled"

    def test_errors_propagated(self):
        initial_errors = ["preflight_error: something failed"]
        result, _, _, _, _ = _run_output(
            human_approved=False, cancel_reason="user_cancel", errors=initial_errors
        )
        assert "preflight_error: something failed" in result["errors"]

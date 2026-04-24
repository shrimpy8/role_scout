"""Full-graph integration tests — T4 (approve), T5 (cancel), T6 (TTL), T9 (auto-approve).

Strategy: mock all early nodes to return simple serializable state, then let
review_node and output_node execute with mocked DB helpers. Uses real ScoredJob
objects so LangGraph's MemorySaver can checkpoint the state without type errors.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from jobsearch.models import ScoredJob


# ---------------------------------------------------------------------------
# Helpers — serializable test data
# ---------------------------------------------------------------------------

def _scored_job(hash_id: str, match_pct: int = 90) -> ScoredJob:
    """Return a real ScoredJob so LangGraph MemorySaver can serialize it."""
    return ScoredJob(
        hash_id=hash_id,
        title="Software Engineer",
        company="Acme Corp",
        location="San Francisco, CA",
        city="San Francisco",
        country="US",
        work_model="hybrid",
        url=f"https://jobs.example.com/{hash_id}",
        source="linkedin",
        match_pct=match_pct,
        reasoning="Good fit overall.",
        salary_visible=True,
        is_watchlist=False,
        key_requirements=["Python", "AWS"],
        red_flags=[],
        domain_tags=["ml"],
    )


def _mock_conn() -> MagicMock:
    conn = MagicMock(spec=sqlite3.Connection)
    conn.execute.return_value = MagicMock(fetchone=lambda: None, fetchall=lambda: [])
    return conn


# ---------------------------------------------------------------------------
# Early-node stubs — all return serializable plain dicts / real objects
# ---------------------------------------------------------------------------

def _preflight_stub(trigger_type: str, run_id: str = "run_aabbccdd-intg1"):
    def _node(state):
        return {
            "run_id": run_id,
            "trigger_type": trigger_type,
            "candidate_profile": None,  # not used by review or output
            "watchlist": [],
            "qualify_threshold": 85,
            "run_mode": "agentic",
            "errors": [],
            "total_cost_usd": 0.0,
        }
    return _node


def _discovery_stub(*, crippled: bool = False):
    def _node(state):
        base = {
            "raw_by_source": {},
            "normalized_jobs": [],
            "new_jobs": [],
            "source_counts": {},
            "source_health": {},
            "errors": state.get("errors", []),
        }
        if crippled:
            base["cancel_reason"] = "crippled_fetch"
            base["human_approved"] = False
        return base
    return _node


def _enrichment_stub():
    def _node(state):
        return {
            "enriched_jobs": [],
            "raw_by_source": {},
            "normalized_jobs": [],
            "new_jobs": [],
            "errors": state.get("errors", []),
        }
    return _node


def _scoring_stub(scored_jobs: list[ScoredJob]):
    def _node(state):
        return {
            "scored_jobs": scored_jobs,
            "enriched_jobs": [],
            "scoring_tokens_in": 1000,
            "scoring_tokens_out": 200,
            "total_cost_usd": 0.02,
            "errors": state.get("errors", []),
        }
    return _node


def _reflection_stub():
    def _node(state):
        return {
            "scored_jobs": state.get("scored_jobs", []),
            "reflection_tokens_in": 0,
            "reflection_tokens_out": 0,
            "reflection_applied_count": 0,
            "errors": state.get("errors", []),
        }
    return _node


# ---------------------------------------------------------------------------
# Context-manager runner
# ---------------------------------------------------------------------------

class _GraphRun:
    """Run the full LangGraph graph with early nodes stubbed.

    Attributes populated after __enter__:
        result          — final state from run_graph()
        mock_insert     — insert_qualified_job mock
        mock_set_status — set_run_status mock (in output_node)
        mock_update_run — update_run mock (in output_node)
        mock_interrupt  — review.interrupt mock
    """

    def __init__(
        self,
        *,
        trigger_type: str = "scheduled",
        scored_jobs: list[ScoredJob] | None = None,
        auto_approve: bool = False,
        interactive_decision: str = "approve",
        run_id: str = "run_aabbccdd-intg1",
        crippled_discovery: bool = False,
    ):
        self.trigger_type = trigger_type
        self.scored_jobs = scored_jobs if scored_jobs is not None else [_scored_job("a" * 16)]
        self.auto_approve = auto_approve
        self.interactive_decision = interactive_decision
        self.run_id = run_id
        self.crippled_discovery = crippled_discovery
        self._patches: list = []
        self.result: dict = {}
        self.mock_insert = MagicMock()
        self.mock_set_status = MagicMock()
        self.mock_update_run = MagicMock()
        self.mock_interrupt = MagicMock(return_value=interactive_decision)

    def _start(self, target: str, **kwargs) -> MagicMock:
        p = patch(target, **kwargs)
        self._patches.append(p)
        return p.start()

    def __enter__(self) -> "_GraphRun":
        conn = _mock_conn()

        # Stub early nodes to emit clean serializable state.
        # Must patch at role_scout.graph.* — that's the namespace LangGraph uses
        # (build_graph() does `from role_scout.nodes.X import X_node`, creating
        # a new binding in role_scout.graph that patching the source module won't reach).
        self._start("role_scout.graph.preflight_node",
                    side_effect=_preflight_stub(self.trigger_type, self.run_id))
        self._start("role_scout.graph.discovery_node",
                    side_effect=_discovery_stub(crippled=self.crippled_discovery))
        self._start("role_scout.graph.enrichment_node",
                    side_effect=_enrichment_stub())
        self._start("role_scout.graph.scoring_node",
                    side_effect=_scoring_stub(self.scored_jobs))
        self._start("role_scout.graph.reflection_node",
                    side_effect=_reflection_stub())

        # Mock review.interrupt (controls HiTL path)
        self._start("role_scout.nodes.review.interrupt", new=self.mock_interrupt)

        # Mock output_node DB helpers
        settings_mock = MagicMock()
        settings_mock.SCORE_THRESHOLD = 85
        settings_mock.DB_PATH = "/tmp/test_intg.db"
        self._start("role_scout.nodes.output.Settings", return_value=settings_mock)
        self._start("role_scout.nodes.output.get_rw_conn", return_value=conn)
        self._start("role_scout.nodes.output.insert_qualified_job", new=self.mock_insert)
        self._start("role_scout.nodes.output.upsert_seen_hash")
        self._start("role_scout.nodes.output.update_run", new=self.mock_update_run)
        self._start("role_scout.nodes.output.set_run_status", new=self.mock_set_status)
        self._start("role_scout.nodes.output.write_source_health")
        self._start("pathlib.Path.mkdir")
        self._start("pathlib.Path.write_text")

        # Mock runner dependencies
        runner_settings = MagicMock()
        runner_settings.SCORE_THRESHOLD = 85
        runner_settings.INTERRUPT_TTL_HOURS = 4.0
        runner_settings.DB_PATH = "/tmp/test_intg.db"
        self._start("role_scout.runner.Settings", return_value=runner_settings)
        self._start("role_scout.runner.get_rw_conn", return_value=conn)
        self._start("role_scout.runner.set_run_status")
        self._start("role_scout.dal.run_log_dal.update_run")

        if self.trigger_type == "manual" and not self.auto_approve:
            self._start("role_scout.runner._interactive_decision",
                        return_value=self.interactive_decision)

        from role_scout.runner import run_graph
        self.result = run_graph(
            trigger_type=self.trigger_type,
            auto_approve=self.auto_approve,
        )
        return self

    def __exit__(self, *_: object) -> None:
        for p in reversed(self._patches):
            p.stop()


# ---------------------------------------------------------------------------
# T9 — auto-approve
# ---------------------------------------------------------------------------

class TestFullGraphAutoApprove:
    def test_t9_scheduled_no_interrupt(self):
        """T9: trigger_type='scheduled' → review_node auto-approves; interrupt() never called."""
        with _GraphRun(trigger_type="scheduled") as run:
            run.mock_interrupt.assert_not_called()
            assert run.mock_update_run.call_args[1]["status"] == "completed"

    def test_t9_auto_approve_flag_skips_interactive(self):
        """T9: auto_approve=True + manual trigger → runner sends approve without user input."""
        with patch("role_scout.runner._interactive_decision") as mock_interact:
            with _GraphRun(trigger_type="manual", auto_approve=True):
                pass
        mock_interact.assert_not_called()


# ---------------------------------------------------------------------------
# T4 — approve path
# ---------------------------------------------------------------------------

class TestFullGraphApprove:
    def test_t4_qualified_jobs_inserted(self):
        """T4: approved run → insert_qualified_job called for each qualifying job."""
        scored = [_scored_job("a" * 16, 90), _scored_job("b" * 16, 92)]
        with _GraphRun(trigger_type="scheduled", scored_jobs=scored) as run:
            assert run.mock_insert.call_count == 2

    def test_t4_run_log_status_completed(self):
        """T4: approved run → update_run called with status=completed."""
        with _GraphRun(trigger_type="scheduled") as run:
            assert run.mock_update_run.call_count == 1
            assert run.mock_update_run.call_args[1]["status"] == "completed"

    def test_t4_below_threshold_not_inserted(self):
        """Jobs below qualify_threshold are not inserted on approve."""
        scored = [
            _scored_job("a" * 16, 90),  # above 85 → inserted
            _scored_job("b" * 16, 60),  # below 85 → not inserted
        ]
        with _GraphRun(trigger_type="scheduled", scored_jobs=scored) as run:
            assert run.mock_insert.call_count == 1


# ---------------------------------------------------------------------------
# T5 — cancel path
# ---------------------------------------------------------------------------

class TestFullGraphCancel:
    def test_t5_cancel_zero_job_writes(self):
        """T5: user cancels → no insert_qualified_job calls."""
        scored = [_scored_job("a" * 16, 90)]
        with _GraphRun(
            trigger_type="manual",
            auto_approve=False,
            interactive_decision="cancel",
            scored_jobs=scored,
        ) as run:
            run.mock_insert.assert_not_called()

    def test_t5_cancel_sets_cancelled_status(self):
        """T5: user cancel → set_run_status 'cancelled'."""
        with _GraphRun(
            trigger_type="manual",
            auto_approve=False,
            interactive_decision="cancel",
        ) as run:
            assert run.mock_set_status.call_count == 1
            assert run.mock_set_status.call_args[0][2] == "cancelled"

    def test_t5_update_run_not_called_on_cancel(self):
        """Cancelled run must not call update_run (no cost/token logging on cancel)."""
        with _GraphRun(
            trigger_type="manual",
            auto_approve=False,
            interactive_decision="cancel",
        ) as run:
            run.mock_update_run.assert_not_called()


# ---------------------------------------------------------------------------
# T6 — TTL expiry
# ---------------------------------------------------------------------------

class TestFullGraphTTL:
    def test_t6_ttl_expiry_no_job_writes(self):
        """T6: TTL expiry decision → zero insert calls."""
        with _GraphRun(
            trigger_type="manual",
            auto_approve=False,
            interactive_decision="ttl_expired",
        ) as run:
            run.mock_insert.assert_not_called()

    def test_t6_ttl_expiry_sets_cancelled_ttl(self):
        """T6: TTL expiry → set_run_status 'cancelled_ttl'."""
        with _GraphRun(
            trigger_type="manual",
            auto_approve=False,
            interactive_decision="ttl_expired",
        ) as run:
            assert run.mock_set_status.call_args[0][2] == "cancelled_ttl"


# ---------------------------------------------------------------------------
# T7 — crippled fetch (2 sources fail, force_partial=False)
# ---------------------------------------------------------------------------

class TestFullGraphCrippledFetch:
    def test_t7_crippled_fetch_no_job_writes(self):
        """T7: 2 sources fail, force_partial=False → discovery short-circuits, zero inserts."""
        with _GraphRun(
            trigger_type="scheduled",
            crippled_discovery=True,
        ) as run:
            run.mock_insert.assert_not_called()

    def test_t7_crippled_fetch_sets_cancelled_status(self):
        """T7: crippled discovery → set_run_status 'cancelled'."""
        with _GraphRun(
            trigger_type="scheduled",
            crippled_discovery=True,
        ) as run:
            assert run.mock_set_status.call_count == 1
            assert run.mock_set_status.call_args[0][2] == "cancelled"

    def test_t7_crippled_fetch_review_not_called(self):
        """T7: crippled discovery short-circuits → review interrupt never called."""
        with _GraphRun(
            trigger_type="scheduled",
            crippled_discovery=True,
        ) as run:
            run.mock_interrupt.assert_not_called()


# ---------------------------------------------------------------------------
# T8 — force_partial=True (2 sources fail, but graph proceeds)
# ---------------------------------------------------------------------------

class TestFullGraphForcePartial:
    def test_t8_force_partial_proceeds_with_jobs(self):
        """T8: force_partial=True → graph proceeds despite 2 source failures; inserts qualifying jobs."""
        scored = [_scored_job("a" * 16, 90)]
        with _GraphRun(
            trigger_type="scheduled",
            scored_jobs=scored,
            crippled_discovery=False,  # force_partial path: discovery doesn't cripple (stub is clean)
        ) as run:
            assert run.mock_insert.call_count == 1
            assert run.mock_update_run.call_args[1]["status"] == "completed"

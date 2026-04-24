# Test Plan: Role Scout Phase 2 — Agentic Pipeline

| Field | Value |
|-------|-------|
| Feature | Role Scout Phase 2 |
| Version | 1.0 |
| Created | 2026-04-24 |
| Status | Draft |
| Approved by | — |
| Approved on | — |
| PRD reference | docs/SPEC.md |
| Tech reference | docs/TECH-DESIGN.md |

---

## Overview

Phase 2 adds a LangGraph agentic pipeline (6 nodes + reflection subgraph), a 9-tool MCP server, resume tailoring, a 4-eval framework, Flask dashboard enhancements, and observability/cost tracking on top of the frozen Phase 1 codebase (`auto_jobsearch/`). This test plan maps all 46 SPEC test scenarios (T1–T46) to automatable functional/integration tests and calls out the two scenarios that require manual verification. All tests run via `uv run pytest`.

**Key constraints from SPEC:**
- Phase 1 (`auto_jobsearch/`) is frozen — tests must never import-and-modify it
- All Claude calls in tests are mocked (rate-limit protection)
- MCP smoke test (T21) is the only mandatory manual step
- Shadow-mode diff verification is an integration test run against fixture data

---

## Test Stack

| Layer | Framework | Location |
|-------|-----------|----------|
| Unit | `pytest` + `pytest-mock` | `tests/unit/` |
| Integration | `pytest` + Flask `test_client` + `pytest-asyncio` | `tests/integration/` |
| E2E (dashboard) | Flask `test_client` (route-level); T32 browser slider = manual | `tests/e2e/` |
| Eval harness | `pytest` + `scipy` + mocked cross-model judge | `tests/unit/test_eval.py` |
| Runner command | `uv run pytest tests/ -v --cov=role_scout` | — |

---

## Functional Tests (Unit-level, mocked dependencies)

These map to SPEC scenarios typed "Unit" — fast, no external calls, all Claude/DB mocked.

| ID | SPEC | Feature | What Is Tested | Pass Condition | Priority |
|----|------|---------|----------------|----------------|----------|
| F01 | T1 | F1 LangGraph nodes | Each node: mock state in, assert state out | All 6 nodes + reflection pass mock state through without error | P0 |
| F02 | T2 | F1 Discovery concurrency | 3 mocked fetchers run concurrently | Wall time < 1.2× slowest individual mock | P0 |
| F03 | T3 | F1 imaplib thread-safety | TrueUp concurrent IMAP invocations | Each invocation opens its own connection; no shared state corruption | P0 |
| F04 | T10 | F1 State trimming | After enrichment node, raw_by_source trimmed | `state["raw_by_source"] == {}` after enrichment mock | P0 |
| F05 | T12 | F2 Reflection cost | Reflection on synthetic 75-job run | Estimated cost < $0.50 (token count × rate) | P0 |
| F06 | T13 | F2 Reflection correction | `salary_visible=False, comp_score=0` → corrected | After reflection, `comp_score == 5` | P0 |
| F07 | T14 | F2 Reflection skip | Score 95 → above borderline band | `reflection_applied=False`, no Claude call made | P0 |
| F08 | T15 | F2 Reflection error handling | Claude returns malformed JSON | Original score kept, `reflection_applied=False`, error logged | P0 |
| F09 | T17 | F3 MCP tool schemas | Each of 9 tools invoked against fixture DB | Returns valid response matching schema (Pydantic) | P0 |
| F10 | T19 | F3 MCP tailor on non-qualified | `tailor_resume` called with unqualified hash_id | Returns `{"error": {"code": "NOT_QUALIFIED", ...}}` | P0 |
| F11 | T20 | F3 Watchlist atomic write | `manage_watchlist` add/remove | Writes via tempfile+rename; concurrent call reads consistent state | P1 |
| F12 | T22 | F4 Tailor cache hit | Same resume_sha + prompt_version + hash_id | Claude not called (mock call_count == 0) | P0 |
| F13 | T23 | F4 Tailor cache miss on resume change | Resume file modified → new sha | Claude called exactly once (mock call_count == 1) | P0 |
| F14 | T24 | F4 Tailor force bypass | `force=True` with cached result | Claude called despite cached row | P0 |
| F15 | T25 | F4 Tailor prompt version bust | Prompt version bumped in file | Cache key differs; Claude called | P0 |
| F16 | T26 | F4 Tailor non-qualified hash | Non-qualified hash_id in DB | 400 response, `code=NOT_QUALIFIED` | P0 |
| F17 | T27 | F4 Tailor malformed Claude JSON | Claude returns unparseable JSON | 500 response, `code=CLAUDE_API_ERROR`; no row written to DB | P0 |
| F18 | T28 | F5 Scorer eval | Eval on fixture ground truth | Spearman computed via scipy and matches expected value | P0 |
| F19 | T29 | F5 Cross-model judge family | Judge model used in alignment eval | Model name does not start with "claude" | P0 |
| F20 | T30 | F5 Tailor eval disagreement flag | LLM–human delta > 1 | Flag raised in eval report | P1 |
| F21 | T31 | F5 Discovery recall empty gold set | Recall eval with 0 gold jobs | Returns 0.0; no ZeroDivisionError | P1 |
| F22 | T37 | F6 Flask host binding | `run.py --serve` with host=0.0.0.0 attempted | Raises `ValueError` before binding | P0 |
| F23 | T41 | F7 Source health JSON written | Every pipeline run | `run_log.source_health_json` is non-null JSON after each run | P0 |
| F24 | T42 | F8 Correlation ID on every log | All structured logs | Parse JSON logs from test run; every line has `correlation_id` key | P0 |
| F25 | T43 | F8 LangSmith off = no network | `LANGSMITH_TRACING=false` | Zero HTTP calls to langsmith.com (mocked httpx asserts 0 calls) | P0 |
| F26 | T44 | F8 Cost computation | Known token counts | `(1_000_000 × $3/M) + (500_000 × $15/M) = $10.50` ± $0.01 | P0 |
| F27 | T46 | F6 Cost banner threshold | Last run cost > $2 | Dashboard injects banner with correct cost string | P1 |

**Total functional tests: 27**

---

## Integration Tests (Real DB, mocked Claude, or real graph execution)

These map to SPEC scenarios typed "Integration", "E2E", or "Eval" — hit real SQLite, real LangGraph graph, Flask test_client routes.

| ID | SPEC | Feature | What Is Tested | Pass Condition | Priority |
|----|------|---------|----------------|----------------|----------|
| I01 | T4 | F1 Full graph happy path | Graph with mocked Claude: approve flow | `run_log.status=completed`; `qualified_jobs` rows written | P0 |
| I02 | T5 | F1 Full graph cancel | User cancels at review_node | `run_log.status=cancelled`; zero rows in `qualified_jobs` | P0 |
| I03 | T6 | F1 Full graph TTL expiry | `INTERRUPT_TTL_SECONDS=1` | Run auto-cancelled; `run_log.status=cancelled_ttl` | P0 |
| I04 | T7 | F1 Partial failure, no --force | 2 of 3 sources fail, no flag | Graph short-circuits at preflight; `run_log.status=failed_partial` | P0 |
| I05 | T8 | F1 Partial failure, --force | 2 of 3 sources fail, flag set | Graph proceeds; result written from 1 good source | P1 |
| I06 | T9 | F1 Auto-approve skips interrupt | `--auto-approve` flag | No `interrupt()` raised; graph completes without pause | P0 |
| I07 | T11 | F1+F8 Cost kill switch | Tokens accumulate past `MAX_COST_USD` mid-run | `CostKillSwitchError` raised; `run_log.reason=cost_kill_switch` | P1 |
| I08 | T16 | F2 Reflection A/B eval | With/without `REFLECTION_ENABLED` | ∆Spearman reported; test asserts report file written | P0 |
| I09 | T18 | F3 MCP concurrent start | `run_pipeline` called while status=running | Returns `{"error": {"code": "PIPELINE_BUSY"}}` | P0 |
| I10 | T32 | F6 Threshold slider | Flask route + JS filter | Route returns rows with `data-score` attrs; JS filter tested via route-level check (browser part = manual) | P0 |
| I11 | T33 | F6 HiTL banner appearance | Poll `/api/pipeline/status` while `review_pending` | Banner data present in response within 6s simulation | P0 |
| I12 | T34 | F6 TTL countdown → cancelled | `ttl_remaining_s=0` in status response | Banner data shows "Run cancelled (TTL)" state | P0 |
| I13 | T35 | F6 Extend 2h — once only | Two extend requests | First → 200, `ttl_extended=True`; second → 400 `ALREADY_EXTENDED` | P1 |
| I14 | T36 | F6 CSRF enforcement | POST without CSRF token | 403 on all 4 write routes | P0 |
| I15 | T38 | F7 Source auto-skip | 3 consecutive fails on a source | 4th run skips that source; logged | P0 |
| I16 | T39 | F7 --force-source override | `--force-source linkedin` on skipped source | Source runs despite skip flag | P1 |
| I17 | T40 | F7 SerpAPI quota guard | SerpAPI remaining < 10 | Source skipped; warning logged; `source_health_json` notes quota | P0 |
| I18 | T45 | F8 Cost kill switch integration | Token accumulation exceeds $5 limit | `CostKillSwitchError` raised; `run_log.reason=cost_kill_switch`; no further Claude calls | P0 |
| I19 | — | Shadow mode diff | `RUN_MODE=shadow` on fixture input | Linear and agentic `scored_jobs` sets match on identical input | P0 |

**Total integration tests: 19**

---

## Pass Threshold

| Metric | Requirement |
|--------|-------------|
| Minimum pass rate | 90% of automatable test cases |
| P0 tests | 100% must pass — no exceptions |
| Unautomat-able tests | Excluded from denominator; verified manually before D5 complete |
| Regressions | Previously passing tests count against threshold |

**Adjusted denominator:** 45 automatable tests / 46 total tests (T21 excluded as manual)

---

## Unautomat-able Tests

| ID | SPEC | Test Case | Reason Not Automatable | Manual Verification Step |
|----|------|-----------|------------------------|--------------------------|
| M01 | T21 | MCP smoke test via Claude Code | Requires live Claude Code CLI session; stdio transport cannot be invoked from pytest | After D5: register MCP server in `.claude.json`, open Claude Code, run `@jobsearch get_jobs limit=5` — assert valid JSON response with ≥1 job |
| M02 | T32 (browser half) | Zero XHR on slider drag | Slider JS runs in browser; Flask test_client cannot execute JS | After D8: open dashboard in Chrome, open DevTools Network tab, drag slider, confirm zero XHR/fetch entries |

---

## Test Results

*(Populated after test runs — do not fill in before executing)*

| ID | Test Name | Status | Failure Reason |
|----|-----------|--------|----------------|
| F01 | node_mock_state_in_out | — | — |
| F02 | discovery_concurrency_timing | — | — |
| ... | ... | — | — |

**Run date:** —
**Pass rate:** —
**P0 pass rate:** —
**Threshold met:** —

---

## Weak Areas

*(Leave blank until after test run)*

---

## Recommended Next Steps

*(Leave blank until after test run)*

---

## Approval

| Action | By | Date | Notes |
|--------|----|------|-------|
| Test plan drafted | Claude Code | 2026-04-24 | Mapped all 46 SPEC T-scenarios; 2 manual |
| Test plan approved | [owner] | 2026-04-24 | Approved verbally — proceed |
| Test run complete | Claude Code | — | — |
| Commit approved | [owner] | — | — |

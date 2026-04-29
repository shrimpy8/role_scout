> **HISTORICAL DOCUMENT** — This file reflects the design decisions made before implementation. The codebase has since evolved: Phase 1 (auto_jobsearch) is now absorbed into `role_scout/compat/` as a frozen sub-package; there is no sibling repo dependency. Treat this as design context, not current truth.

---

# SPEC: Role Scout Phase 2

| Field | Value |
|-------|-------|
| Parent | [PRD-CORE.md](./PRD-CORE.md) |
| Version | 1.0 |
| Owner | [project-owner] |
| Status | Approved |
| Updated | 2026-04-23 |

> Implementation-ready specification for all 8 Phase 2 features. Each feature section is self-contained. Developers should implement without asking questions.

---

## 1. Cross-Cutting Decisions (apply to all features)

| ID | Decision | Rationale |
|----|----------|-----------|
| X1 | **Phase 1 is frozen.** No files under `auto_jobsearch/` may be modified. Import as editable dep via `pyproject.toml` `[tool.uv.sources]`. | Stability; single source of truth for the working pipeline |
| X2 | **New code lives in `role_scout/`** (Phase 2 folder at repo root). | Isolated blast radius |
| X3 | **Prompts split.** Phase 1 prompts stay in `auto_jobsearch/prompts/`. New prompts (`resume_tailor_system.md`, `scoring_reflection_system.md`) live in `role_scout/prompts/`. | Frozen Phase 1 includes frozen prompts |
| X4 | **Config-driven values.** All thresholds, TTLs, budgets, model IDs come from `.env` via `pydantic-settings`. No magic numbers at call sites. | CLAUDE.md mandate |
| X5 | **Single code path for scheduled + interactive.** launchd calls `uv run python run.py --agentic --auto-approve`. No separate linear script. | Avoids prod/dev divergence |
| X6 | **Feature flag for rollout.** Env var `RUN_MODE=linear\|agentic\|shadow`. Default `shadow` for 2 weeks. | Safe promotion path |
| X7 | **All new write routes enforce CSRF.** Flask-WTF CSRF tokens; 403 on missing/invalid. | Security |
| X8 | **All Flask routes bind to 127.0.0.1 only.** Enforced in `run.py --serve` via `host="127.0.0.1"`. | Security |
| X9 | **All new DB migrations are additive `ALTER TABLE ADD COLUMN`** wrapped in `try/except sqlite3.OperationalError`. No destructive changes. | Idempotent, zero-downtime |
| X10 | **`structlog` JSON logs. Every log line includes `correlation_id`** (== `run_id` for pipeline logs; request UUID for dashboard). | CLAUDE.md mandate |

---

## 2. F1 — LangGraph Workflow

### 2.1 Overview

**Job supported:** pause-and-steer pipeline (JTBD Primary Job).
**User outcome:** run the pipeline, see a pause banner mid-run, approve/cancel.
**Business outcome:** no wasted Claude spend on runs the user would have canceled; discovery runs in parallel (~3× faster).

**User story.** As the user, I want the pipeline to pause after scoring so I can review the qualified set before it persists, so I don't have to clean up bad runs after the fact.

### 2.2 Graph Topology

```
START → preflight → discovery → enrichment → scoring → reflection → review ─┬─ approved → output → END
                                                                              ├─ cancelled → END (no writes)
                                                                              └─ tle_expired → END (logged cancelled)
```

**Removed from the original plan:** the `threshold_adjusted → scoring` re-entry edge. Threshold is a display filter only (see F6 §7.3); changing it never re-scores.

### 2.3 Acceptance Criteria

**Happy path (interactive `--agentic`, Flask running):**
GIVEN the user runs `uv run python run.py --agentic --serve`
WHEN fetch/enrich/score/reflect complete
THEN `run_log.status` transitions to `review_pending`
AND the Flask banner appears within 5s
AND no rows are written to `qualified_jobs` yet
WHEN the user clicks Approve
THEN `output_node` writes rows, exports JDs, sets `run_log.status=completed`
AND the banner disappears within 5s

**Auto-approve (scheduled / MCP):**
GIVEN the run was started with `--auto-approve` OR via the MCP `run_pipeline` tool
WHEN the graph reaches `review_node`
THEN `review_node` sets `human_approved=True` immediately and records `trigger_type` in state
AND no banner is shown
AND no interrupt is raised

**Cancel:**
GIVEN the banner is shown and the user clicks Cancel
THEN `run_log.status=cancelled`, zero DB writes to `qualified_jobs` or `seen_hashes`, JD files not exported

**TTL expiry:**
GIVEN the banner has been showing for 4 hours with no response
THEN the run is auto-cancelled, `run_log.status=cancelled_ttl`, reason="4h interrupt TTL"

**Partial-failure circuit breaker:**
GIVEN 2 of 3 sources failed in `discovery_node` and `--force-partial` was not set
WHEN `preflight_node` checks source_counts after discovery (post-hoc gate)
THEN the graph short-circuits to END with `run_log.status=failed`, reason="crippled_fetch"
AND if `--force-partial` flag is set, the graph proceeds with a logged warning

### 2.4 State Schema (TypedDict)

See [TECH-DESIGN §3.1](./TECH-DESIGN.md#31-state-schema-jobsearchstate) for full typed definition and per-node trimming rules. State size cap: **10 MB serialized** (assertion in each node).

### 2.5 Node Contracts

| Node | Input state keys | Output state keys | Side effects | Must-fail on |
|------|------------------|-------------------|--------------|--------------|
| `preflight` | `run_id`, `trigger_type` | `candidate_profile`, `watchlist`, `qualify_threshold` | Insert `run_log` row (status=running) | Missing `.env`, missing `resume_summary.md`, DB init error |
| `discovery` | `candidate_profile`, `watchlist` | `raw_by_source`, `normalized_jobs`, `new_jobs`, `source_counts`, `errors[]` | `seen_hashes` NOT touched until output_node | All 3 sources fail (without `--force-partial`) |
| `enrichment` | `new_jobs` (then **trim** `raw_by_source`, `normalized_jobs`) | `enriched_jobs`, `watchlist_hits` | None | Zero enrichable jobs AND `new_jobs` was non-empty (bug state) |
| `scoring` | `enriched_jobs`, `candidate_profile`, `qualify_threshold` | `scored_jobs` (all scored, not yet filtered), `scoring_tokens_in`, `scoring_tokens_out` | Claude API call | Claude rate limit after 3 retries |
| `reflection` | `scored_jobs` (filter to 75–89% subset) | `scored_jobs` (updated w/ reflected scores + `reflection_applied: bool` per job), `reflection_tokens_in/out` | Claude API call (only on borderline subset) | Claude rate limit after 3 retries |
| `review` | `scored_jobs` (filter ≥ threshold for display) | `human_approved: bool`, `cancel_reason: str \| None` | `run_log.status=review_pending`; waits on `interrupt()` OR auto-approves | TTL (4h) expiry |
| `output` | `scored_jobs` (filtered ≥ threshold) | `exported_count` | Insert `qualified_jobs` rows, upsert `seen_hashes`, export JD files, `run_log.status=completed` + cost columns | DB write error |

### 2.6 Conditional Routing (after `review`)

```python
def route_after_review(state: JobSearchState) -> str:
    if state["human_approved"]:
        return "output"
    return END  # cancelled (user OR TTL)
```

No `scoring` re-entry. Threshold changes are post-hoc display filters.

### 2.7 Checkpointing

- **Phase 2:** `MemorySaver` in-process. Acceptable because TTL auto-cancels stuck runs.
- **Phase 3:** `SqliteSaver` for crash recovery. Out of scope.

### 2.8 API / CLI Contract

```bash
uv run python run.py --agentic                          # interactive, HiTL enabled
uv run python run.py --agentic --dry-run                # no DB writes, auto-approve
uv run python run.py --agentic --auto-approve           # scheduled/launchd use
uv run python run.py --agentic --force-partial          # proceed even if 2/3 sources failed
uv run python run.py                                    # Phase 1 linear orchestrator (kept)
```

### 2.9 Edge Cases

| Scenario | Detection | Behavior | User Feedback | Recovery |
|----------|-----------|----------|---------------|----------|
| 1 source fails | `errors[]` has 1 entry | Proceed | Banner shows "1 source failed: LinkedIn (timeout)" | Manual re-run |
| 2 sources fail | `errors[]` has 2 entries | Short-circuit to END | run_log reason="crippled_fetch" | Re-run with `--force-partial` or fix source |
| All 3 sources fail | `errors[]` has 3 entries | Short-circuit regardless of `--force-partial` | run_log status=failed, reason="total_fetch_failure" | Fix sources |
| Zero qualified jobs | `len(filter(≥threshold, scored_jobs)) == 0` | Proceed to review (empty-set banner) | Banner: "0 qualified at 85%. Try lowering threshold." | User lowers threshold (display-only) or cancels |
| TTL expiry | Background task at `review_pending + 4h` | Auto-cancel, log reason | Dashboard shows cancelled banner on next load | Re-run |
| Concurrent run start | New start attempt while `run_log.status=running` exists | Reject with `PIPELINE_BUSY` | CLI error; MCP returns error code | Wait for current, or kill process (manual) |
| Claude rate limit | 429 after 3 retries | Fail `scoring` or `reflection` node | run_log status=failed | Re-run after cooldown |
| Claude cost > `MAX_COST_USD` env kill-switch | Check after each Claude call | Abort run; skip remaining Claude steps | run_log reason="cost_kill_switch" | Raise kill-switch or fix prompts |

### 2.10 Test Scenarios (node + graph)

| # | Scenario | Type | Priority |
|---|----------|------|----------|
| T1 | Each node: mock state in, assert state out | Unit | P0 |
| T2 | `discovery` with 3 mocked fetchers — concurrent timing < 1.2× slowest | Unit | P0 |
| T3 | `imaplib` concurrent invocations each open own connection | Unit | P0 |
| T4 | Full graph with mocked Claude — happy path approve | Integration | P0 |
| T5 | Full graph — user cancel | Integration | P0 |
| T6 | Full graph — TTL expiry (test with `TTL_SECONDS=1`) | Integration | P0 |
| T7 | Full graph — 2 sources fail, `--force-partial` off → short-circuit | Integration | P0 |
| T8 | Full graph — 2 sources fail, `--force-partial` on → proceed | Integration | P1 |
| T9 | Auto-approve mode skips `interrupt()` entirely | Integration | P0 |
| T10 | State size assertion: after enrichment, `raw_by_source` is trimmed to `{}` | Unit | P0 |
| T11 | `cost_kill_switch` aborts mid-run when sum(input+output tokens × rate) exceeds `MAX_COST_USD` | Integration | P1 |

---

## 3. F2 — Reflection-on-Scoring

### 3.1 Overview

Claude reviews its own scoring output on borderline jobs (score 75–89%) to catch internal inconsistencies (e.g., `comp_score=0` when `salary_visible=False`, subscores summing to a score that doesn't match the total).

### 3.2 Acceptance Criteria

GIVEN `scored_jobs` includes a job with `match_pct=78` and `comp_score=0, salary_visible=False`
WHEN `reflection_node` runs
THEN Claude is sent the job + original score + reflection prompt
AND the corrected score (e.g., `comp_score=5`, new `match_pct=83`) is written back to the job
AND `reflection_applied=True` on that job

GIVEN a job with `match_pct=95` (above 89% band)
WHEN `reflection_node` runs
THEN the job is NOT sent to Claude (above-band skip)
AND `reflection_applied=False`

GIVEN a job with `match_pct=60` (below 70% band)
WHEN `reflection_node` runs
THEN the job is NOT sent to Claude (below-band skip)
AND `reflection_applied=False`

GIVEN reflection causes `match_pct` to cross the qualify threshold (e.g., 78 → 86)
WHEN `review_node` surfaces results
THEN the job appears in the qualified set

### 3.3 Prompt (`role_scout/prompts/scoring_reflection_system.md`)

Input: `$original_score_json`, `$subscores_json`, `$job_json`, `$candidate_profile_json`.
Output (JSON): `{ "revised_score": int, "revised_subscores": {...}, "reasoning": str, "changed": bool }`.
Instructions: check (a) subscores sum consistent with total ±2 pts, (b) `salary_visible=False` forces `comp_score=5`, (c) no dimension score > 30 (domain cap) or > 20 (seniority cap) etc. per Phase 1 rubric.

### 3.4 Cost Constraint

Borderline band typically 20–30% of scored_jobs. For ~75 jobs: ~20 reflection calls/run. Budget: **< $0.50/run** additional. Asserted by test T12.

### 3.5 Eval Gate

See F5. Required: Spearman ∆ ≥ +0.05 with reflection vs. without, on 50+ ground-truth jobs. If fail, remove reflection subgraph (keep code dead behind `REFLECTION_ENABLED=false`).

### 3.6 Test Scenarios

| # | Scenario | Type | Priority |
|---|----------|------|----------|
| T12 | Reflection cost < $0.50 on synthetic 75-job run | Unit | P0 |
| T13 | `salary_visible=False, comp_score=0` → corrected to 5 | Unit | P0 |
| T14 | Score 95 → skipped (above band) | Unit | P0 |
| T15 | Claude returns malformed JSON → log error, keep original score, `reflection_applied=False` | Unit | P0 |
| T16 | Eval gate comparison: with-reflection ≥ without-reflection Spearman | Eval | P0 |

---

## 4. F3 — MCP Server

### 4.1 Overview

Expose pipeline as Claude Code tools via stdio. `role_scout/mcp_server/server.py`, ~150 LOC, `mcp` SDK (pinned exact version).

### 4.2 Tool Contracts

| Tool | Input | Output | Calls |
|------|-------|--------|-------|
| `run_pipeline` | `dry_run: bool = False` | `{ run_id, status, exported_count, cost_usd, duration_s }` | Graph with `auto_approve=True` |
| `get_jobs` | `status: str = "new", limit: int = 10, source: str \| None = None` | `list[JobSummary]` | `get_qualified_jobs()` DAL |
| `get_job_detail` | `hash_id: str` | `JobDetail` (full record + JD text) | `get_job_by_hash_id()` DAL |
| `analyze_job` | `hash_id: str, force: bool = False` | `AlignmentResult` | `run_alignment()` (Phase 1) |
| `tailor_resume` | `hash_id: str, force: bool = False` | `TailoredResume` | `tailor_resume()` (F4) |
| `update_job_status` | `hash_id: str, status: Literal["new","reviewed","applied","rejected"]` | `{ ok: bool }` | `update_job_status()` DAL |
| `get_run_history` | `limit: int = 5` | `list[RunLogEntry]` (includes cost, token counts) | `get_run_logs()` DAL |
| `get_watchlist` | (none) | `list[str]` | Reads `watchlist.yaml` |
| `manage_watchlist` | `action: Literal["add","remove"], company: str` | `{ ok: bool, watchlist: list[str] }` | Write `watchlist.yaml` (atomic via tempfile+rename) |

### 4.3 `run_pipeline` Auto-Approve Semantics

**Decision:** MCP-invoked `run_pipeline` always runs with `auto_approve=True`. Rationale: MCP tools are request/response — they cannot hold a connection open waiting for human approval. A future Phase 3 "async tool" pattern could return a `run_id` and poll, but is out of scope.

Mitigation for risk of blind auto-approve: the tool returns `exported_count` and `cost_usd` in the response, so Claude Code shows the user what happened.

### 4.4 Concurrency — Single Writer

GIVEN a run is active (`run_log.status='running'` or `'review_pending'`)
WHEN MCP `run_pipeline` is invoked
THEN the tool returns `{ error: "PIPELINE_BUSY", run_id: <active_run_id> }` immediately
AND no new run starts

### 4.5 Claude Code Registration

```json
{
  "mcpServers": {
    "role_scout": {
      "command": "uv",
      "args": ["run", "python", "run.py", "--mcp"],
      "cwd": "/path/to/role_scout"
    }
  }
}
```

Published to project README. Users copy to `.claude.json`.

### 4.6 SDK Version Pinning

`pyproject.toml`: `mcp == 1.0.x` (exact minor, patch allowed). Upgrade requires running smoke test `scripts/mcp_smoke.py` which exercises all 9 tools against a fixture DB.

### 4.7 Edge Cases

| Scenario | Behavior |
|----------|----------|
| `get_job_detail` called with unknown `hash_id` | Return `{ error: "NOT_FOUND" }` |
| `update_job_status` with invalid status | Return validation error, no DB write |
| `manage_watchlist` add of existing company | Idempotent, returns current list |
| `tailor_resume` on non-qualified hash_id | Return error — only qualified jobs can be tailored |
| MCP server crashes | stdio reconnect handled by Claude Code; no state lost (all state in DB) |

### 4.8 Test Scenarios

| # | Scenario | Type | Priority |
|---|----------|------|----------|
| T17 | Each of 9 tools invoked against fixture DB returns valid schema | Unit | P0 |
| T18 | `run_pipeline` concurrent-start returns `PIPELINE_BUSY` | Integration | P0 |
| T19 | `tailor_resume` on non-qualified hash returns error | Unit | P0 |
| T20 | `manage_watchlist` writes atomically (tempfile + rename) | Unit | P1 |
| T21 | Smoke test: claude code actually invokes each tool end-to-end | Manual | P0 |

---

## 5. F4 — Resume Tailoring

### 5.1 Overview

On-demand, one-shot Claude call per job to produce `TailoredResume` (summary, bullets, keywords).

### 5.2 Interface

```python
def tailor_resume(
    job: ScoredJob,
    resume_summary: str,
    api_key: str,
    prompt_version: str,
) -> TailoredResume
```

### 5.3 Model

```python
class TailoredResume(BaseModel):
    hash_id: str
    job_title: str
    company: str
    tailored_summary: str               # 3-sentence exec summary
    tailored_bullets: list[str]         # 5-7 achievement bullets
    keywords_incorporated: list[str]    # JD keywords surfaced
    cache_key: str                      # sha256(resume_summary + prompt_version + hash_id)
    prompt_version: str
    tailored_at: datetime
```

### 5.4 Cache Key — Resolves Staleness

`cache_key = sha256(resume_sha + "|" + prompt_version + "|" + hash_id)[:16]`

Where `resume_sha = sha256(file_bytes_of(resume_summary.md))`.

GIVEN `resume_summary.md` is edited
WHEN user clicks Tailor on a previously tailored job
THEN cache key mismatch → Claude is called again (fresh tailoring)
AND the previous `tailored_resume` JSON in DB is overwritten

GIVEN the cache key matches
WHEN user clicks Tailor
THEN Claude is NOT called; cached JSON is returned from DB

### 5.5 DB Migration (additive)

```sql
ALTER TABLE qualified_jobs ADD COLUMN tailored_resume TEXT;  -- JSON blob of TailoredResume
```

Applied in `init_db()` with `try/except sqlite3.OperationalError` (idempotent).

### 5.6 Flask Route

`POST /api/tailor/<hash_id>` — CSRF required.
Body: `{ "force": bool }` (default false).
Response 200: `TailoredResume` JSON.
Response 404: `{ error: "JOB_NOT_FOUND" }`.
Response 400: `{ error: "NOT_QUALIFIED" }`.
Response 500: `{ error: "CLAUDE_API_ERROR", detail: str }`.

### 5.7 UI (expand panel)

See [EXP-BRIEF §4](./EXP-BRIEF.md#4-tailor-button--cached-vs-fresh) for wireframe and states.

### 5.8 Prompt (`role_scout/prompts/resume_tailor_system.md`)

Sections: background (`$resume_summary`), target (`$title`, `$company`, `$description`, `$key_requirements`). Rules: no fabrication — reframe existing achievements only. Output: JSON matching `TailoredResume` fields except metadata.

**Prompt versioning.** `prompt_version` string embedded in prompt file as first-line comment: `<!-- version: 2026-04-23-v1 -->`. Bumping version busts cache.

### 5.9 Test Scenarios

| # | Scenario | Type | Priority |
|---|----------|------|----------|
| T22 | Cache hit: same resume_sha + prompt_version + hash_id → no Claude call | Unit | P0 |
| T23 | Resume file edited → cache_key differs → Claude called | Unit | P0 |
| T24 | `force=true` bypasses cache | Unit | P0 |
| T25 | Prompt bumps version → cache busted | Unit | P0 |
| T26 | Non-qualified hash_id → 400 `NOT_QUALIFIED` | Unit | P0 |
| T27 | Malformed Claude JSON → 500 `CLAUDE_API_ERROR`, no DB write | Unit | P0 |

---

## 6. F5 — Eval Framework

### 6.1 Overview

4 evals: scorer, alignment, tailor, discovery recall. Gate promotion from shadow → default.

### 6.2 Ground Truth Dataset (`role_scout/eval/ground_truth.yaml`)

- **≥50 jobs** (not 20). Target 75.
- Drawn from first 3 real pipeline runs + hand-curated edge cases.
- Required coverage: no-comp (≥10), remote (≥8), watchlist (≥8), expected-reject (≥10), staff/principal seniority (≥8), non-SF locations (≥5).
- Each job has: full JD, `human_score`, `human_subscores`, `human_rationale` (1–2 sentences), `edge_case_tag` (enum).

### 6.3 Scorer Eval (`eval/scorer_eval.py`)

Metrics:
- Spearman rank correlation (AI rank vs. human rank)
- Agreement % (|AI − human| ≤ 10)
- Disagreement table (misses > 10 pts)
- **Reflection A/B:** run with `REFLECTION_ENABLED=true` and `=false`, report ∆Spearman

**Targets:** Spearman ≥ 0.80, Agreement ≥ 80%. **Gate to promote:** both met AND reflection ∆ ≥ +0.05 (else strip reflection).

### 6.4 Alignment Eval (`eval/alignment_eval.py`)

- Run `run_alignment()` (Phase 1) on 10 job-resume pairs
- LLM-as-Judge: **GPT-4 or Gemini 2.5** (cross-family to avoid self-preference bias)
- Rate 1–5 per section: `strong_matches`, `reframing_opportunities`, `genuine_gaps`
- Report per-section mean + overall mean

**Target:** overall mean ≥ 4.0. **Gate:** met on ≥ 8/10 pairs.

### 6.5 Tailor Eval (`eval/tailor_eval.py`) — NEW

- Run `tailor_resume()` on 10 qualified jobs
- LLM-as-Judge (GPT-4/Gemini 2.5): rate 1–5 on `relevance`, `no_fabrication`, `keyword_fit`, `reframe_quality`
- Manual spot-check: user rates 2 of 10 (20%) by hand; flag disagreements > 1 pt between LLM and human

**Target:** LLM mean ≥ 4.0 AND human spot-check mean ≥ 4.0 AND LLM–human disagreement ≤ 1 pt on spot-check pairs.

### 6.6 Discovery Recall Eval (`eval/discovery_recall_eval.py`) — NEW

- User manually searches LinkedIn/Google/TrueUp for "PM San Francisco last 7 days" and bookmarks 20 jobs (human gold set)
- Pipeline run same day
- Metric: `recall@pipeline = |gold ∩ pipeline_results| / |gold|`

**Target:** recall ≥ 90%.

### 6.7 CLI

```bash
uv run python eval/run_eval.py                    # all 4
uv run python eval/run_eval.py --scorer           # scorer + reflection A/B
uv run python eval/run_eval.py --alignment
uv run python eval/run_eval.py --tailor
uv run python eval/run_eval.py --recall
```

Output: `eval/reports/YYYY-MM-DD-<eval>.md` with tables and decision (PASS/FAIL/REVIEW).

### 6.8 Test Scenarios

| # | Scenario | Type | Priority |
|---|----------|------|----------|
| T28 | Scorer eval on fixture → Spearman computed, reported | Unit | P0 |
| T29 | Cross-model judge actually uses different model family | Unit | P0 |
| T30 | Tailor eval LLM–human disagreement flag triggers when ∆ > 1 | Unit | P1 |
| T31 | Recall eval handles empty gold set gracefully | Unit | P1 |

---

## 7. F6 — Flask Dashboard Enhancements

### 7.1 Overview

Enhance existing `auto_jobsearch/jobsearch/dashboard/` — do not rewrite. New components inherit existing CSS.

### 7.2 Threshold Slider — DISPLAY FILTER ONLY

GIVEN the slider is at 85
WHEN the user drags it to 80
THEN the jobs table client-side filters rows where `match_pct >= 80`
AND NO re-score occurs
AND NO API call is made (pure client-side JS filter)

GIVEN the user closes the browser and reopens
THEN the slider resets to `.env`'s `SCORE_THRESHOLD` default
AND the server never persists the slider state

### 7.3 Watchlist CRUD Panel

`POST /api/watchlist` (body `{ company: str }`) — add. CSRF required.
`DELETE /api/watchlist/<company>` — remove. CSRF required.
Atomic write to `watchlist.yaml` (tempfile + rename).
UI: optimistic update on click; revert on server error with toast.

GIVEN user types "Anthropic" and clicks Add
WHEN POST succeeds
THEN the ★ badges in the jobs table update to show Anthropic as watchlist (without page reload, triggered by SSE or manual refresh button — see §7.6)

### 7.4 HiTL Review Banner

Polls `GET /api/pipeline/status` every **5 seconds** (not sub-second — SQLite concurrency).

GIVEN `run_log.status=review_pending`
WHEN the dashboard is open
THEN a banner appears within 5s at the top of the page
AND shows: qualified count, top 3 matches with %, watchlist hits, **countdown to TTL expiry (HH:MM remaining)**
AND has 3 buttons: Approve / Adjust Threshold / Cancel / Extend 2h

**Approve:** `POST /api/pipeline/resume` with `{ approved: true }`.
**Adjust Threshold:** opens inline input; submit updates `qualify_threshold` and re-filters display (no re-score).
**Cancel:** `POST /api/pipeline/resume` with `{ approved: false, cancel_reason: "user_cancel" }`.
**Extend 2h:** `POST /api/pipeline/extend` pushes TTL by 2h (max 1 extension per run).

### 7.5 Tailor Button

In expanded row panel, alongside Align button.
States: Idle → Loading (spinner) → Success (shows tailored content inline) → Error (toast + retry).
Cached-vs-fresh indicator: badge "cached (2h old)" vs "fresh".
Force button: ↻ icon to bypass cache.

### 7.6 Live Updates (no SSE, simple polling)

Dashboard polls `/api/pipeline/status` every 5s. Response includes watchlist revision number; on change, re-renders badge column.

### 7.7 Security

| Route | Method | Auth | CSRF |
|-------|--------|------|------|
| `/api/tailor/<hash_id>` | POST | localhost | Required |
| `/api/pipeline/resume` | POST | localhost | Required |
| `/api/pipeline/extend` | POST | localhost | Required |
| `/api/pipeline/status` | GET | localhost | Not required (read-only) |
| `/api/watchlist` | POST | localhost | Required |
| `/api/watchlist/<company>` | DELETE | localhost | Required |

All bind to `127.0.0.1` (enforced in `run.py --serve`).

### 7.8 UI States per Feature

See [EXP-BRIEF](./EXP-BRIEF.md).

### 7.9 Test Scenarios

| # | Scenario | Type | Priority |
|---|----------|------|----------|
| T32 | Threshold slider change: zero network requests, client-side filter only | E2E | P0 |
| T33 | Banner appears within 6s of `status=review_pending` | E2E | P0 |
| T34 | TTL countdown decrements; at 0:00 → "Run cancelled (TTL)" | E2E | P0 |
| T35 | Extend 2h button works once, disabled second time | E2E | P1 |
| T36 | Missing CSRF token on POST returns 403 | Integration | P0 |
| T37 | External bind attempted (host=0.0.0.0) raises error in `run.py --serve` | Unit | P0 |

---

## 8. F7 — Discovery Improvements

### 8.1 Source Health Tracking

New column on `run_log`: `source_health_json TEXT` — per-run snapshot.

```json
{
  "linkedin":   { "status": "ok",     "jobs": 42, "duration_s": 18.2, "error": null },
  "google":     { "status": "ok",     "jobs": 38, "duration_s": 12.7, "error": null },
  "trueup":     { "status": "failed", "jobs": 0,  "duration_s": 3.1,  "error": "IMAP auth failed" }
}
```

**Auto-skip logic.** Before fetching, `preflight_node` reads last 3 `run_log` rows; if a source is `failed` in all 3, skip it this run and log warning. User must manually re-enable via `--force-source linkedin` flag.

### 8.2 Query Observability

`source_health_json` also records exact query params:

```json
{ "linkedin": { "query": { "title": "Product Manager", "location": "San Francisco", "date_posted": "past_week" }, "raw_count": 42, "after_dedup": 12 } }
```

Dashboard debug page (`/debug/runs`) lists last 10 runs with per-source breakdown.

### 8.3 SerpAPI Quota

On `preflight_node`: GET `https://serpapi.com/account` → parse `this_month_usage` vs `searches_per_month`.

GIVEN remaining < 10
WHEN preflight runs
THEN log warning; set `source_counts["google"] = 0`; append error `"serpapi_quota_low"`; DO NOT call SerpAPI this run

Dashboard sidebar widget shows "SerpAPI: 87/100 used" with color (green < 70, yellow 70–90, red > 90).

### 8.4 Test Scenarios

| # | Scenario | Type | Priority |
|---|----------|------|----------|
| T38 | 3 consecutive failed runs → source auto-skipped on 4th | Integration | P0 |
| T39 | `--force-source linkedin` overrides skip | Integration | P0 |
| T40 | SerpAPI quota < 10 → skip + warning logged | Integration | P0 |
| T41 | `source_health_json` written on every run_log row | Unit | P0 |

---

## 9. F8 — Observability & Cost Tracking

### 9.1 Logging

- `structlog` JSON throughout `role_scout/`
- Every log line includes `correlation_id` (== `run_id`) and `node_name` (for graph logs)
- Log levels: DEBUG (dev), INFO (normal), WARNING (skipped source, cache bust), ERROR (node fail), EXCEPTION (uncaught)
- Log file path from `LOG_FILE` env var; default stderr

### 9.2 LangSmith (opt-in)

- Toggle via `LANGSMITH_TRACING=true` in `.env`
- When on: graph runs traced to LangSmith project `role_scout`
- When off: zero runtime cost, no dependency loaded

### 9.3 Cost Tracking — `run_log` Columns (additive migration)

```sql
ALTER TABLE run_log ADD COLUMN input_tokens INTEGER DEFAULT 0;
ALTER TABLE run_log ADD COLUMN output_tokens INTEGER DEFAULT 0;
ALTER TABLE run_log ADD COLUMN estimated_cost_usd REAL DEFAULT 0.0;
ALTER TABLE run_log ADD COLUMN source_health_json TEXT;
ALTER TABLE run_log ADD COLUMN trigger_type TEXT DEFAULT 'manual';  -- manual|scheduled|mcp|dry_run
```

Cost formula per model in `role_scout/cost.py`:
```python
CLAUDE_SONNET_46_INPUT_USD_PER_MTOK = 3.00
CLAUDE_SONNET_46_OUTPUT_USD_PER_MTOK = 15.00
cost = (input_tokens / 1_000_000) * 3.00 + (output_tokens / 1_000_000) * 15.00
```

Each Claude call aggregates tokens into state; `output_node` writes the total to `run_log`.

### 9.4 Kill Switch

Env `MAX_COST_USD=5.00` (default). Checked after every Claude call:

GIVEN running cost > `MAX_COST_USD`
WHEN the next Claude call would proceed
THEN the node raises `CostKillSwitchError`
AND graph transitions to END with `run_log.status=failed, reason=cost_kill_switch`

### 9.5 Dashboard Cost Warning

GIVEN last run `estimated_cost_usd > 2.00`
WHEN dashboard loads
THEN a yellow banner shows "Last run cost $X.XX — above $2 target"

### 9.6 Test Scenarios

| # | Scenario | Type | Priority |
|---|----------|------|----------|
| T42 | Every log line has correlation_id | Unit | P0 |
| T43 | LangSmith off = zero network calls | Unit | P0 |
| T44 | Cost computed correctly for a known token count | Unit | P0 |
| T45 | Kill switch at $5: abort before scoring 76th job | Integration | P0 |
| T46 | Cost banner shown when last run > $2 | E2E | P1 |

---

## 10. Open Questions

| # | Question | Blocks | Owner | Deadline | Decision |
|---|----------|--------|-------|----------|----------|
| Q1 | Exact `mcp` SDK patch version to pin | F3 start | [owner] | Day 5 | Test latest 1.0.x before pin |
| Q2 | Cross-model judge: GPT-4 or Gemini 2.5? | F5 alignment/tailor eval | [owner] | Day 7 | Pick based on API key availability; default GPT-4-turbo |
| Q3 | Extend 2h button: max 1 extension or multiple? | F6 banner | [owner] | Day 8 | Max 1 (locked in spec §7.4) |
| Q4 | Is `/debug/runs` route password-protected? | F7 observability | [owner] | Day 9 | No — localhost-only is sufficient |

---

## 11. Dependencies

| Dep | Version | Type | Fallback |
|-----|---------|------|----------|
| `langgraph` | ≥ 0.2.0 | Core | None — required |
| `mcp` | == 1.0.x | Core | None — pin required |
| `scipy` | ≥ 1.13.0 | Eval | None |
| `structlog` | ≥ 24.x | Logging | Phase 1 already uses |
| `pydantic-settings` | ≥ 2.x | Config | Phase 1 already uses |
| `openai` OR `google-generativeai` | Latest | Eval judge | Skip eval with warning if absent |
| Phase 1 `jobsearch` | editable local | Core | N/A |

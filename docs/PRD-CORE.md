# PRD-CORE: Role Scout Phase 2 — Agentic Job Search Pipeline

| Field | Value |
|-------|-------|
| Version | 1.0 |
| Owner | [project-owner] |
| Status | Approved |
| Updated | 2026-04-23 |
| Parent | `docs/PHASE2-AGENTIC-PLAN.md` (superseded by this PRD set) |
| Related | [SPEC.md](./SPEC.md) · [TECH-DESIGN.md](./TECH-DESIGN.md) · [EXP-BRIEF.md](./EXP-BRIEF.md) |

---

## 1. The "So What?"

### One-Liner
Wrap the stable Phase 1 pipeline in a LangGraph workflow with HiTL review, add a scoring self-critique loop, expose the pipeline conversationally to Claude Code via MCP, and generate tailored resume content on demand — so the user spends time on high-quality jobs instead of triage.

### Problem (Quantified)

| Who | Pain | Impact | Evidence |
|-----|------|--------|----------|
| Solo PM job seeker (owner) | Linear pipeline: fetch → score → export, no pause, no steering | ~90s per run, no mid-run correction; 100% of runs either fully succeed or fully waste the Claude spend | Phase 1 orchestrator code; scheduled Mon/Thu runs complete without human in loop |
| Same user | No conversational surface — to inspect a job they must open the dashboard, scroll, download JD | 3–5 context switches per job review | Phase 1 UX |
| Same user | Manual resume tailoring per application | 20–40 min per job × 3–5 apps/wk = 1–3 hrs/wk | Self-reported |
| Same user | Scoring reliability unknown beyond anecdotes (e.g., Phase 1 `comp_score=0 when salary_visible=False` bug required a hardcoded patch) | Silent false-positives/negatives in the qualified set | Phase 1 `scorer.py` auto-correction code |
| Same user | No visibility into pipeline cost per run | Risk of silent Claude spend drift as volume grows | No token/$ logging exists |

### Why Now?
- **Phase 1 is stable** — frozen on `main`, 3 live sources, tests green. No risk of building Phase 2 on a moving base.
- **LangGraph + MCP SDK are production-ready** — both ≥1.0, with stable interrupt/resume and stdio transport.
- **Reflection pattern on scoring is cheap** — Claude cost for re-reviewing 70–89% borderline jobs is <20% of the base scoring cost.
- **Cost of waiting** — every week that passes without an eval harness is a week of unmeasured prompt drift.

### The Bet

> We believe a solo PM job seeker will triage roles faster and apply with higher-quality tailored content if we add (a) pause-and-steer review, (b) scoring self-critique, (c) Claude Code conversational access, and (d) one-shot resume tailoring — because the current linear pipeline can't be corrected mid-run, scoring errors are silent, and tailoring is manual.

**Riskiest Assumptions**

| Assumption | If Wrong | Validation |
|------------|----------|------------|
| Scoring reflection measurably improves quality | Burns Claude cost for no gain | Eval: run with/without reflection on same 50 ground-truth jobs; require Spearman ∆ ≥ +0.05 to keep |
| One-shot resume tailoring is "good enough" (no Planner-Executor needed) | User hand-edits every output heavily — cost wasted | Manual quality rating on 10 real outputs; require mean ≥ 4.0/5.0 or reconsider |
| MCP tools provide daily value over opening the dashboard | Feature is used once, abandoned | Usage log over 30 days; require ≥ 10 tool invocations/week |
| Shadow mode (agentic vs linear diff) reveals no regressions | Agentic path has silent behavioral drift | 2-week shadow run; zero diffs on `scored_jobs` set for same input |

### Success Metrics

| Metric | Definition | Target | Baseline (Phase 1) |
|--------|------------|--------|---------------------|
| **North Star** | Weekly applications sent with tailored content | 5+/wk | 0 (manual, untracked) |
| **Leading: Scoring quality** | Spearman correlation (AI rank vs. human rank) on 50+ ground-truth jobs | ≥ 0.80 | Unmeasured |
| **Leading: Score agreement** | % of jobs where `|AI_score − human_score| ≤ 10` | ≥ 80% | Unmeasured |
| **Leading: Alignment quality** | Cross-model LLM-judge score (GPT-4 judging Claude alignment output) | Mean ≥ 4.0/5.0 | Unmeasured |
| **Leading: Tailor quality** | Mixed LLM-judge + manual spot-check on 20% of outputs | Mean ≥ 4.0/5.0 | N/A |
| **Leading: Discovery recall** | % of jobs found by manual cross-search also found by pipeline | ≥ 90% | Unmeasured |
| **Guardrail: Cost/run** | Claude input+output tokens × pricing, logged per run_log row | ≤ $2.00 | Unmeasured |
| **Guardrail: p95 latency** | End-to-end run time (fetch → export) p95 over last 10 runs | < 3 min | ~90s p50 (no p95) |
| **Guardrail: Scheduled reliability** | % of launchd Mon/Thu runs completing `status=completed` | ≥ 95% | ~95% (informal) |
| **Guardrail: Eval pass gate** | All 5 leading metrics ≥ target before promoting agentic from shadow → default | Pass before Week 3 | N/A |

---

## 2. Jobs To Be Done

### Primary Job

> When I sit down to review this week's roles, I want to quickly see a small, high-quality set with per-role tailoring ready, so I can send applications today without spending the evening triaging and rewriting my resume.

### Forces

| Force | Strength | Design Implication |
|-------|----------|---------------------|
| Push (pain of Phase 1) — can't pause, no critique of scores, manual tailoring | **H** | HiTL review + reflection + one-shot tailor button earn their place |
| Pull (appeal of agentic) — "Claude Code can just run my search and tell me the top 3" | **H** | MCP tools are primary non-dashboard surface |
| Anxiety — "will reflection/agentic path regress my working pipeline?" | **M** | Shadow mode for 2 weeks; feature flag `RUN_MODE=linear\|agentic`; zero changes to `auto_jobsearch/` |
| Habit — scheduled Mon/Thu launchd run, Flask dashboard browse | **H** | Preserve both. Enhance dashboard, don't replace. launchd stays single-command |

### Progress Metrics

| Dimension | From | To |
|-----------|------|-----|
| Functional | Fetch → score → export (no pause, no conversation, no tailoring) | Fetch → score → reflect → pause → approve → export; tailoring on-demand; conversational via Claude Code |
| Emotional | "Did the pipeline miss something? Is this score right?" (anxiety) | "I can see why each score was given, flag weird ones, adjust threshold live" (confidence) |
| Social | Apply with generic resume | Apply with JD-tailored resume generated in seconds |

---

## 3. Solution Overview

### Features

| # | Feature | User Outcome | Priority | Spec Section |
|---|---------|--------------|----------|--------------|
| F1 | LangGraph workflow (6 nodes + reflection subgraph) | Pause mid-run to approve/cancel; parallel fetch ~3× faster | P0 | [SPEC §2](./SPEC.md#2-f1-langgraph-workflow) |
| F2 | Reflection-on-scoring (borderline 70–89% re-review) | Catches subscore/total inconsistencies silently | P0 | [SPEC §3](./SPEC.md#3-f2-reflection-on-scoring) |
| F3 | MCP server (9 tools, stdio) | Claude Code can run pipeline, fetch jobs, tailor, update status conversationally | P0 | [SPEC §4](./SPEC.md#4-f3-mcp-server) |
| F4 | Resume tailoring (one-shot + quality eval) | Tailored summary/bullets/keywords per JD on demand | P0 | [SPEC §5](./SPEC.md#5-f4-resume-tailoring) |
| F5 | Eval framework (50+ ground truth, cross-model judge) | Quantified scoring/alignment/tailoring/discovery quality | P0 | [SPEC §6](./SPEC.md#6-f5-eval-framework) |
| F6 | Flask dashboard enhancements (slider, watchlist CRUD, HiTL banner, Tailor button) | Adjust threshold live; manage watchlist without editing yaml; review pipeline in-browser | P0 | [SPEC §7](./SPEC.md#7-f6-flask-dashboard-enhancements) |
| F7 | Discovery improvements (source health, query observability, SerpAPI quota) | Failing source auto-skipped; SerpAPI quota never exhausted silently | P1 | [SPEC §8](./SPEC.md#8-f7-discovery-improvements) |
| F8 | Observability + cost tracking (structlog, LangSmith opt-in, run_log token columns) | Every run has correlation_id, cost, trace | P0 | [SPEC §9](./SPEC.md#9-f8-observability--cost-tracking) |

### Out of Scope (Phase 2)

| Excluded | Reason | Revisit When |
|----------|--------|--------------|
| Web-based HiTL push notification | Flask banner polling is sufficient for solo user | Phase 3 or multi-user |
| `SqliteSaver` checkpointing (crash recovery) | `MemorySaver` is adequate for interactive Phase 2; 4h TTL auto-cancels stuck runs | Phase 3 |
| Multi-resume variants | Single `resume_summary.md` covers current targeting | When targeting ≥2 role archetypes |
| Outcome feedback loop (interview rate → retrain prompt) | No application volume yet | After 20+ applications logged |
| ReAct on discovery | Only 3 sources; decision surface too small | Sources ≥ 6 |
| Planner-Executor on tailoring | One-shot baseline unproven-bad; user edits output anyway | If tailor quality eval < 3.5/5.0 |
| Streamlit replacement for Flask | Flask handles expand panels, CSRF, downloads already | Never — decided |
| Wellfound source | Permanently dropped | Never |
| TrueUp URL re-verification (HH2-714) | Live check, no code needed | Post-next-digest — already scheduled |
| Location edge case `"REMOTE (BERLIN, DE)"` (HH2-715) | Low impact for SF Bay Area targeting | When targeting EU |

### Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Orchestration | LangGraph ≥ 0.2.0 | Native interrupt/resume, MemorySaver, conditional edges |
| Agent surface | `mcp` SDK (pinned exact version) | Anthropic official; stdio transport for Claude Code |
| Eval correlation | scipy ≥ 1.13.0 | Spearman rank correlation |
| LLM (scoring/alignment/tailor) | Claude Sonnet 4.6 via `anthropic` SDK | Phase 1 choice; unchanged |
| LLM (cross-model judge) | OpenAI GPT-4 or Gemini 2.5 | Break same-family self-preference bias |
| Logging | `structlog` (JSON) | CLAUDE.md mandate; Phase 1 already uses |
| Tracing (opt-in) | LangSmith | Toggle via `LANGSMITH_TRACING=true`; off by default |
| DB | SQLite + `PRAGMA journal_mode=WAL` | Concurrent dashboard reads during writes |
| Package mgr | `uv` only | CLAUDE.md mandate |
| Phase 1 | Editable dep: `{ path = "../auto_jobsearch", editable = true }` | Zero modification to frozen codebase |

---

## 4. Timeline & Risks

### Milestones

| # | Deliverable | Exit Criteria (Go/No-Go) |
|---|-------------|--------------------------|
| D1 | New folder, `pyproject.toml`, deps installed, `JobSearchState` TypedDict, empty graph compiles | `uv run pytest` green; `from jobsearch import ...` works |
| D2 | `preflight_node` + `discovery_node` with `asyncio.gather`; source health tracking (F7) | `--agentic --dry-run` fetches 3 sources concurrently; source_health columns populated; 3 consecutive-fail auto-skip works |
| D3 | `enrichment_node` concurrent; `scoring_node` wired to Phase 1 `score_jobs_batch()`; reflection subgraph on borderline 70–89% (F2) | Dry run prints reflection deltas; cost logged |
| D4 | `review_node` with `interrupt()`; output_node; 4h TTL auto-cancel | Full graph end-to-end with CLI HiTL (terminal mode) — 3 clean runs |
| D5 | MCP server (F3) — all 9 tools, stdio, `run_pipeline` auto-approves, Claude Code config tested | "@jobsearch get_jobs limit=5" works in Claude Code |
| D6 | `resume_tailor.py` + prompt + DB migration (additive) + Flask route + Tailor button (F4, F6) | Tailor button returns structured output cached by (resume_hash, prompt_ver, hash_id) |
| D7 | Ground truth dataset (50+ jobs) + `scorer_eval.py` + `alignment_eval.py` + `tailor_eval.py` + `discovery_recall_eval.py` (F5) | `uv run eval/run_eval.py` produces all 4 reports |
| D8 | Flask threshold slider (display-filter only), watchlist CRUD panel, HiTL polling banner (F6) | Slider filters table client-side; add/remove watchlist companies without page reload; banner shows on `status=review_pending` |
| D9 | Observability wiring: structlog correlation_id, LangSmith toggle, cost columns in `run_log` (F8); launchd plist updated for `--agentic --auto-approve` | Every run_log row has input_tokens, output_tokens, estimated_cost_usd; scheduled run tagged `trigger=scheduled` |
| D10 | Shadow-mode comparison harness + integration tests (80% coverage gate) + docs update | Shadow run on 3 consecutive real fetches produces zero `scored_jobs` diff; pytest coverage ≥ 80% |
| **Shadow period** | Run 2 weeks with `RUN_MODE=linear` default + shadow agentic on every run | Zero diffs OR all diffs explained; eval gates pass → flip default to `agentic` |

### Top Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|------------|--------|------------|
| R1 | Reflection doesn't improve Spearman | M | M | Eval gate: require ∆ ≥ +0.05 to keep; else remove, keep as dead code |
| R2 | MCP `run_pipeline` called while graph has active interrupt (another session) | M | H | Single-writer lock in run_log (`status=running` blocks new starts); MCP returns `PIPELINE_BUSY` error |
| R3 | Shadow mode shows divergence between linear and agentic outputs | M | H | Mandatory 2-week shadow; any unexplained diff blocks promotion |
| R4 | SQLite write contention between Flask polling and graph writes | M | M | WAL mode + read-only dashboard connections + 5s poll interval (not sub-second) |
| R5 | 4h interrupt TTL cancels a run the user was about to approve | L | M | Dashboard banner shows countdown; email/push is Phase 3. User can extend once via "Extend 2h" button |
| R6 | Cost per run exceeds $2 target (Claude price change, larger fetches) | L | L | Dashboard warning banner > $2; kill-switch `MAX_COST_USD` env var aborts before score step |
| R7 | Cross-model judge provider (GPT-4/Gemini) outage breaks eval | L | L | Eval is offline; retry with exponential backoff, 3 attempts; skip with warning, don't fail build |
| R8 | `imaplib` (TrueUp) breaks under `asyncio.to_thread` concurrent use | L | H | Each invocation opens its own IMAP connection; Day 2 concurrent test gate |
| R9 | MCP SDK breaking change on upgrade | L | M | Pin exact minor version in `pyproject.toml`; upgrade only with smoke test |
| R10 | Resume tailor quality is <4.0 on eval | M | M | Fallback: Planner-Executor variant (already scoped as Out of Scope → becomes Phase 2.5 if triggered) |

---

## 5. Links

| Resource | Link |
|----------|------|
| SPEC | [./SPEC.md](./SPEC.md) |
| TECH-DESIGN | [./TECH-DESIGN.md](./TECH-DESIGN.md) |
| EXP-BRIEF | [./EXP-BRIEF.md](./EXP-BRIEF.md) |
| Phase 1 plan (superseded) | [../PHASE2-AGENTIC-PLAN.md](../PHASE2-AGENTIC-PLAN.md) |
| Session handover | [../PHASE2_SESSION-HANDOVER.md](../PHASE2_SESSION-HANDOVER.md) |
| Phase 1 codebase (frozen) | `auto_jobsearch/` (sibling package, imported as editable dep) |

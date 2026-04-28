> **HISTORICAL DOCUMENT** — This file reflects the design decisions made before implementation. The codebase has since evolved: Phase 1 (auto_jobsearch) is now absorbed into `role_scout/compat/` as a frozen sub-package; there is no sibling repo dependency. Treat this as design context, not current truth.

---

> **HISTORICAL DOCUMENT** — This file is superseded by the Phase 2 PRD set (`docs/PRD-CORE.md`, `docs/SPEC.md`, `docs/TECH-DESIGN.md`). It is retained for reference only. Do not implement from this document; some decisions recorded here (e.g. the `threshold_adjusted→scoring` graph edge) were explicitly reversed during PRD authoring.

# Phase 2 — Agentic Job Search Pipeline

| Field | Value |
|-------|-------|
| Created | 2026-04-22 |
| Status | Approved |
| Builds On | Phase 1 (`auto_jobsearch/`) — frozen, unchanged |
| Development | New dedicated folder — separate from Phase 1 repo |

---

## 1. Motivation

Phase 1 is a working, linear CLI pipeline:

```
fetch → normalize → dedup → enrich → watchlist → score → export
```

It runs end-to-end in a single process with no parallelism, no way to pause mid-run, no conversational interface, and no automated resume output.

Phase 2 addresses all four gaps by wrapping the same Phase 1 modules (unchanged) in a LangGraph multi-agent graph, adding an MCP server for Claude Code conversational access, adding automated resume tailoring, and adding a lightweight eval framework for prompt quality measurement.

**Core design principle (carried from Phase 1):** Every Phase 1 function takes typed inputs, returns typed outputs (Pydantic), produces no side effects. This means every Phase 1 module is already a valid agent tool — zero refactoring required.

---

## 2. Development Strategy

Phase 2 is built in a **new dedicated folder** — not as a branch of `auto_jobsearch/`. The Phase 1 codebase remains untouched on `main`. The new folder imports Phase 1 modules as a local package dependency (`pip install -e ../auto_jobsearch`). This keeps Phase 1 stable and production-ready while Phase 2 is being built.

---

## 3. What Does NOT Change (Phase 1 — frozen)

Every file in `jobsearch/` remains unchanged and is imported directly by Phase 2 nodes:

| Module | Phase 2 role |
|--------|-------------|
| `jobsearch/fetchers/linkedin.py` | Discovery agent tool |
| `jobsearch/fetchers/google_jobs.py` | Discovery agent tool |
| `jobsearch/fetchers/trueup.py` | Discovery agent tool |
| `jobsearch/pipeline/normalize.py` | Discovery node |
| `jobsearch/pipeline/dedup.py` | Discovery node |
| `jobsearch/pipeline/enrich.py` | Enrichment node |
| `jobsearch/pipeline/watchlist.py` | Enrichment node |
| `jobsearch/pipeline/scorer.py` | Scoring node |
| `jobsearch/pipeline/export.py` | Output node |
| `jobsearch/pipeline/alignment.py` | MCP tool + existing route |
| `jobsearch/db/` | All nodes + MCP tools |
| `jobsearch/models.py` | Shared data models |
| `jobsearch/config.py` | Shared configuration |
| `jobsearch/dashboard/` | Flask dashboard — kept and enhanced (not replaced by Streamlit) |
| `prompts/scoring_system.md` | Unchanged scoring prompt |
| `prompts/alignment_system.md` | Unchanged alignment prompt |

**Why keep Flask instead of Streamlit?** The existing Flask dashboard handles the expand-row interaction, CSRF protection, status updates, and JD file downloads. Streamlit does not support complex per-row expand panels, persistent background processes, or download-as-attachment flows without significant workarounds. Phase 2 enhances Flask with the missing interactive features rather than rewriting it.

---

## 4. New Directory Structure (Phase 2 additions)

```
role_scout/                   # New dedicated Phase 2 folder
├── agents/                          # LangGraph graph + nodes
│   ├── __init__.py
│   ├── state.py                     # JobSearchState TypedDict
│   ├── graph.py                     # StateGraph definition + compilation
│   └── nodes/
│       ├── preflight.py             # Load profile, watchlist, init DB, open run_log
│       ├── discovery.py             # Parallel fetch + normalize + dedup
│       ├── enrichment.py            # Parallel URL enrichment
│       ├── scoring.py               # Batch score via scorer.py
│       ├── review.py                # HiTL interrupt — surface results, await human decision
│       └── output.py                # Export JDs, persist to DB, close run_log
├── mcp_server/                      # MCP server exposing pipeline as tools
│   ├── __init__.py
│   └── server.py                    # ~150 lines, stdio transport, 9 tools
├── eval/                            # Scoring + alignment quality measurement
│   ├── __init__.py
│   ├── ground_truth.yaml            # 20 manually scored jobs with human ratings
│   ├── scorer_eval.py               # Spearman correlation: AI rank vs. human rank
│   ├── alignment_eval.py            # LLM-as-Judge on alignment quality
│   └── run_eval.py                  # CLI: uv run python eval/run_eval.py
├── prompts/
│   └── resume_tailor_system.md      # New resume tailoring prompt
├── resume_tailor.py                 # tailor_resume() function
├── run.py                           # CLI entry — --agentic, --mcp, --eval flags
└── pyproject.toml                   # langgraph, mcp, scipy + Phase 1 as local dep
```

---

## 5. Component 1: LangGraph Agent Graph

### 5.1 Agent State (`agents/state.py`)

```python
class JobSearchState(TypedDict):
    run_id: str
    trigger_type: str                         # manual | scheduled | dry_run
    candidate_profile: CandidateProfile
    watchlist: list[str]
    qualify_threshold: int                    # default 85, adjustable at HiTL checkpoint

    # Discovery outputs
    raw_by_source: dict[str, list[dict]]      # source → raw job dicts
    normalized_jobs: list[NormalizedJob]
    new_jobs: list[NormalizedJob]             # post-dedup

    # Enrichment + scoring outputs
    enriched_jobs: list[NormalizedJob]
    watchlist_hits: dict[str, int]
    scored_jobs: list[ScoredJob]              # match_pct >= qualify_threshold

    # Human review
    human_approved: bool
    threshold_adjusted: bool                  # True if user changed threshold → re-score

    # Output
    exported_count: int

    # Audit
    source_counts: dict[str, int]
    errors: list[str]
```

### 5.2 Graph Topology (`agents/graph.py`)

```
START
  ↓
preflight_node
  ↓
discovery_node          ← asyncio.gather over 3 fetchers concurrently
  ↓
enrichment_node         ← asyncio.gather over N jobs concurrently
  ↓
scoring_node            ← calls scorer.score_jobs_batch() unchanged
  ↓
review_node             ← LangGraph interrupt(); HiTL pause
  ↓ [conditional routing]
  ├── human_approved=True  → output_node → END
  ├── threshold_adjusted=True → scoring_node (retry with new threshold)
  └── cancelled            → END (no DB writes)
```

**Conditional edge after `review_node`:**
```python
def route_after_review(state: JobSearchState) -> str:
    if state["human_approved"]:
        return "output"
    if state["threshold_adjusted"]:
        return "scoring"
    return END
```

### 5.3 Node Contracts

**`preflight_node`** — loads profile + watchlist, inits DB, opens run_log with status="running"

**`discovery_node`** — runs all 3 fetchers concurrently via `asyncio.gather(asyncio.to_thread(...))`, normalizes per source, deduplicates combined list. Failures are non-fatal: append to errors[], source count = 0.

**`enrichment_node`** — calls `enrich_descriptions()` and `tag_watchlist()`. Enrichment is already per-job isolated; concurrent via `asyncio.gather`.

**`scoring_node`** — calls `score_jobs_batch(enriched_jobs, candidate_profile, qualify_threshold=state["qualify_threshold"])`. Clears scored_jobs if re-entered after threshold adjustment.

**`review_node`** — calls LangGraph `interrupt()`. Resume payload: `{"approved": bool, "new_threshold": int | None}`. Surfaces score distribution + top matches in CLI.

**`output_node`** — `insert_qualified_job()` → `export_jd()` → `update_jd_filename()` per job, then `expire_old_hashes()` and `update_run_log(status="completed")`.

### 5.4 Parallel Fetch Strategy

Phase 1 fetchers are synchronous (httpx sync, imaplib). `asyncio.to_thread()` runs each in the thread pool — true I/O concurrency without rewriting fetchers.

Expected improvement: ~90s sequential → ~35s concurrent (bounded by slowest source, typically the Apify LinkedIn actor).

### 5.5 HiTL CLI Interface (Phase 2)

```
── Pipeline Review ──────────────────────────────────
  Fetched: 108  New: 75  Qualified at 85%: 31
  Top matches: WorkOS 84%, Anthropic 82%, Anthropic 77%
  Watchlist hits: Anthropic (2)

  [a] Approve and export   [t] Change threshold   [x] Cancel
>
```

Web-based HiTL (push notification + browser approve/reject) is Phase 3.

### 5.6 Checkpointing

Phase 2: `MemorySaver` (in-process). Interrupt state is lost if process is killed; acceptable for interactive use. Phase 3: `SqliteSaver` for crash recovery.

---

## 6. Component 2: MCP Server (`mcp_server/server.py`)

Exposes the pipeline as Claude Code tools via stdio transport. ~150 lines using Anthropic's `mcp` Python SDK.

### Tools

| Tool | Signature | Calls |
|------|-----------|-------|
| `run_pipeline` | `(dry_run: bool = False)` | orchestrator or agents/graph.py |
| `get_jobs` | `(status: str = "new", limit: int = 10, source: str = None)` | `get_qualified_jobs()` DAL |
| `get_job_detail` | `(hash_id: str)` | `get_job_by_hash_id()` DAL |
| `analyze_job` | `(hash_id: str, force: bool = False)` | `run_alignment()` |
| `tailor_resume` | `(hash_id: str, force: bool = False)` | `tailor_resume()` (new) |
| `update_job_status` | `(hash_id: str, status: str)` | `update_job_status()` DAL |
| `get_run_history` | `(limit: int = 5)` | `get_run_logs()` DAL |
| `get_watchlist` | `()` | reads `watchlist.yaml` |
| `manage_watchlist` | `(action: str, company: str)` | add/remove in `watchlist.yaml` |

### Claude Code Registration

```json
{
  "mcpServers": {
    "jobsearch": {
      "command": "uv",
      "args": ["run", "python", "run.py", "--mcp"],
      "cwd": "/path/to/auto_jobsearch_v2"
    }
  }
}
```

Example conversational interactions:
- "Show my top 5 new jobs"
- "Analyze the WorkOS PM role for me"
- "Mark Anthropic as applied"
- "Run the pipeline"

---

## 7. Component 3: Automated Resume Tailoring

### New Function (`resume_tailor.py`)

```python
def tailor_resume(job: ScoredJob, api_key: str) -> TailoredResume:
    """Generate tailored resume content for a specific job using Claude."""
```

**Process:**
1. Load `config/resume_summary.md` (existing — also used by alignment)
2. Load `prompts/resume_tailor_system.md` (new)
3. Strip HTML from description (reuse `_strip_html` from normalize.py)
4. Interpolate: `$resume_summary`, `$title`, `$company`, `$description`, `$key_requirements`
5. Call Claude (`claude-sonnet-4-6`, max_tokens=2048, timeout=60s)
6. Parse JSON response → `TailoredResume`

### New Model

```python
class TailoredResume(BaseModel):
    hash_id: str
    job_title: str
    company: str
    tailored_summary: str           # 3-sentence exec summary reframed for this JD
    tailored_bullets: list[str]     # 5-7 achievement bullets reordered/reframed for fit
    keywords_incorporated: list[str]  # JD keywords surfaced in the resume
    tailored_at: datetime
```

### DB Change (additive migration, idempotent)

```sql
ALTER TABLE qualified_jobs ADD COLUMN tailored_resume TEXT;
```

Applied in `init_db()` via `try/except sqlite3.OperationalError` — safe to run on existing DB.

### Prompt (`prompts/resume_tailor_system.md`)

Sections: candidate background (`$resume_summary`), target role (`$title` at `$company`, `$description`), scoring signals (`$key_requirements`). Instructions: produce tailored_summary, tailored_bullets (reframe — no fabrication), keywords_incorporated. Output: JSON matching `TailoredResume`.

### New Dashboard Route

`POST /api/tailor/<hash_id>` — mirrors `/api/alignment/<hash_id>`. Returns cached result if available; force-recomputes with `force=True`. New "Tailor" button in expanded row panel, alongside the existing "Align" button.

---

## 8. Component 4: Eval Framework

### Ground Truth Dataset (`eval/ground_truth.yaml`)

20 manually scored jobs with human ratings covering all edge cases: no comp listed, remote, watchlist companies, jobs that should be rejected. Format per job:

```yaml
- title: "Senior Product Manager"
  company: "Stripe"
  location: "San Francisco, CA"
  work_model: "hybrid"
  company_stage: "Public"
  salary_visible: false
  description: "...full JD text..."
  human_score: 88
  human_subscores: { seniority: 28, domain: 23, location: 18, stage: 12, comp: 5 }
```

### Scorer Eval (`eval/scorer_eval.py`)

- Run `score_jobs_batch()` on 20 ground truth jobs
- Compute Spearman rank correlation (AI rank vs. human rank)
- Compute agreement % (|AI score - human score| ≤ 10)
- Print disagreement table for misses > 10 points

**Target:** ≥ 0.80 Spearman correlation, ≥ 80% agreement within ±10 points.

### Alignment Eval (`eval/alignment_eval.py`)

- Run `run_alignment()` on 10 job-resume pairs
- LLM-as-Judge: send each alignment result to Claude, ask for quality rating 1–5 with reasoning
- Report mean quality score per section (strong_matches, reframing_opportunities, genuine_gaps)

**Target:** Mean LLM-judge quality ≥ 4.0 / 5.0.

### CLI (`eval/run_eval.py`)

```bash
uv run python eval/run_eval.py              # both evals
uv run python eval/run_eval.py --scorer     # scorer only
uv run python eval/run_eval.py --alignment  # alignment only
```

---

## 9. Component 5: Flask Dashboard Enhancements

### Score Threshold Slider

Range input (75–95, step 1) in sidebar. On change: POST `/api/config/threshold` → updates in-memory threshold. Jobs table client-side filters by `match_pct`. This is a display filter only — does not re-score; acts on already-qualified jobs.

### Watchlist Management Panel

Text input + Add button at bottom of sidebar. × button per company in watchlist list. POST `/api/watchlist` (add) / DELETE `/api/watchlist/<company>` (remove) — writes to `watchlist.yaml`. Updates ★ badges in table without page reload.

### HiTL Review Banner

Polls GET `/api/pipeline/status` every 5s. If `run_log.status = "review_pending"`, shows a top-of-page banner with the scored job count + Approve / Adjust threshold / Cancel buttons. POST `/api/pipeline/resume` → graph continues.

---

## 10. `run.py` Extensions

New flags (additive — Phase 1 flags unchanged):

| Flag | Action |
|------|--------|
| `--agentic` | Run via LangGraph graph |
| `--agentic --dry-run` | Graph dry run — no DB persist, HiTL skipped |
| `--mcp` | Start MCP server via stdio |
| `--eval` | Run eval suite |

---

## 11. New Dependencies

```toml
langgraph>=0.2.0    # agent graph + interrupt/resume + MemorySaver
mcp>=1.0.0          # MCP server SDK (Anthropic official)
scipy>=1.13.0       # Spearman correlation in eval
```

Phase 1 `auto_jobsearch` installed as local editable dependency:
```toml
[tool.uv.sources]
jobsearch = { path = "../auto_jobsearch", editable = true }
```

---

## 12. Implementation Milestones (10 working days)

### Week 1: Graph + Parallel Execution

| Day | Deliverable | Exit Criteria |
|-----|-------------|---------------|
| 1 | New folder, pyproject.toml, deps, `JobSearchState`, empty graph skeleton | `uv run pytest` passes; Phase 1 import works |
| 2 | `preflight_node` + `discovery_node` with `asyncio.gather` | `--agentic --dry-run` fetches 3 sources in parallel, prints source counts |
| 3 | `enrichment_node` with concurrent enrichment | Enrichment quality matches Phase 1 |
| 4 | `scoring_node` wired to Phase 1 `score_jobs_batch()` | `--agentic --dry-run` scores and prints qualified jobs |
| 5 | `review_node` with `interrupt()` + CLI HiTL + `output_node` | Full graph end-to-end: fetch → score → CLI review → export. 3 successful runs |

### Week 2: MCP + Resume Tailor + Eval

| Day | Deliverable | Exit Criteria |
|-----|-------------|---------------|
| 6 | MCP server — all 9 tools, Claude Code config | "show my top 5 jobs" works in Claude Code |
| 7 | `resume_tailor.py` + prompt + DB column + dashboard route + Tailor button | Tailor button returns structured output in expand panel |
| 8 | Ground truth dataset + `scorer_eval.py` | `--scorer` prints Spearman r and agreement % |
| 9 | `alignment_eval.py` + Flask threshold slider + watchlist panel | `--alignment` prints quality scores; slider filters table |
| 10 | HiTL Flask banner + integration testing + docs | All 5 verification steps pass |

---

## 13. Uncertainty Flags

| Item | Risk | Mitigation |
|------|------|-----------|
| LangGraph `interrupt()` requires a checkpointer to survive process restart | High | Use `MemorySaver` for Phase 2; `SqliteSaver` is Phase 3 |
| `mcp` SDK version compatibility | Medium | Pin to tested version; smoke test before connecting Claude Code |
| `asyncio.to_thread()` + Apify httpx client thread safety | Medium | Each fetcher creates its own httpx client; should be safe — verify with concurrent test on Day 2 |
| LangGraph conditional re-entry on threshold change | Medium | Test `Command(goto="scoring")` pattern on Day 5 before building dashboard |
| Ground truth dataset representativeness | Medium | Draw from first 3 real pipeline runs; include edge cases (no comp, remote, watchlist, rejected) |
| Resume tailor prompt quality | Unknown | Manual evaluation on 5 real jobs before finalizing prompt |

---

## 14. Verification

```bash
# Phase 1 tests still pass (Phase 1 unchanged)
cd ../auto_jobsearch && uv run pytest -q

# Graph dry run — parallel fetch, score, no persist
uv run python run.py --agentic --dry-run

# Full agentic run — fetch → score → CLI review → export
uv run python run.py --agentic

# MCP server responds
uv run python run.py --mcp
# Then in Claude Code: "@jobsearch get_jobs"

# Resume tailor — via dashboard Tailor button on any qualified job

# Eval suite
uv run python eval/run_eval.py
```

---

## 15. Out of Scope (Phase 3)

- Web-based HiTL review (push notification + browser approve/reject)
- LangGraph `SqliteSaver` for crash recovery
- Multi-resume support (variants for different role types)
- Application outcome feedback loop (interview rate → retrain scoring prompt)
- Automated email / push notifications on pipeline completion
- TrueUp URL verification (pending next weekly digest — no code change needed)

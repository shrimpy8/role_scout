# Role Scout

**Automated job search pipeline with AI scoring, agentic review, and resume tailoring.**

Role Scout fetches job listings from multiple sources, scores them against your resume using Claude, flags the most relevant ones for human review, and generates tailored resume bullets on demand. It runs on a schedule, surfaces results through a lightweight web dashboard, and exposes a conversational interface via Claude Code's MCP protocol.

---

## Why Role Scout?

Job searching is repetitive and noisy. Role Scout solves three specific problems:

1. **Signal-to-noise**: Most job boards return hundreds of results. Role Scout scores each listing against your actual resume and work preferences, filters to the matches that matter, and explains why each scored the way it did.

2. **Review without micromanagement**: The pipeline runs automatically (Mon/Thu mornings via launchd), pauses for your approval before exporting, and cancels itself if you don't respond within 4 hours — no orphaned state.

3. **Application prep**: For each qualified job, one click generates Claude-tailored resume bullets, a professional summary, and a keyword list extracted from the job description. No copy-pasting between tabs.

---

## Architecture

Role Scout has two layers:

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 2 — Agentic Layer (this repo)                        │
│                                                             │
│  LangGraph DAG ──► HiTL interrupt ──► Flask dashboard      │
│       │                                    │                │
│   Reflection                          MCP server           │
│   (borderline                     (Claude Code CLI)        │
│    70-89% re-score)                                         │
└────────────────────────────┬────────────────────────────────┘
                             │ imports as editable dep
┌────────────────────────────▼────────────────────────────────┐
│  Phase 1 — Linear Pipeline (auto_jobsearch/ — frozen)       │
│                                                             │
│  fetch → normalize → dedup → enrich → score → export       │
│  (LinkedIn · Google Jobs · TrueUp email alerts)            │
└─────────────────────────────────────────────────────────────┘
```

### LangGraph pipeline nodes

| Node | What it does |
|------|-------------|
| **preflight** | Validates sources, checks SerpAPI quota, applies circuit breaker if ≥2 sources fail |
| **discovery** | Fetches jobs from all active sources in parallel; records per-source health |
| **normalize** | Deduplicates and normalises raw listings; trims raw_by_source from state |
| **enrichment** | Fetches full job descriptions where needed |
| **scoring** | Sends jobs to Claude in batches; records token usage and cost |
| **reflection** | Second Claude pass on borderline jobs (70-89%); reviews subscores for internal consistency |
| **review** | Persists qualified jobs, logs run metrics, then issues interrupt() for HiTL |
| **export** | Runs after human approval; writes to the export sheet |

**HiTL flow**: the graph pauses at `review`. The Flask dashboard polls `/api/pipeline/status` every 5 seconds and renders an approval banner. You click Approve or Cancel (or use keyboard shortcuts A / Esc). If no response arrives within 4 hours the run auto-cancels and is logged as `cancel_reason=ttl_expired`.

---

## Features

### Dashboard
- Qualified jobs table, sortable by score
- Threshold slider — **display filter only**, never re-scores
- Watchlist panel: add companies to highlight their jobs with ★
- HiTL review banner with TTL countdown and +2h extend button
- Per-run cost warning when a run exceeds $2
- Tailor panel: per-job expand with Claude-generated summary, bullets, and keywords

### Resume tailoring
- One-shot Claude call per job (not a multi-turn planner)
- Results cached per `(hash_id, sha256(resume), prompt_version)` — changes to your resume or prompt automatically invalidate the cache
- Force-refresh available via the UI or API

### MCP server (Claude Code integration)
Connect Claude Code to Role Scout and ask questions like:
- "Show my top 5 jobs from the last run"
- "Tailor my resume for job abc123"
- "Trigger a dry-run pipeline fetch"

Nine tools exposed over stdio: `get_pipeline_status`, `run_pipeline`, `get_jobs`, `get_job_detail`, `tailor_resume`, `get_run_logs`, `get_watchlist`, `add_to_watchlist`, `remove_from_watchlist`.

### Eval harness
Three eval tracks run against 50+ ground-truth jobs:
- **Scorer eval**: Spearman correlation between Claude scores and manual labels
- **Alignment eval**: LLM-as-judge (cross-model) on Claude's reasoning
- **Tailor eval**: LLM-as-judge + 20% manual spot-check on generated bullets

### Shadow mode
Runs both the Phase 1 linear pipeline and the Phase 2 agentic graph on the same input, diffs `scored_jobs`, and writes a JSON report to `shadow_diffs/`. Disagreements (delta > 2 pts, or job present in only one path) are flagged. Shadow mode is the default during the first two weeks of deployment.

---

## Requirements

- Python 3.11+
- `uv` package manager (`brew install uv` or see [uv docs](https://docs.astral.sh/uv/))
- API keys: Anthropic, SerpAPI, Apify
- IMAP credentials for email-based job alerts (TrueUp source)

---

## Installation

```bash
git clone <repo-url>
cd role_scout

# Install all dependencies (creates .venv automatically)
uv sync

# Copy and fill in environment variables
cp .env.example .env
```

---

## Configuration

All runtime configuration lives in `.env`. No values are hardcoded.

### Required

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude API key (claude-3-5-sonnet or better recommended) |
| `SERPAPI_KEY` | SerpAPI key for Google Jobs source |
| `APIFY_TOKEN` | Apify token for LinkedIn scraper actor |
| `IMAP_EMAIL` | Email address that receives TrueUp job alert digests |
| `IMAP_APP_PASSWORD` | App-specific password for that IMAP account |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `role_scout.db` | SQLite database file path |
| `RUN_MODE` | `shadow` | `agentic` · `shadow` · `linear` |
| `SCORE_THRESHOLD` | `85` | Minimum match % to qualify a job |
| `MAX_COST_USD` | `5.00` | Per-run cost kill-switch (USD) |
| `LOG_LEVEL` | `INFO` | `DEBUG` · `INFO` · `WARNING` · `ERROR` |
| `LANGSMITH_TRACING` | `false` | Enable LangSmith graph traces |
| `LANGSMITH_API_KEY` | — | Required when `LANGSMITH_TRACING=true` |
| `LANGSMITH_PROJECT` | `role_scout` | LangSmith project name |
| `FLASK_SECRET_KEY` | — | Required for Flask session/CSRF (generate with `python -c "import secrets; print(secrets.token_hex(32))"`) |

---

## Usage

### Run the agentic pipeline (interactive)

```bash
uv run python run.py --agentic
```

The pipeline runs, pauses at the review node, and waits for you to approve or cancel via the dashboard (start with `--serve` in a separate terminal) or the CLI prompt.

### Run the dashboard

```bash
uv run python run.py --serve
# Open http://127.0.0.1:5000
```

The dashboard binds to `127.0.0.1` only — it is not exposed to the network.

### Dry run (no DB writes)

```bash
uv run python run.py --agentic --dry-run
```

Full pipeline execution with `trigger_type=dry_run`. Nothing is persisted.

### Shadow mode

```bash
uv run python run.py --shadow
```

Runs both pipelines, diffs results, writes report to `shadow_diffs/YYYY-MM-DD-<run_id>.json`.

### MCP server

```bash
uv run python run.py --mcp
```

Starts the stdio MCP server. Typically you do not run this directly — it is launched by Claude Code via the registration in `~/.claude.json`.

### All CLI flags

| Flag | Description |
|------|-------------|
| `--agentic` | Execute the LangGraph pipeline |
| `--shadow` | Run shadow mode (both pipelines + diff) |
| `--serve` | Start the Flask dashboard on `127.0.0.1:5000` |
| `--mcp` | Start the MCP server on stdio |
| `--auto-approve` | Skip HiTL; approve automatically (used by scheduled runs) |
| `--dry-run` | No DB writes (`trigger_type=dry_run`) |
| `--force-partial` | Continue even if ≥2 discovery sources fail |
| `--source NAME` | Override active sources; repeatable (e.g. `--source linkedin --source google`) |

---

## Claude Code (MCP) setup

Add to `~/.claude.json` (or your project `.claude.json`):

```json
{
  "mcpServers": {
    "role_scout": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "python", "run.py", "--mcp"],
      "cwd": "/absolute/path/to/role_scout"
    }
  }
}
```

Replace `/absolute/path/to/role_scout` with the path to this repository. Then restart Claude Code and try: *"Show me my top scoring jobs from the last run."*

---

## Scheduled runs (macOS launchd)

To run the pipeline automatically every Monday and Thursday at 08:00:

1. Copy the example plist from `launchd/com.rolescout.pipeline.plist.example`
2. Edit the paths and environment variables inside it
3. Place it in `~/Library/LaunchAgents/`
4. Load it: `launchctl load ~/Library/LaunchAgents/com.rolescout.pipeline.plist`

Scheduled runs use `--auto-approve` (no HiTL prompt). The dashboard still shows the banner and lets you cancel after the fact within the TTL window.

---

## Shadow mode promotion

Before switching `RUN_MODE=agentic` in production, all three of these gates must pass:

1. **Zero disagreements** (delta > 2 pts) across 6 consecutive real fetches
2. **All eval gates pass** (see below)
3. **Coverage ≥ 80%** (`uv run pytest --cov`)

### Eval gates

| Eval | Gate | Metric |
|------|------|--------|
| Scorer Spearman | ≥ 0.80 | Correlation vs 50+ ground-truth labels |
| Alignment mean | ≥ 4.0/5.0 | LLM-as-judge on Claude's scoring reasoning |
| Tailor quality mean | ≥ 4.0/5.0 | LLM-as-judge + 20% manual spot-check |
| Discovery recall | ≥ 90% | Known jobs found vs manual search baseline |
| Cost per run | ≤ $2.00 | 95th percentile across shadow runs |
| p95 latency | < 3 min | End-to-end pipeline wall-clock time |
| Scheduled reliability | ≥ 95% | Successful runs / scheduled runs over 30 days |

Run evals:

```bash
uv run python -m role_scout.eval.run_eval --all
# Reports written to eval/reports/YYYY-MM-DD-<type>.md
```

---

## Observability

Every run emits structured JSON logs via `structlog`. Each log line includes:
- `correlation_id` — unique per run, propagated through all nodes
- `run_id` — stable DB identifier for the run
- `event` — dot-namespaced event name (e.g. `preflight.ok`, `scoring.batch_complete`)

Example log line:
```json
{
  "event": "scoring.batch_complete",
  "correlation_id": "c1d2e3f4-...",
  "run_id": "550e8400-...",
  "batch": 1,
  "jobs_scored": 12,
  "input_tokens": 8240,
  "output_tokens": 1180,
  "cost_usd": 0.043,
  "level": "info",
  "timestamp": "2026-04-24T08:03:11Z"
}
```

Set `LOG_LEVEL=DEBUG` in `.env` to see per-node state transitions and Claude prompt previews.

**LangSmith**: set `LANGSMITH_TRACING=true` to send graph traces to LangSmith. Useful for debugging multi-turn reflection passes and inspecting exact prompts sent to Claude.

---

## Project structure

```
role_scout/
├── src/role_scout/
│   ├── dashboard/          # Flask app (routes, templates, static JS)
│   │   ├── __init__.py     # create_app(), security headers
│   │   ├── routes.py       # API + page routes
│   │   ├── templates/      # base.html, index.html, debug_runs.html
│   │   └── static/js/      # banner.js, threshold.js, watchlist.js
│   ├── nodes/              # LangGraph node implementations
│   │   ├── preflight.py    # Source validation + circuit breaker
│   │   ├── discovery.py    # Multi-source job fetching
│   │   ├── normalize.py    # Dedup + normalise
│   │   ├── enrichment.py   # Description fetching
│   │   ├── scoring.py      # Claude batch scoring
│   │   ├── reflection.py   # Borderline re-score pass
│   │   ├── review.py       # Persist + HiTL interrupt
│   │   └── export.py       # Post-approval export
│   ├── dal/                # Data access layer
│   ├── mcp_server/         # MCP stdio server + tool schemas
│   ├── eval/               # Eval harness (scorer, alignment, tailor)
│   ├── prompts/            # Claude prompt templates
│   ├── graph.py            # LangGraph DAG definition
│   ├── runner.py           # Pipeline orchestrator + resolve_pending()
│   ├── tailor.py           # Resume tailoring (cache + Claude call)
│   ├── claude_client.py    # Anthropic SDK wrapper with timeout
│   ├── db.py               # SQLite helpers (WAL, ro_conn, rw_conn)
│   └── config.py           # pydantic-settings Settings model
├── auto_jobsearch/         # Phase 1 linear pipeline (frozen — do not modify)
├── tests/
│   ├── unit/               # Per-node unit tests (mocked state in/out)
│   ├── integration/        # Full-graph tests with mocked Claude
│   └── e2e/                # Flask route E2E tests
├── docs/                   # PRD, spec, tech design, API spec, data model
├── shadow_diffs/           # Shadow mode diff reports (gitignored)
├── eval/reports/           # Eval output (gitignored)
├── run.py                  # Entry point
├── pyproject.toml
└── .env.example
```

---

## Development

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=role_scout --cov-report=term --cov-fail-under=80

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Dependency vulnerability scan
uv run pip-audit
```

### Quality gates (required before promotion)

| Gate | Command | Threshold |
|------|---------|-----------|
| Tests | `uv run pytest` | All pass |
| Coverage | `uv run pytest --cov --cov-fail-under=80` | ≥ 80% |
| Lint | `uv run ruff check .` | Zero errors |
| Vuln scan | `uv run pip-audit` | Zero high/critical CVEs |
| Shadow runs | Manual review of `shadow_diffs/` | Zero unexplained diffs |

---

## Key design decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Agentic framework | LangGraph | Native interrupt() support; checkpointing via MemorySaver; explicit node boundaries make unit testing straightforward |
| Scoring approach | Batch Claude calls | One call per N jobs; cheaper and faster than per-job calls; reflection pass handles borderline cases |
| Tailoring approach | One-shot Claude call | Simpler than Planner-Executor; quality is sufficient; cache makes repeat access free |
| Threshold slider | Display filter only | Re-scoring would be expensive and slow; the slider filters the already-scored list client-side |
| HiTL mechanism | Flask banner + interrupt() | Browser is the natural review surface; CLI prompt is a fallback for terminal-only runs |
| DB | SQLite + WAL | Single-user tool; WAL enables concurrent reads from dashboard while pipeline writes; no replication needed |
| Dashboard binding | 127.0.0.1 only | Job search data and API keys are sensitive; not exposing to network is the right default |
| MCP transport | stdio only | Claude Code's standard; no additional network surface |

---

## Security notes

- The dashboard binds to `127.0.0.1` — it is not reachable from other machines on your network
- All write routes (`/api/tailor`, `/api/pipeline/resume`, `/api/pipeline/extend`, `/api/watchlist`) require a CSRF token
- User-supplied values rendered in the browser are HTML-escaped in templates and in all JavaScript DOM writes
- Security headers are set on every response: `Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`
- `.env` is gitignored; never commit API keys

---

## License

MIT

# CLAUDE.md — Role Scout

Overrides `~/Documents/GitHub/CLAUDE.md` where they conflict.

## Project Identity

- **What it is:** Single-user LangGraph agentic pipeline + Flask dashboard + MCP server for job discovery and scoring.
- **Stack:** Python 3.12, LangGraph, Flask, SQLite, Anthropic Claude, pydantic-settings, structlog.
- **Run:** `uv run python run.py` (pipeline) · `uv run python run.py --serve` (dashboard, localhost only)
- **Test:** `uv run pytest tests/unit/` (fast) · `uv run pytest tests/integration/` (needs real DB)
- **Lint:** `uv run ruff check .` · auto-fixed by PostToolUse hook on save

## Architecture Touchpoints

When touching these areas, read the linked file first — they have non-obvious invariants:

| Area | File | Key invariant |
|------|------|---------------|
| LangGraph state | `models/state.py` | `total=False` TypedDict; `assert_state_size` must be called before returning from every node |
| DB connections | `db.py` | Always use `rw_conn()`/`ro_conn()` context managers — never `get_rw_conn()` raw unless in a node that manages its own `finally` |
| HiTL signalling | `runner.py` | `_pending_decisions` is shared between pipeline thread and Flask thread — always acquire `_pending_decisions_lock` |
| Cost kill-switch | `cost.py` | Check `check_cost_kill_switch()` before every Claude call; estimates must include a 1.5× safety margin |
| API responses | `dashboard/routes.py` | All 2xx responses must use `jsonify_ok()`; see API-SPEC.md §1.5 |
| CSP | `dashboard/__init__.py` | No `unsafe-inline` in `script-src` — all JS must be in `static/js/` |

## Patterns That Must Never Appear

These are the root causes of all 26 issues found in the 2026-05 audit. Each one is a hard rule, not a guideline.

### Resource Management
- **NEVER** open a DB connection, file, or network socket without a guaranteed close path.
  - Use `with rw_conn(...) as conn:` or a `try/finally` with `conn.close()` in `finally`.
  - The pattern `conn = get_rw_conn(...); try: ...; except: log; return` is a leak — `conn` never closes on the except path.

### External Call Timeouts
- **EVERY** HTTP request, DB query with a long-running risk, and LLM SDK call must have an explicit timeout.
  - `anthropic.Anthropic(api_key=..., timeout=CLAUDE_TIMEOUT_S)` — not `anthropic.Anthropic(api_key=...)`.
  - `requests.get(url, timeout=15)` — never `requests.get(url)`.
  - Undiscovered hangs block the entire pipeline indefinitely.

### Thread Safety
- **ANY** dict, list, or counter mutated from more than one thread must be protected by a `threading.Lock`.
  - This includes Flask request threads writing to module-level state that pipeline threads read.
  - CPython's GIL does not protect compound operations (`pop` + `put`, `read` + `write`).

### Secrets and Security Config
- **NEVER** provide a non-empty default for security-critical config values.
  - Wrong: `SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-key")`
  - Right: read the value, and if it equals the dev sentinel AND `LOG_LEVEL != DEBUG`, raise `RuntimeError`.
  - The dev fallback must be explicitly opt-in (DEBUG mode only), not the silent default.

### Path Handling
- **NEVER** validate file paths from user input by string-matching on `".."` or `"/"`.
  - Use `Path.resolve()` and assert the resolved path starts within the allowed directory.
  - String checks miss null bytes, URL encoding, and symlinks.

### Error Handling
- **NEVER** use `except: pass` or `except Exception: pass` outside of a top-level handler.
  - Swallowed exceptions hide bugs permanently — the failure disappears, the damage (corrupt state, leaked connection, wrong data) persists.
  - Always log at WARNING or above with structured context before swallowing.
  - Catch specific exception types (`json.JSONDecodeError`, `OSError`, `sqlite3.OperationalError`).

### Content Security Policy
- **NEVER** add `'unsafe-inline'` to `script-src`.
  - Move any inline `<script>` block to an external file in `static/js/`.
  - Data from Jinja templates → HTML `data-*` attributes → read by JS. Never interpolate Jinja into `<script>` blocks.

### API Response Contracts
- **ALWAYS** write or update `API-SPEC.md` before implementing a new route.
  - All 2xx responses use `jsonify_ok(data, **meta)` — never raw `jsonify()`.
  - All error responses use `{"error": {"code": "SNAKE_CASE", "message": "...", "details": []}}`.
  - HTTP status codes must be semantically correct — no 200 with an error body.

### LLM Cost Estimates
- **ALWAYS** use a 1.5× safety multiplier on any estimated (non-exact) token count.
  - The kill-switch fires based on accumulated cost — underestimates let the pipeline overspend.
  - If the SDK returns exact token counts, use them. If estimating, multiply by 1.5.

### Observability
- **EVERY** node must bind `correlation_id=run_id` to structlog context at the top.
- **EVERY** external call logs at entry (what was sent) and at exit (status, duration).
- **NEVER** log PII, API keys, or full job descriptions — truncate to 200 chars for debug context.

## Test Requirements

Every PR must include tests for:

| Change type | Required tests |
|-------------|----------------|
| New route | Happy path + at least one 4xx error path + at least one 5xx error path |
| New node | Normal execution + at least one exception path that verifies `errors` list |
| New DAL function | Happy path + missing-row case + malformed-data case |
| New Claude call | Mock response success + mock exception (verifies error propagates, not swallows) |
| Any shared mutable state | Concurrent-access test or explicit comment explaining why it's safe |

The `autouse` fixture in `tests/conftest.py` sets `LOG_LEVEL=DEBUG` — required for `create_app()` to work without `FLASK_SECRET_KEY`.

## Quality Gate Before Every PR

Run `standards/quality-bar.md` checklist. If any item is unchecked, fix before opening the PR.

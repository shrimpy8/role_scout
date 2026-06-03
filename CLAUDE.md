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

## Security Patterns Learned (2026-06-03)

Derived from RS-01 through RS-06 found and fixed on 2026-06-03.

### SSRF / Server-Side URL Fetching
- **NEVER** call `httpx.Client(follow_redirects=True)` or `requests.get(url)` on user-supplied URLs without pre-flight validation.
- **ALWAYS** pass every URL (including each redirect target) through `validate_fetchable_url()` before the request executes.
- Block loopback (`127.0.0.0/8`, `::1`), link-local (`169.254.0.0/16`, `fe80::/10`), RFC1918 (`10/8`, `172.16/12`, `192.168/16`), multicast (`224.0.0.0/4`), CGN (`100.64.0.0/10`), and cloud-metadata IPs (`169.254.169.254`) — resolve DNS first, then check the resolved IP.
- Implement redirects as a manual loop (max 5 hops); validate each `Location` value through `validate_fetchable_url()` before following. Automatic redirect following cannot validate intermediate targets.
- Unit tests are required for: `localhost`, `127.0.0.1`, `[::1]`, `169.254.169.254`, private RFC1918 addresses, DNS names that resolve to private IPs, and public URLs that redirect to private IPs.

### Cost Kill-Switch Coverage
- **EVERY** Claude call site — in `ingest/extractor.py`, `nodes/scoring.py`, and anywhere else Anthropic is called — must call `check_cost_kill_switch()` immediately before the SDK call.
- After each Claude call returns, add the **exact SDK-reported token cost** via `compute_cost(input_tokens, output_tokens)` to `accumulated_cost`. Never use a stale or estimated figure for subsequent kill-switch checks.
- `_call_claude()` (or equivalent) must return `(text, input_tokens, output_tokens)` so callers can account for cost accurately.
- Thread `accumulated_cost` and `max_cost` into every function that wraps Claude calls (`score_jobs_batch()`, `analyze_urls()`, etc.). Functions that do not receive a budget parameter are a kill-switch bypass by design.
- When the kill switch fires mid-batch, return a graceful per-item `error_msg="cost_kill_switch"` result — do not raise an exception that aborts the whole request.

### System Prompt Data Boundaries
- **NEVER** interpolate user-supplied content (JD text, resume text, company name, job title from external sources) into a system prompt.
- System prompt files (`alignment_system.md`, `resume_tailor_system.md`, and any others) must be **static** — no `{field}` or `$field` substitution of external data at call time.
- Place all variable content in the **user message** under explicit XML tags: `<job_description>`, `<resume_summary>`, `<company>`, `<title>`. The system prompt must explicitly state that content inside those tags is data, not instructions.
- Add regression tests with adversarial JD content (e.g. "ignore previous instructions") asserting that the constructed system prompt string contains none of the JD or resume text.

### Client-Supplied Payload Integrity
- **NEVER** trust scores, IDs, hashes, or content that the browser assembled and POSTed back unchanged.
- **ALWAYS** sign server-computed analysis results with HMAC-SHA256 (`SECRET_KEY`) before returning them to the client (`_sign_job()`), and verify the signature before any schema validation or DB insert (`_verify_job_sig()`).
- Return HTTP 422 with code `TAMPERED_PAYLOAD` on signature mismatch — do not silently discard or proceed.
- Test: tamper with `match_pct`, `company`, or `url` in the confirmed payload and assert 422/403.

### Error Envelope Consistency
- **EVERY** error response — including from ingest routes — must go through `jsonify_error(code, message, status, details=None)`, which injects `g.request_id` into `meta`.
- **NEVER** return a raw `{"error": {"code": ..., "message": ...}}` dict directly from a route handler — it breaks the documented API contract and drops the correlation ID exactly when debugging matters most.
- When adding `jsonify_error()`, immediately grep all sibling routes for raw error returns and convert them before marking the fix done.

### CDN Script Sources in CSP
- **NEVER** add an external domain (e.g. `https://cdn.jsdelivr.net`) to `script-src` in the CSP header.
- Vendor all JS and CSS dependencies locally under `dashboard/static/vendor/` and serve them via `url_for('static', ...)`.
- CDN script allowance means the local app's security depends on an external host's integrity and availability — unacceptable even for a localhost-only deployment.
- Add a smoke test asserting the `Content-Security-Policy` header contains no remote origins in `script-src` when running outside DEBUG mode.

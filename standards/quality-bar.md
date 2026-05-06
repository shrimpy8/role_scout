# Quality Bar — Role Scout

Run this before marking any PR ready for review. Each item maps to a real bug category found in the 2026-05 audit. Unresolved items are blockers.

---

## 1. Resource Management

- [ ] Every DB connection is closed in a `finally` block or via `with rw_conn()/ro_conn()`.
- [ ] No `conn = get_rw_conn(...)` call exists without a matching `finally: conn.close()`.
- [ ] No `conn.close()` call appears in an `except` branch *and* in `finally` (double-close).
- [ ] Every opened file handle is closed (context manager or `try/finally`).

## 2. External Call Timeouts

- [ ] Every `anthropic.Anthropic(...)` call includes `timeout=CLAUDE_TIMEOUT_S`.
- [ ] Every `requests.get/post(...)` call includes an explicit `timeout=` argument.
- [ ] No network or LLM call can block indefinitely.

## 3. Thread Safety

- [ ] Every module-level dict or list mutated from multiple threads is guarded by a `threading.Lock`.
- [ ] No compound read-modify-write on shared state outside a lock.

## 4. Security

- [ ] `FLASK_SECRET_KEY` (and any equivalent secret) raises `RuntimeError` if unset in non-DEBUG env.
- [ ] No `unsafe-inline` in `script-src` CSP directive.
- [ ] No inline `<script>` blocks in templates — all JS is in `static/js/`.
- [ ] All file paths from user input validated with `Path.resolve()` + prefix assertion (not string matching).
- [ ] No secrets, PII, or API keys in logs — even at DEBUG level.

## 5. Error Handling

- [ ] No `except: pass` or `except Exception: pass` outside a top-level handler.
- [ ] Every swallowed exception has a `log.warning(...)` or `log.exception(...)` before it.
- [ ] Every `except` block catches a specific exception type, not bare `Exception` unless justified.
- [ ] Error log calls use `log.exception(...)` (not `log.error(...)`) when re-raising or swallowing so the traceback is captured.

## 6. API Contracts

- [ ] Every new route has a corresponding entry in `API-SPEC.md` (or the spec was updated).
- [ ] All 2xx responses use `jsonify_ok(data, **meta)` — never raw `jsonify({...})`.
- [ ] All error responses use `{"error": {"code": "SNAKE_CASE", "message": "...", "details": []}}`.
- [ ] HTTP status codes are semantically correct (404 = not found, 422 = validation, 409 = conflict, etc.).
- [ ] List endpoints are paginated — no unbounded `SELECT * FROM table`.

## 7. LangGraph Nodes

- [ ] `assert_state_size({**state, **state_update})` is called before every node return.
- [ ] `run_id` is bound to structlog context at the top of every node (`log.bind(correlation_id=run_id, ...)`).
- [ ] Every exception path appends to `errors: list[str]` in the returned state update.
- [ ] Token estimates use a 1.5× safety multiplier when exact counts are unavailable.

## 8. Tests

- [ ] New routes: happy path test + ≥1 error path test (4xx or 5xx).
- [ ] New nodes: normal execution test + exception path test verifying `errors` list populated.
- [ ] New DAL functions: happy path + missing-row case.
- [ ] New Claude calls: mock success + mock exception (verifies error is captured, not swallowed).
- [ ] `uv run pytest tests/unit/` passes with 0 failures.
- [ ] `uv run ruff check .` shows no new errors introduced by this PR.

## 9. Observability

- [ ] Every new log call is structured (`log.info("event_name", key=value)`) — no f-string messages.
- [ ] New external calls log at entry and exit with status/duration.
- [ ] No `print()` statements in production code paths.

## 10. Config

- [ ] Any new value that a non-engineer might want to change lives in `config.py` (pydantic-settings), not hardcoded.
- [ ] Any new `Settings` field has a sensible default AND is documented in `.env.example`.
- [ ] No magic numbers in business logic — use named constants.

---

## How to Use

Before opening a PR, paste this checklist into the PR description and check each item. If an item doesn't apply (e.g., "no new routes added"), mark it `N/A` with a one-line reason.

Items left unchecked without justification are merge blockers.

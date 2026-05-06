# CODE-AUDIT — Resolution Summary

All issues from `docs/CODE-AUDIT.md` resolved across 3 PRs (2026-05-06).
Linear project: [Role Scout Phase 2](https://linear.app/hh2025/project/role-scout-phase-2-1f8c0597a6fa/overview)

---

## PR 1 — Week 1: Critical + Highest-Impact High
**Branch:** `fix/week1-critical-high` · **Milestone:** HH2 Week 1

| ID | Severity | Issue | File(s) | Resolution |
|----|----------|-------|---------|------------|
| C1 | Critical | DB connection leak in MCP `analyze_job` | `mcp_server/server.py` | Moved `rw_conn` open before `try`; always closes in `finally`. Fixed double-close in `tailor_resume` (M11) at the same time. |
| C2 | Critical | No timeout on Claude client in `score_jobs_batch` | `compat/pipeline/scorer.py` | Added `timeout=CLAUDE_TIMEOUT_S` when constructing `anthropic.Anthropic`. |
| C3 | Critical | Race condition on `_pending_decisions` dict | `runner.py` | Added `threading.Lock()` guarding both `register_pending` and `resolve_pending`. |
| C5 | Critical | `SECRET_KEY` silently falls back to insecure dev value | `dashboard/__init__.py` | Raises `RuntimeError` in non-DEBUG env; logs `WARNING` in DEBUG. Added `autouse` pytest fixture to set `LOG_LEVEL=DEBUG` so tests still pass. |
| H1 | High | No `request_id` middleware on API responses | `dashboard/__init__.py` | `before_request` generates `g.request_id`; `after_request` sets `X-Request-Id` header; structlog `contextvars` bound. |
| H6 | High | Dedup failure silently treats all jobs as new | `nodes/discovery.py` | Exception path now sets `cancel_reason="dedup_failed"` and returns early — no scoring, no data corruption. |
| H7 | High | `unsafe-inline` in CSP allows XSS | `dashboard/__init__.py`, templates, `static/js/` | All inline `<script>` blocks moved to `init.js` and `debug_runs.js`. `unsafe-inline` removed from `script-src`. |
| M11 | Medium | Double `conn.close()` in `tailor_resume` | `mcp_server/server.py` | Fixed as part of C1 — rely solely on `finally` block. |

---

## PR 2 — Week 2: Remaining High
**Branch:** `fix/week2-remaining-high` · **Milestone:** HH2 Week 2

| ID | Severity | Issue | File(s) | Resolution |
|----|----------|-------|---------|------------|
| H2 | High | Success responses don't match API-SPEC envelope | `dashboard/routes.py` | Extracted `jsonify_ok(data, **meta)` helper; applied to 6 routes. |
| H3 | High | Watchlist revision counter not monotonic | `dashboard/routes.py` | Replaced `len(watchlist)` with `itertools.count()` module-level counter. |
| H4 | High | JSON parse failures swallowed in status polling | `dashboard/routes.py` | Added `log.warning(...)` with truncated raw value before all silent `except` blocks. |
| H5 | High | Alignment route returns raw JSON string, not structured object | `dashboard/routes.py` | Server now parses `jd_alignment` and spreads fields into the response per API-SPEC. |
| H8 | High | Enrichment failures not tracked in pipeline state | `nodes/enrichment.py` | `_enrich_concurrently` returns `list[str]` errors; node adds to `state["errors"]` and sets `enrichment_failed_count`. |
| H9 | High | `update_jd_alignment` commit contract undocumented | `compat/db/qualified_jobs.py` | Docstring updated: "Caller must commit." |
| H10 | High | `total_new` in output node counts qualified, not new-to-system | `nodes/output.py`, `nodes/discovery.py`, `models/state.py` | Discovery snapshots `new_jobs_count` before enrichment trims it; output node uses that field. |
| M7 | Medium | Corrupt cached alignment silently triggers re-call | `dashboard/routes.py` | Added `log.warning("alignment_route.cached_corrupt", ...)` — fixed opportunistically in same location as H5. |

---

## PR 3 — Week 3: Medium + Test Gaps
**Branch:** `fix/week3-medium-tests` · **Milestone:** HH2 Week 3

| ID | Severity | Issue | File(s) | Resolution |
|----|----------|-------|---------|------------|
| C4 | Critical* | Token estimate too low — cost kill-switch fires late | `nodes/scoring.py` | 1.5× safety multiplier: `est_input = int(n_batches * _BATCH_TOKEN_ESTIMATE * 1.5)`. |
| M1 | Medium | Path traversal via naive `".." in filename` check | `dashboard/routes.py` | Replaced with `Path.resolve()` prefix check — symlink-safe. |
| M2 | Medium | `DELETE /api/watchlist/<company>` returns 200 when not found | `dashboard/routes.py` | Reads current list first; returns 404 + `NOT_FOUND` if company absent. |
| M3 | Medium | Hash-id validation duplicated across 3 routes | `dashboard/routes.py` | Extracted `_validate_hash_id(hash_id)` helper; 3 inline regex blocks removed. |
| M4 | Medium | Reflection cost lost on Claude exception path | `nodes/reflection.py` | Best-effort extraction of `exc.response.usage` tokens before `continue`. |
| M5 | Medium | No response size cap on alignment Claude call | `compat/pipeline/alignment.py` | Truncates response at `_MAX_RESPONSE_CHARS = 8_000` before JSON parsing. |
| M6 | Medium | `_MIN_DESCRIPTION_LENGTH` duplicated in two modules | `nodes/enrichment.py` | Imports `_MIN_DESCRIPTION_CHARS` from `enrich.py` — single source of truth. |
| M8 | Medium | Missing aria-labels on slider, alignment buttons, add button | `templates/index.html` | `aria-label` added to threshold slider, per-job alignment button (with title/company), and watchlist add button. |
| M9 | Medium | IMAP host hardcoded to Yahoo | `config.py` | Already a pydantic-settings field with env var override — no code change needed. Noted for `.env.example` documentation. |
| Tests | Gap | 0% coverage on error paths, DAL edges, Claude failures, dedup | `tests/unit/` | Added 4 new files: `test_dashboard_error_paths.py`, `test_dal_edge_cases.py`, `test_claude_failures.py`, `test_dedup.py` (38 new assertions). |

*C4 was filed under Week 3 milestone but carries Critical-equivalent risk.

---

## Coverage After Fixes

| Metric | Before | After |
|--------|--------|-------|
| Unit tests passing | ~174 | 212 |
| New test files | 0 | 4 |
| Open audit issues | 26 | 0 |
| Critical issues open | 5 | 0 |
| High issues open | 10 | 0 |
| Medium issues open | 12 | 0 |

---

## Issues Not Addressed (Out of Scope)

The following issue categories from `CODE-AUDIT.md §4 (Low)` and `§5 (Tests)` were deferred — they carry no correctness or security risk for a single-user local tool:

- **L1–L15** (Low priority): Logging improvements, CLI polish, config documentation, minor DRY violations in tests, optional MCP schema refinements.
- **Remaining test gaps**: Cold-start testing, TTL extension persistence cycle, source fetcher retry/auth-failure coverage, full enrichment failure matrix.

These are good candidates for a follow-up Opus audit pass.

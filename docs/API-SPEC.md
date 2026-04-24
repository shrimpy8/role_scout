# API-SPEC: Role Scout Phase 2 Flask REST API

| Field | Value |
|-------|-------|
| Parent | [PRD-CORE.md](./PRD-CORE.md) |
| Related | [SPEC.md](./SPEC.md) · [TECH-DESIGN.md](./TECH-DESIGN.md) · [DATA-MODEL.md](./DATA-MODEL.md) |
| Version | 1.0 |
| Owner | [project-owner] |
| Status | Approved |
| Updated | 2026-04-23 |

> Implementation-ready OpenAPI 3.1 contract for the 7 new Flask routes added on top of the Phase 1 dashboard. All routes bind to `127.0.0.1` only. All write routes require CSRF.

---

## 1. Scope & Conventions

### 1.1 Routes covered

| # | Method | Path | Purpose |
|---|--------|------|---------|
| 1 | GET | `/api/pipeline/status` | Poll current run state (for dashboard banner) |
| 2 | POST | `/api/pipeline/resume` | Approve or cancel a paused run |
| 3 | POST | `/api/pipeline/extend` | Extend interrupt TTL by 2h (once per run) |
| 4 | POST | `/api/tailor/{hash_id}` | Tailor resume for a qualified job |
| 5 | POST | `/api/watchlist` | Add a company to watchlist |
| 6 | DELETE | `/api/watchlist/{company}` | Remove a company from watchlist |
| 7 | GET | `/api/runs` | Paginated debug listing of recent runs |

### 1.2 Versioning — no `/v1/` prefix

**Decision.** Paths are `/api/*` without a version prefix.

**Rationale.** This is a single-user local tool bound to `127.0.0.1`. There are no external consumers, no public clients, no long-lived integrations. The only caller is the bundled Flask dashboard UI (same repo, same deploy). Versioning adds ceremony without benefit; breaking changes can be made in-repo with a coordinated dashboard update. If Phase 3 exposes this API beyond localhost, versioning will be added then (and this doc updated to `/api/v1/`).

### 1.3 Auth & CSRF

| Layer | Mechanism |
|-------|-----------|
| Network | Bind to `127.0.0.1` only; external bind attempts raise at startup (SPEC test T37) |
| State-changing requests | `X-CSRFToken` header OR `_csrf_token` form field, validated against Flask session (Flask-WTF) |
| Read-only GET | No CSRF required |

All write routes (POST/DELETE) return **403** with `error.code=CSRF_INVALID` on missing/invalid token.

### 1.4 Error Envelope (uniform across all endpoints)

```json
{
  "error": {
    "code": "SNAKE_CASE_CODE",
    "message": "Human-readable explanation.",
    "details": [
      { "field": "company", "issue": "max 100 chars" }
    ],
    "request_id": "req_a1b2c3d4",
    "correlation_id": "run_e5f6g7h8"
  }
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `code` | string (SNAKE_CASE) | ✓ | Stable error key for client branching |
| `message` | string | ✓ | Human-readable (user-safe; no stack traces) |
| `details` | array\<object> | ✗ | Per-field validation errors |
| `request_id` | string | ✓ | Per-request UUID (`req_` prefix, 16 hex) — auto-generated in `before_request` hook |
| `correlation_id` | string | ✗ | Present when the request relates to a run (`run_` prefix + run UUID) |

**Never return HTTP 200 with an error body.** HTTP status is always semantically correct.

### 1.5 Success Envelope

All 2xx responses use the `{ "data": {...} }` wrapper for consistency with agent consumption (MCP tools re-use these same shapes where applicable):

```json
{ "data": { ... }, "meta": { "request_id": "req_...", "correlation_id": "run_..." } }
```

**Exception:** `GET /api/pipeline/status` returns a raw status object (no `data` wrapper) because it is polled every 5s and the extra envelope is pure overhead; this is the single exception, documented here so consumers know.

### 1.6 Rate Limiting

**Decision.** No Flask-side rate limiter. Localhost, single user, single browser tab.

**Headers emitted (informational only, no enforcement):**
```
X-RateLimit-Limit: 600
X-RateLimit-Remaining: 599
X-RateLimit-Reset: 1745000000
```

The counter resets every 60s. This lets us bolt on real rate limiting later without a header-contract change.

### 1.7 Common Headers

| Header | Requests | Responses | Purpose |
|--------|----------|-----------|---------|
| `Content-Type: application/json` | POST/DELETE w/ body | all | — |
| `X-CSRFToken` | POST/DELETE | — | CSRF check |
| `X-Request-ID` | optional | always | Correlation (server generates if absent) |
| `X-Run-ID` | — | when applicable | Pipeline run correlation |

### 1.8 Idempotency

| Method | Idempotent? | `Idempotency-Key` header |
|--------|-------------|--------------------------|
| GET | ✓ (always safe) | N/A |
| DELETE | ✓ (repeat returns 404) | Not required |
| POST `/api/watchlist` | ✓ by content (adding an existing company is a no-op) | Not required |
| POST `/api/pipeline/resume` | ✗ (state transition) | Not required — single writer lock prevents double-resolve |
| POST `/api/pipeline/extend` | ✗ (but max 1 extension enforced) | Not required |
| POST `/api/tailor/{hash_id}` | ✓ (cache key makes re-calls no-op unless `force=true`) | Not required |

Rationale: localhost + single user + single-writer DB lock makes idempotency-key ceremony unnecessary. Adding it later is a non-breaking additive change.

---

## 2. OpenAPI 3.1 Spec (complete)

```yaml
openapi: "3.1.0"
info:
  title: "Role Scout Phase 2 Dashboard API"
  version: "1.0.0"
  description: |
    Local, single-user REST API for the Phase 2 Flask dashboard.
    Bound to 127.0.0.1. CSRF-protected writes. No external consumers.
  contact:
    name: role-scout
servers:
  - url: http://127.0.0.1:5000
    description: Local dashboard (only valid host)

components:
  securitySchemes:
    csrf:
      type: apiKey
      in: header
      name: X-CSRFToken
      description: Required on all POST/DELETE requests

  parameters:
    HashIdPath:
      name: hash_id
      in: path
      required: true
      schema:
        type: string
        pattern: "^[a-f0-9]{16}$"
      description: 16-char lowercase hex job ID (from Phase 1 dedup hash)
      example: "a1b2c3d4e5f60718"
    CompanyPath:
      name: company
      in: path
      required: true
      schema:
        type: string
        minLength: 1
        maxLength: 100
        pattern: "^[^\\n\\r]+$"
      description: Company name (URL-encoded; newlines forbidden)
      example: "Anthropic"
    RunsLimit:
      name: limit
      in: query
      required: false
      schema:
        type: integer
        minimum: 1
        maximum: 100
        default: 20
    RunsOffset:
      name: offset
      in: query
      required: false
      schema:
        type: integer
        minimum: 0
        default: 0

  schemas:
    ErrorEnvelope:
      type: object
      required: [error]
      properties:
        error:
          type: object
          required: [code, message, request_id]
          properties:
            code:
              type: string
              pattern: "^[A-Z][A-Z0-9_]*$"
            message: { type: string }
            details:
              type: array
              items:
                type: object
                properties:
                  field: { type: string }
                  issue: { type: string }
            request_id:
              type: string
              pattern: "^req_[a-f0-9]{16}$"
            correlation_id:
              type: string
              pattern: "^run_[a-f0-9-]+$"

    Meta:
      type: object
      properties:
        request_id: { type: string, pattern: "^req_[a-f0-9]{16}$" }
        correlation_id: { type: string, pattern: "^run_[a-f0-9-]+$" }

    PipelineStatus:
      type: object
      required: [run_active, watchlist_revision]
      properties:
        run_active: { type: boolean }
        run_id:
          oneOf: [{ type: string }, { type: "null" }]
          description: present when run_active=true
        status:
          type: string
          enum: [running, review_pending, completed, failed, cancelled, cancelled_ttl]
        trigger_type:
          type: string
          enum: [manual, scheduled, mcp, dry_run]
        started_at: { type: string, format: date-time }
        ttl_deadline:
          oneOf: [{ type: string, format: date-time }, { type: "null" }]
          description: present only when status=review_pending
        ttl_extended: { type: boolean }
        fetched_count: { type: integer, minimum: 0 }
        new_count: { type: integer, minimum: 0 }
        qualified_count: { type: integer, minimum: 0 }
        cost_so_far_usd: { type: number, minimum: 0 }
        top_matches:
          type: array
          maxItems: 5
          items:
            type: object
            required: [hash_id, company, title, match_pct]
            properties:
              hash_id: { type: string, pattern: "^[a-f0-9]{16}$" }
              company: { type: string }
              title: { type: string }
              match_pct: { type: integer, minimum: 0, maximum: 100 }
        watchlist_hits:
          type: object
          additionalProperties: { type: integer }
        source_health:
          type: object
          additionalProperties:
            type: object
            properties:
              status: { type: string, enum: [ok, failed, skipped, quota_low] }
              jobs: { type: integer }
              duration_s: { type: number }
              error:
                oneOf: [{ type: string }, { type: "null" }]
        watchlist_revision:
          type: integer
          description: Increments on every watchlist change; dashboard uses to decide whether to refresh ★ badges

    PipelineResumeRequest:
      type: object
      required: [approved]
      properties:
        approved: { type: boolean }
        cancel_reason:
          type: string
          enum: [user_cancel]
          description: Required when approved=false

    PipelineExtendResponse:
      type: object
      required: [ttl_deadline, ttl_extended]
      properties:
        ttl_deadline: { type: string, format: date-time }
        ttl_extended:
          type: boolean
          description: always true in a success response (it just got extended)

    TailorRequest:
      type: object
      properties:
        force:
          type: boolean
          default: false
          description: Bypass cache and regenerate

    TailoredResume:
      type: object
      required: [hash_id, job_title, company, tailored_summary, tailored_bullets, keywords_incorporated, cache_key, prompt_version, tailored_at, cached]
      properties:
        hash_id: { type: string, pattern: "^[a-f0-9]{16}$" }
        job_title: { type: string }
        company: { type: string }
        tailored_summary: { type: string, maxLength: 2000 }
        tailored_bullets:
          type: array
          minItems: 3
          maxItems: 10
          items: { type: string, maxLength: 400 }
        keywords_incorporated:
          type: array
          items: { type: string, maxLength: 80 }
        cache_key: { type: string, pattern: "^[a-f0-9]{16}$" }
        prompt_version: { type: string }
        tailored_at: { type: string, format: date-time }
        cached:
          type: boolean
          description: true if returned from DB cache; false if regenerated this request

    WatchlistAddRequest:
      type: object
      required: [company]
      properties:
        company:
          type: string
          minLength: 1
          maxLength: 100
          pattern: "^[^\\n\\r]+$"

    WatchlistResponse:
      type: object
      required: [watchlist, revision]
      properties:
        watchlist:
          type: array
          items: { type: string }
        revision: { type: integer }

    RunLogEntry:
      type: object
      required: [run_id, status, trigger_type, started_at, input_tokens, output_tokens, estimated_cost_usd]
      properties:
        run_id: { type: string }
        status:
          type: string
          enum: [running, review_pending, completed, failed, cancelled, cancelled_ttl]
        trigger_type: { type: string, enum: [manual, scheduled, mcp, dry_run] }
        started_at: { type: string, format: date-time }
        completed_at:
          oneOf: [{ type: string, format: date-time }, { type: "null" }]
        input_tokens: { type: integer, minimum: 0 }
        output_tokens: { type: integer, minimum: 0 }
        estimated_cost_usd: { type: number, minimum: 0 }
        fetched_count: { type: integer, minimum: 0 }
        qualified_count: { type: integer, minimum: 0 }
        exported_count: { type: integer, minimum: 0 }
        source_health:
          $ref: "#/components/schemas/PipelineStatus/properties/source_health"
        errors:
          type: array
          items: { type: string }
        cancel_reason:
          oneOf: [{ type: string }, { type: "null" }]

    RunsListResponse:
      type: object
      required: [data, pagination]
      properties:
        data:
          type: array
          items: { $ref: "#/components/schemas/RunLogEntry" }
        pagination:
          type: object
          required: [limit, offset, total, has_more]
          properties:
            limit: { type: integer }
            offset: { type: integer }
            total: { type: integer }
            has_more: { type: boolean }

  responses:
    Error400:
      description: Validation error
      content:
        application/json:
          schema: { $ref: "#/components/schemas/ErrorEnvelope" }
    Error403CSRF:
      description: CSRF token missing or invalid
      content:
        application/json:
          schema: { $ref: "#/components/schemas/ErrorEnvelope" }
    Error404:
      description: Not found
      content:
        application/json:
          schema: { $ref: "#/components/schemas/ErrorEnvelope" }
    Error409:
      description: Conflict (e.g., state doesn't permit action)
      content:
        application/json:
          schema: { $ref: "#/components/schemas/ErrorEnvelope" }
    Error422:
      description: Unprocessable entity
      content:
        application/json:
          schema: { $ref: "#/components/schemas/ErrorEnvelope" }
    Error500:
      description: Internal server error
      content:
        application/json:
          schema: { $ref: "#/components/schemas/ErrorEnvelope" }

paths:

  /api/pipeline/status:
    get:
      summary: Poll current pipeline run state
      description: |
        Polled by the dashboard every 5s while the page is open.
        Returns raw status object (not wrapped in {data}) to minimize poll overhead.
      responses:
        "200":
          description: Current status (raw object, no data wrapper)
          content:
            application/json:
              schema: { $ref: "#/components/schemas/PipelineStatus" }
              examples:
                idle:
                  value:
                    run_active: false
                    watchlist_revision: 12
                review_pending:
                  value:
                    run_active: true
                    run_id: "run_a1b2c3d4"
                    status: "review_pending"
                    trigger_type: "manual"
                    started_at: "2026-04-23T14:02:10Z"
                    ttl_deadline: "2026-04-23T18:02:10Z"
                    ttl_extended: false
                    fetched_count: 108
                    new_count: 75
                    qualified_count: 31
                    cost_so_far_usd: 0.87
                    top_matches:
                      - { hash_id: "a1b2c3d4e5f60718", company: "WorkOS", title: "Senior PM", match_pct: 88 }
                      - { hash_id: "b2c3d4e5f607a1b2", company: "Anthropic", title: "Staff PM", match_pct: 84 }
                      - { hash_id: "c3d4e5f607a1b2c3", company: "Stripe", title: "PM, Payments", match_pct: 81 }
                    watchlist_hits: { "Anthropic": 2 }
                    source_health:
                      linkedin: { status: "ok", jobs: 42, duration_s: 18.2, error: null }
                      google:   { status: "ok", jobs: 38, duration_s: 12.7, error: null }
                      trueup:   { status: "ok", jobs: 28, duration_s: 3.1,  error: null }
                    watchlist_revision: 12
        "500": { $ref: "#/components/responses/Error500" }

  /api/pipeline/resume:
    post:
      summary: Approve or cancel a paused run
      security: [{ csrf: [] }]
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/PipelineResumeRequest" }
            examples:
              approve: { value: { approved: true } }
              cancel:  { value: { approved: false, cancel_reason: "user_cancel" } }
      responses:
        "200":
          description: Run resumed or cancelled; graph proceeds
          content:
            application/json:
              schema:
                type: object
                required: [data, meta]
                properties:
                  data:
                    type: object
                    properties:
                      run_id: { type: string }
                      next_status: { type: string, enum: [running, cancelled] }
                  meta: { $ref: "#/components/schemas/Meta" }
        "403": { $ref: "#/components/responses/Error403CSRF" }
        "409":
          description: |
            Conflict. Possible error codes:
            - NO_ACTIVE_RUN — no run in review_pending state
            - ALREADY_RESOLVED — run already approved/cancelled (double-submit)
          content:
            application/json:
              schema: { $ref: "#/components/schemas/ErrorEnvelope" }
        "422": { $ref: "#/components/responses/Error422" }

  /api/pipeline/extend:
    post:
      summary: Extend interrupt TTL by 2 hours (max 1 extension per run)
      security: [{ csrf: [] }]
      responses:
        "200":
          description: TTL extended
          content:
            application/json:
              schema:
                type: object
                required: [data, meta]
                properties:
                  data: { $ref: "#/components/schemas/PipelineExtendResponse" }
                  meta: { $ref: "#/components/schemas/Meta" }
        "403": { $ref: "#/components/responses/Error403CSRF" }
        "409":
          description: |
            Conflict. Possible error codes:
            - NO_ACTIVE_RUN
            - TTL_ALREADY_EXTENDED — ttl_extended already true

  /api/tailor/{hash_id}:
    post:
      summary: Generate (or return cached) tailored resume for a qualified job
      security: [{ csrf: [] }]
      parameters:
        - $ref: "#/components/parameters/HashIdPath"
      requestBody:
        required: false
        content:
          application/json:
            schema: { $ref: "#/components/schemas/TailorRequest" }
            examples:
              default: { value: { force: false } }
              force:   { value: { force: true } }
      responses:
        "200":
          description: Tailored resume (fresh or cached)
          content:
            application/json:
              schema:
                type: object
                required: [data, meta]
                properties:
                  data: { $ref: "#/components/schemas/TailoredResume" }
                  meta: { $ref: "#/components/schemas/Meta" }
        "400":
          description: |
            Possible error codes:
            - NOT_QUALIFIED — hash_id is known but match_pct < threshold
        "403": { $ref: "#/components/responses/Error403CSRF" }
        "404":
          description: |
            Possible error codes:
            - JOB_NOT_FOUND
        "500":
          description: |
            Possible error codes:
            - CLAUDE_API_ERROR — upstream failure; body includes detail string (no stack trace)

  /api/watchlist:
    post:
      summary: Add a company to the watchlist
      description: Idempotent. Adding an existing company returns 200 with the unchanged list.
      security: [{ csrf: [] }]
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/WatchlistAddRequest" }
            example: { company: "Anthropic" }
      responses:
        "200":
          description: Current watchlist (with added company)
          content:
            application/json:
              schema:
                type: object
                required: [data, meta]
                properties:
                  data: { $ref: "#/components/schemas/WatchlistResponse" }
                  meta: { $ref: "#/components/schemas/Meta" }
        "400":
          description: |
            Possible error codes:
            - VALIDATION_ERROR — empty / > 100 chars / contains newline
        "403": { $ref: "#/components/responses/Error403CSRF" }
        "500":
          description: |
            Possible error codes:
            - WATCHLIST_WRITE_ERROR — atomic rename failed

  /api/watchlist/{company}:
    delete:
      summary: Remove a company from the watchlist
      security: [{ csrf: [] }]
      parameters:
        - $ref: "#/components/parameters/CompanyPath"
      responses:
        "200":
          description: Updated watchlist (with company removed)
          content:
            application/json:
              schema:
                type: object
                properties:
                  data: { $ref: "#/components/schemas/WatchlistResponse" }
                  meta: { $ref: "#/components/schemas/Meta" }
        "403": { $ref: "#/components/responses/Error403CSRF" }
        "404":
          description: |
            Possible error codes:
            - COMPANY_NOT_IN_WATCHLIST — idempotent-by-intent, but we return 404 so automation knows
        "500":
          description: |
            Possible error codes:
            - WATCHLIST_WRITE_ERROR

  /api/runs:
    get:
      summary: Paginated debug listing of recent run_log entries (newest first)
      parameters:
        - $ref: "#/components/parameters/RunsLimit"
        - $ref: "#/components/parameters/RunsOffset"
      responses:
        "200":
          description: List of run log entries, newest first
          content:
            application/json:
              schema: { $ref: "#/components/schemas/RunsListResponse" }
              examples:
                default:
                  value:
                    data:
                      - run_id: "run_a1b2c3d4"
                        status: "completed"
                        trigger_type: "scheduled"
                        started_at: "2026-04-23T08:00:00Z"
                        completed_at: "2026-04-23T08:02:47Z"
                        input_tokens: 82000
                        output_tokens: 6400
                        estimated_cost_usd: 0.342
                        fetched_count: 108
                        qualified_count: 31
                        exported_count: 31
                        source_health:
                          linkedin: { status: "ok", jobs: 42, duration_s: 18.2, error: null }
                          google:   { status: "ok", jobs: 38, duration_s: 12.7, error: null }
                          trueup:   { status: "ok", jobs: 28, duration_s: 3.1,  error: null }
                        errors: []
                        cancel_reason: null
                    pagination: { limit: 20, offset: 0, total: 47, has_more: true }
        "422": { $ref: "#/components/responses/Error422" }

security:
  - {}     # Most endpoints require no auth (localhost-only); CSRF is applied per-route
```

**Note on pagination.** `/api/runs` uses `limit/offset` rather than cursor because the data is bounded (≤ 90-day retention, typically < 100 rows) and primarily browsed in reverse-chronological order. Cursor pagination would add complexity without payoff. Swap to cursor only if Phase 3 exposes this to a public client.

---

## 3. Error Code Inventory

All 2xx-supported endpoints may additionally return generic 5xx with `INTERNAL_ERROR`. Per-route specific codes:

| Route | 4xx Codes | 5xx Codes |
|-------|-----------|-----------|
| `GET /api/pipeline/status` | — | `INTERNAL_ERROR` |
| `POST /api/pipeline/resume` | `CSRF_INVALID`, `VALIDATION_ERROR`, `NO_ACTIVE_RUN`, `ALREADY_RESOLVED` | `INTERNAL_ERROR`, `PIPELINE_RESUME_ERROR` |
| `POST /api/pipeline/extend` | `CSRF_INVALID`, `NO_ACTIVE_RUN`, `TTL_ALREADY_EXTENDED` | `INTERNAL_ERROR` |
| `POST /api/tailor/{hash_id}` | `CSRF_INVALID`, `VALIDATION_ERROR`, `NOT_QUALIFIED`, `JOB_NOT_FOUND` | `CLAUDE_API_ERROR`, `INTERNAL_ERROR` |
| `POST /api/watchlist` | `CSRF_INVALID`, `VALIDATION_ERROR` | `WATCHLIST_WRITE_ERROR`, `INTERNAL_ERROR` |
| `DELETE /api/watchlist/{company}` | `CSRF_INVALID`, `COMPANY_NOT_IN_WATCHLIST` | `WATCHLIST_WRITE_ERROR`, `INTERNAL_ERROR` |
| `GET /api/runs` | `VALIDATION_ERROR` (bad limit/offset) | `INTERNAL_ERROR` |

---

## 4. Example Request/Response Transcripts

### 4.1 Approve a paused run

```
POST /api/pipeline/resume HTTP/1.1
Host: 127.0.0.1:5000
Content-Type: application/json
X-CSRFToken: 0f8e9d7c6b5a4321

{ "approved": true }

HTTP/1.1 200 OK
Content-Type: application/json
X-Request-ID: req_a1b2c3d4e5f60718
X-Run-ID: run_e5f6g7h8

{
  "data": { "run_id": "run_e5f6g7h8", "next_status": "running" },
  "meta": { "request_id": "req_a1b2c3d4e5f60718", "correlation_id": "run_e5f6g7h8" }
}
```

### 4.2 Tailor a job (cache hit)

```
POST /api/tailor/a1b2c3d4e5f60718 HTTP/1.1
X-CSRFToken: ...
Content-Type: application/json

{ "force": false }

HTTP/1.1 200 OK

{
  "data": {
    "hash_id": "a1b2c3d4e5f60718",
    "job_title": "Senior Product Manager",
    "company": "WorkOS",
    "tailored_summary": "...",
    "tailored_bullets": ["...", "..."],
    "keywords_incorporated": ["B2B SaaS", "identity"],
    "cache_key": "f0e9d8c7b6a51234",
    "prompt_version": "2026-04-23-v1",
    "tailored_at": "2026-04-23T10:12:00Z",
    "cached": true
  },
  "meta": { "request_id": "req_...", "correlation_id": "run_..." }
}
```

### 4.3 Tailor a job (non-qualified)

```
POST /api/tailor/deadbeefdeadbeef HTTP/1.1
X-CSRFToken: ...

HTTP/1.1 400 Bad Request

{
  "error": {
    "code": "NOT_QUALIFIED",
    "message": "This job's match percentage is below the qualify threshold.",
    "request_id": "req_..."
  }
}
```

### 4.4 Watchlist add (with CSRF missing)

```
POST /api/watchlist HTTP/1.1
Content-Type: application/json

{ "company": "Anthropic" }

HTTP/1.1 403 Forbidden

{
  "error": {
    "code": "CSRF_INVALID",
    "message": "Missing or invalid CSRF token.",
    "request_id": "req_..."
  }
}
```

---

## 5. AI-Agent Consumption Checklist

| Requirement | Decision | Status |
|-------------|----------|--------|
| Machine-readable spec | OpenAPI 3.1 inline above (also dump to `openapi/spec.yaml` at repo root during implementation) | ✓ |
| Uniform error envelope | `error.code`, `error.message`, `error.details[]`, `error.request_id`, `error.correlation_id` | ✓ |
| Uniform success wrapper | `{ data: ..., meta: {...} }` except raw `/api/pipeline/status` (poll-optimized, documented) | ✓ |
| Idempotency | GET/DELETE/tailor/watchlist-add safe to retry; resume/extend protected by single-writer state check | ✓ (see §1.8) |
| Versioning | `/api/*` without `/v1`; rationale documented §1.2 | ✓ |
| Rate-limit headers | Informational `X-RateLimit-*`; no enforcement | ✓ (see §1.6) |
| Retry safety | GET/DELETE/PATCH safe; POST protected per-route | ✓ |
| Specific error codes | Enumerated per-route §3 | ✓ |
| Auth token contract | N/A — localhost binding + CSRF only; documented §1.3 | ✓ |
| Pagination scheme | `limit/offset` on `/api/runs`; cursor not needed (bounded data, documented §2 note) | ✓ |

---

## 6. Implementation Pointers

- Flask blueprint: `role_scout/dashboard_ext/routes.py` — does NOT live under `auto_jobsearch/` (frozen).
- Register blueprint on the existing Flask app in a new module `role_scout/dashboard_ext/__init__.py::register(app)`.
- `before_request`: generate `X-Request-ID` if absent; bind to structlog context.
- `after_request`: attach `X-Request-ID` and `X-Run-ID` (when known) to every response.
- CSRF: Flask-WTF `csrf.init_app(app)`; `@csrf.exempt` on the GET routes only.
- Rate-limit headers: middleware in `role_scout/dashboard_ext/middleware.py` sets informational counters.
- Pydantic request/response models live in `DATA-MODEL.md` and become `role_scout/dashboard_ext/models.py`.

---

## 7. Open Points (resolve during implementation)

| # | Question | Owner | Deadline | Pre-decided answer (from source docs) |
|---|----------|-------|----------|----------------------------------------|
| Q1 | Should `/api/pipeline/status` be SSE instead of polling? | [owner] | Day 8 | No — ADR-ish decision in SPEC §7.6: 5s polling is sufficient |
| Q2 | Should `/api/runs` support filtering by `status` or `trigger_type`? | [owner] | Day 9 | Defer — add query params in a non-breaking additive change if needed |
| Q3 | Should tailor responses include token counts? | [owner] | Day 6 | No for now; they are logged server-side to `run_log`; surface via `/api/runs` if needed |

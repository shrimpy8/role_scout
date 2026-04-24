# DATA-MODEL: Role Scout Phase 2 Data Contract

| Field | Value |
|-------|-------|
| Parent | [PRD-CORE.md](./PRD-CORE.md) |
| Related | [SPEC.md](./SPEC.md) · [TECH-DESIGN.md](./TECH-DESIGN.md) · [API-SPEC.md](./API-SPEC.md) · [MCP-SCHEMAS.md](./MCP-SCHEMAS.md) |
| Version | 1.0 |
| Owner | [project-owner] |
| Status | Approved |
| Updated | 2026-04-23 |

> Full DDL for additive SQLite migrations + every Pydantic domain model Phase 2 introduces. Backend engineer can generate migrations and DAL code directly from this doc.

---

## 1. Design Principles

Locked upstream (ADR-16 et al.) — restated here so this doc is self-contained:

1. **Additive-only schema changes.** `ALTER TABLE ADD COLUMN` with `try/except sqlite3.OperationalError("duplicate column name")`. No drops, no renames, no type changes.
2. **No new tables.** Phase 2 reuses `qualified_jobs`, `run_log`, `seen_hashes`, and `alignment_cache` (from Phase 1). JSON blobs carry new structured data.
3. **Typed JSON blobs.** Every TEXT column storing JSON has a Pydantic model. Write path: `model.model_dump_json()`. Read path: `Model.model_validate_json(text)`.
4. **Pydantic v2 syntax.** `Annotated`, `StringConstraints`, `field_validator`, `model_config = ConfigDict(...)`. No v1 `Config` classes.
5. **ISO 8601 UTC timestamps.** `datetime` with `tzinfo=timezone.utc`. Serialized via `.isoformat()`. Naive datetimes rejected at validation.
6. **hash_id format.** 16 lowercase hex chars: `^[a-f0-9]{16}$`. Enforced everywhere.
7. **No schema changes to Phase 1 tables' existing columns.** Only adds.

---

## 2. Storage Selection

| Store | Decision | Rationale |
|-------|----------|-----------|
| Primary DB | SQLite with WAL | Single-user local; zero-ops; Phase 1 choice; WAL allows concurrent readers while a writer is active |
| Cache | DB columns (`tailored_resume`, existing `alignment_cache`) | No Redis; cache is small and tied to rows |
| File storage | Local filesystem (`output/jds/`) | JDs are small text files; versioned by hash_id |
| Config storage | `config/*.yaml` files (atomic rename on write) | Human-editable; watchlist needs PM-friendly format |
| Client storage | `sessionStorage` for threshold slider UI state | Non-sensitive UI preference; never persisted server-side |
| Checkpointer (LangGraph) | `MemorySaver` in-process | 4h TTL auto-cancels stuck runs; SqliteSaver deferred to Phase 3 (ADR-2) |

---

## 3. Migrations (full DDL)

Applied in `role_scout/migrations.py::run_migrations(conn)`. Called from `init_db()` on every startup — each migration is idempotent.

```python
# role_scout/migrations.py
from __future__ import annotations
import sqlite3
import structlog

log = structlog.get_logger()

# Phase 2 migrations, applied in order. Each is idempotent via ALTER ... ADD COLUMN
# with a try/except on sqlite3.OperationalError("duplicate column name").
PHASE2_MIGRATIONS: list[tuple[str, str]] = [
    ("qualified_jobs_tailored_resume",
     "ALTER TABLE qualified_jobs ADD COLUMN tailored_resume TEXT"),
    ("run_log_input_tokens",
     "ALTER TABLE run_log ADD COLUMN input_tokens INTEGER NOT NULL DEFAULT 0"),
    ("run_log_output_tokens",
     "ALTER TABLE run_log ADD COLUMN output_tokens INTEGER NOT NULL DEFAULT 0"),
    ("run_log_estimated_cost_usd",
     "ALTER TABLE run_log ADD COLUMN estimated_cost_usd REAL NOT NULL DEFAULT 0.0"),
    ("run_log_source_health_json",
     "ALTER TABLE run_log ADD COLUMN source_health_json TEXT"),
    ("run_log_trigger_type",
     "ALTER TABLE run_log ADD COLUMN trigger_type TEXT NOT NULL DEFAULT 'manual'"),
    ("run_log_ttl_deadline",
     "ALTER TABLE run_log ADD COLUMN ttl_deadline TEXT"),
    ("run_log_ttl_extended",
     "ALTER TABLE run_log ADD COLUMN ttl_extended INTEGER NOT NULL DEFAULT 0"),
    ("run_log_cancel_reason",
     "ALTER TABLE run_log ADD COLUMN cancel_reason TEXT"),
    # Index to accelerate GET /api/runs (newest first) and source-health auto-skip lookback
    ("run_log_idx_started_at",
     "CREATE INDEX IF NOT EXISTS idx_run_log_started_at ON run_log(started_at DESC)"),
    # Index to accelerate MCP get_jobs filter by status
    ("qualified_jobs_idx_status",
     "CREATE INDEX IF NOT EXISTS idx_qualified_jobs_status ON qualified_jobs(status)"),
]


def run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    for name, sql in PHASE2_MIGRATIONS:
        try:
            conn.execute(sql)
            conn.commit()
            log.info("migration_applied", name=name)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "duplicate column name" in msg or "already exists" in msg:
                log.debug("migration_skipped_idempotent", name=name)
                continue
            raise
```

### 3.1 Post-migration schema (new columns only)

```sql
-- qualified_jobs  (Phase 1 table; added column shown)
-- Pre-existing columns UNCHANGED.
ALTER TABLE qualified_jobs ADD COLUMN tailored_resume TEXT;  -- JSON of TailoredResumeRecord

-- run_log  (Phase 1 table; added columns shown)
ALTER TABLE run_log ADD COLUMN input_tokens        INTEGER NOT NULL DEFAULT 0;
ALTER TABLE run_log ADD COLUMN output_tokens       INTEGER NOT NULL DEFAULT 0;
ALTER TABLE run_log ADD COLUMN estimated_cost_usd  REAL    NOT NULL DEFAULT 0.0;
ALTER TABLE run_log ADD COLUMN source_health_json  TEXT;                 -- JSON of dict[SourceName, SourceHealthEntry]
ALTER TABLE run_log ADD COLUMN trigger_type        TEXT    NOT NULL DEFAULT 'manual';  -- 'manual'|'scheduled'|'mcp'|'dry_run'
ALTER TABLE run_log ADD COLUMN ttl_deadline        TEXT;                 -- ISO 8601 UTC; NULL unless status ever was review_pending
ALTER TABLE run_log ADD COLUMN ttl_extended        INTEGER NOT NULL DEFAULT 0;  -- 0 or 1 (SQLite bool)
ALTER TABLE run_log ADD COLUMN cancel_reason       TEXT;                 -- NULL unless cancelled

CREATE INDEX IF NOT EXISTS idx_run_log_started_at     ON run_log(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_qualified_jobs_status  ON qualified_jobs(status);
```

### 3.2 Idempotency proof

For each migration statement `S`:

1. **First run (fresh DB):** `S` succeeds, column/index created.
2. **Second run (column already exists):** SQLite raises `OperationalError("duplicate column name: X")`. The `except` catches that specific substring and skips. Net effect: no-op.
3. **Index variants use `IF NOT EXISTS`:** idempotent by construction.
4. **Concurrent init?** Only one process calls `init_db()` at startup; `PRAGMA busy_timeout=5000` absorbs any incidental lock. No cross-process race.

### 3.3 Rollback

**Not supported.** Additive migrations don't require rollback. If a column turns out to be a mistake, leave it in place, stop writing to it, and delete the reader code. No destructive SQL at migration time, ever.

### 3.4 Sequencing

Order in `PHASE2_MIGRATIONS` is irrelevant to correctness (each is independent ALTER or CREATE INDEX) but stable for log readability. **New migrations append to the list** — never insert in the middle.

---

## 4. Pydantic Domain Models (full source)

Organized by layer. Drop into these files:
- `role_scout/models/core.py` — shared value objects
- `role_scout/models/state.py` — LangGraph state schema
- `role_scout/models/records.py` — DB row representations + JSON blob models
- `role_scout/models/api.py` — Flask request/response envelopes
- (`role_scout/mcp_server/schemas.py` already defined in [MCP-SCHEMAS.md](./MCP-SCHEMAS.md) — imports from `core.py` and `records.py`)

### 4.1 `role_scout/models/core.py` — shared types

```python
"""Shared value objects and type aliases used across Phase 2."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# ---- Primitive types ----

HashId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{16}$")]
RunId = Annotated[str, StringConstraints(pattern=r"^run_[a-f0-9\-]+$")]
RequestId = Annotated[str, StringConstraints(pattern=r"^req_[a-f0-9]{16}$")]
CompanyName = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"^[^\n\r]+$")]
JobStatus = Literal["new", "reviewed", "applied", "rejected"]
SourceName = Literal["linkedin", "google", "trueup"]
RunStatus = Literal["running", "review_pending", "completed", "failed", "cancelled", "cancelled_ttl"]
TriggerType = Literal["manual", "scheduled", "mcp", "dry_run"]
RunMode = Literal["linear", "agentic", "shadow"]
CancelReason = Literal["user_cancel", "ttl_expired", "crippled_fetch", "cost_kill_switch"]


class BaseSchema(BaseModel):
    """All Phase 2 models inherit from this. Strict, timezone-aware."""
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def _reject_naive_datetimes(cls, v):
        if isinstance(v, datetime) and v.tzinfo is None:
            raise ValueError("Naive datetimes are not permitted; use UTC (tzinfo=timezone.utc).")
        return v


# ---- Shared value objects (serialized into JSON blobs or API bodies) ----

class SourceHealthEntry(BaseSchema):
    status: Literal["ok", "failed", "skipped", "quota_low"]
    jobs: int = Field(ge=0)
    duration_s: float = Field(ge=0)
    error: str | None = None
    raw_count: int | None = Field(default=None, ge=0)
    after_dedup: int | None = Field(default=None, ge=0)
    query_params: dict[str, str | int] | None = None


class ErrorDetail(BaseSchema):
    code: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$")]
    message: str
    details: list[dict[str, str]] = Field(default_factory=list)
    request_id: RequestId | None = None
    correlation_id: RunId | None = None


class ErrorEnvelope(BaseSchema):
    error: ErrorDetail


class Meta(BaseSchema):
    request_id: RequestId
    correlation_id: RunId | None = None
```

### 4.2 `role_scout/models/state.py` — LangGraph state

```python
"""LangGraph state schema. Passed between nodes; serialized by MemorySaver."""
from __future__ import annotations
from datetime import datetime
from typing import TypedDict

from jobsearch.models import CandidateProfile, NormalizedJob, ScoredJob  # Phase 1 imports
from role_scout.models.core import (
    CancelReason, RunId, RunMode, SourceName, SourceHealthEntry, TriggerType,
)


class JobSearchState(TypedDict, total=False):
    # ---- Immutable, set in preflight ----
    run_id: RunId
    trigger_type: TriggerType
    started_at: datetime
    candidate_profile: CandidateProfile
    watchlist: list[str]
    qualify_threshold: int                             # from .env SCORE_THRESHOLD
    run_mode: RunMode

    # ---- Discovery outputs (TRIMMED after enrichment) ----
    raw_by_source: dict[SourceName, list[dict]]        # trimmed -> {} after enrichment_node
    normalized_jobs: list[NormalizedJob]               # trimmed -> [] after enrichment_node
    new_jobs: list[NormalizedJob]                      # trimmed -> [] after enrichment_node
    source_counts: dict[SourceName, int]
    source_health: dict[SourceName, SourceHealthEntry]

    # ---- Enrichment (TRIMMED after scoring) ----
    enriched_jobs: list[NormalizedJob]                 # trimmed -> [] after scoring_node

    # ---- Scoring + reflection ----
    watchlist_hits: dict[str, int]
    scored_jobs: list[ScoredJob]
    scoring_tokens_in: int
    scoring_tokens_out: int
    reflection_tokens_in: int
    reflection_tokens_out: int
    reflection_applied_count: int

    # ---- Review ----
    human_approved: bool
    cancel_reason: CancelReason | None
    ttl_deadline: datetime                             # started_at + 4h; or +2h if extended
    ttl_extended: bool

    # ---- Output ----
    exported_count: int
    total_cost_usd: float

    # ---- Accumulated ----
    errors: list[str]
```

**State size assertion** in every node (TECH-DESIGN §3.2). Helper:

```python
import json, sys
def assert_state_size(state: JobSearchState, cap_mb: int = 10) -> None:
    size = sys.getsizeof(json.dumps(state, default=str).encode())
    if size > cap_mb * 1024 * 1024:
        raise StateSizeExceeded(f"State size {size} bytes exceeds {cap_mb} MB cap")
```

### 4.3 `role_scout/models/records.py` — DB row representations & JSON blobs

```python
"""Pydantic models mirroring SQLite rows + the JSON blob shapes stored inside TEXT columns."""
from __future__ import annotations
from datetime import datetime
from typing import Annotated
from pydantic import Field, StringConstraints, field_validator

from role_scout.models.core import (
    BaseSchema, CancelReason, HashId, JobStatus, RunId, RunStatus,
    SourceHealthEntry, SourceName, TriggerType,
)


# -------- JSON blobs stored in TEXT columns --------

class TailoredResumeRecord(BaseSchema):
    """Shape stored in qualified_jobs.tailored_resume (TEXT column, JSON-encoded)."""
    hash_id: HashId
    job_title: str
    company: str
    tailored_summary: Annotated[str, StringConstraints(max_length=2000)]
    tailored_bullets: list[Annotated[str, StringConstraints(max_length=400)]] = Field(min_length=3, max_length=10)
    keywords_incorporated: list[Annotated[str, StringConstraints(max_length=80)]]
    cache_key: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{16}$")]
    prompt_version: str
    resume_sha: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]  # full SHA-256 of resume file
    tailored_at: datetime

    @field_validator("tailored_bullets")
    @classmethod
    def _bullets_non_blank(cls, v: list[str]) -> list[str]:
        if any(not b.strip() for b in v):
            raise ValueError("bullets must not be blank")
        return v


class SourceHealthBlob(BaseSchema):
    """Shape stored in run_log.source_health_json."""
    linkedin: SourceHealthEntry | None = None
    google: SourceHealthEntry | None = None
    trueup: SourceHealthEntry | None = None

    def as_dict(self) -> dict[SourceName, SourceHealthEntry]:
        return {k: v for k, v in {
            "linkedin": self.linkedin, "google": self.google, "trueup": self.trueup,
        }.items() if v is not None}


# -------- DB row projections (what DAL returns) --------

class QualifiedJobRow(BaseSchema):
    """qualified_jobs row, typed. `tailored_resume` is the parsed JSON blob or None."""
    hash_id: HashId
    company: str
    title: str
    location: str | None
    source: SourceName
    url: str
    apply_url: str | None
    description: str
    salary_visible: bool
    work_model: str | None
    company_stage: str | None
    match_pct: int = Field(ge=0, le=100)
    subscores: dict[str, int]
    reflection_applied: bool
    status: JobStatus
    watchlist: bool
    discovered_at: datetime
    jd_filename: str | None
    tailored_resume: TailoredResumeRecord | None = None


class RunLogRow(BaseSchema):
    """run_log row, typed. `source_health` is the parsed JSON blob."""
    run_id: RunId
    status: RunStatus
    trigger_type: TriggerType
    started_at: datetime
    completed_at: datetime | None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    fetched_count: int = Field(ge=0)
    qualified_count: int = Field(ge=0)
    exported_count: int = Field(ge=0)
    source_health: dict[SourceName, SourceHealthEntry] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    cancel_reason: CancelReason | None = None
    ttl_deadline: datetime | None = None
    ttl_extended: bool = False
```

### 4.4 `role_scout/models/api.py` — Flask request/response models

```python
"""Pydantic models for Flask API request bodies and response envelopes."""
from __future__ import annotations
from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import Field

from role_scout.models.core import (
    BaseSchema, CompanyName, HashId, Meta, RunId, RunStatus, SourceHealthEntry,
    SourceName, TriggerType,
)
from role_scout.models.records import RunLogRow, TailoredResumeRecord

T = TypeVar("T")


class DataEnvelope(BaseSchema, Generic[T]):
    """Uniform 2xx envelope for all endpoints except /api/pipeline/status."""
    data: T
    meta: Meta


# ---- /api/pipeline/status ----

class TopMatch(BaseSchema):
    hash_id: HashId
    company: str
    title: str
    match_pct: int = Field(ge=0, le=100)


class PipelineStatusResponse(BaseSchema):
    """Raw response (no `data` wrapper) — optimized for 5s polling."""
    run_active: bool
    run_id: RunId | None = None
    status: RunStatus | None = None
    trigger_type: TriggerType | None = None
    started_at: datetime | None = None
    ttl_deadline: datetime | None = None
    ttl_extended: bool = False
    fetched_count: int | None = Field(default=None, ge=0)
    new_count: int | None = Field(default=None, ge=0)
    qualified_count: int | None = Field(default=None, ge=0)
    cost_so_far_usd: float | None = Field(default=None, ge=0)
    top_matches: list[TopMatch] = Field(default_factory=list, max_length=5)
    watchlist_hits: dict[str, int] = Field(default_factory=dict)
    source_health: dict[SourceName, SourceHealthEntry] = Field(default_factory=dict)
    watchlist_revision: int = Field(ge=0)


# ---- /api/pipeline/resume ----

class PipelineResumeRequest(BaseSchema):
    approved: bool
    cancel_reason: Literal["user_cancel"] | None = None

    def model_post_init(self, __ctx) -> None:
        if not self.approved and self.cancel_reason is None:
            raise ValueError("cancel_reason required when approved=false")


class PipelineResumeData(BaseSchema):
    run_id: RunId
    next_status: Literal["running", "cancelled"]


# ---- /api/pipeline/extend ----

class PipelineExtendData(BaseSchema):
    ttl_deadline: datetime
    ttl_extended: Literal[True]


# ---- /api/tailor/{hash_id} ----

class TailorRequest(BaseSchema):
    force: bool = False


TailorData = TailoredResumeRecord  # same shape; cached field added in API layer, not persisted
# Note: the API-layer response adds `cached: bool` at runtime; see docs/API-SPEC.md schema


class TailorResponseBody(BaseSchema):
    """What the Flask endpoint actually returns (adds `cached` flag)."""
    hash_id: HashId
    job_title: str
    company: str
    tailored_summary: str
    tailored_bullets: list[str]
    keywords_incorporated: list[str]
    cache_key: str
    prompt_version: str
    tailored_at: datetime
    cached: bool


# ---- /api/watchlist ----

class WatchlistAddRequest(BaseSchema):
    company: CompanyName


class WatchlistResponseBody(BaseSchema):
    watchlist: list[str]
    revision: int = Field(ge=0)


# ---- /api/runs ----

class RunsPagination(BaseSchema):
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    has_more: bool


class RunsListResponse(BaseSchema):
    data: list[RunLogRow]
    pagination: RunsPagination
```

---

## 5. Field-Level Validation Rules (cross-reference)

| Field | Rule | Enforced at |
|-------|------|-------------|
| `hash_id` | `^[a-f0-9]{16}$` | `core.HashId`; every input model + URL path param |
| `run_id` | `^run_[a-f0-9\-]+$` | `core.RunId` |
| `request_id` | `^req_[a-f0-9]{16}$` | `core.RequestId`; generated in Flask `before_request` |
| `company` | 1–100 chars, no newline | `core.CompanyName`; path param + request body |
| `match_pct`, `subscore values` | 0–100 int | `QualifiedJobRow.match_pct`, `PipelineStatusResponse.top_matches[].match_pct` |
| `threshold` (client-side slider) | 75–95 int (clamped) | Dashboard JS; server never receives |
| `status` (job) | `new\|reviewed\|applied\|rejected` | `JobStatus` |
| `status` (run) | 6-value enum | `RunStatus` |
| `trigger_type` | 4-value enum | `TriggerType` |
| `source` | `linkedin\|google\|trueup` | `SourceName` |
| `tokens` | `ge=0` int | `RunLogRow` |
| `estimated_cost_usd` | `ge=0` float | `RunLogRow`, `PipelineStatusResponse.cost_so_far_usd` |
| `tailored_bullets` | 3–10 items, each ≤ 400 chars, non-blank | `TailoredResumeRecord` |
| `tailored_summary` | ≤ 2000 chars | `TailoredResumeRecord` |
| `keywords_incorporated[]` | Each ≤ 80 chars | `TailoredResumeRecord` |
| `resume_sha` | 64-char hex (SHA-256) | `TailoredResumeRecord` |
| `cache_key` | 16-char hex (truncated SHA-256) | `TailoredResumeRecord` |
| `datetime` fields | `tzinfo` required (UTC) | `BaseSchema._reject_naive_datetimes` |
| `cancel_reason` | Present iff not approved (for resume); 4-value enum (for run_log) | `PipelineResumeRequest.model_post_init`; `CancelReason` |
| `ttl_deadline` | Max `started_at + 4h`, or `+6h` if extended once | Enforced in graph logic (not schema) |

---

## 6. Relationships

```
candidate_profile  ──▶  used by every run (read-only from config/candidate_profile.yaml)
watchlist          ──▶  used by every run; persisted in config/watchlist.yaml
                       revision tracked in-memory + emitted in PipelineStatusResponse

run_log (1) ──────────────┐
                          │ correlation_id == run_log.run_id
qualified_jobs (N) ───────┘
  ├── one row per unique hash_id (post-dedup)
  ├── hash_id also appears in seen_hashes (60-day TTL)
  └── tailored_resume (0..1 JSON blob) attached per row

alignment_cache (1..*) ────── linked by hash_id to qualified_jobs
  (existing Phase 1 table — unchanged)

seen_hashes (N) ─── dedup ledger; one row per hash with first_seen timestamp
  (existing Phase 1 table — unchanged; expired via expire_old_hashes after 60d)
```

**No foreign key constraints** (Phase 1 convention — SQLite single-writer is trusted). Integrity maintained by DAL-layer transactional writes in `output_node`.

---

## 7. State ↔ DB Mapping

| State (`JobSearchState`) field | DB column written | When |
|--------------------------------|-------------------|------|
| `run_id` | `run_log.run_id` | `preflight_node` insert |
| `trigger_type` | `run_log.trigger_type` | `preflight_node` insert |
| `started_at` | `run_log.started_at` | `preflight_node` insert |
| `source_counts`, `source_health` | `run_log.source_health_json` | `output_node` update |
| `scored_jobs` (filtered ≥ threshold) | N rows in `qualified_jobs` | `output_node` insert |
| `scoring_tokens_in + reflection_tokens_in` | `run_log.input_tokens` | `output_node` update |
| `scoring_tokens_out + reflection_tokens_out` | `run_log.output_tokens` | `output_node` update |
| `total_cost_usd` | `run_log.estimated_cost_usd` | `output_node` update |
| `exported_count` | `run_log.exported_count` | `output_node` update |
| `human_approved`, `cancel_reason` | `run_log.status`, `run_log.cancel_reason` | `review_node` / TTL watcher |
| `ttl_deadline`, `ttl_extended` | `run_log.ttl_deadline`, `run_log.ttl_extended` | `review_node` / `extend_ttl` |
| `errors` | `run_log.errors` (existing column) | `output_node` update |

State keys not persisted: `raw_by_source`, `normalized_jobs`, `enriched_jobs`, `new_jobs` (all trimmed; only exist mid-run).

---

## 8. Cache Keys

### 8.1 `tailored_resume`

```
cache_key = sha256(resume_sha || "|" || prompt_version || "|" || hash_id)[:16]
```

- `resume_sha` = full SHA-256 hex of `config/resume_summary.md` bytes
- `prompt_version` = first-line HTML comment in `prompts/resume_tailor_system.md` (e.g., `2026-04-23-v1`)
- Cache hit if: row exists AND `cache_key` matches recomputed value
- Cache miss triggers fresh Claude call + overwrite of row's `tailored_resume` JSON

Full `resume_sha` (not truncated) is stored inside the JSON blob so cache validity can be rechecked without reading the file each time (fast path: compare stored sha to file sha; slow path: recompute cache_key).

### 8.2 `alignment_cache`

Phase 1 table, unchanged — uses `(hash_id, resume_hash)` key. Reuses Phase 1 DAL.

---

## 9. Removed / Rejected Patterns

| Pattern | Why rejected |
|---------|--------------|
| UUIDv4 PKs on Phase 1 tables | Phase 1 uses `hash_id` as natural key; changing = breaking |
| `updated_at` columns | Phase 1 never added them; Phase 2 doesn't need them for new columns (all either immutable after insert or written in one transaction) |
| Soft deletes | No deletion in Phase 2; `seen_hashes` cleanup handles expiry |
| `snake_case` rename of any Phase 1 column | Phase 1 already snake_case; no renames anyway |
| Full ORM (SQLAlchemy, Tortoise) | Phase 1 uses raw `sqlite3`; matching that keeps the codebase coherent |
| Alembic migrations | Overkill for additive-only changes; the in-code `PHASE2_MIGRATIONS` list is simpler and idempotent |
| Separate `tailored_resumes` table | 1:1 with `qualified_jobs`; JSON column is simpler; no need to query across tailored rows |

---

## 10. AI-Agent Consumption Checklist

| Requirement | Status |
|-------------|--------|
| All entities have explicit field types | ✓ |
| No nullable FK (no FKs at all — documented) | ✓ |
| snake_case throughout | ✓ |
| UUIDs / natural keys documented | ✓ (Phase 1 `hash_id`, Phase 2 `run_id` prefixed) |
| Storage selection has rationale | ✓ §2 |
| Schema sketch precise enough to write migrations | ✓ §3 |
| Indexing strategy declared | ✓ §3.1 (2 new indexes; rationale in-line) |
| Idempotency proof for migrations | ✓ §3.2 |
| JSON blobs have Pydantic models | ✓ `TailoredResumeRecord`, `SourceHealthBlob` |
| Timestamps are timezone-aware | ✓ enforced by `BaseSchema._reject_naive_datetimes` |
| Validation rules documented cross-ref | ✓ §5 |
| State→DB mapping documented | ✓ §7 |
| Cache key semantics documented | ✓ §8 |

---

## 11. Approval

| Field | Value |
|-------|-------|
| Status | APPROVED (by upstream ADRs in TECH-DESIGN §6) |
| Approved via | PRD-CORE / SPEC / TECH-DESIGN lock-in 2026-04-23 |
| Next | `dev-workflows: prd-to-tasks` → generate `DEVELOPMENT_TODOS.md` |

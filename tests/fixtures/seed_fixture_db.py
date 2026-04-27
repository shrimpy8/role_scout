"""Fixture DB creation and seeding for tests.

Creates an in-memory (or file) SQLite DB with Phase 1 schema + Phase 2 migrations,
then seeds it with deterministic test data covering all status, source, and feature
combinations needed by the test suite.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from jobsearch.db.connection import init_db as _p1_init_db
from jobsearch.db.qualified_jobs import insert_qualified_job
from jobsearch.db.run_log import insert_run_log
from jobsearch.db.seen_hashes import upsert_seen_hash
from jobsearch.models import RunLog, ScoredJob

from role_scout.migrations import run_migrations

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

# 10 deterministic hash_ids: "0000000000000001" .. "000000000000000a"
_HASH_IDS: list[str] = [f"{i:0>16x}" for i in range(1, 11)]

# (title, company, location, city, country, work_model, source,
#  match_pct, status, salary_visible, comp_range, is_watchlist, tailored_resume)
_JOB_ROWS: list[tuple] = [
    # --- 4 × new ---
    (
        "Staff ML Engineer", "Acme AI", "San Francisco, CA", "San Francisco", "US",
        "hybrid", "linkedin", 95, "new", True, "$200K–$240K", False, None,
    ),
    (
        "Senior Software Engineer", "Beta Corp", "Remote", "Remote", "US",
        "remote", "google_jobs", 90, "new", False, None, False, None,
    ),
    (
        "Principal Engineer", "Gamma Inc", "New York, NY", "New York", "US",
        "onsite", "trueup", 85, "new", False, None, True,
        json.dumps({"tailored_summary": "test", "bullets": ["a", "b", "c"]}),
    ),
    (
        "Engineering Manager", "Delta Tech", "Austin, TX", "Austin", "US",
        "hybrid", "linkedin", 82, "new", False, None, False, None,
    ),
    # --- 2 × reviewed ---
    (
        "Data Engineer", "Epsilon Labs", "Seattle, WA", "Seattle", "US",
        "remote", "google_jobs", 78, "reviewed", False, None, False, None,
    ),
    (
        "Platform Engineer", "Zeta Systems", "Boston, MA", "Boston", "US",
        "onsite", "trueup", 75, "reviewed", False, None, False, None,
    ),
    # --- 2 × applied ---
    (
        "Software Architect", "Eta Ventures", "Denver, CO", "Denver", "US",
        "hybrid", "linkedin", 70, "applied", False, None, False, None,
    ),
    (
        "Backend Engineer", "Theta Cloud", "Chicago, IL", "Chicago", "US",
        "remote", "google_jobs", 65, "applied", False, None, False, None,
    ),
    # --- 2 × rejected ---
    (
        "DevOps Engineer", "Iota Networks", "Miami, FL", "Miami", "US",
        "onsite", "trueup", 60, "rejected", False, None, False, None,
    ),
    (
        "Embedded Systems Engineer", "Kappa Robotics", "Portland, OR", "Portland", "US",
        "hybrid", "linkedin", 55, "rejected", False, None, False, None,
    ),
]

_RUN_ROWS: list[tuple[str, str, str]] = [
    # (run_id, status, trigger_type)
    ("run_aabbccdd-0001", "completed", "manual"),
    ("run_aabbccdd-0002", "failed", "scheduled"),
    ("run_aabbccdd-0003", "running", "manual"),
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_scored_job(
    hash_id: str,
    title: str,
    company: str,
    location: str,
    city: str,
    country: str,
    work_model: str,
    source: str,
    match_pct: int,
    status: str,
    salary_visible: bool,
    comp_range: str | None,
    is_watchlist: bool,
) -> ScoredJob:
    """Construct a minimal but valid ScoredJob for fixture seeding."""
    # Phase 1 ScoredJob uses non-UTC datetimes (no tzinfo requirement)
    scored_at = datetime(2025, 6, 1, 10, 0, 0)
    return ScoredJob(
        hash_id=hash_id,
        title=title,
        company=company,
        location=location,
        city=city,
        country=country,
        work_model=work_model,
        url=f"https://jobs.example.com/{hash_id}",
        source=source,  # type: ignore[arg-type]
        match_pct=match_pct,
        reasoning="Fixture seed reasoning — not used in assertions.",
        status=status,
        salary_visible=salary_visible,
        comp_range=comp_range,
        is_watchlist=is_watchlist,
        key_requirements=["Python", "distributed systems"],
        red_flags=[],
        domain_tags=["ml", "infra"],
        scored_at=scored_at,
    )


# Full Phase 2 schema: P1 tables + P2 columns and expanded constraints in one shot.
# Used by both _init_in_memory and _init_file_db so test DBs are always created
# with the correct schema without needing a table rebuild migration.
_FULL_SCHEMA = """
    PRAGMA journal_mode=WAL;
    PRAGMA foreign_keys=ON;

    CREATE TABLE IF NOT EXISTS seen_hashes (
        hash_id       TEXT PRIMARY KEY,
        source        TEXT NOT NULL DEFAULT '',
        title         TEXT NOT NULL DEFAULT '',
        company       TEXT NOT NULL DEFAULT '',
        first_seen_at TEXT NOT NULL,
        last_seen_at  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS qualified_jobs (
        hash_id             TEXT PRIMARY KEY,
        title               TEXT NOT NULL,
        company             TEXT NOT NULL,
        location            TEXT NOT NULL,
        city                TEXT NOT NULL DEFAULT '',
        country             TEXT NOT NULL DEFAULT '',
        work_model          TEXT NOT NULL DEFAULT 'unknown'
                            CHECK(work_model IN ('remote','hybrid','onsite','unknown')),
        url                 TEXT NOT NULL,
        apply_url           TEXT,
        source              TEXT NOT NULL
                            CHECK(source IN ('linkedin','google_jobs','wellfound','trueup')),
        posted_date         TEXT,
        comp_range          TEXT,
        salary_visible      INTEGER NOT NULL DEFAULT 0 CHECK(salary_visible IN (0,1)),
        company_stage       TEXT,
        is_watchlist        INTEGER NOT NULL DEFAULT 0 CHECK(is_watchlist IN (0,1)),
        match_pct           INTEGER NOT NULL CHECK(match_pct BETWEEN 0 AND 100),
        seniority_score     INTEGER CHECK(seniority_score BETWEEN 0 AND 30),
        domain_score        INTEGER CHECK(domain_score BETWEEN 0 AND 25),
        location_score      INTEGER CHECK(location_score BETWEEN 0 AND 20),
        stage_score         INTEGER CHECK(stage_score BETWEEN 0 AND 15),
        comp_score          INTEGER CHECK(comp_score BETWEEN 0 AND 10),
        reasoning           TEXT NOT NULL,
        key_requirements    TEXT NOT NULL DEFAULT '[]',
        red_flags           TEXT NOT NULL DEFAULT '[]',
        domain_alignment    TEXT,
        seniority_match     TEXT,
        location_fit        TEXT,
        company_stage_fit   TEXT,
        description         TEXT,
        description_snippet TEXT,
        company_size        TEXT,
        domain_tags         TEXT NOT NULL DEFAULT '[]',
        jd_alignment        TEXT,
        status              TEXT NOT NULL DEFAULT 'new'
                            CHECK(status IN ('new','reviewed','applied','rejected')),
        jd_filename         TEXT,
        jd_downloaded       INTEGER NOT NULL DEFAULT 0 CHECK(jd_downloaded IN (0,1)),
        scored_at           TEXT NOT NULL,
        fetched_at          TEXT,
        run_id              TEXT,
        tailored_resume     TEXT
    );

    CREATE TABLE IF NOT EXISTS run_log (
        run_id             TEXT PRIMARY KEY,
        started_at         TEXT NOT NULL,
        completed_at       TEXT,
        status             TEXT NOT NULL DEFAULT 'running'
                           CHECK(status IN (
                               'running','completed','failed',
                               'review_pending','cancelled','cancelled_ttl'
                           )),
        trigger_type       TEXT NOT NULL DEFAULT 'manual'
                           CHECK(trigger_type IN ('scheduled','manual','dry_run')),
        source_linkedin    INTEGER NOT NULL DEFAULT 0,
        source_google_jobs INTEGER NOT NULL DEFAULT 0,
        source_wellfound   INTEGER NOT NULL DEFAULT 0,
        source_trueup      INTEGER NOT NULL DEFAULT 0,
        total_fetched      INTEGER NOT NULL DEFAULT 0,
        total_new          INTEGER NOT NULL DEFAULT 0,
        total_qualified    INTEGER NOT NULL DEFAULT 0,
        watchlist_hits     TEXT NOT NULL DEFAULT '{}',
        errors             TEXT NOT NULL DEFAULT '[]',
        input_tokens       INTEGER NOT NULL DEFAULT 0,
        output_tokens      INTEGER NOT NULL DEFAULT 0,
        estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
        source_health_json TEXT,
        ttl_deadline       TEXT,
        ttl_extended       INTEGER NOT NULL DEFAULT 0,
        cancel_reason      TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_qualified_jobs_status
        ON qualified_jobs(status);
    CREATE INDEX IF NOT EXISTS idx_qualified_jobs_run_id
        ON qualified_jobs(run_id);
    CREATE INDEX IF NOT EXISTS idx_qualified_jobs_match_pct
        ON qualified_jobs(match_pct DESC);
    CREATE INDEX IF NOT EXISTS idx_qualified_jobs_company
        ON qualified_jobs(company);
    CREATE INDEX IF NOT EXISTS idx_qualified_jobs_scored_at
        ON qualified_jobs(scored_at DESC);
    CREATE INDEX IF NOT EXISTS idx_seen_hashes_last_seen
        ON seen_hashes(last_seen_at);
    CREATE INDEX IF NOT EXISTS idx_run_log_started_at
        ON run_log(started_at DESC);
"""


def _init_in_memory() -> sqlite3.Connection:
    """Bootstrap schema + migrations directly on an in-memory connection."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_FULL_SCHEMA)
    return conn


def _init_file_db(path: str) -> sqlite3.Connection:
    """Bootstrap a file-based DB with the full Phase 2 schema, then return an open rw connection."""
    from pathlib import Path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_FULL_SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def _seed(conn: sqlite3.Connection) -> None:
    """Insert all fixture rows into the open connection."""
    assert len(_HASH_IDS) == len(_JOB_ROWS), "hash_id / job row count mismatch"

    for hash_id, row in zip(_HASH_IDS, _JOB_ROWS, strict=True):
        (
            title, company, location, city, country, work_model, source,
            match_pct, status, salary_visible, comp_range, is_watchlist, tailored_resume,
        ) = row

        job = _build_scored_job(
            hash_id=hash_id,
            title=title,
            company=company,
            location=location,
            city=city,
            country=country,
            work_model=work_model,
            source=source,
            match_pct=match_pct,
            status=status,
            salary_visible=salary_visible,
            comp_range=comp_range,
            is_watchlist=is_watchlist,
        )
        insert_qualified_job(conn, job)

        # Back-fill tailored_resume after insert (Phase 2 column)
        if tailored_resume is not None:
            conn.execute(
                "UPDATE qualified_jobs SET tailored_resume = ? WHERE hash_id = ?",
                (tailored_resume, hash_id),
            )

    conn.commit()

    # Seed seen_hashes with the same 10 hash_ids
    for hash_id, row in zip(_HASH_IDS, _JOB_ROWS, strict=True):
        title = row[0]
        company = row[1]
        source = row[6]
        upsert_seen_hash(conn, hash_id, source=source, title=title, company=company)
    conn.commit()

    # Seed run_log
    started_times = [
        datetime(2025, 5, 28, 9, 0, 0),
        datetime(2025, 5, 29, 10, 0, 0),
        datetime(2025, 5, 30, 11, 0, 0),
    ]
    completed_times: list[datetime | None] = [
        datetime(2025, 5, 28, 9, 45, 0),
        datetime(2025, 5, 29, 10, 12, 0),
        None,
    ]

    for (run_id, status, trigger_type), started_at, completed_at in zip(
        _RUN_ROWS, started_times, completed_times, strict=True
    ):
        run = RunLog(
            run_id=run_id,
            status=status,
            trigger_type=trigger_type,
            started_at=started_at,
            completed_at=completed_at,
        )
        insert_run_log(conn, run)
    conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_fixture_db(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Create and seed a test SQLite DB. Returns open connection.

    Args:
        path: File path or ``":memory:"`` for an in-memory DB (default).

    Returns:
        An open ``sqlite3.Connection`` with all fixture rows inserted.
        Caller is responsible for closing it.
    """
    path_str = str(path)
    if path_str == ":memory:":
        conn = _init_in_memory()
    else:
        conn = _init_file_db(path_str)

    _seed(conn)
    return conn


def fixture_db() -> sqlite3.Connection:
    """Pytest fixture: in-memory seeded DB.

    Intended for use in conftest.py::

        @pytest.fixture
        def db():
            return fixture_db()
    """
    return create_fixture_db(":memory:")

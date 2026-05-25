"""DB connection helpers: init_db, get_db, new_run_id."""

import sqlite3
import uuid
from pathlib import Path

from role_scout.compat.logging import get_logger

logger = get_logger(__name__)

SEEN_HASH_TTL_DAYS = 60


def init_db(db_path: str = "output/jobsearch.db") -> None:
    """Create tables and indexes if they don't exist. Safe to call repeatedly (idempotent)."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript("""
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
                hash_id          TEXT PRIMARY KEY,
                title            TEXT NOT NULL,
                company          TEXT NOT NULL,
                location         TEXT NOT NULL,
                city             TEXT NOT NULL DEFAULT '',
                country          TEXT NOT NULL DEFAULT '',
                work_model       TEXT NOT NULL DEFAULT 'unknown'
                                 CHECK(work_model IN ('remote','hybrid','onsite','unknown')),
                url              TEXT NOT NULL,
                apply_url        TEXT,
                source           TEXT NOT NULL
                                 CHECK(source IN ('linkedin','google_jobs','trueup')),
                posted_date      TEXT,
                comp_range       TEXT,
                salary_visible   INTEGER NOT NULL DEFAULT 0 CHECK(salary_visible IN (0,1)),
                company_stage    TEXT,
                is_watchlist     INTEGER NOT NULL DEFAULT 0 CHECK(is_watchlist IN (0,1)),
                match_pct        INTEGER NOT NULL CHECK(match_pct BETWEEN 0 AND 100),
                seniority_score  INTEGER CHECK(seniority_score BETWEEN 0 AND 30),
                domain_score     INTEGER CHECK(domain_score BETWEEN 0 AND 25),
                location_score   INTEGER CHECK(location_score BETWEEN 0 AND 20),
                stage_score      INTEGER CHECK(stage_score BETWEEN 0 AND 15),
                comp_score       INTEGER CHECK(comp_score BETWEEN 0 AND 10),
                reasoning        TEXT NOT NULL,
                key_requirements TEXT NOT NULL DEFAULT '[]',
                red_flags        TEXT NOT NULL DEFAULT '[]',
                domain_alignment TEXT,
                seniority_match  TEXT,
                location_fit     TEXT,
                company_stage_fit TEXT,
                description      TEXT,
                description_snippet TEXT,
                company_size     TEXT,
                domain_tags      TEXT NOT NULL DEFAULT '[]',
                jd_alignment     TEXT,
                status           TEXT NOT NULL DEFAULT 'new'
                                 CHECK(status IN ('new','reviewed','applied','rejected','not_a_fit','not_available')),
                jd_filename      TEXT,
                jd_downloaded    INTEGER NOT NULL DEFAULT 0 CHECK(jd_downloaded IN (0,1)),
                scored_at        TEXT NOT NULL,
                fetched_at       TEXT,
                run_id           TEXT
            );

            CREATE TABLE IF NOT EXISTS run_log (
                run_id           TEXT PRIMARY KEY,
                started_at       TEXT NOT NULL,
                completed_at     TEXT,
                status           TEXT NOT NULL DEFAULT 'running'
                                 CHECK(status IN ('running','completed','failed')),
                trigger_type     TEXT NOT NULL DEFAULT 'manual'
                                 CHECK(trigger_type IN ('scheduled','manual','dry_run')),
                source_linkedin  INTEGER NOT NULL DEFAULT 0,
                source_google_jobs INTEGER NOT NULL DEFAULT 0,
                source_wellfound INTEGER NOT NULL DEFAULT 0,
                source_trueup    INTEGER NOT NULL DEFAULT 0,
                total_fetched    INTEGER NOT NULL DEFAULT 0,
                total_new        INTEGER NOT NULL DEFAULT 0,
                total_qualified  INTEGER NOT NULL DEFAULT 0,
                watchlist_hits   TEXT NOT NULL DEFAULT '{}',
                errors           TEXT NOT NULL DEFAULT '[]'
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
        """)
        conn.commit()
        # Additive migrations for columns added after initial schema creation
        for migration in [
            "ALTER TABLE qualified_jobs ADD COLUMN country TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE qualified_jobs ADD COLUMN jd_alignment TEXT",
            "ALTER TABLE qualified_jobs ADD COLUMN apply_url TEXT",
        ]:
            try:
                conn.execute(migration)
                conn.commit()
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "duplicate column name" not in msg and "already exists" not in msg:
                    raise  # genuine schema error — don't swallow

        # Rebuild qualified_jobs if the status CHECK constraint is missing the new statuses.
        # SQLite cannot ALTER a CHECK constraint — table reconstruction is required.
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='qualified_jobs'"
        ).fetchone()
        if row and "not_a_fit" not in row[0]:
            logger.info("db_migration_start", reason="status_check_constraint_update")
            conn.executescript("""
                PRAGMA foreign_keys=OFF;

                CREATE TABLE qualified_jobs_migration_backup AS
                    SELECT * FROM qualified_jobs;

                DROP TABLE qualified_jobs;

                CREATE TABLE qualified_jobs (
                    hash_id          TEXT PRIMARY KEY,
                    title            TEXT NOT NULL,
                    company          TEXT NOT NULL,
                    location         TEXT NOT NULL,
                    city             TEXT NOT NULL DEFAULT '',
                    country          TEXT NOT NULL DEFAULT '',
                    work_model       TEXT NOT NULL DEFAULT 'unknown'
                                     CHECK(work_model IN ('remote','hybrid','onsite','unknown')),
                    url              TEXT NOT NULL,
                    apply_url        TEXT,
                    source           TEXT NOT NULL
                                     CHECK(source IN ('linkedin','google_jobs','trueup')),
                    posted_date      TEXT,
                    comp_range       TEXT,
                    salary_visible   INTEGER NOT NULL DEFAULT 0 CHECK(salary_visible IN (0,1)),
                    company_stage    TEXT,
                    is_watchlist     INTEGER NOT NULL DEFAULT 0 CHECK(is_watchlist IN (0,1)),
                    match_pct        INTEGER NOT NULL CHECK(match_pct BETWEEN 0 AND 100),
                    seniority_score  INTEGER CHECK(seniority_score BETWEEN 0 AND 30),
                    domain_score     INTEGER CHECK(domain_score BETWEEN 0 AND 25),
                    location_score   INTEGER CHECK(location_score BETWEEN 0 AND 20),
                    stage_score      INTEGER CHECK(stage_score BETWEEN 0 AND 15),
                    comp_score       INTEGER CHECK(comp_score BETWEEN 0 AND 10),
                    reasoning        TEXT NOT NULL,
                    key_requirements TEXT NOT NULL DEFAULT '[]',
                    red_flags        TEXT NOT NULL DEFAULT '[]',
                    domain_alignment TEXT,
                    seniority_match  TEXT,
                    location_fit     TEXT,
                    company_stage_fit TEXT,
                    description      TEXT,
                    description_snippet TEXT,
                    company_size     TEXT,
                    domain_tags      TEXT NOT NULL DEFAULT '[]',
                    jd_alignment     TEXT,
                    status           TEXT NOT NULL DEFAULT 'new'
                                     CHECK(status IN ('new','reviewed','applied','rejected','not_a_fit','not_available')),
                    jd_filename      TEXT,
                    jd_downloaded    INTEGER NOT NULL DEFAULT 0 CHECK(jd_downloaded IN (0,1)),
                    scored_at        TEXT NOT NULL,
                    fetched_at       TEXT,
                    run_id           TEXT,
                    tailored_resume  TEXT
                );

                INSERT INTO qualified_jobs SELECT * FROM qualified_jobs_migration_backup;

                DROP TABLE qualified_jobs_migration_backup;

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

                PRAGMA foreign_keys=ON;
            """)
            logger.info("db_migration_done", reason="status_check_constraint_update")

        logger.info("db_initialised", path=db_path)
    finally:
        conn.close()


def get_db(db_path: str = "output/jobsearch.db") -> sqlite3.Connection:
    """Open and return a SQLite connection with row_factory set.

    Caller is responsible for calling conn.close() — prefer rw_conn/ro_conn
    context managers for new code.  This function exists for legacy callers
    (e.g. preflight.py) that manage their own connection lifecycle.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def new_run_id() -> str:
    """Generate a short unique run identifier."""
    return uuid.uuid4().hex[:8]

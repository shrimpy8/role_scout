"""Phase 2 additive SQLite migrations. Called from init_db() on every startup."""
from __future__ import annotations

import sqlite3

import structlog

log = structlog.get_logger()

# Each entry: (migration_name, sql). Applied in order; each is idempotent.
PHASE2_MIGRATIONS: list[tuple[str, str]] = [
    (
        "qualified_jobs_tailored_resume",
        "ALTER TABLE qualified_jobs ADD COLUMN tailored_resume TEXT",
    ),
    (
        "run_log_input_tokens",
        "ALTER TABLE run_log ADD COLUMN input_tokens INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "run_log_output_tokens",
        "ALTER TABLE run_log ADD COLUMN output_tokens INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "run_log_estimated_cost_usd",
        "ALTER TABLE run_log ADD COLUMN estimated_cost_usd REAL NOT NULL DEFAULT 0.0",
    ),
    (
        "run_log_source_health_json",
        "ALTER TABLE run_log ADD COLUMN source_health_json TEXT",
    ),
    (
        "run_log_trigger_type",
        "ALTER TABLE run_log ADD COLUMN trigger_type TEXT NOT NULL DEFAULT 'manual'",
    ),
    (
        "run_log_ttl_deadline",
        "ALTER TABLE run_log ADD COLUMN ttl_deadline TEXT",
    ),
    (
        "run_log_ttl_extended",
        "ALTER TABLE run_log ADD COLUMN ttl_extended INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "run_log_cancel_reason",
        "ALTER TABLE run_log ADD COLUMN cancel_reason TEXT",
    ),
    (
        "run_log_idx_started_at",
        "CREATE INDEX IF NOT EXISTS idx_run_log_started_at ON run_log(started_at DESC)",
    ),
    (
        "qualified_jobs_idx_status",
        "CREATE INDEX IF NOT EXISTS idx_qualified_jobs_status ON qualified_jobs(status)",
    ),
]


def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply Phase 2 migrations and set required PRAGMAs."""
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

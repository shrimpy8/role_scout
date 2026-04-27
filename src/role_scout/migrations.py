"""Phase 2 additive SQLite migrations. Called from init_db() on every startup."""
from __future__ import annotations

import sqlite3

import structlog

log = structlog.get_logger()

# Each entry: (migration_name, sql_or_steps).
# sql_or_steps may be a single SQL string or a list of SQL strings run in sequence.
PHASE2_MIGRATIONS: list[tuple[str, str | list[str]]] = [
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
    # Phase 1 CHECK only allowed ('running','completed','failed').
    # Phase 2 adds review_pending, cancelled, cancelled_ttl.
    # Uses PRAGMA writable_schema to patch sqlite_master directly — no table
    # rebuild required, so no WAL lock contention.
    (
        "run_log_expand_status_constraint",
        [
            "PRAGMA writable_schema=ON",
            "UPDATE sqlite_master SET sql = replace(sql,"
            " 'CHECK(status IN (''running'',''completed'',''failed''))',"
            " 'CHECK(status IN (''running'',''completed'',''failed'',''review_pending'',''cancelled'',''cancelled_ttl''))')"
            " WHERE type='table' AND name='run_log' AND sql NOT LIKE '%review_pending%'",
            "PRAGMA writable_schema=OFF",
        ],
    ),
]


def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply Phase 2 migrations and set required PRAGMAs."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")

    for name, steps in PHASE2_MIGRATIONS:
        sql_list = [steps] if isinstance(steps, str) else steps
        try:
            for sql in sql_list:
                conn.execute(sql)
            conn.commit()
            log.info("migration_applied", name=name)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "duplicate column name" in msg or "already exists" in msg:
                log.debug("migration_skipped_idempotent", name=name)
                continue
            raise

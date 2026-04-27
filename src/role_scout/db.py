"""Database initialisation and connection helpers for Phase 2.

Calls Phase 1 `init_db()` (creates all base tables), then runs the Phase 2
additive migrations. Safe to call on every startup — both are idempotent.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import structlog
from role_scout.compat.db.connection import init_db as _p1_init_db

from role_scout.migrations import run_migrations

log = structlog.get_logger()


def init_db(db_path: str | Path = "../auto_jobsearch/output/jobsearch.db") -> None:
    """Create all tables (Phase 1) then apply Phase 2 migrations. Idempotent."""
    path = str(db_path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    _p1_init_db(path)

    conn = sqlite3.connect(path)
    try:
        run_migrations(conn)
    finally:
        conn.close()

    log.info("db_initialised", path=path)


def get_rw_conn(db_path: str | Path) -> sqlite3.Connection:
    """Return a read-write connection with Row factory and WAL pragmas."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_ro_conn(db_path: str | Path) -> sqlite3.Connection:
    """Return a read-only connection for dashboard queries."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def rw_conn(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for read-write DB connections. Auto-closes on exit."""
    conn = get_rw_conn(db_path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def ro_conn(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for read-only DB connections. Auto-closes on exit."""
    conn = get_ro_conn(db_path)
    try:
        yield conn
    finally:
        conn.close()

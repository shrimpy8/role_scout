"""seen_hashes table operations: dedup checks and TTL expiry."""

import sqlite3
from datetime import datetime, timedelta, timezone

from role_scout.compat.db.connection import SEEN_HASH_TTL_DAYS


def is_new_job(conn: sqlite3.Connection, hash_id: str) -> bool:
    """Return True if hash_id has never been seen OR its TTL (60 days) has expired."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_HASH_TTL_DAYS)).isoformat()
    row = conn.execute(
        "SELECT last_seen_at FROM seen_hashes WHERE hash_id = ?", (hash_id,)
    ).fetchone()
    if row is None:
        return True
    return row["last_seen_at"] < cutoff


def upsert_seen_hash(
    conn: sqlite3.Connection,
    hash_id: str,
    source: str = "",
    title: str = "",
    company: str = "",
) -> None:
    """Insert or update a seen hash, refreshing last_seen_at to now."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO seen_hashes (hash_id, source, title, company, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(hash_id) DO UPDATE SET last_seen_at = excluded.last_seen_at
        """,
        (hash_id, source, title, company, now, now),
    )


def expire_old_hashes(conn: sqlite3.Connection, days: int = SEEN_HASH_TTL_DAYS) -> int:
    """Delete seen_hashes records older than `days`. Returns count of deleted rows."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    result = conn.execute(
        "DELETE FROM seen_hashes WHERE last_seen_at < ?", (cutoff,)
    )
    return result.rowcount

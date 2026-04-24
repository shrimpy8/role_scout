"""TTL utilities for HiTL review interrupt deadline management."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def compute_ttl_deadline(ttl_hours: float) -> datetime:
    """Return a timezone-aware UTC datetime `ttl_hours` from now."""
    return datetime.now(timezone.utc) + timedelta(hours=ttl_hours)


def is_ttl_expired(deadline: datetime) -> bool:
    """Return True if `deadline` has passed (UTC now >= deadline)."""
    return datetime.now(timezone.utc) >= deadline

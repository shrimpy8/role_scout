"""Thin wrapper around Phase 1 fetch_trueup for use in asyncio.to_thread.

Each invocation creates its own IMAP connection (thread-safe by construction —
fetch_trueup opens and closes the connection inside the function).
"""
from __future__ import annotations

import time
from typing import Any

import structlog
from jobsearch.fetchers.trueup import fetch_trueup

log = structlog.get_logger()


def run_trueup(
    imap_email: str,
    imap_password: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Synchronous TrueUp IMAP fetch. Returns (raw_jobs, query_params).

    Called via asyncio.to_thread — each call opens its own IMAP connection,
    so concurrent calls are safe (no shared mailbox state).
    """
    query_params: dict[str, Any] = {
        "host": "imap.gmail.com",
        "folder": "INBOX",
        "max_emails": 3,
    }
    t0 = time.monotonic()
    try:
        raw = fetch_trueup(
            user=imap_email,
            password=imap_password,
        )
        duration = time.monotonic() - t0
        log.info("trueup_fetch_done", count=len(raw), duration_s=round(duration, 2))
        return raw, query_params
    except Exception as exc:
        duration = time.monotonic() - t0
        log.exception("trueup_fetch_failed", duration_s=round(duration, 2))
        raise

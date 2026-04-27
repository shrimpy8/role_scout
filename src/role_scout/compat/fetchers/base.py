"""Shared fetch timing, error-boundary logging, and Apify HTTP client for all source fetchers."""

import time
from collections.abc import Generator
from contextlib import contextmanager

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from role_scout.compat.logging import get_logger

logger = get_logger(__name__)

_APIFY_BASE_URL = "https://api.apify.com/v2"
_APIFY_TIMEOUT_S = 300


@retry(
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=16),
    reraise=True,
)
def run_apify_actor(token: str, actor_id: str, payload: dict, source: str) -> list[dict]:
    """POST to the Apify sync endpoint for *actor_id*; raise on non-2xx or timeout."""
    endpoint = f"{_APIFY_BASE_URL}/acts/{actor_id}/run-sync-get-dataset-items"
    with httpx.Client(timeout=_APIFY_TIMEOUT_S) as client:
        resp = client.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
    if resp.status_code == 429:
        logger.warning("apify_rate_limited", source=source, status=429)
        raise httpx.HTTPStatusError("Rate limited", request=resp.request, response=resp)
    resp.raise_for_status()
    return resp.json()


@contextmanager
def fetch_context(source: str) -> Generator[None, None, None]:
    """Log fetch_started/fetch_complete around a fetcher body."""
    logger.info("fetch_started", source=source)
    start = time.monotonic()
    try:
        yield
    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.exception("fetch_failed", source=source, duration_ms=duration_ms)
        raise
    else:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info("fetch_complete", source=source, duration_ms=duration_ms)


def safe_fetch(source: str, fn, *args, **kwargs) -> list[dict]:
    """Call fn(*args, **kwargs), log timing, and return [] on any exception."""
    logger.info("fetch_started", source=source)
    start = time.monotonic()
    try:
        results = fn(*args, **kwargs)
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info("fetch_complete", source=source, count=len(results), duration_ms=duration_ms)
        return results
    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.exception("fetch_failed", source=source, duration_ms=duration_ms)
        return []

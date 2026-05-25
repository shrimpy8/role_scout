"""Best-effort HTTP fetch + text extraction for JD URLs."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import httpx
from bs4 import BeautifulSoup

from role_scout.compat.logging import get_logger

logger = get_logger(__name__)

_MIN_CONTENT_CHARS = 400
_MAX_CONTENT_CHARS = 4000

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Ordered list of CSS selectors to try; first match with enough text wins.
_JD_SELECTORS = [
    # Greenhouse
    '[class*="job__description"]',
    "#job-description",
    # Ashby
    '[data-testid="job-description"]',
    '[class*="ashby-job"]',
    # ZipRecruiter
    '[data-automation="jobDescriptionText"]',
    # Builtin
    '[class*="job-description"]',
    ".description",
    # Generic
    '[id*="job-description"]',
    '[class*="jobDescription"]',
    '[class*="job_description"]',
    "main",
    "article",
]

# Tags to strip entirely before extracting text.
_NOISE_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript", "iframe"}


@dataclass
class FetchResult:
    url: str
    raw_text: str
    status: Literal["ok", "thin", "failed"]
    error: str | None = field(default=None)


def _extract_text(html: str) -> str:
    """Parse HTML with BS4 and return visible JD text, stripped of boilerplate."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(list(_NOISE_TAGS)):
        tag.decompose()

    # Try each selector; take the first that yields enough text.
    for selector in _JD_SELECTORS:
        try:
            el = soup.select_one(selector)
        except Exception:
            continue
        if el:
            text = el.get_text(separator=" ", strip=True)
            if len(text) >= _MIN_CONTENT_CHARS:
                return _normalise_whitespace(text)[:_MAX_CONTENT_CHARS]

    # Fallback: all <p> text from <body>.
    body = soup.find("body")
    if body:
        paragraphs = [p.get_text(separator=" ", strip=True) for p in body.find_all("p")]
        text = " ".join(p for p in paragraphs if p)
        return _normalise_whitespace(text)[:_MAX_CONTENT_CHARS]

    return ""


def _normalise_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def fetch_url(url: str, timeout_s: float = 15.0) -> FetchResult:
    """HTTP GET a URL and extract visible job description text.

    Returns FetchResult with status:
      - "ok"     : text extracted successfully (>= 300 chars)
      - "thin"   : page loaded but content too short (JS-heavy or paywalled)
      - "failed" : network error, timeout, or non-2xx response
    """
    logger.debug("ingest_fetch_start", url=url[:80])
    try:
        with httpx.Client(
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
            timeout=timeout_s,
        ) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        logger.warning("ingest_fetch_timeout", url=url[:80])
        return FetchResult(url=url, raw_text="", status="failed", error=f"Timeout after {timeout_s}s")
    except httpx.HTTPStatusError as exc:
        logger.warning("ingest_fetch_http_error", url=url[:80], status_code=exc.response.status_code)
        return FetchResult(url=url, raw_text="", status="failed", error=f"HTTP {exc.response.status_code}")
    except httpx.HTTPError as exc:
        logger.warning("ingest_fetch_error", url=url[:80], error=str(exc)[:100])
        return FetchResult(url=url, raw_text="", status="failed", error=str(exc)[:200])

    raw_text = _extract_text(response.text)
    if len(raw_text) < _MIN_CONTENT_CHARS:
        logger.info("ingest_fetch_thin", url=url[:80], chars=len(raw_text))
        return FetchResult(url=url, raw_text=raw_text, status="thin")

    logger.info("ingest_fetch_ok", url=url[:80], chars=len(raw_text))
    return FetchResult(url=url, raw_text=raw_text, status="ok")

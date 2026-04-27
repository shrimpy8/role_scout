"""Enrich NormalizedJob descriptions by fetching the source URL."""

import json as _json

import httpx
from bs4 import BeautifulSoup

from role_scout.compat.logging import get_logger
from role_scout.compat.models import NormalizedJob

logger = get_logger(__name__)

_MIN_DESCRIPTION_CHARS = 200

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _extract_description(html: str) -> str | None:
    """Extract job description text from HTML using a three-tier strategy."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = _json.loads(tag.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                for node in item.get("@graph", [item]):
                    if not isinstance(node, dict):
                        continue
                    desc = node.get("description", "")
                    if desc and len(desc) >= _MIN_DESCRIPTION_CHARS:
                        clean = BeautifulSoup(desc, "html.parser").get_text(separator=" ", strip=True)
                        if len(clean) >= _MIN_DESCRIPTION_CHARS:
                            return clean
        except (_json.JSONDecodeError, AttributeError):
            continue

    og = soup.find("meta", {"property": "og:description"})
    if og:
        content = og.get("content", "")
        if content and len(content) >= _MIN_DESCRIPTION_CHARS:
            return content

    text = soup.get_text(separator=" ", strip=True)
    if len(text) >= _MIN_DESCRIPTION_CHARS:
        return text

    return None


def _fetch_text(url: str, timeout: int) -> str | None:
    """Fetch URL and return extracted description text, or None on failure."""
    try:
        response = httpx.get(
            url,
            headers={"user-agent": _USER_AGENT},
            follow_redirects=True,
            timeout=timeout,
        )
        if response.status_code != 200:
            logger.warning("enrich_non_200", url=url, status=response.status_code)
            return None
        try:
            text = _extract_description(response.text)
        except Exception:
            logger.exception("enrich_parse_error", url=url)
            return None
        if text is None:
            logger.warning("enrich_too_short", url=url)
        elif "<" in text:
            text = BeautifulSoup(text, "html.parser").get_text(separator=" ", strip=True)
            if len(text) < _MIN_DESCRIPTION_CHARS:
                logger.warning("enrich_too_short_after_rstrip", url=url)
                text = None
        return text
    except httpx.TimeoutException:
        logger.warning("enrich_timeout", url=url)
        return None
    except httpx.HTTPError as exc:
        logger.warning("enrich_http_error", url=url, error=str(exc))
        return None


def enrich_descriptions(jobs: list[NormalizedJob], timeout: int = 15) -> list[NormalizedJob]:
    """Fetch and populate full descriptions for all jobs."""
    for job in jobs:
        urls_to_try = [u for u in [job.apply_url, job.url] if u]
        if not urls_to_try:
            continue

        for url in urls_to_try:
            text = _fetch_text(url, timeout)
            if text:
                job.description = text
                logger.debug("enrich_success", url=url, chars=len(text), hash_id=job.hash_id)
                break
        else:
            logger.warning(
                "enrich_all_urls_failed",
                hash_id=job.hash_id,
                company=job.company,
                tried=urls_to_try,
            )

    return jobs

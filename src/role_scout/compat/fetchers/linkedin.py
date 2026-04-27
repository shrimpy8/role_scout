"""LinkedIn Jobs fetcher via Apify actor harvestapi~linkedin-job-search."""

from role_scout.compat.fetchers.base import run_apify_actor
from role_scout.compat.logging import get_logger

logger = get_logger(__name__)

_ACTOR_ID = "harvestapi~linkedin-job-search"


def fetch_linkedin(
    token: str,
    queries: list[str],
    location: str = "San Francisco Bay Area",
    max_items: int = 50,
    posted_within: str = "month",
) -> list[dict]:
    """Fetch LinkedIn jobs via Apify (harvestapi~linkedin-job-search)."""
    _HARD_CAP = 100
    max_items = min(max_items, _HARD_CAP)

    per_title_cap = max(1, max_items // max(len(queries), 1))

    payload = {
        "jobTitles": queries,
        "locations": [location],
        "maxItems": per_title_cap,
        "employmentType": ["full-time"],
        "experienceLevel": ["mid-senior"],
        "postedLimit": posted_within,
        "sortBy": "relevance",
    }
    logger.info("linkedin_fetch_params", queries=queries, location=location, max_items=max_items, per_title_cap=per_title_cap)
    items = run_apify_actor(token, _ACTOR_ID, payload, "linkedin")
    logger.info("linkedin_fetched", count=len(items))
    return items

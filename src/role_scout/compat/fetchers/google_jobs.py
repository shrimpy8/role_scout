"""Google Jobs fetcher via SerpAPI official client."""

import serpapi
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from role_scout.compat.logging import get_logger

logger = get_logger(__name__)

_TIMEOUT_S = 30


@retry(
    retry=retry_if_exception_type(serpapi.TimeoutError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=16),
    reraise=True,
)
def _search(client: serpapi.Client, params: dict) -> dict:
    """Execute one SerpAPI search call; retried on timeout."""
    return dict(client.search(params))


_DATE_CHIPS = {
    "24h": "date_posted:today",
    "week": "date_posted:week",
    "month": "date_posted:month",
}


def fetch_google_jobs(
    api_key: str,
    queries: list[str],
    location: str = "San Francisco, California, United States",
    max_pages: int = 2,
    posted_within: str = "month",
    max_results: int = 50,
) -> list[dict]:
    """Fetch Google Jobs results via SerpAPI."""
    client = serpapi.Client(api_key=api_key, timeout=_TIMEOUT_S)
    all_jobs: list[dict] = []
    date_chip = _DATE_CHIPS.get(posted_within, "date_posted:month")
    chips = f"{date_chip},employment_type:FULLTIME"

    for query in queries:
        logger.info("google_jobs_query_start", query=query, location=location)
        next_page_token: str | None = None

        for page_num in range(max_pages):
            params: dict = {
                "engine": "google_jobs",
                "q": query,
                "location": location,
                "hl": "en",
                "gl": "us",
                "chips": chips,
            }
            if next_page_token:
                params["next_page_token"] = next_page_token

            try:
                data = _search(client, params)
            except serpapi.HTTPError as e:
                if e.status_code in (402, 429):
                    logger.error(
                        "serpapi_quota_exceeded",
                        source="google_jobs",
                        status=e.status_code,
                        query=query,
                    )
                    return all_jobs
                logger.exception("google_jobs_search_error", query=query, page=page_num)
                break
            except Exception:
                logger.exception("google_jobs_search_unexpected", query=query, page=page_num)
                break

            job_cards: list[dict] = data.get("jobs_results", [])
            if not job_cards:
                logger.info("google_jobs_no_results", query=query, page=page_num)
                break

            all_jobs.extend(job_cards)

            if len(all_jobs) >= max_results:
                logger.info("google_jobs_max_results_reached", count=len(all_jobs))
                return all_jobs[:max_results]

            next_page_token = data.get("serpapi_pagination", {}).get("next_page_token")
            if not next_page_token:
                break

        logger.info("google_jobs_query_done", query=query, total_so_far=len(all_jobs))

    logger.info("google_jobs_fetched", count=len(all_jobs))
    return all_jobs

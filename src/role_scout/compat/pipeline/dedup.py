"""dedup_jobs(): filter already-seen jobs via seen_hashes table (60-day TTL)."""

import sqlite3

from role_scout.compat.db import is_new_job, upsert_seen_hash
from role_scout.compat.logging import get_logger
from role_scout.compat.models import NormalizedJob

logger = get_logger(__name__)


def dedup_jobs(conn: sqlite3.Connection, jobs: list[NormalizedJob]) -> list[NormalizedJob]:
    """Return only jobs not seen in the last 60 days; upsert all new hash_ids."""
    new_jobs: list[NormalizedJob] = []
    seen_count = 0

    for job in jobs:
        if is_new_job(conn, job.hash_id):
            upsert_seen_hash(conn, job.hash_id, source=job.source, title=job.title, company=job.company)
            new_jobs.append(job)
        else:
            logger.debug("dedup_skipped", hash_id=job.hash_id, company=job.company, title=job.title)
            seen_count += 1

    logger.info(
        "dedup_complete",
        total_input=len(jobs),
        new=len(new_jobs),
        already_seen=seen_count,
    )
    return new_jobs

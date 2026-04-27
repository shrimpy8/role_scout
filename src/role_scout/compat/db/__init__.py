"""role_scout.compat.db — SQLite data access layer.

All public symbols are re-exported here so callers use
`from role_scout.compat.db import X` regardless of which sub-module houses X.
"""

from role_scout.compat.db.connection import SEEN_HASH_TTL_DAYS, get_db, init_db, new_run_id
from role_scout.compat.db.qualified_jobs import (
    get_job_by_hash_id,
    get_job_count_by_source,
    get_job_count_by_status,
    get_qualified_jobs,
    insert_qualified_job,
    update_jd_alignment,
    update_jd_filename,
    update_job_status,
)
from role_scout.compat.db.run_log import get_run_logs, insert_run_log, update_run_log
from role_scout.compat.db.seen_hashes import expire_old_hashes, is_new_job, upsert_seen_hash

__all__ = [
    # connection
    "SEEN_HASH_TTL_DAYS",
    "get_db",
    "init_db",
    "new_run_id",
    # seen_hashes
    "is_new_job",
    "upsert_seen_hash",
    "expire_old_hashes",
    # qualified_jobs
    "insert_qualified_job",
    "update_job_status",
    "update_jd_filename",
    "update_jd_alignment",
    "get_qualified_jobs",
    "get_job_by_hash_id",
    "get_job_count_by_status",
    "get_job_count_by_source",
    # run_log
    "insert_run_log",
    "update_run_log",
    "get_run_logs",
]

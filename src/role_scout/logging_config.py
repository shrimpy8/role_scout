"""Structured logging factory for role_scout/.

Thin wrapper around Phase 1's jobsearch.logging — reuses the same structlog
configuration (JSON output, rotating file, merge_contextvars for correlation_id)
without duplicating setup code.

Usage in every module:
    from role_scout.logging_config import get_logger
    logger = get_logger(__name__)

Bind correlation_id once at the start of each run:
    import structlog
    structlog.contextvars.bind_contextvars(correlation_id=run_id)
"""

import structlog
from jobsearch.logging import get_logger as _p1_get_logger
from jobsearch.logging import setup_logging


def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    """Return a structlog BoundLogger for the given module name."""
    return _p1_get_logger(name)


__all__ = ["get_logger", "setup_logging"]

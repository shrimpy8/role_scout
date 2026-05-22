"""Phase 2 DAL for the YAML-backed company watchlist."""
from __future__ import annotations

from pathlib import Path

import structlog
import yaml

from role_scout.dal._yaml_io import atomic_write_yaml_list

log = structlog.get_logger()

DEFAULT_WATCHLIST_PATH = Path("config/watchlist.yaml")


def get_watchlist(path: Path = DEFAULT_WATCHLIST_PATH) -> list[str]:
    """Read the watchlist from a YAML file.

    Args:
        path: Path to the watchlist YAML file.

    Returns:
        Sorted list of company names, or an empty list if the file is missing.
    """
    if not path.exists():
        log.debug("watchlist_dal.get_watchlist.missing", path=str(path))
        return []

    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict):
        log.warning("watchlist_dal.get_watchlist.malformed", path=str(path))
        return []

    companies = data.get("companies", [])
    if not isinstance(companies, list):
        log.warning(
            "watchlist_dal.get_watchlist.bad_companies_field",
            path=str(path),
            type=type(companies).__name__,
        )
        return []

    return [str(c) for c in companies]


def add_to_watchlist(company: str, path: Path = DEFAULT_WATCHLIST_PATH) -> list[str]:
    """Add a company to the watchlist atomically.

    Idempotent: if the company is already present the existing list is returned
    unchanged and no file write is performed.

    Args:
        company: Company name to add.
        path: Path to the watchlist YAML file.

    Returns:
        Updated sorted list of company names.
    """
    current = get_watchlist(path)
    if company in current:
        log.debug("watchlist_dal.add_to_watchlist.already_present", company=company)
        return current

    updated = sorted(set(current) | {company})
    _atomic_write(path, updated)
    log.info("watchlist_dal.add_to_watchlist", company=company, total=len(updated))
    return updated


def remove_from_watchlist(company: str, path: Path = DEFAULT_WATCHLIST_PATH) -> list[str]:
    """Remove a company from the watchlist atomically.

    Idempotent: if the company is not present the existing list is returned
    unchanged and no file write is performed.

    Args:
        company: Company name to remove.
        path: Path to the watchlist YAML file.

    Returns:
        Updated sorted list of company names.
    """
    current = get_watchlist(path)
    if company not in current:
        log.debug("watchlist_dal.remove_from_watchlist.not_present", company=company)
        return current

    updated = sorted(c for c in current if c != company)
    _atomic_write(path, updated)
    log.info("watchlist_dal.remove_from_watchlist", company=company, total=len(updated))
    return updated


_atomic_write = atomic_write_yaml_list

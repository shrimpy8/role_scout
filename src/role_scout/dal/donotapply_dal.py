"""DAL for the YAML-backed do-not-apply company exclusion list."""
from __future__ import annotations

from pathlib import Path

import structlog
import yaml

from role_scout.dal._yaml_io import atomic_write_yaml_list

log = structlog.get_logger()

DEFAULT_DONOTAPPLY_PATH = Path("config/donotapply.yaml")


def get_donotapply(path: Path = DEFAULT_DONOTAPPLY_PATH) -> list[str]:
    """Read the do-not-apply list from a YAML file.

    Returns:
        Sorted list of company names (original casing), or [] if file is missing.
    """
    if not path.exists():
        log.debug("donotapply_dal.get.missing", path=str(path))
        return []

    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict):
        log.warning("donotapply_dal.get.malformed", path=str(path))
        return []

    companies = data.get("companies", [])
    if not isinstance(companies, list):
        log.warning("donotapply_dal.get.bad_field", path=str(path), type=type(companies).__name__)
        return []

    return [str(c) for c in companies]


def add_to_donotapply(company: str, path: Path = DEFAULT_DONOTAPPLY_PATH) -> list[str]:
    """Add a company to the do-not-apply list atomically. Idempotent."""
    current = get_donotapply(path)
    if company in current:
        log.debug("donotapply_dal.add.already_present", company=company)
        return current

    updated = sorted(set(current) | {company})
    _atomic_write(path, updated)
    log.info("donotapply_dal.add", company=company, total=len(updated))
    return updated


def remove_from_donotapply(company: str, path: Path = DEFAULT_DONOTAPPLY_PATH) -> list[str]:
    """Remove a company from the do-not-apply list atomically. Idempotent."""
    current = get_donotapply(path)
    if company not in current:
        log.debug("donotapply_dal.remove.not_present", company=company)
        return current

    updated = sorted(c for c in current if c != company)
    _atomic_write(path, updated)
    log.info("donotapply_dal.remove", company=company, total=len(updated))
    return updated


def get_excluded_set(path: Path | None = DEFAULT_DONOTAPPLY_PATH) -> frozenset[str]:
    """Return a lowercased frozenset for fast O(1) membership checks in the pipeline."""
    if path is None:
        return frozenset()
    return frozenset(c.lower() for c in get_donotapply(path))


def get_locked_set(env_csv: str) -> frozenset[str]:
    """Parse DONOTAPPLY_COMPANIES env var into a lowercased frozenset."""
    return frozenset(c.strip().lower() for c in env_csv.split(",") if c.strip())


def get_locked_list(env_csv: str) -> list[str]:
    """Parse DONOTAPPLY_COMPANIES env var into a sorted list (original casing preserved)."""
    return sorted({c.strip() for c in env_csv.split(",") if c.strip()})


def get_full_excluded_set(path: Path | None, env_csv: str) -> frozenset[str]:
    """Union of YAML-managed list and env-seeded list. Used by the pipeline."""
    return get_excluded_set(path) | get_locked_set(env_csv)


_atomic_write = atomic_write_yaml_list

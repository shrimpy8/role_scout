"""DAL for the YAML-backed do-not-apply company exclusion list."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import structlog
import yaml

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


def _atomic_write(path: Path, companies: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.dump({"companies": companies}, default_flow_style=False, allow_unicode=True)

    dir_fd = str(path.parent)
    fd, tmp_path = tempfile.mkstemp(dir=dir_fd, suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

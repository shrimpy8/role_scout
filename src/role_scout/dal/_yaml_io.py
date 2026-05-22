"""Shared atomic YAML-list writer used by both watchlist_dal and donotapply_dal."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml


def atomic_write_yaml_list(path: Path, companies: list[str]) -> None:
    """Write a sorted company list to a YAML file atomically.

    Uses tempfile + os.replace so the file is never partially written.
    Raises OSError on any I/O failure (temp creation, write, or rename).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.dump({"companies": companies}, default_flow_style=False, allow_unicode=True)

    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

"""Shared monotonic watchlist revision counter.

Imported by both dashboard/routes.py and mcp_server/server.py so that
all clients — dashboard and MCP — see the same revision sequence.

Resets to 0 on process restart, which is acceptable: clients always
fetch the current watchlist on page/session load, establishing a fresh
baseline regardless of the previous revision.
"""
from __future__ import annotations

import itertools

_revision_iter = itertools.count(1)
_current_revision: int = 0


def next_revision() -> int:
    """Increment and return the next watchlist revision."""
    global _current_revision
    _current_revision = next(_revision_iter)
    return _current_revision


def current_revision() -> int:
    """Return the current revision without incrementing."""
    return _current_revision

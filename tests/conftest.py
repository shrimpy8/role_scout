"""Root conftest — ensures editable sibling installs resolve under Anaconda Python."""

import sqlite3
import sys
from pathlib import Path

import pytest

_AUTO_JOBSEARCH = Path(__file__).parents[1] / ".." / "auto_jobsearch"
_resolved = str(_AUTO_JOBSEARCH.resolve())
if _resolved not in sys.path:
    sys.path.insert(0, _resolved)


@pytest.fixture
def fixture_db() -> sqlite3.Connection:
    """In-memory seeded SQLite DB for all MCP/DAL tests."""
    from tests.fixtures.seed_fixture_db import create_fixture_db
    conn = create_fixture_db(":memory:")
    yield conn
    conn.close()

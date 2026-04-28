"""Root conftest."""

import sqlite3

import pytest


@pytest.fixture
def fixture_db() -> sqlite3.Connection:
    """In-memory seeded SQLite DB for all MCP/DAL tests."""
    from tests.fixtures.seed_fixture_db import create_fixture_db
    conn = create_fixture_db(":memory:")
    yield conn
    conn.close()

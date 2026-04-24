"""Unit tests for tailor_resume() — T22–T27.

T22: Cache hit — same resume_sha + prompt_version + hash_id → no Claude call.
T23: Resume file edited → cache_key differs → Claude called.
T24: force=True → Claude called even with cached row.
T25: Prompt file version bumped → cache_key differs → fresh call.
T26: Non-qualified hash_id → NotQualifiedError (400).
T27: Malformed Claude JSON → TailorParseError, no tailored_resume row written.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from role_scout.tailor import (
    NotQualifiedError,
    TailorParseError,
    _make_cache_key,
    tailor_resume,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_JSON = json.dumps({
    "tailored_summary": "Results-driven engineer with ML focus.",
    "tailored_bullets": [
        "Led ML pipeline to production (40% latency reduction).",
        "Built distributed training on 64-GPU cluster.",
        "Reduced model serving costs 30% via quantisation.",
    ],
    "keywords_incorporated": ["MLOps", "distributed training", "quantisation"],
})

_PROMPT_VERSION = "v1.0"
_PROMPT_TEXT = f"<!-- version: {_PROMPT_VERSION} -->\nSystem prompt content."

_RESUME_TEXT = "Experienced ML engineer with Python, AWS, distributed systems."
_RESUME_SHA = hashlib.sha256(_RESUME_TEXT.encode()).hexdigest()[:16]


def _cache_key(hash_id: str) -> str:
    return _make_cache_key(_RESUME_SHA, _PROMPT_VERSION, hash_id)


def _setup_db_with_job(conn: sqlite3.Connection, hash_id: str, match_pct: int) -> None:
    """Insert a minimal qualifying job row directly."""
    conn.execute(
        """
        INSERT OR REPLACE INTO qualified_jobs
        (hash_id, title, company, location, city, country, work_model, url,
         source, match_pct, reasoning, key_requirements, red_flags, domain_tags, scored_at)
        VALUES (?, 'Senior ML Engineer', 'Acme AI', 'SF, CA', 'SF', 'US',
                'hybrid', 'https://jobs.example.com/x', 'linkedin',
                ?, 'Good fit', '[]', '[]', '[]', '2025-06-01T10:00:00')
        """,
        (hash_id, match_pct),
    )
    conn.commit()


def _patch_env(mock_call_return: str = _VALID_JSON) -> list:
    """Return list of context managers to patch external deps."""
    return [
        patch("role_scout.tailor._read_prompt", return_value=(_PROMPT_TEXT, _PROMPT_VERSION)),
        patch("role_scout.tailor._read_resume", return_value=(_RESUME_TEXT, _RESUME_SHA)),
        patch("role_scout.tailor.call_claude", return_value=(mock_call_return, 1000, 200)),
    ]


# ---------------------------------------------------------------------------
# T22 — Cache hit → no Claude call
# ---------------------------------------------------------------------------

class TestT22CacheHit:
    def test_cache_hit_skips_claude(self, fixture_db: sqlite3.Connection) -> None:
        """T22: same resume+prompt+hash → Claude not called."""
        hash_id = "0000000000000001"
        ck = _cache_key(hash_id)
        cached_data = {
            "hash_id": hash_id,
            "job_title": "Staff ML Engineer",
            "company": "Acme AI",
            "tailored_summary": "Cached summary.",
            "tailored_bullets": ["Bullet 1.", "Bullet 2.", "Bullet 3."],
            "keywords_incorporated": ["Python"],
            "cache_key": ck,
            "prompt_version": _PROMPT_VERSION,
            "tailored_at": "2025-06-01T10:00:00+00:00",
            "cached": False,
        }
        fixture_db.execute(
            "UPDATE qualified_jobs SET tailored_resume = ? WHERE hash_id = ?",
            (json.dumps(cached_data), hash_id),
        )
        fixture_db.commit()

        with patch("role_scout.tailor._read_prompt", return_value=(_PROMPT_TEXT, _PROMPT_VERSION)), \
             patch("role_scout.tailor._read_resume", return_value=(_RESUME_TEXT, _RESUME_SHA)), \
             patch("role_scout.tailor.call_claude") as mock_claude:
            result = tailor_resume(
                fixture_db, hash_id, qualify_threshold=85, force=False, api_key="fake"
            )

        mock_claude.assert_not_called()
        assert result.cached is True
        assert result.cache_key == ck

    def test_cache_hit_returns_correct_fields(self, fixture_db: sqlite3.Connection) -> None:
        """T22: cached result has all required fields."""
        hash_id = "0000000000000001"
        ck = _cache_key(hash_id)
        cached_data = {
            "hash_id": hash_id,
            "job_title": "Staff ML Engineer",
            "company": "Acme AI",
            "tailored_summary": "Great fit.",
            "tailored_bullets": ["Led ML pipeline.", "Built cluster.", "Reduced cost."],
            "keywords_incorporated": ["MLOps"],
            "cache_key": ck,
            "prompt_version": _PROMPT_VERSION,
            "tailored_at": "2025-06-01T10:00:00+00:00",
            "cached": False,
        }
        fixture_db.execute(
            "UPDATE qualified_jobs SET tailored_resume = ? WHERE hash_id = ?",
            (json.dumps(cached_data), hash_id),
        )
        fixture_db.commit()

        with patch("role_scout.tailor._read_prompt", return_value=(_PROMPT_TEXT, _PROMPT_VERSION)), \
             patch("role_scout.tailor._read_resume", return_value=(_RESUME_TEXT, _RESUME_SHA)), \
             patch("role_scout.tailor.call_claude"):
            result = tailor_resume(fixture_db, hash_id, qualify_threshold=85, force=False, api_key="fake")

        for field in ("hash_id", "job_title", "company", "tailored_summary", "tailored_bullets", "cache_key"):
            assert hasattr(result, field)


# ---------------------------------------------------------------------------
# T23 — Resume change → cache miss → Claude called
# ---------------------------------------------------------------------------

class TestT23ResumeCacheMiss:
    def test_different_resume_sha_calls_claude(self, fixture_db: sqlite3.Connection) -> None:
        """T23: different resume_sha → cache miss → Claude called once."""
        hash_id = "0000000000000001"
        old_sha = "deadbeef00000000"  # different from _RESUME_SHA
        old_ck = _make_cache_key(old_sha, _PROMPT_VERSION, hash_id)
        stale_data = {
            "hash_id": hash_id,
            "job_title": "Staff ML Engineer",
            "company": "Acme AI",
            "tailored_summary": "Old summary.",
            "tailored_bullets": ["Old bullet 1.", "Old bullet 2.", "Old bullet 3."],
            "keywords_incorporated": [],
            "cache_key": old_ck,
            "prompt_version": _PROMPT_VERSION,
            "tailored_at": "2025-01-01T10:00:00+00:00",
            "cached": False,
        }
        fixture_db.execute(
            "UPDATE qualified_jobs SET tailored_resume = ? WHERE hash_id = ?",
            (json.dumps(stale_data), hash_id),
        )
        fixture_db.commit()

        with patch("role_scout.tailor._read_prompt", return_value=(_PROMPT_TEXT, _PROMPT_VERSION)), \
             patch("role_scout.tailor._read_resume", return_value=(_RESUME_TEXT, _RESUME_SHA)), \
             patch("role_scout.tailor.call_claude", return_value=(_VALID_JSON, 1000, 200)) as mock_claude:
            result = tailor_resume(fixture_db, hash_id, qualify_threshold=85, force=False, api_key="fake")

        mock_claude.assert_called_once()
        assert result.cached is False
        assert result.cache_key == _cache_key(hash_id)


# ---------------------------------------------------------------------------
# T24 — force=True bypasses cache
# ---------------------------------------------------------------------------

class TestT24ForceBypassesCache:
    def test_force_true_calls_claude_despite_cache(self, fixture_db: sqlite3.Connection) -> None:
        """T24: force=True → Claude called even when valid cache exists."""
        hash_id = "0000000000000001"
        ck = _cache_key(hash_id)
        cached_data = {
            "hash_id": hash_id,
            "job_title": "Staff ML Engineer",
            "company": "Acme AI",
            "tailored_summary": "Cached.",
            "tailored_bullets": ["b1", "b2", "b3"],
            "keywords_incorporated": [],
            "cache_key": ck,
            "prompt_version": _PROMPT_VERSION,
            "tailored_at": "2025-06-01T10:00:00+00:00",
            "cached": False,
        }
        fixture_db.execute(
            "UPDATE qualified_jobs SET tailored_resume = ? WHERE hash_id = ?",
            (json.dumps(cached_data), hash_id),
        )
        fixture_db.commit()

        with patch("role_scout.tailor._read_prompt", return_value=(_PROMPT_TEXT, _PROMPT_VERSION)), \
             patch("role_scout.tailor._read_resume", return_value=(_RESUME_TEXT, _RESUME_SHA)), \
             patch("role_scout.tailor.call_claude", return_value=(_VALID_JSON, 1000, 200)) as mock_claude:
            result = tailor_resume(fixture_db, hash_id, qualify_threshold=85, force=True, api_key="fake")

        mock_claude.assert_called_once()
        assert result.cached is False


# ---------------------------------------------------------------------------
# T25 — Prompt version bump → cache miss
# ---------------------------------------------------------------------------

class TestT25PromptVersionBump:
    def test_prompt_version_bump_invalidates_cache(self, fixture_db: sqlite3.Connection) -> None:
        """T25: new prompt version → cache_key differs → Claude called."""
        hash_id = "0000000000000001"
        old_version = "v0.9"
        old_ck = _make_cache_key(_RESUME_SHA, old_version, hash_id)
        stale_data = {
            "hash_id": hash_id,
            "job_title": "Staff ML Engineer",
            "company": "Acme AI",
            "tailored_summary": "Old.",
            "tailored_bullets": ["b1", "b2", "b3"],
            "keywords_incorporated": [],
            "cache_key": old_ck,
            "prompt_version": old_version,
            "tailored_at": "2025-01-01T10:00:00+00:00",
            "cached": False,
        }
        fixture_db.execute(
            "UPDATE qualified_jobs SET tailored_resume = ? WHERE hash_id = ?",
            (json.dumps(stale_data), hash_id),
        )
        fixture_db.commit()

        new_version = "v1.1"
        new_prompt = f"<!-- version: {new_version} -->\nUpdated prompt."
        with patch("role_scout.tailor._read_prompt", return_value=(new_prompt, new_version)), \
             patch("role_scout.tailor._read_resume", return_value=(_RESUME_TEXT, _RESUME_SHA)), \
             patch("role_scout.tailor.call_claude", return_value=(_VALID_JSON, 1000, 200)) as mock_claude:
            result = tailor_resume(fixture_db, hash_id, qualify_threshold=85, force=False, api_key="fake")

        mock_claude.assert_called_once()
        assert result.prompt_version == new_version
        new_expected_ck = _make_cache_key(_RESUME_SHA, new_version, hash_id)
        assert result.cache_key == new_expected_ck


# ---------------------------------------------------------------------------
# T26 — Non-qualified hash_id → NotQualifiedError
# ---------------------------------------------------------------------------

class TestT26NotQualified:
    def test_below_threshold_raises_not_qualified(self, fixture_db: sqlite3.Connection) -> None:
        """T26: job with match_pct=60 below threshold=85 → NotQualifiedError."""
        with patch("role_scout.tailor._read_prompt", return_value=(_PROMPT_TEXT, _PROMPT_VERSION)), \
             patch("role_scout.tailor._read_resume", return_value=(_RESUME_TEXT, _RESUME_SHA)), \
             patch("role_scout.tailor.call_claude"):
            with pytest.raises(NotQualifiedError):
                # hash "000000000000000a" has match_pct=55 in fixture
                tailor_resume(
                    fixture_db, "000000000000000a", qualify_threshold=85, force=False, api_key="fake"
                )

    def test_unknown_hash_id_raises_not_qualified(self, fixture_db: sqlite3.Connection) -> None:
        """T26: hash_id not in DB → NotQualifiedError."""
        with patch("role_scout.tailor._read_prompt", return_value=(_PROMPT_TEXT, _PROMPT_VERSION)), \
             patch("role_scout.tailor._read_resume", return_value=(_RESUME_TEXT, _RESUME_SHA)):
            with pytest.raises(NotQualifiedError):
                tailor_resume(
                    fixture_db, "ffffffffffffffff", qualify_threshold=85, force=False, api_key="fake"
                )


# ---------------------------------------------------------------------------
# T27 — Malformed Claude JSON → TailorParseError, no DB write
# ---------------------------------------------------------------------------

class TestT27MalformedJson:
    def test_malformed_json_raises_tailor_parse_error(self, fixture_db: sqlite3.Connection) -> None:
        """T27: Claude returns non-JSON → TailorParseError raised."""
        with patch("role_scout.tailor._read_prompt", return_value=(_PROMPT_TEXT, _PROMPT_VERSION)), \
             patch("role_scout.tailor._read_resume", return_value=(_RESUME_TEXT, _RESUME_SHA)), \
             patch("role_scout.tailor.call_claude", return_value=("this is not json", 100, 10)):
            with pytest.raises(TailorParseError):
                tailor_resume(
                    fixture_db, "0000000000000001", qualify_threshold=85, force=True, api_key="fake"
                )

    def test_malformed_json_no_db_write(self, fixture_db: sqlite3.Connection) -> None:
        """T27: on TailorParseError, tailored_resume must NOT be written to DB."""
        # Clear any existing tailored_resume
        fixture_db.execute(
            "UPDATE qualified_jobs SET tailored_resume = NULL WHERE hash_id = ?",
            ("0000000000000001",),
        )
        fixture_db.commit()

        with patch("role_scout.tailor._read_prompt", return_value=(_PROMPT_TEXT, _PROMPT_VERSION)), \
             patch("role_scout.tailor._read_resume", return_value=(_RESUME_TEXT, _RESUME_SHA)), \
             patch("role_scout.tailor.call_claude", return_value=('{"partial": true}', 100, 10)):
            with pytest.raises(TailorParseError):
                tailor_resume(
                    fixture_db, "0000000000000001", qualify_threshold=85, force=True, api_key="fake"
                )

        row = fixture_db.execute(
            "SELECT tailored_resume FROM qualified_jobs WHERE hash_id = ?",
            ("0000000000000001",),
        ).fetchone()
        assert row[0] is None

    def test_missing_bullets_field_raises(self, fixture_db: sqlite3.Connection) -> None:
        """T27: JSON missing tailored_bullets → TailorParseError."""
        bad_json = json.dumps({"tailored_summary": "ok", "keywords_incorporated": []})
        with patch("role_scout.tailor._read_prompt", return_value=(_PROMPT_TEXT, _PROMPT_VERSION)), \
             patch("role_scout.tailor._read_resume", return_value=(_RESUME_TEXT, _RESUME_SHA)), \
             patch("role_scout.tailor.call_claude", return_value=(bad_json, 100, 10)):
            with pytest.raises(TailorParseError):
                tailor_resume(
                    fixture_db, "0000000000000001", qualify_threshold=85, force=True, api_key="fake"
                )

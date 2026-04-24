"""Unit tests for TTL utilities."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from role_scout.ttl import compute_ttl_deadline, is_ttl_expired


class TestComputeTTLDeadline:
    def test_deadline_is_in_future(self):
        deadline = compute_ttl_deadline(4.0)
        assert deadline > datetime.now(timezone.utc)

    def test_deadline_is_timezone_aware(self):
        deadline = compute_ttl_deadline(1.0)
        assert deadline.tzinfo is not None

    def test_deadline_approximately_correct(self):
        ttl_hours = 2.0
        before = datetime.now(timezone.utc)
        deadline = compute_ttl_deadline(ttl_hours)
        after = datetime.now(timezone.utc)
        expected_min = before + timedelta(hours=ttl_hours)
        expected_max = after + timedelta(hours=ttl_hours)
        assert expected_min <= deadline <= expected_max

    def test_sub_hour_ttl(self):
        deadline = compute_ttl_deadline(0.5)
        assert deadline > datetime.now(timezone.utc)
        assert deadline < datetime.now(timezone.utc) + timedelta(hours=1)


class TestIsTTLExpired:
    def test_past_deadline_is_expired(self):
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert is_ttl_expired(past) is True

    def test_future_deadline_is_not_expired(self):
        future = datetime.now(timezone.utc) + timedelta(hours=4)
        assert is_ttl_expired(future) is False

    def test_boundary_at_now_is_expired(self):
        now = datetime.now(timezone.utc) - timedelta(microseconds=1)
        assert is_ttl_expired(now) is True

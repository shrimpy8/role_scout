"""Unit tests for DAL edge cases — watchlist idempotency, malformed YAML, atomic write."""
from __future__ import annotations

from pathlib import Path

import pytest


class TestWatchlistDalEdgeCases:
    def test_add_idempotent(self, tmp_path):
        """Adding the same company twice returns the same list without duplication."""
        from role_scout.dal import watchlist_dal
        wl = tmp_path / "watchlist.yaml"
        watchlist_dal.add_to_watchlist("Acme", path=wl)
        result = watchlist_dal.add_to_watchlist("Acme", path=wl)
        assert result.count("Acme") == 1

    def test_remove_idempotent(self, tmp_path):
        """Removing a company that is not present returns the current list unchanged."""
        from role_scout.dal import watchlist_dal
        wl = tmp_path / "watchlist.yaml"
        watchlist_dal.add_to_watchlist("Beta", path=wl)
        result = watchlist_dal.remove_from_watchlist("DoesNotExist", path=wl)
        assert "Beta" in result
        assert "DoesNotExist" not in result

    def test_remove_returns_empty_list_when_only_entry(self, tmp_path):
        from role_scout.dal import watchlist_dal
        wl = tmp_path / "watchlist.yaml"
        watchlist_dal.add_to_watchlist("Solo", path=wl)
        result = watchlist_dal.remove_from_watchlist("Solo", path=wl)
        assert result == []

    def test_get_watchlist_missing_file(self, tmp_path):
        """Missing watchlist file returns empty list, not an error."""
        from role_scout.dal import watchlist_dal
        result = watchlist_dal.get_watchlist(path=tmp_path / "nonexistent.yaml")
        assert result == []

    def test_get_watchlist_malformed_yaml(self, tmp_path):
        """Malformed YAML (not a dict) returns empty list."""
        from role_scout.dal import watchlist_dal
        wl = tmp_path / "watchlist.yaml"
        wl.write_text("- just a list\n- not a dict\n", encoding="utf-8")
        result = watchlist_dal.get_watchlist(path=wl)
        assert result == []

    def test_get_watchlist_missing_companies_key(self, tmp_path):
        """YAML dict without 'companies' key returns empty list."""
        from role_scout.dal import watchlist_dal
        wl = tmp_path / "watchlist.yaml"
        wl.write_text("other_key:\n  - value\n", encoding="utf-8")
        result = watchlist_dal.get_watchlist(path=wl)
        assert result == []

    def test_add_creates_parent_dirs(self, tmp_path):
        """add_to_watchlist creates parent directories if they don't exist."""
        from role_scout.dal import watchlist_dal
        wl = tmp_path / "nested" / "dir" / "watchlist.yaml"
        watchlist_dal.add_to_watchlist("Acme", path=wl)
        assert wl.exists()

    def test_watchlist_sorted_alphabetically(self, tmp_path):
        """Watchlist entries are always returned sorted."""
        from role_scout.dal import watchlist_dal
        wl = tmp_path / "watchlist.yaml"
        watchlist_dal.add_to_watchlist("Zebra Inc", path=wl)
        watchlist_dal.add_to_watchlist("Acme Corp", path=wl)
        watchlist_dal.add_to_watchlist("Middle Co", path=wl)
        result = watchlist_dal.get_watchlist(path=wl)
        assert result == sorted(result)

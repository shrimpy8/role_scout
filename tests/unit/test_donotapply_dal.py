"""Unit tests for donotapply_dal."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from role_scout.dal.donotapply_dal import (
    add_to_donotapply,
    get_donotapply,
    get_excluded_set,
    get_full_excluded_set,
    get_locked_list,
    get_locked_set,
    remove_from_donotapply,
)


@pytest.fixture()
def yaml_path(tmp_path: Path) -> Path:
    return tmp_path / "donotapply.yaml"


class TestGetDonotapply:
    def test_missing_file_returns_empty(self, yaml_path: Path) -> None:
        assert get_donotapply(yaml_path) == []

    def test_reads_companies(self, yaml_path: Path) -> None:
        yaml_path.write_text(yaml.dump({"companies": ["Acme", "BigCorp"]}))
        assert get_donotapply(yaml_path) == ["Acme", "BigCorp"]

    def test_malformed_file_returns_empty(self, yaml_path: Path) -> None:
        yaml_path.write_text("not a dict")
        assert get_donotapply(yaml_path) == []

    def test_missing_companies_key_returns_empty(self, yaml_path: Path) -> None:
        yaml_path.write_text(yaml.dump({"other": []}))
        assert get_donotapply(yaml_path) == []


class TestAddToDonotapply:
    def test_add_new_company(self, yaml_path: Path) -> None:
        result = add_to_donotapply("Acme", yaml_path)
        assert "Acme" in result

    def test_idempotent(self, yaml_path: Path) -> None:
        add_to_donotapply("Acme", yaml_path)
        result = add_to_donotapply("Acme", yaml_path)
        assert result.count("Acme") == 1

    def test_persisted(self, yaml_path: Path) -> None:
        add_to_donotapply("Acme", yaml_path)
        assert "Acme" in get_donotapply(yaml_path)

    def test_sorted(self, yaml_path: Path) -> None:
        add_to_donotapply("Zebra", yaml_path)
        add_to_donotapply("Alpha", yaml_path)
        result = get_donotapply(yaml_path)
        assert result == sorted(result)


class TestRemoveFromDonotapply:
    def test_remove_existing(self, yaml_path: Path) -> None:
        add_to_donotapply("Acme", yaml_path)
        result = remove_from_donotapply("Acme", yaml_path)
        assert "Acme" not in result

    def test_idempotent_when_not_present(self, yaml_path: Path) -> None:
        result = remove_from_donotapply("Ghost", yaml_path)
        assert result == []

    def test_persisted(self, yaml_path: Path) -> None:
        add_to_donotapply("Acme", yaml_path)
        remove_from_donotapply("Acme", yaml_path)
        assert "Acme" not in get_donotapply(yaml_path)


class TestGetExcludedSet:
    def test_returns_frozenset(self, yaml_path: Path) -> None:
        add_to_donotapply("Acme Corp", yaml_path)
        result = get_excluded_set(yaml_path)
        assert isinstance(result, frozenset)

    def test_lowercased(self, yaml_path: Path) -> None:
        add_to_donotapply("Acme Corp", yaml_path)
        result = get_excluded_set(yaml_path)
        assert "acme corp" in result
        assert "Acme Corp" not in result

    def test_empty_when_no_file(self, yaml_path: Path) -> None:
        assert get_excluded_set(yaml_path) == frozenset()

    def test_none_path_returns_empty(self) -> None:
        assert get_excluded_set(None) == frozenset()


class TestGetLockedSet:
    def test_parses_csv(self) -> None:
        result = get_locked_set("Acme, BadCo,EvilCorp")
        assert result == frozenset({"acme", "badco", "evilcorp"})

    def test_empty_string_returns_empty(self) -> None:
        assert get_locked_set("") == frozenset()

    def test_lowercased(self) -> None:
        result = get_locked_set("MixedCase")
        assert "mixedcase" in result
        assert "MixedCase" not in result

    def test_whitespace_stripped(self) -> None:
        result = get_locked_set("  Acme  ,  BadCo  ")
        assert "acme" in result
        assert "badco" in result


class TestGetLockedList:
    def test_returns_sorted(self) -> None:
        result = get_locked_list("Zebra, Alpha, Mango")
        assert result == sorted(result)

    def test_preserves_original_casing(self) -> None:
        result = get_locked_list("Acme")
        assert "Acme" in result

    def test_deduplicates(self) -> None:
        result = get_locked_list("Acme, Acme, Acme")
        assert len(result) == 1

    def test_empty_returns_empty(self) -> None:
        assert get_locked_list("") == []


class TestGetFullExcludedSet:
    def test_unions_yaml_and_env(self, yaml_path: Path) -> None:
        add_to_donotapply("YamlCo", yaml_path)
        result = get_full_excluded_set(yaml_path, "EnvCo")
        assert "yamlco" in result
        assert "envco" in result

    def test_none_path_uses_env_only(self) -> None:
        result = get_full_excluded_set(None, "EnvOnly")
        assert "envonly" in result

    def test_empty_env_uses_yaml_only(self, yaml_path: Path) -> None:
        add_to_donotapply("YamlCo", yaml_path)
        result = get_full_excluded_set(yaml_path, "")
        assert "yamlco" in result

    def test_both_empty_returns_empty(self, yaml_path: Path) -> None:
        assert get_full_excluded_set(yaml_path, "") == frozenset()

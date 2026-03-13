"""Tests for castor.fleet.group_policy (FleetManager + GroupPolicy).

Covers: from_config parsing, malformed-entry warning, resolve_config deep-merge,
get_robot_groups, apply_to_all, add/remove helpers.  (Closes #640, relates #641)
"""

from __future__ import annotations

import logging

import pytest

from castor.fleet.group_policy import FleetManager, GroupPolicy, _deep_merge

# ---------------------------------------------------------------------------
# GroupPolicy.matches
# ---------------------------------------------------------------------------


class TestGroupPolicyMatches:
    def test_exact_match(self) -> None:
        gp = GroupPolicy(name="prod", robots=["RRN-000000000001"])
        assert gp.matches("RRN-000000000001")

    def test_case_insensitive(self) -> None:
        gp = GroupPolicy(name="prod", robots=["rrn-000000000001"])
        assert gp.matches("RRN-000000000001")

    def test_no_match(self) -> None:
        gp = GroupPolicy(name="prod", robots=["RRN-000000000001"])
        assert not gp.matches("RRN-000000000099")

    def test_disabled_group_never_matches(self) -> None:
        gp = GroupPolicy(name="prod", robots=["RRN-000000000001"], enabled=False)
        assert not gp.matches("RRN-000000000001")

    def test_matches_any(self) -> None:
        gp = GroupPolicy(name="prod", robots=["RRN-000000000001", "RRN-000000000002"])
        assert gp.matches_any(["RRN-000000000002", "RRN-000000000099"])
        assert not gp.matches_any(["RRN-000000000099"])


# ---------------------------------------------------------------------------
# FleetManager.from_config
# ---------------------------------------------------------------------------

_VALID_CONFIG = {
    "fleet": {
        "groups": {
            "production": {
                "robots": ["RRN-000000000001", "RRN-000000000002"],
                "policy": {"agent": {"confidence_gates": [{"threshold": 0.92}]}},
                "description": "Production fleet",
                "tags": ["prod"],
                "enabled": True,
            },
            "staging": {
                "robots": ["RRN-000000000003"],
                "policy": {"agent": {"confidence_gates": [{"threshold": 0.7}]}},
            },
        }
    }
}


class TestFleetManagerFromConfig:
    def test_parses_valid_config(self) -> None:
        fm = FleetManager.from_config(_VALID_CONFIG)
        assert len(fm.list_groups()) == 2

    def test_group_attributes(self) -> None:
        fm = FleetManager.from_config(_VALID_CONFIG)
        prod = next(g for g in fm.list_groups() if g.name == "production")
        assert prod.robots == ["RRN-000000000001", "RRN-000000000002"]
        assert prod.description == "Production fleet"
        assert prod.tags == ["prod"]
        assert prod.enabled is True

    def test_empty_config_returns_no_groups(self) -> None:
        fm = FleetManager.from_config({})
        assert fm.list_groups() == []

    def test_no_fleet_key(self) -> None:
        fm = FleetManager.from_config({"other": "stuff"})
        assert fm.list_groups() == []

    def test_malformed_group_entry_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        config = {
            "fleet": {
                "groups": {
                    "bad": "this-is-a-string-not-a-dict",
                    "good": {"robots": ["RRN-000000000001"], "policy": {}},
                }
            }
        }
        with caplog.at_level(logging.WARNING, logger="castor.fleet.group_policy"):
            fm = FleetManager.from_config(config)

        # bad group is dropped
        assert len(fm.list_groups()) == 1
        assert fm.list_groups()[0].name == "good"
        # warning was emitted
        assert any("bad" in r.message and "malformed" in r.message for r in caplog.records)

    def test_malformed_group_type_in_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        config = {"fleet": {"groups": {"bad": [1, 2, 3]}}}
        with caplog.at_level(logging.WARNING, logger="castor.fleet.group_policy"):
            FleetManager.from_config(config)
        assert any("list" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# FleetManager.resolve_config
# ---------------------------------------------------------------------------


class TestResolveConfig:
    def test_no_matching_group_returns_base(self) -> None:
        fm = FleetManager.from_config(_VALID_CONFIG)
        base = {"agent": {"model": "gpt-4"}}
        result = fm.resolve_config("RRN-000000000099", base)
        assert result == base

    def test_matching_group_overrides_base(self) -> None:
        fm = FleetManager.from_config(_VALID_CONFIG)
        base = {"agent": {"confidence_gates": [{"threshold": 0.5}]}}
        result = fm.resolve_config("RRN-000000000001", base)
        # production policy sets threshold 0.92
        assert result["agent"]["confidence_gates"] == [{"threshold": 0.92}]

    def test_multiple_groups_applied_in_order(self) -> None:
        config = {
            "fleet": {
                "groups": {
                    "first": {"robots": ["RRN-000000000001"], "policy": {"x": 1}},
                    "second": {"robots": ["RRN-000000000001"], "policy": {"x": 2}},
                }
            }
        }
        fm = FleetManager.from_config(config)
        result = fm.resolve_config("RRN-000000000001", {})
        # second group wins
        assert result["x"] == 2

    def test_base_config_is_not_mutated(self) -> None:
        fm = FleetManager.from_config(_VALID_CONFIG)
        base = {"agent": {"confidence_gates": [{"threshold": 0.5}]}}
        fm.resolve_config("RRN-000000000001", base)
        assert base["agent"]["confidence_gates"] == [{"threshold": 0.5}]


# ---------------------------------------------------------------------------
# FleetManager.apply_to_all
# ---------------------------------------------------------------------------


class TestApplyToAll:
    def test_returns_per_robot_configs(self) -> None:
        fm = FleetManager.from_config(_VALID_CONFIG)
        base = {"agent": {"confidence_gates": [{"threshold": 0.5}]}}
        results = fm.apply_to_all(
            ["RRN-000000000001", "RRN-000000000003", "RRN-000000000099"], base
        )
        assert len(results) == 3
        # prod robot gets 0.92
        assert results["RRN-000000000001"]["agent"]["confidence_gates"][0]["threshold"] == 0.92
        # staging robot gets 0.7
        assert results["RRN-000000000003"]["agent"]["confidence_gates"][0]["threshold"] == 0.7
        # unknown robot keeps base
        assert results["RRN-000000000099"]["agent"]["confidence_gates"][0]["threshold"] == 0.5


# ---------------------------------------------------------------------------
# Add / remove helpers
# ---------------------------------------------------------------------------


class TestMutationHelpers:
    def test_add_group(self) -> None:
        fm = FleetManager()
        gp = GroupPolicy(name="test", robots=["RRN-000000000001"])
        fm.add_group(gp)
        assert len(fm.list_groups()) == 1

    def test_remove_group(self) -> None:
        fm = FleetManager(groups=[GroupPolicy(name="a"), GroupPolicy(name="b")])
        removed = fm.remove_group("a")
        assert removed
        assert len(fm.list_groups()) == 1
        assert fm.list_groups()[0].name == "b"

    def test_remove_nonexistent_group(self) -> None:
        fm = FleetManager()
        assert not fm.remove_group("ghost")

    def test_add_robot_to_group(self) -> None:
        fm = FleetManager(groups=[GroupPolicy(name="prod", robots=[])])
        result = fm.add_robot_to_group("prod", "RRN-000000000001")
        assert result
        assert "RRN-000000000001" in fm.list_groups()[0].robots

    def test_add_robot_duplicate_ignored(self) -> None:
        fm = FleetManager(groups=[GroupPolicy(name="prod", robots=["RRN-000000000001"])])
        fm.add_robot_to_group("prod", "RRN-000000000001")
        assert fm.list_groups()[0].robots.count("RRN-000000000001") == 1

    def test_remove_robot_from_group(self) -> None:
        fm = FleetManager(groups=[GroupPolicy(name="prod", robots=["RRN-000000000001"])])
        result = fm.remove_robot_from_group("prod", "RRN-000000000001")
        assert result
        assert fm.list_groups()[0].robots == []

    def test_remove_robot_not_in_group(self) -> None:
        fm = FleetManager(groups=[GroupPolicy(name="prod", robots=[])])
        assert not fm.remove_robot_from_group("prod", "RRN-000000000099")


# ---------------------------------------------------------------------------
# _deep_merge
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_scalar_override(self) -> None:
        result = _deep_merge({"a": 1}, {"a": 2})
        assert result == {"a": 2}

    def test_nested_dict_merged(self) -> None:
        result = _deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 99}})
        assert result == {"a": {"x": 1, "y": 99}}

    def test_list_replaced_not_extended(self) -> None:
        result = _deep_merge({"a": [1, 2, 3]}, {"a": [4]})
        assert result == {"a": [4]}

    def test_new_key_added(self) -> None:
        result = _deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_base_not_mutated(self) -> None:
        base = {"a": {"x": 1}}
        _deep_merge(base, {"a": {"x": 2}})
        assert base == {"a": {"x": 1}}

"""Tests for castor/tools_ext/permissions.py and profiles.py."""

import pytest

from castor.tools_ext.permissions import PermissionMode, check_permission, get_tools_for_loa
from castor.tools_ext.profiles import get_profile, parse_profile_prefix


# --- permissions ---
def test_read_only_tool_accessible_at_loa0():
    ok, _ = check_permission("robot_status", 0)
    assert ok


def test_actuator_denied_at_loa0():
    ok, reason = check_permission("robot_navigate", 0)
    assert not ok
    assert "LoA 1" in reason


def test_actuator_allowed_at_loa1():
    ok, _ = check_permission("robot_drive", 1)
    assert ok


def test_safety_override_requires_loa3():
    ok, _ = check_permission("safety_override", 2)
    assert not ok
    ok2, _ = check_permission("safety_override", 3)
    assert ok2


def test_unknown_tool_defaults_read_only():
    ok, _ = check_permission("totally_unknown_tool", 0)
    assert ok


def test_get_tools_for_loa0_only_readonly():
    tools = get_tools_for_loa(0)
    assert "robot_status" in tools
    assert "robot_navigate" not in tools


# --- profiles ---
def test_deep_profile_fields():
    p = get_profile("deep")
    assert p.model == "claude-opus-4-6"
    assert p.thinking_budget == 10000
    assert p.max_turns == 25
    assert p.isolated is True


def test_quick_profile_fields():
    p = get_profile("quick")
    assert p.thinking_budget == 0
    assert p.tool_permission == PermissionMode.READ_ONLY
    assert p.max_turns == 3
    assert p.isolated is False


def test_parse_prefix_deep():
    name, text = parse_profile_prefix("$deep debug the nav stack")
    assert name == "deep"
    assert text == "debug the nav stack"


def test_parse_prefix_quick():
    name, text = parse_profile_prefix("$quick what is cpu temp")
    assert name == "quick"
    assert "cpu" in text


def test_parse_no_prefix():
    name, text = parse_profile_prefix("plain message")
    assert name is None
    assert text == "plain message"


def test_get_profile_unknown_raises():
    with pytest.raises(ValueError):
        get_profile("nonexistent")

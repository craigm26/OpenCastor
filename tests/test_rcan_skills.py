"""tests/test_rcan_skills.py — Tests for RCAN skill module (issue #791)."""

from __future__ import annotations

import pytest

from castor.skills.rcan_skills import RCAN_SKILLS, get_skill, list_skills


# ---------------------------------------------------------------------------
# 1. list_skills() returns all 5 skills
# ---------------------------------------------------------------------------


def test_list_skills_returns_five():
    skills = list_skills()
    assert len(skills) == 5


def test_list_skills_returns_list():
    skills = list_skills()
    assert isinstance(skills, list)


def test_list_skills_names():
    names = {s["name"] for s in list_skills()}
    expected = {"rcan_status", "rcan_telemetry", "rcan_navigate", "rcan_estop", "rcan_audit"}
    assert names == expected


# ---------------------------------------------------------------------------
# 2. get_skill() returns correct skill
# ---------------------------------------------------------------------------


def test_get_skill_rcan_status():
    skill = get_skill("rcan_status")
    assert skill is not None
    assert skill["name"] == "rcan_status"


def test_get_skill_rcan_estop():
    skill = get_skill("rcan_estop")
    assert skill is not None
    assert skill["name"] == "rcan_estop"


def test_get_skill_invalid_returns_none():
    assert get_skill("nonexistent_skill") is None


def test_get_skill_empty_string_returns_none():
    assert get_skill("") is None


# ---------------------------------------------------------------------------
# 3. LoA requirements are correct
# ---------------------------------------------------------------------------


def test_loa_rcan_status_is_zero():
    assert get_skill("rcan_status")["loa_required"] == 0


def test_loa_rcan_telemetry_is_zero():
    assert get_skill("rcan_telemetry")["loa_required"] == 0


def test_loa_rcan_navigate_is_one():
    assert get_skill("rcan_navigate")["loa_required"] == 1


def test_loa_rcan_estop_is_zero():
    assert get_skill("rcan_estop")["loa_required"] == 0


def test_loa_rcan_audit_is_one():
    assert get_skill("rcan_audit")["loa_required"] == 1


# ---------------------------------------------------------------------------
# 4. rcan_estop handler returns SAFETY message type
# ---------------------------------------------------------------------------


def test_rcan_estop_handler_message_type():
    skill = get_skill("rcan_estop")
    result = skill["handler"]({}, {})
    assert result["rcan_message_type"] == "SAFETY"


def test_rcan_estop_handler_command():
    skill = get_skill("rcan_estop")
    result = skill["handler"]({}, {})
    assert result["command"] == "ESTOP"


def test_rcan_estop_handler_status_ok():
    skill = get_skill("rcan_estop")
    result = skill["handler"]({}, {})
    assert result["status"] == "ok"


def test_rcan_estop_handler_custom_reason():
    skill = get_skill("rcan_estop")
    result = skill["handler"]({}, {"reason": "operator override"})
    assert result["reason"] == "operator override"


# ---------------------------------------------------------------------------
# 5. Other handler smoke tests
# ---------------------------------------------------------------------------


def test_rcan_status_handler_message_type():
    skill = get_skill("rcan_status")
    result = skill["handler"]({}, {})
    assert result["rcan_message_type"] == "DISCOVER"
    assert result["status"] == "ok"


def test_rcan_telemetry_handler_message_type():
    skill = get_skill("rcan_telemetry")
    result = skill["handler"]({}, {})
    assert result["rcan_message_type"] == "SENSOR_DATA"
    assert result["status"] == "ok"
    assert "timestamp" in result


def test_rcan_navigate_handler_message_type():
    skill = get_skill("rcan_navigate")
    result = skill["handler"]({}, {"x": 1.0, "y": 2.5, "z": 0.0, "frame": "odom"})
    assert result["rcan_message_type"] == "COMMAND"
    assert result["command"] == "NAVIGATE"
    assert result["waypoint"]["x"] == pytest.approx(1.0)
    assert result["waypoint"]["frame"] == "odom"


def test_rcan_navigate_handler_defaults():
    skill = get_skill("rcan_navigate")
    result = skill["handler"]({}, {})
    assert result["waypoint"]["frame"] == "map"
    assert result["waypoint"]["x"] == pytest.approx(0.0)


def test_rcan_audit_handler_missing_log():
    skill = get_skill("rcan_audit")
    result = skill["handler"]({}, {"log_path": "/nonexistent/audit.log"})
    assert result["status"] == "error"
    assert result["rcan_message_type"] == "EVENT"
    assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# 6. Skill dict structure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("skill_name", ["rcan_status", "rcan_telemetry", "rcan_navigate", "rcan_estop", "rcan_audit"])
def test_skill_has_required_keys(skill_name):
    skill = get_skill(skill_name)
    for key in ("name", "description", "rcan_message_type", "loa_required", "version", "handler"):
        assert key in skill, f"'{key}' missing from skill '{skill_name}'"


@pytest.mark.parametrize("skill_name", ["rcan_status", "rcan_telemetry", "rcan_navigate", "rcan_estop", "rcan_audit"])
def test_skill_handler_is_callable(skill_name):
    skill = get_skill(skill_name)
    assert callable(skill["handler"])


# ---------------------------------------------------------------------------
# 7. RCAN_SKILLS constant
# ---------------------------------------------------------------------------


def test_rcan_skills_constant_length():
    assert len(RCAN_SKILLS) == 5


def test_rcan_skills_constant_is_list():
    assert isinstance(RCAN_SKILLS, list)


# ---------------------------------------------------------------------------
# 8. list_skills returns independent copy
# ---------------------------------------------------------------------------


def test_list_skills_returns_copy():
    skills_a = list_skills()
    skills_b = list_skills()
    skills_a.clear()
    assert len(skills_b) == 5


# ---------------------------------------------------------------------------
# 9. Skills module importable from castor.skills package
# ---------------------------------------------------------------------------


def test_package_exports():
    from castor.skills import RCAN_SKILLS as _RC, get_skill as _gs, list_skills as _ls

    assert callable(_ls)
    assert callable(_gs)
    assert isinstance(_RC, list)


# ---------------------------------------------------------------------------
# 10. rcan_status uses config rrn when present
# ---------------------------------------------------------------------------


def test_rcan_status_uses_config_rrn():
    skill = get_skill("rcan_status")
    config = {"rcan_protocol": {"rrn": "rrn:test:robot-42", "loa": 1, "version": "2.0"}}
    result = skill["handler"](config, {})
    assert result["rrn"] == "rrn:test:robot-42"
    assert result["loa"] == 1
    assert result["rcan_version"] == "2.0"

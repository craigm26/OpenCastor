"""Tests for the Safety Protocol Engine."""

from __future__ import annotations

from pathlib import Path

from castor.safety.protocol import (
    SafetyProtocol,
    check_write_protocol,
)

# ---------------------------------------------------------------------------
# Default rules loading
# ---------------------------------------------------------------------------


class TestDefaultRules:
    def test_all_ten_rules_loaded(self):
        proto = SafetyProtocol()
        assert len(proto.rules) == 10

    def test_expected_rule_ids(self):
        proto = SafetyProtocol()
        expected = {
            "MOTION_001", "MOTION_002", "MOTION_003",
            "FORCE_001", "WORKSPACE_001", "THERMAL_001",
            "SOFTWARE_001", "EMERGENCY_001", "PROPERTY_001", "PRIVACY_001",
        }
        assert set(proto.rules.keys()) == expected

    def test_all_enabled_by_default(self):
        proto = SafetyProtocol()
        for rule in proto.rules.values():
            assert rule.enabled is True

    def test_list_rules_returns_all(self):
        proto = SafetyProtocol()
        table = proto.list_rules()
        assert len(table) == 10
        assert all("rule_id" in row for row in table)
        assert all("params" in row for row in table)


# ---------------------------------------------------------------------------
# YAML config overrides
# ---------------------------------------------------------------------------


class TestYAMLConfig:
    def _write_yaml(self, tmp: Path, content: str) -> str:
        p = tmp / "protocol.yaml"
        p.write_text(content)
        return str(p)

    def test_override_param(self, tmp_path):
        yaml_content = """\
safety_protocol:
  version: 1
  rules:
    MOTION_001:
      enabled: true
      params:
        max_velocity_ms: 0.5
"""
        path = self._write_yaml(tmp_path, yaml_content)
        proto = SafetyProtocol(config_path=path)
        assert proto.rules["MOTION_001"].params["max_velocity_ms"] == 0.5

    def test_disable_rule_via_config(self, tmp_path):
        yaml_content = """\
safety_protocol:
  version: 1
  rules:
    PRIVACY_001:
      enabled: false
"""
        path = self._write_yaml(tmp_path, yaml_content)
        proto = SafetyProtocol(config_path=path)
        assert proto.rules["PRIVACY_001"].enabled is False

    def test_override_thermal_params(self, tmp_path):
        yaml_content = """\
safety_protocol:
  version: 1
  rules:
    THERMAL_001:
      enabled: true
      params:
        warn_temp_c: 60
        critical_temp_c: 80
"""
        path = self._write_yaml(tmp_path, yaml_content)
        proto = SafetyProtocol(config_path=path)
        assert proto.rules["THERMAL_001"].params["warn_temp_c"] == 60
        assert proto.rules["THERMAL_001"].params["critical_temp_c"] == 80

    def test_override_severity(self, tmp_path):
        yaml_content = """\
safety_protocol:
  version: 1
  rules:
    MOTION_001:
      severity: critical
"""
        path = self._write_yaml(tmp_path, yaml_content)
        proto = SafetyProtocol(config_path=path)
        assert proto.rules["MOTION_001"].severity == "critical"

    def test_unknown_rule_ignored(self, tmp_path):
        yaml_content = """\
safety_protocol:
  version: 1
  rules:
    BOGUS_999:
      enabled: false
"""
        path = self._write_yaml(tmp_path, yaml_content)
        proto = SafetyProtocol(config_path=path)
        assert "BOGUS_999" not in proto.rules

    def test_multiple_overrides(self, tmp_path):
        yaml_content = """\
safety_protocol:
  version: 1
  rules:
    MOTION_001:
      params:
        max_velocity_ms: 0.3
    MOTION_002:
      params:
        max_angular_velocity_rads: 1.0
    FORCE_001:
      params:
        max_force_n: 30.0
        max_force_human_n: 5.0
"""
        path = self._write_yaml(tmp_path, yaml_content)
        proto = SafetyProtocol(config_path=path)
        assert proto.rules["MOTION_001"].params["max_velocity_ms"] == 0.3
        assert proto.rules["MOTION_002"].params["max_angular_velocity_rads"] == 1.0
        assert proto.rules["FORCE_001"].params["max_force_n"] == 30.0
        assert proto.rules["FORCE_001"].params["max_force_human_n"] == 5.0


# ---------------------------------------------------------------------------
# Rule checks
# ---------------------------------------------------------------------------


class TestMotionRules:
    def test_linear_velocity_ok(self):
        proto = SafetyProtocol()
        v = proto.check_action({"linear_velocity": 0.5})
        assert v == []

    def test_linear_velocity_violation(self):
        proto = SafetyProtocol()
        v = proto.check_action({"linear_velocity": 1.5})
        assert len(v) == 1
        assert v[0].rule_id == "MOTION_001"

    def test_linear_velocity_custom_limit(self, tmp_path):
        yaml = "safety_protocol:\n  rules:\n    MOTION_001:\n      params:\n        max_velocity_ms: 0.3\n"
        p = tmp_path / "p.yaml"
        p.write_text(yaml)
        proto = SafetyProtocol(config_path=str(p))
        assert proto.check_action({"linear_velocity": 0.4}) != []
        assert proto.check_action({"linear_velocity": 0.2}) == []

    def test_angular_velocity_violation(self):
        proto = SafetyProtocol()
        v = proto.check_action({"angular_velocity": 3.0})
        assert len(v) == 1
        assert v[0].rule_id == "MOTION_002"

    def test_estop_response_critical(self):
        proto = SafetyProtocol()
        v = proto.check_action({"estop_response_ms": 150})
        assert len(v) == 1
        assert v[0].severity == "critical"


class TestForceRules:
    def test_force_ok(self):
        proto = SafetyProtocol()
        assert proto.check_action({"contact_force": 30}) == []

    def test_force_violation(self):
        proto = SafetyProtocol()
        v = proto.check_action({"contact_force": 60})
        assert len(v) == 1
        assert v[0].rule_id == "FORCE_001"

    def test_force_human_nearby(self):
        proto = SafetyProtocol()
        v = proto.check_action({"contact_force": 15, "human_nearby": True})
        assert len(v) == 1
        assert v[0].severity == "critical"

    def test_force_human_ok(self):
        proto = SafetyProtocol()
        assert proto.check_action({"contact_force": 8, "human_nearby": True}) == []


class TestThermalRules:
    def test_temp_ok(self):
        proto = SafetyProtocol()
        assert proto.check_action({"temperature_c": 50}) == []

    def test_temp_warning(self):
        proto = SafetyProtocol()
        v = proto.check_action({"temperature_c": 85})
        assert len(v) == 1
        assert v[0].severity == "warning"

    def test_temp_critical(self):
        proto = SafetyProtocol()
        v = proto.check_action({"temperature_c": 95})
        assert len(v) == 1
        assert v[0].severity == "critical"

    def test_temp_custom_thresholds(self, tmp_path):
        yaml = "safety_protocol:\n  rules:\n    THERMAL_001:\n      params:\n        warn_temp_c: 50\n        critical_temp_c: 70\n"
        p = tmp_path / "p.yaml"
        p.write_text(yaml)
        proto = SafetyProtocol(config_path=str(p))
        assert proto.check_action({"temperature_c": 55})[0].severity == "warning"
        assert proto.check_action({"temperature_c": 75})[0].severity == "critical"


class TestOtherRules:
    def test_watchdog_timeout(self):
        proto = SafetyProtocol()
        v = proto.check_action({"watchdog_elapsed_ms": 200})
        assert len(v) == 1
        assert v[0].rule_id == "SOFTWARE_001"

    def test_estop_unavailable(self):
        proto = SafetyProtocol()
        v = proto.check_action({"estop_available": False})
        assert len(v) == 1
        assert v[0].rule_id == "EMERGENCY_001"

    def test_estop_available_ok(self):
        proto = SafetyProtocol()
        assert proto.check_action({"estop_available": True}) == []

    def test_destructive_no_auth(self):
        proto = SafetyProtocol()
        v = proto.check_action({"destructive": True, "authorized": False})
        assert len(v) == 1
        assert v[0].rule_id == "PROPERTY_001"

    def test_destructive_with_auth(self):
        proto = SafetyProtocol()
        assert proto.check_action({"destructive": True, "authorized": True}) == []

    def test_sensor_no_consent(self):
        proto = SafetyProtocol()
        v = proto.check_action({"sensor_active": True, "consent_granted": False})
        assert len(v) == 1
        assert v[0].rule_id == "PRIVACY_001"

    def test_sensor_with_consent(self):
        proto = SafetyProtocol()
        assert proto.check_action({"sensor_active": True, "consent_granted": True}) == []

    def test_workspace_bounds(self):
        proto = SafetyProtocol()
        proto.rules["WORKSPACE_001"].params["bounds"] = {
            "x_min": -1, "x_max": 1, "y_min": -1, "y_max": 1, "z_min": 0, "z_max": 2,
        }
        v = proto.check_action({"position": [5.0, 0.0, 1.0]})
        assert len(v) == 1
        assert v[0].rule_id == "WORKSPACE_001"

    def test_workspace_ok(self):
        proto = SafetyProtocol()
        proto.rules["WORKSPACE_001"].params["bounds"] = {
            "x_min": -1, "x_max": 1, "y_min": -1, "y_max": 1,
        }
        assert proto.check_action({"position": [0.0, 0.0, 0.0]}) == []


# ---------------------------------------------------------------------------
# Enable/disable with audit
# ---------------------------------------------------------------------------


class TestEnableDisable:
    def test_disable_rule(self):
        proto = SafetyProtocol()
        assert proto.disable_rule("MOTION_001")
        assert proto.rules["MOTION_001"].enabled is False
        # Should not fire when disabled
        assert proto.check_action({"linear_velocity": 99.0}) == []

    def test_enable_rule(self):
        proto = SafetyProtocol()
        proto.disable_rule("MOTION_001")
        assert proto.enable_rule("MOTION_001")
        assert proto.rules["MOTION_001"].enabled is True
        v = proto.check_action({"linear_velocity": 99.0})
        assert len(v) == 1

    def test_disable_unknown_rule(self):
        proto = SafetyProtocol()
        assert proto.disable_rule("BOGUS") is False

    def test_audit_logged(self):
        proto = SafetyProtocol()
        proto.disable_rule("MOTION_001")
        proto.enable_rule("MOTION_001")
        log = proto.get_audit_log()
        events = [e["event"] for e in log]
        assert "disable_rule" in events
        assert "enable_rule" in events


# ---------------------------------------------------------------------------
# Violations summary
# ---------------------------------------------------------------------------


class TestViolationsSummary:
    def test_summary(self):
        proto = SafetyProtocol()
        proto.check_action({"linear_velocity": 5.0})
        proto.check_action({"temperature_c": 95})
        proto.check_action({"linear_velocity": 5.0})
        summary = proto.get_violations_summary()
        assert summary["motion"] == 2
        assert summary["thermal"] == 1


# ---------------------------------------------------------------------------
# Integration: check_write_protocol
# ---------------------------------------------------------------------------


class TestWriteProtocol:
    def test_motor_velocity(self):
        proto = SafetyProtocol()
        v = check_write_protocol(proto, "/dev/motor/left", {"velocity": 5.0})
        assert len(v) >= 1
        assert any(r.rule_id == "MOTION_001" for r in v)

    def test_sensor_consent(self):
        proto = SafetyProtocol()
        v = check_write_protocol(proto, "/dev/sensor/camera", {"consent": False})
        assert any(r.rule_id == "PRIVACY_001" for r in v)

    def test_gpio_destructive(self):
        proto = SafetyProtocol()
        v = check_write_protocol(proto, "/dev/gpio/cutter", {"authorized": False})
        assert any(r.rule_id == "PROPERTY_001" for r in v)

    def test_non_dict_data(self):
        proto = SafetyProtocol()
        assert check_write_protocol(proto, "/dev/motor/left", "raw") == []

    def test_non_dev_path(self):
        proto = SafetyProtocol()
        assert check_write_protocol(proto, "/tmp/foo", {"velocity": 99}) == []


# ---------------------------------------------------------------------------
# Multiple violations in one action
# ---------------------------------------------------------------------------


class TestMultipleViolations:
    def test_combined(self):
        proto = SafetyProtocol()
        v = proto.check_action({
            "linear_velocity": 5.0,
            "angular_velocity": 10.0,
            "temperature_c": 95,
        })
        ids = {r.rule_id for r in v}
        assert "MOTION_001" in ids
        assert "MOTION_002" in ids
        assert "THERMAL_001" in ids


# ---------------------------------------------------------------------------
# Empty / no-op actions
# ---------------------------------------------------------------------------


class TestNoOp:
    def test_empty_action(self):
        proto = SafetyProtocol()
        assert proto.check_action({}) == []

    def test_irrelevant_keys(self):
        proto = SafetyProtocol()
        assert proto.check_action({"foo": "bar", "baz": 42}) == []

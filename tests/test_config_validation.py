"""Tests for castor.config_validation — RCAN config validation (#71)."""

import pytest

from castor.config_validation import log_validation_result, validate_rcan_config

# ---------------------------------------------------------------------------
# Minimal valid config fixture
# ---------------------------------------------------------------------------

_VALID = {
    "rcan_version": "1.0",
    "metadata": {"robot_name": "TestBot"},
    "agent": {"model": "gpt-4o"},
    "physics": {},
    "drivers": [{"type": "pca9685"}],
    "network": {},
    "rcan_protocol": {},
}


def _make_config(**overrides):
    cfg = dict(_VALID)
    cfg.update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------

class TestValidConfig:
    def test_valid_config_returns_true(self):
        ok, errors = validate_rcan_config(_VALID)
        assert ok is True
        assert errors == []

    def test_extra_keys_are_allowed(self):
        cfg = _make_config(custom_field="hello")
        ok, _ = validate_rcan_config(cfg)
        assert ok is True


# ---------------------------------------------------------------------------
# Top-level keys
# ---------------------------------------------------------------------------

class TestTopLevelKeys:
    @pytest.mark.parametrize(
        "missing_key",
        [
            "rcan_version",
            "metadata",
            "agent",
            "physics",
            "drivers",
            "network",
            "rcan_protocol",
        ],
    )
    def test_missing_top_level_key(self, missing_key):
        cfg = {k: v for k, v in _VALID.items() if k != missing_key}
        ok, errors = validate_rcan_config(cfg)
        assert ok is False
        assert any(missing_key in e for e in errors)

    def test_not_a_dict_returns_false(self):
        ok, errors = validate_rcan_config("not a dict")
        assert ok is False
        assert len(errors) == 1


# ---------------------------------------------------------------------------
# agent block
# ---------------------------------------------------------------------------

class TestAgentBlock:
    def test_missing_model_key(self):
        cfg = _make_config(agent={})
        ok, errors = validate_rcan_config(cfg)
        assert ok is False
        assert any("agent.model" in e for e in errors)

    def test_empty_model_string(self):
        cfg = _make_config(agent={"model": ""})
        ok, errors = validate_rcan_config(cfg)
        assert ok is False
        assert any("agent.model" in e for e in errors)

    def test_agent_not_a_dict(self):
        cfg = _make_config(agent="gpt-4o")
        ok, errors = validate_rcan_config(cfg)
        assert ok is False
        assert any("agent" in e for e in errors)


# ---------------------------------------------------------------------------
# metadata block
# ---------------------------------------------------------------------------

class TestMetadataBlock:
    def test_missing_robot_name(self):
        cfg = _make_config(metadata={})
        ok, errors = validate_rcan_config(cfg)
        assert ok is False
        assert any("metadata.robot_name" in e for e in errors)

    def test_empty_robot_name(self):
        cfg = _make_config(metadata={"robot_name": ""})
        ok, errors = validate_rcan_config(cfg)
        assert ok is False

    def test_metadata_not_a_dict(self):
        cfg = _make_config(metadata="TestBot")
        ok, errors = validate_rcan_config(cfg)
        assert ok is False


# ---------------------------------------------------------------------------
# drivers block
# ---------------------------------------------------------------------------

class TestDriversBlock:
    def test_empty_drivers_list(self):
        cfg = _make_config(drivers=[])
        ok, errors = validate_rcan_config(cfg)
        assert ok is False
        assert any("drivers" in e for e in errors)

    def test_drivers_not_a_list(self):
        cfg = _make_config(drivers={"type": "pca9685"})
        ok, errors = validate_rcan_config(cfg)
        assert ok is False

    def test_drivers_with_multiple_entries_ok(self):
        cfg = _make_config(drivers=[{"type": "pca9685"}, {"type": "dynamixel"}])
        ok, _ = validate_rcan_config(cfg)
        assert ok is True


# ---------------------------------------------------------------------------
# log_validation_result helper
# ---------------------------------------------------------------------------

class TestLogValidationResult:
    def test_returns_true_for_valid(self):
        assert log_validation_result(_VALID) is True

    def test_returns_false_for_invalid(self):
        assert log_validation_result({}) is False

    def test_uses_label_in_log(self, caplog):
        import logging

        with caplog.at_level(logging.ERROR, logger="OpenCastor.ConfigValidation"):
            log_validation_result({}, label="MyRobot")
        assert "MyRobot" in caplog.text

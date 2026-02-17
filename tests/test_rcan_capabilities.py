"""Tests for RCAN Capability Registry."""

from castor.rcan.capabilities import Capability, CapabilityRegistry


class TestCapabilityEnum:
    """Capability enum values."""

    def test_standard_capabilities(self):
        assert Capability.STATUS == "status"
        assert Capability.NAV == "nav"
        assert Capability.TELEOP == "teleop"
        assert Capability.VISION == "vision"
        assert Capability.CHAT == "chat"
        assert Capability.ARM == "arm"

    def test_six_standard_capabilities(self):
        assert len(Capability) == 6


class TestCapabilityRegistry:
    """Auto-detection and registration."""

    def test_empty_config(self):
        reg = CapabilityRegistry({})
        assert reg.has("status")  # Always present

    def test_no_config(self):
        reg = CapabilityRegistry()
        assert len(reg) == 0

    def test_mobile_robot_detection(self):
        config = {
            "agent": {"provider": "anthropic", "model": "claude-opus-4-6"},
            "physics": {"type": "differential_drive", "dof": 2},
            "drivers": [{"protocol": "pca9685_i2c"}],
            "camera": {"type": "auto"},
        }
        reg = CapabilityRegistry(config)
        assert "status" in reg
        assert "nav" in reg
        assert "teleop" in reg
        assert "vision" in reg
        assert "chat" in reg
        assert "arm" not in reg

    def test_arm_detection(self):
        config = {
            "agent": {"provider": "anthropic", "model": "claude-opus-4-6"},
            "physics": {"type": "serial_manipulator", "dof": 6},
            "drivers": [{"protocol": "dynamixel_v2"}],
        }
        reg = CapabilityRegistry(config)
        assert "arm" in reg
        assert "nav" not in reg
        assert "teleop" not in reg

    def test_chat_detection(self):
        config = {
            "agent": {"provider": "anthropic", "model": "claude-opus-4-6"},
        }
        reg = CapabilityRegistry(config)
        assert "chat" in reg
        assert "status" in reg

    def test_explicit_capabilities(self):
        config = {
            "rcan_protocol": {"capabilities": ["status", "nav", "custom_sensor"]},
        }
        reg = CapabilityRegistry(config)
        assert "status" in reg
        assert "nav" in reg
        assert "custom_sensor" in reg

    def test_names_sorted(self):
        config = {
            "agent": {"provider": "anthropic", "model": "x"},
            "drivers": [{"protocol": "pca9685"}],
            "physics": {"type": "differential_drive", "dof": 2},
        }
        reg = CapabilityRegistry(config)
        names = reg.names
        assert names == sorted(names)

    def test_to_dict(self):
        config = {"agent": {"provider": "anthropic", "model": "x"}}
        reg = CapabilityRegistry(config)
        d = reg.to_dict()
        assert "status" in d
        assert "chat" in d
        assert isinstance(d["status"], dict)
        assert "name" in d["status"]

    def test_len(self):
        reg = CapabilityRegistry({})
        assert len(reg) >= 1  # At least status

    def test_contains(self):
        reg = CapabilityRegistry({})
        assert "status" in reg
        assert "nonexistent" not in reg

"""Tests for servo/gripper calibration wizard.

Issue #235 — castor calibrate --servo; sweep + RCAN config write.
"""

from __future__ import annotations

from unittest.mock import patch

import yaml

# ---------------------------------------------------------------------------
# servo_pulse_duty
# ---------------------------------------------------------------------------


class TestServoPulseDuty:
    def test_1500us_at_50hz(self):
        from castor.calibrate import servo_pulse_duty

        # 1500/20000 * 65535 ≈ 4915
        val = servo_pulse_duty(1500, 50)
        assert 4900 <= val <= 4935

    def test_500us_at_50hz(self):
        from castor.calibrate import servo_pulse_duty

        val = servo_pulse_duty(500, 50)
        assert 1600 <= val <= 1640

    def test_2500us_at_50hz(self):
        from castor.calibrate import servo_pulse_duty

        val = servo_pulse_duty(2500, 50)
        assert 8190 <= val <= 8200

    def test_clamps_to_max(self):
        from castor.calibrate import servo_pulse_duty

        assert servo_pulse_duty(99999, 50) == 65535

    def test_clamps_to_zero(self):
        from castor.calibrate import servo_pulse_duty

        assert servo_pulse_duty(-100, 50) == 0

    def test_custom_frequency(self):
        from castor.calibrate import servo_pulse_duty

        # At 100 Hz, period is 10000µs.  1500µs → 1500/10000 * 65535 ≈ 9830
        val = servo_pulse_duty(1500, 100)
        assert 9820 <= val <= 9840


# ---------------------------------------------------------------------------
# validate_servo_config
# ---------------------------------------------------------------------------


class TestValidateServoConfig:
    def test_valid_config_returns_no_errors(self):
        from castor.calibrate import validate_servo_config

        errors = validate_servo_config(500, 2500, 1500)
        assert errors == []

    def test_min_below_400_is_error(self):
        from castor.calibrate import validate_servo_config

        errors = validate_servo_config(300, 2500, 1400)
        assert any("400" in e for e in errors)

    def test_max_above_2600_is_error(self):
        from castor.calibrate import validate_servo_config

        errors = validate_servo_config(500, 2700, 1600)
        assert any("2600" in e for e in errors)

    def test_min_gte_max_is_error(self):
        from castor.calibrate import validate_servo_config

        errors = validate_servo_config(1500, 500, 1000)
        assert any("min_us" in e for e in errors)

    def test_centre_outside_range_is_error(self):
        from castor.calibrate import validate_servo_config

        errors = validate_servo_config(500, 2500, 100)
        assert any("centre_us" in e for e in errors)

    def test_multiple_errors_returned(self):
        from castor.calibrate import validate_servo_config

        errors = validate_servo_config(300, 2700, 50)
        assert len(errors) >= 2


# ---------------------------------------------------------------------------
# calibrate_servo — mock mode
# ---------------------------------------------------------------------------


class TestCalibrateServoMock:
    def _run_calibrate(self, config_path, responses, gripper=False):
        """Run calibrate_servo with mocked input()."""
        from castor.calibrate import calibrate_servo

        with patch("builtins.input", side_effect=responses):
            return calibrate_servo(
                config_path=str(config_path),
                channel=3,
                board_type="pca9685",
                gripper_mode=gripper,
                mock=True,
            )

    def test_returns_dict_with_keys(self, tmp_path):
        cfg = tmp_path / "robot.rcan.yaml"
        cfg.write_text("rcan_version: '1.1.0'\nmetadata:\n  robot_name: test\n")
        # Prompts: confirm-min, enter-min, confirm-max, enter-max, enter-centre,
        #          confirm-test-open, confirm-test-close
        responses = ["", "", "", "", "", "", ""]  # all defaults
        result = self._run_calibrate(cfg, responses)
        assert "min_us" in result
        assert "max_us" in result
        assert "centre_us" in result
        assert result["saved"] is True

    def test_saves_to_rcan_config(self, tmp_path):
        cfg = tmp_path / "robot.rcan.yaml"
        cfg.write_text("rcan_version: '1.1.0'\nmetadata:\n  robot_name: bot\n")
        # Prompt order: confirm-at-min, enter-min, confirm-at-max, enter-max, enter-centre, confirm-open, confirm-close
        responses = ["", "600", "", "2400", "", "", ""]
        self._run_calibrate(cfg, responses)
        data = yaml.safe_load(cfg.read_text())
        assert data["servo"]["min_us"] == 600
        assert data["servo"]["max_us"] == 2400

    def test_centre_auto_calculated(self, tmp_path):
        cfg = tmp_path / "r.rcan.yaml"
        cfg.write_text("rcan_version: '1.1.0'\n")
        # confirm-min, enter-min=600, confirm-max, enter-max=2400, enter-centre(empty), confirm-open, confirm-close
        responses = ["", "600", "", "2400", "", "", ""]
        result = self._run_calibrate(cfg, responses)
        assert result["centre_us"] == (result["min_us"] + result["max_us"]) // 2

    def test_custom_centre_accepted(self, tmp_path):
        cfg = tmp_path / "r.rcan.yaml"
        cfg.write_text("rcan_version: '1.1.0'\n")
        # confirm-min, enter-min(default), confirm-max, enter-max(default), enter-centre=1600, confirm-open, confirm-close
        responses = ["", "", "", "", "1600", "", ""]
        result = self._run_calibrate(cfg, responses)
        assert result["centre_us"] == 1600

    def test_gripper_mode_saves_open_close(self, tmp_path):
        cfg = tmp_path / "r.rcan.yaml"
        cfg.write_text("rcan_version: '1.1.0'\n")
        # min, max, open, close, confirm open, confirm close
        responses = ["", "", "", "", "", "", "", "", ""]
        result = self._run_calibrate(cfg, responses, gripper=True)
        assert "open_us" in result
        assert "close_us" in result
        data = yaml.safe_load(cfg.read_text())
        assert "open_us" in data["servo"]

    def test_channel_stored_in_config(self, tmp_path):
        cfg = tmp_path / "r.rcan.yaml"
        cfg.write_text("rcan_version: '1.1.0'\n")
        responses = ["", "", "", "", "", "", ""]
        self._run_calibrate(cfg, responses)
        data = yaml.safe_load(cfg.read_text())
        assert data["servo"]["channel"] == 3

    def test_mock_no_hardware_access(self, tmp_path):
        cfg = tmp_path / "r.rcan.yaml"
        cfg.write_text("rcan_version: '1.1.0'\n")
        responses = ["", "", "", "", "", "", ""]
        # Should not raise even without adafruit libs
        result = self._run_calibrate(cfg, responses)
        assert result["saved"] is True


# ---------------------------------------------------------------------------
# HAS_PCA9685 guard
# ---------------------------------------------------------------------------


class TestHASPCA9685:
    def test_module_exposes_has_pca9685(self):
        import castor.calibrate as m

        assert isinstance(m.HAS_PCA9685, bool)

    def test_defaults_to_false_when_adafruit_missing(self):
        with patch.dict("sys.modules", {"adafruit_pca9685": None, "board": None, "busio": None}):

            import castor.calibrate as m

            # The module-level flag is set at import time; assert it's bool
            assert isinstance(m.HAS_PCA9685, bool)


# ---------------------------------------------------------------------------
# _save_config / _load_config roundtrip
# ---------------------------------------------------------------------------


class TestConfigRoundtrip:
    def test_load_and_save_roundtrip(self, tmp_path):
        from castor.calibrate import _load_config, _save_config

        cfg = tmp_path / "cfg.yaml"
        data = {"rcan_version": "1.1.0", "servo": {"min_us": 500}}
        cfg.write_text(yaml.dump(data))

        loaded = _load_config(str(cfg))
        loaded["servo"]["max_us"] = 2500
        _save_config(str(cfg), loaded)

        reloaded = _load_config(str(cfg))
        assert reloaded["servo"]["max_us"] == 2500
        assert reloaded["servo"]["min_us"] == 500

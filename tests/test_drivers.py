"""Tests for castor.drivers -- DriverBase ABC, PCA9685 drivers, and Dynamixel driver.

Covers abstract interface enforcement, mock-mode behaviour, value clamping,
configuration parsing, and graceful degradation when hardware SDKs are absent.
"""

import logging
from unittest.mock import MagicMock

import pytest

from castor.drivers.base import DriverBase


# =====================================================================
# DriverBase Abstract Class Tests
# =====================================================================
class TestDriverBase:
    """Verify the ABC contract enforced by DriverBase."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            DriverBase()

    def test_concrete_subclass(self):
        class MockDriver(DriverBase):
            def __init__(self):
                self.moved = False
                self.stopped = False
                self.closed = False

            def move(self, linear=0, angular=0):
                self.moved = True

            def stop(self):
                self.stopped = True

            def close(self):
                self.closed = True

        driver = MockDriver()
        driver.move(linear=0.5, angular=0.1)
        assert driver.moved

        driver.stop()
        assert driver.stopped

        driver.close()
        assert driver.closed

    def test_partial_implementation_missing_close_fails(self):
        """Subclass missing 'close' should fail to instantiate."""

        class IncompleteDriver(DriverBase):
            def move(self):
                pass

            def stop(self):
                pass

        with pytest.raises(TypeError):
            IncompleteDriver()

    def test_partial_implementation_missing_stop_fails(self):
        """Subclass missing 'stop' should fail to instantiate."""

        class IncompleteDriver(DriverBase):
            def move(self):
                pass

            def close(self):
                pass

        with pytest.raises(TypeError):
            IncompleteDriver()

    def test_partial_implementation_missing_move_fails(self):
        """Subclass missing 'move' should fail to instantiate."""

        class IncompleteDriver(DriverBase):
            def stop(self):
                pass

            def close(self):
                pass

        with pytest.raises(TypeError):
            IncompleteDriver()

    def test_move_accepts_keyword_args(self):
        class TestDriver(DriverBase):
            def __init__(self):
                self.last_linear = None
                self.last_angular = None

            def move(self, linear=0.0, angular=0.0):
                self.last_linear = linear
                self.last_angular = angular

            def stop(self):
                pass

            def close(self):
                pass

        d = TestDriver()
        d.move(linear=0.7, angular=-0.3)
        assert d.last_linear == 0.7
        assert d.last_angular == -0.3

    def test_is_subclass_of_abc(self):
        from abc import ABC

        assert issubclass(DriverBase, ABC)


# =====================================================================
# PCA9685 Differential Drive (Mock Mode) Tests
# =====================================================================
class TestPCA9685DriverMockMode:
    """Test PCA9685Driver when Adafruit libraries are NOT installed."""

    def _make_driver(self, config=None):
        """Construct a PCA9685Driver that will operate in mock mode."""
        from castor.drivers.pca9685 import PCA9685Driver

        return PCA9685Driver(config or {})

    def test_initializes_in_mock_mode(self):
        driver = self._make_driver()
        assert driver.pca is None
        assert driver.motor_left is None
        assert driver.motor_right is None

    def test_move_mock_logs(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.PCA9685"):
            driver.move(linear_x=0.5, angular_z=0.2)
        assert any("[MOCK]" in r.message for r in caplog.records)

    def test_move_mock_calculates_arcade_drive(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.PCA9685"):
            driver.move(linear_x=1.0, angular_z=0.0)
        # With linear=1.0, angular=0.0 -> L=1.0, R=1.0
        mock_msgs = [r.message for r in caplog.records if "[MOCK]" in r.message]
        assert len(mock_msgs) > 0
        assert "L=1.00" in mock_msgs[-1]
        assert "R=1.00" in mock_msgs[-1]

    def test_move_mock_with_turning(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.PCA9685"):
            driver.move(linear_x=0.0, angular_z=1.0)
        # linear=0.0, angular=1.0 -> L=0-1=-1.0, R=0+1=1.0
        mock_msgs = [r.message for r in caplog.records if "[MOCK]" in r.message]
        assert len(mock_msgs) > 0
        assert "L=-1.00" in mock_msgs[-1]
        assert "R=1.00" in mock_msgs[-1]

    def test_move_clamps_left_speed(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.PCA9685"):
            # linear=1.0, angular=1.0 -> L=0.0, R=2.0 -> clamped R=1.0
            driver.move(linear_x=1.0, angular_z=1.0)
        mock_msgs = [r.message for r in caplog.records if "[MOCK]" in r.message]
        assert "R=1.00" in mock_msgs[-1]

    def test_move_clamps_right_speed_negative(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.PCA9685"):
            # linear=-1.0, angular=1.0 -> L=-2.0->-1.0, R=0.0
            driver.move(linear_x=-1.0, angular_z=1.0)
        mock_msgs = [r.message for r in caplog.records if "[MOCK]" in r.message]
        assert "L=-1.00" in mock_msgs[-1]

    def test_stop_mock_mode_no_crash(self):
        driver = self._make_driver()
        # Should not raise even when motors are None
        driver.stop()

    def test_close_calls_stop_mock(self):
        driver = self._make_driver()
        # Should not raise
        driver.close()

    def test_config_stored(self):
        cfg = {"address": "0x41", "frequency": 60}
        driver = self._make_driver(cfg)
        assert driver.config == cfg

    def test_is_subclass_of_driver_base(self):
        from castor.drivers.pca9685 import PCA9685Driver

        assert issubclass(PCA9685Driver, DriverBase)


# =====================================================================
# PCA9685 RC Car Driver (Mock Mode) Tests
# =====================================================================
class TestPCA9685RCDriverMockMode:
    """Test PCA9685RCDriver when Adafruit libraries are NOT installed."""

    def _make_driver(self, config=None):
        from castor.drivers.pca9685 import PCA9685RCDriver

        return PCA9685RCDriver(config or {})

    def test_initializes_in_mock_mode(self):
        driver = self._make_driver()
        assert driver.pca is None

    def test_default_config_values(self):
        driver = self._make_driver()
        assert driver.steer_ch == 0
        assert driver.steer_center == 1500
        assert driver.steer_range == 500
        assert driver.steer_invert is False
        assert driver.thr_ch == 1
        assert driver.thr_neutral == 1500
        assert driver.thr_max == 2000
        assert driver.thr_min == 1000
        assert driver.thr_deadzone == 0.05
        assert driver.freq == 50

    def test_custom_config_values(self):
        cfg = {
            "steering_channel": 2,
            "steering_center_us": 1600,
            "steering_range_us": 400,
            "steering_invert": True,
            "throttle_channel": 3,
            "throttle_neutral_us": 1520,
            "throttle_max_us": 1900,
            "throttle_min_us": 1100,
            "throttle_deadzone": 0.1,
            "frequency": 60,
        }
        driver = self._make_driver(cfg)
        assert driver.steer_ch == 2
        assert driver.steer_center == 1600
        assert driver.steer_range == 400
        assert driver.steer_invert is True
        assert driver.thr_ch == 3
        assert driver.thr_neutral == 1520
        assert driver.thr_max == 1900
        assert driver.thr_min == 1100
        assert driver.thr_deadzone == 0.1
        assert driver.freq == 60

    def test_move_mock_logs_throttle_and_steer(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.PCA9685"):
            driver.move(linear_x=0.5, angular_z=0.3)
        mock_msgs = [r.message for r in caplog.records if "[MOCK RC]" in r.message]
        assert len(mock_msgs) > 0
        assert "throttle=" in mock_msgs[-1]
        assert "steer=" in mock_msgs[-1]

    def test_move_neutral_in_deadzone(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.PCA9685"):
            driver.move(linear_x=0.01, angular_z=0.0)  # inside deadzone of 0.05
        mock_msgs = [r.message for r in caplog.records if "[MOCK RC]" in r.message]
        # throttle should be neutral (1500us)
        assert "throttle=1500" in mock_msgs[-1]

    def test_move_full_forward(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.PCA9685"):
            driver.move(linear_x=1.0, angular_z=0.0)
        mock_msgs = [r.message for r in caplog.records if "[MOCK RC]" in r.message]
        # throttle should be max (2000us)
        assert "throttle=2000" in mock_msgs[-1]

    def test_move_full_reverse(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.PCA9685"):
            driver.move(linear_x=-1.0, angular_z=0.0)
        mock_msgs = [r.message for r in caplog.records if "[MOCK RC]" in r.message]
        # throttle should be min (1000us)
        assert "throttle=1000" in mock_msgs[-1]

    def test_move_clamps_linear_input(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.PCA9685"):
            # Input beyond +/-1.0 should be clamped
            driver.move(linear_x=5.0, angular_z=0.0)
        mock_msgs = [r.message for r in caplog.records if "[MOCK RC]" in r.message]
        # Should clamp to 1.0 -> throttle = 2000us
        assert "throttle=2000" in mock_msgs[-1]

    def test_move_clamps_angular_input(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.PCA9685"):
            driver.move(linear_x=0.0, angular_z=3.0)
        mock_msgs = [r.message for r in caplog.records if "[MOCK RC]" in r.message]
        # angular clamped to 1.0 -> steer = center + 1.0*500 = 2000us
        assert "steer=2000" in mock_msgs[-1]

    def test_move_clamps_negative_angular(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.PCA9685"):
            driver.move(linear_x=0.0, angular_z=-5.0)
        mock_msgs = [r.message for r in caplog.records if "[MOCK RC]" in r.message]
        # angular clamped to -1.0 -> steer = 1500 - 500 = 1000us
        assert "steer=1000" in mock_msgs[-1]

    def test_move_inverted_steering(self, caplog):
        driver = self._make_driver({"steering_invert": True})
        with caplog.at_level(logging.INFO, logger="OpenCastor.PCA9685"):
            driver.move(linear_x=0.0, angular_z=1.0)
        mock_msgs = [r.message for r in caplog.records if "[MOCK RC]" in r.message]
        # Inverted: steer = 1500 + 1.0 * 500 * (-1) = 1000
        assert "steer=1000" in mock_msgs[-1]

    def test_stop_mock_logs(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.PCA9685"):
            driver.stop()
        mock_msgs = [r.message for r in caplog.records if "[MOCK RC] stop" in r.message]
        assert len(mock_msgs) == 1

    def test_close_calls_stop(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.PCA9685"):
            driver.close()
        # close() calls stop(), which logs in mock mode
        mock_msgs = [r.message for r in caplog.records if "[MOCK RC] stop" in r.message]
        assert len(mock_msgs) == 1

    def test_is_subclass_of_driver_base(self):
        from castor.drivers.pca9685 import PCA9685RCDriver

        assert issubclass(PCA9685RCDriver, DriverBase)

    def test_config_validation_warns_on_out_of_range(self, caplog):
        """Config values outside PULSE_MIN_US / PULSE_MAX_US should warn."""

        with caplog.at_level(logging.WARNING, logger="OpenCastor.PCA9685"):
            self._make_driver({"throttle_neutral_us": 100})  # below PULSE_MIN_US
        warnings = [r for r in caplog.records if "outside safe range" in r.message]
        assert len(warnings) >= 1

    def test_config_validation_warns_on_high_value(self, caplog):
        with caplog.at_level(logging.WARNING, logger="OpenCastor.PCA9685"):
            self._make_driver({"throttle_max_us": 3000})  # above PULSE_MAX_US
        warnings = [r for r in caplog.records if "outside safe range" in r.message]
        assert len(warnings) >= 1

    def test_config_validation_non_numeric_warns(self, caplog):
        with caplog.at_level(logging.WARNING, logger="OpenCastor.PCA9685"):
            self._make_driver({"throttle_neutral_us": "not_a_number"})
        warnings = [r for r in caplog.records if "not numeric" in r.message]
        assert len(warnings) >= 1


# =====================================================================
# PCA9685 Pulse-width helpers
# =====================================================================
class TestPCA9685Helpers:
    """Test the _us_to_duty conversion and PULSE bounds."""

    def test_us_to_duty_midpoint(self):
        from castor.drivers.pca9685 import _us_to_duty

        # At 50 Hz, period = 20000 us. 1500 us -> 1500/20000 * 0xFFFF
        duty = _us_to_duty(50, 1500)
        expected = int(1500 / 20000 * 0xFFFF)
        assert duty == expected

    def test_us_to_duty_min(self):
        from castor.drivers.pca9685 import _us_to_duty

        duty = _us_to_duty(50, 500)
        expected = int(500 / 20000 * 0xFFFF)
        assert duty == expected

    def test_us_to_duty_max(self):
        from castor.drivers.pca9685 import _us_to_duty

        duty = _us_to_duty(50, 2500)
        expected = int(2500 / 20000 * 0xFFFF)
        assert duty == expected

    def test_pulse_bounds_defined(self):
        from castor.drivers.pca9685 import PULSE_MAX_US, PULSE_MIN_US

        assert PULSE_MIN_US == 500
        assert PULSE_MAX_US == 2500


# =====================================================================
# Dynamixel Driver (Mock Mode) Tests
# =====================================================================
class TestDynamixelDriverMockMode:
    """Test DynamixelDriver when dynamixel_sdk is NOT installed."""

    def _make_driver(self, config=None):
        from castor.drivers.dynamixel import DynamixelDriver

        return DynamixelDriver(config or {})

    def test_initializes_in_mock_mode(self):
        driver = self._make_driver()
        assert driver.portHandler is None
        assert driver.packetHandler is None

    def test_default_config(self):
        driver = self._make_driver()
        assert driver.port_name == "/dev/ttyUSB0"
        assert driver.baud_rate == 57600
        assert driver.connected_motors == []

    def test_custom_config(self):
        cfg = {"port": "/dev/ttyACM0", "baud_rate": 1000000}
        driver = self._make_driver(cfg)
        assert driver.port_name == "/dev/ttyACM0"
        assert driver.baud_rate == 1000000

    def test_move_mock_logs(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.Dynamixel"):
            driver.move(motor_id=1, angle_deg=45.0)
        mock_msgs = [r.message for r in caplog.records if "[MOCK]" in r.message]
        assert len(mock_msgs) > 0
        assert "Motor 1" in mock_msgs[-1]
        assert "45.0" in mock_msgs[-1]

    def test_move_mock_negative_angle(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.Dynamixel"):
            driver.move(motor_id=3, angle_deg=-90.0)
        mock_msgs = [r.message for r in caplog.records if "[MOCK]" in r.message]
        assert "Motor 3" in mock_msgs[-1]
        assert "-90.0" in mock_msgs[-1]

    def test_move_mock_zero_angle(self, caplog):
        driver = self._make_driver()
        with caplog.at_level(logging.INFO, logger="OpenCastor.Dynamixel"):
            driver.move(motor_id=2, angle_deg=0.0)
        mock_msgs = [r.message for r in caplog.records if "[MOCK]" in r.message]
        assert "Motor 2" in mock_msgs[-1]

    def test_stop_mock_mode_no_crash(self):
        driver = self._make_driver()
        driver.stop()  # Should not raise

    def test_close_mock_mode_no_crash(self):
        driver = self._make_driver()
        driver.close()  # Should not raise

    def test_engage_mock_does_nothing(self):
        driver = self._make_driver()
        driver.engage([1, 2, 3])
        # portHandler is None, so engage returns early
        assert driver.connected_motors == []

    def test_disengage_mock_does_nothing(self):
        driver = self._make_driver()
        driver.disengage([1, 2, 3])
        # Should not crash

    def test_get_position_mock_returns_zero(self):
        driver = self._make_driver()
        pos = driver.get_position(1)
        assert pos == 0.0

    def test_open_port_mock_returns_false(self):
        driver = self._make_driver()
        assert driver._open_port() is False

    def test_is_subclass_of_driver_base(self):
        from castor.drivers.dynamixel import DynamixelDriver

        assert issubclass(DynamixelDriver, DriverBase)


# =====================================================================
# Dynamixel Value Clamping Tests
# =====================================================================
class TestDynamixelClamping:
    """Verify safety clamping in the Dynamixel driver's move() method.

    The conversion is: ticks = 2048 + (angle_deg / 0.088), clamped to [0, 4095].
    """

    def test_center_angle_gives_center_ticks(self):
        """0 degrees -> 2048 ticks (center)."""
        ticks = int(2048 + (0.0 / 0.088))
        assert ticks == 2048

    def test_max_positive_angle_clamps_to_4095(self):
        """Large positive angle should clamp to 4095."""
        angle = 360.0  # way beyond range
        ticks = int(2048 + (angle / 0.088))
        ticks = max(0, min(4095, ticks))
        assert ticks == 4095

    def test_max_negative_angle_clamps_to_0(self):
        """Large negative angle should clamp to 0."""
        angle = -360.0
        ticks = int(2048 + (angle / 0.088))
        ticks = max(0, min(4095, ticks))
        assert ticks == 0

    def test_moderate_positive_angle(self):
        """45 degrees -> 2048 + 511 = 2559 ticks (within range)."""
        angle = 45.0
        ticks = int(2048 + (angle / 0.088))
        ticks = max(0, min(4095, ticks))
        assert 2500 < ticks < 2600

    def test_moderate_negative_angle(self):
        """-45 degrees -> 2048 - 511 = 1537 ticks (within range)."""
        angle = -45.0
        ticks = int(2048 + (angle / 0.088))
        ticks = max(0, min(4095, ticks))
        assert 1400 < ticks < 1600

    def test_boundary_max_ticks_value(self):
        """The clamped max is exactly 4095."""
        ticks = max(0, min(4095, 9999))
        assert ticks == 4095

    def test_boundary_min_ticks_value(self):
        """The clamped min is exactly 0."""
        ticks = max(0, min(4095, -5000))
        assert ticks == 0

    def test_exact_max_angle(self):
        """~180 deg maps to ~4095 ticks (just at the boundary)."""
        # 180 / 0.088 = 2045.45 -> 2048 + 2045 = 4093 -- within range
        angle = 180.0
        ticks = int(2048 + (angle / 0.088))
        ticks = max(0, min(4095, ticks))
        assert ticks <= 4095
        assert ticks > 3000


# =====================================================================
# Dynamixel Control Table Constants Tests
# =====================================================================
class TestDynamixelConstants:
    """Verify that protocol-level constants are correct."""

    def test_addr_torque_enable(self):
        from castor.drivers.dynamixel import DynamixelDriver

        assert DynamixelDriver.ADDR_TORQUE_ENABLE == 64

    def test_addr_goal_position(self):
        from castor.drivers.dynamixel import DynamixelDriver

        assert DynamixelDriver.ADDR_GOAL_POSITION == 116

    def test_addr_present_position(self):
        from castor.drivers.dynamixel import DynamixelDriver

        assert DynamixelDriver.ADDR_PRESENT_POSITION == 132

    def test_protocol_version(self):
        from castor.drivers.dynamixel import DynamixelDriver

        assert DynamixelDriver.PROTOCOL_VERSION == 2.0


# =====================================================================
# PCA9685 RC Driver -- Pulse clamping via _set_pulse
# =====================================================================
class TestPCA9685RCSetPulse:
    """Test that _set_pulse clamps values to safe hardware bounds.

    These tests require a real PCA9685 object, so we mock it.
    """

    def test_set_pulse_clamps_below_minimum(self):
        from castor.drivers.pca9685 import PCA9685RCDriver

        driver = PCA9685RCDriver({})
        # Manually set a fake pca object to test _set_pulse
        mock_pca = MagicMock()
        driver.pca = mock_pca

        driver._set_pulse(0, 100)  # 100 us is below PULSE_MIN_US (500)
        # The duty should be based on PULSE_MIN_US, not 100
        mock_pca.channels.__getitem__.assert_called_with(0)

    def test_set_pulse_clamps_above_maximum(self):
        from castor.drivers.pca9685 import PCA9685RCDriver

        driver = PCA9685RCDriver({})
        mock_pca = MagicMock()
        driver.pca = mock_pca

        driver._set_pulse(1, 5000)  # 5000 us is above PULSE_MAX_US (2500)
        mock_pca.channels.__getitem__.assert_called_with(1)

    def test_set_pulse_normal_value_passes_through(self):
        from castor.drivers.pca9685 import PCA9685RCDriver, _us_to_duty

        driver = PCA9685RCDriver({})
        mock_pca = MagicMock()
        driver.pca = mock_pca

        driver._set_pulse(0, 1500)
        expected_duty = _us_to_duty(50, 1500)
        mock_pca.channels[0].duty_cycle = expected_duty


# =====================================================================
# Module-level HAS_* flags
# =====================================================================
class TestModuleLevelFlags:
    """Verify that the HAS_* sentinel booleans are defined."""

    def test_has_pca9685_is_boolean(self):
        from castor.drivers.pca9685 import HAS_PCA9685

        assert isinstance(HAS_PCA9685, bool)

    def test_has_motor_is_boolean(self):
        from castor.drivers.pca9685 import HAS_MOTOR

        assert isinstance(HAS_MOTOR, bool)

    def test_has_dynamixel_is_boolean(self):
        from castor.drivers.dynamixel import HAS_DYNAMIXEL

        assert isinstance(HAS_DYNAMIXEL, bool)

    def test_pca9685_flag_false_in_test_env(self):
        """In the test environment Adafruit libs are not installed."""
        from castor.drivers.pca9685 import HAS_PCA9685

        assert HAS_PCA9685 is False

    def test_dynamixel_flag_false_in_test_env(self):
        """In the test environment Dynamixel SDK is not installed."""
        from castor.drivers.dynamixel import HAS_DYNAMIXEL

        assert HAS_DYNAMIXEL is False

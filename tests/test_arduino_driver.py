"""Tests for castor.drivers.arduino_driver.ArduinoSerialDriver.

All tests run without real hardware — pyserial is patched so the driver always
starts in hardware-simulated mode (or mock mode when the patch is absent).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_CONFIG = {
    "port": "/dev/ttyACM0",
    "baud": 115200,
    "max_pwm": 255,
    "deadband_pwm": 40,
    "timeout_s": 0.5,
    "invert_left": False,
    "invert_right": False,
}


def _make_fake_serial(readline_data: bytes = b'{"ack":true}\n'):
    """Return a mock serial.Serial instance that echoes *readline_data*."""
    ser = MagicMock()
    ser.readline.return_value = readline_data
    return ser


def _driver_with_mock_serial(config=None, readline_data=b'{"ack":true}\n'):
    """Create an ArduinoSerialDriver with a mocked serial port."""
    cfg = dict(_BASE_CONFIG)
    if config:
        cfg.update(config)

    fake_ser = _make_fake_serial(readline_data)

    with (
        patch("castor.drivers.arduino_driver.HAS_SERIAL", True),
        patch("castor.drivers.arduino_driver.serial") as mock_serial_mod,
        patch("castor.drivers.arduino_driver.time.sleep"),
    ):
        mock_serial_mod.Serial.return_value = fake_ser
        from castor.drivers.arduino_driver import ArduinoSerialDriver

        drv = ArduinoSerialDriver(cfg)
        drv._ser = fake_ser  # keep reference for assertions
        return drv


# ---------------------------------------------------------------------------
# Mock-mode (no pyserial) tests
# ---------------------------------------------------------------------------


class TestArduinoDriverMockMode:
    def test_mock_mode_when_no_pyserial(self):
        with patch("castor.drivers.arduino_driver.HAS_SERIAL", False):
            from castor.drivers.arduino_driver import ArduinoSerialDriver

            drv = ArduinoSerialDriver(_BASE_CONFIG)
        assert drv._mode == "mock"

    def test_health_check_mock(self):
        with patch("castor.drivers.arduino_driver.HAS_SERIAL", False):
            from castor.drivers.arduino_driver import ArduinoSerialDriver

            drv = ArduinoSerialDriver(_BASE_CONFIG)
        hc = drv.health_check()
        assert hc["ok"] is False
        assert hc["mode"] == "mock"
        assert "pyserial" in hc["error"]

    def test_move_does_not_raise_in_mock(self):
        with patch("castor.drivers.arduino_driver.HAS_SERIAL", False):
            from castor.drivers.arduino_driver import ArduinoSerialDriver

            drv = ArduinoSerialDriver(_BASE_CONFIG)
        drv.move(linear=0.5, angular=0.0)  # should log and not raise

    def test_stop_does_not_raise_in_mock(self):
        with patch("castor.drivers.arduino_driver.HAS_SERIAL", False):
            from castor.drivers.arduino_driver import ArduinoSerialDriver

            drv = ArduinoSerialDriver(_BASE_CONFIG)
        drv.stop()

    def test_close_does_not_raise_in_mock(self):
        with patch("castor.drivers.arduino_driver.HAS_SERIAL", False):
            from castor.drivers.arduino_driver import ArduinoSerialDriver

            drv = ArduinoSerialDriver(_BASE_CONFIG)
        drv.close()


# ---------------------------------------------------------------------------
# Hardware-simulated mode tests
# ---------------------------------------------------------------------------


class TestArduinoDriverHardwareMode:
    def test_hardware_mode_set_on_open(self):
        drv = _driver_with_mock_serial()
        assert drv._mode == "hardware"

    def test_move_sends_drive_command(self):
        drv = _driver_with_mock_serial()
        drv.move(linear=1.0, angular=0.0)
        written = drv._ser.write.call_args[0][0].decode()
        payload = json.loads(written)
        assert payload["cmd"] == "drive"
        assert payload["left"] == 255
        assert payload["right"] == 255

    def test_move_with_angular(self):
        """Turning left: right faster, left slower."""
        drv = _driver_with_mock_serial()
        drv.move(linear=0.5, angular=0.5)
        written = drv._ser.write.call_args[0][0].decode()
        payload = json.loads(written)
        # left = linear + angular = 1.0 → 255; right = 0.5 - 0.5 = 0 → 0
        assert payload["left"] == 255
        assert payload["right"] == 0

    def test_stop_sends_stop_command(self):
        drv = _driver_with_mock_serial()
        drv.stop()
        written = drv._ser.write.call_args[0][0].decode()
        payload = json.loads(written)
        assert payload["cmd"] == "stop"

    def test_health_check_hardware(self):
        drv = _driver_with_mock_serial()
        hc = drv.health_check()
        assert hc["ok"] is True
        assert hc["mode"] == "hardware"

    def test_close_closes_serial(self):
        drv = _driver_with_mock_serial()
        ser_ref = drv._ser  # capture before close() sets it to None
        drv.close()
        ser_ref.close.assert_called_once()
        assert drv._mode == "mock"

    def test_legacy_linear_x_angular_z(self):
        """Accept legacy linear_x / angular_z keyword aliases."""
        drv = _driver_with_mock_serial()
        drv.move(linear_x=1.0, angular_z=0.0)
        written = drv._ser.write.call_args[0][0].decode()
        payload = json.loads(written)
        assert payload["cmd"] == "drive"
        assert payload["left"] == payload["right"] == 255

    def test_invert_left(self):
        drv = _driver_with_mock_serial(config={"invert_left": True})
        drv.move(linear=1.0, angular=0.0)
        written = drv._ser.write.call_args[0][0].decode()
        payload = json.loads(written)
        assert payload["left"] == -255
        assert payload["right"] == 255

    def test_deadband_applied(self):
        """Very small inputs should be zero (below deadband) or at deadband."""
        drv = _driver_with_mock_serial(config={"deadband_pwm": 40})
        drv.move(linear=0.01, angular=0.0)
        written = drv._ser.write.call_args[0][0].decode()
        payload = json.loads(written)
        # 0.01 * 255 = 2.55 < 40, so deadband kicks in → 40
        assert payload["left"] == 40
        assert payload["right"] == 40


# ---------------------------------------------------------------------------
# PWM mixing / clamping
# ---------------------------------------------------------------------------


class TestTankMixing:
    """Unit-test the internal _mix_tank helper directly."""

    def _drv(self, **kwargs):
        cfg = dict(_BASE_CONFIG, **kwargs)
        with patch("castor.drivers.arduino_driver.HAS_SERIAL", False):
            from castor.drivers.arduino_driver import ArduinoSerialDriver

            return ArduinoSerialDriver(cfg)

    def test_forward(self):
        drv = self._drv(deadband_pwm=0)
        left, r = drv._mix_tank(1.0, 0.0)
        assert left == 255
        assert r == 255

    def test_reverse(self):
        drv = self._drv(deadband_pwm=0)
        left, r = drv._mix_tank(-1.0, 0.0)
        assert left == -255
        assert r == -255

    def test_turn_left(self):
        drv = self._drv(deadband_pwm=0)
        left, r = drv._mix_tank(0.0, -1.0)
        # angular=-1 → left=-1→-255, right=+1→255
        assert left == -255
        assert r == 255

    def test_turn_right(self):
        drv = self._drv(deadband_pwm=0)
        left, r = drv._mix_tank(0.0, 1.0)
        assert left == 255
        assert r == -255

    def test_zero(self):
        drv = self._drv(deadband_pwm=0)
        left, r = drv._mix_tank(0.0, 0.0)
        assert left == 0
        assert r == 0

    def test_clamping(self):
        drv = self._drv(deadband_pwm=0)
        left, r = drv._mix_tank(2.0, 2.0)
        assert abs(left) <= 255
        assert abs(r) <= 255

    def test_custom_max_pwm(self):
        drv = self._drv(max_pwm=200, deadband_pwm=0)
        left, r = drv._mix_tank(1.0, 0.0)
        assert left == 200
        assert r == 200


# ---------------------------------------------------------------------------
# Extra helpers
# ---------------------------------------------------------------------------


class TestExtraHelpers:
    def test_query_sensor_returns_none_in_mock(self):
        with patch("castor.drivers.arduino_driver.HAS_SERIAL", False):
            from castor.drivers.arduino_driver import ArduinoSerialDriver

            drv = ArduinoSerialDriver(_BASE_CONFIG)
        result = drv.query_sensor("hcsr04")
        assert result is None

    def test_set_servo_returns_none_in_mock(self):
        with patch("castor.drivers.arduino_driver.HAS_SERIAL", False):
            from castor.drivers.arduino_driver import ArduinoSerialDriver

            drv = ArduinoSerialDriver(_BASE_CONFIG)
        result = drv.set_servo(pin=3, angle=90)
        assert result is None

    def test_query_sensor_hardware(self):
        sensor_reply = b'{"sensor":"hcsr04","distance_mm":342}\n'
        drv = _driver_with_mock_serial(readline_data=sensor_reply)
        result = drv.query_sensor("hcsr04")
        assert result is not None
        assert result["distance_mm"] == 342

    def test_set_servo_hardware(self):
        drv = _driver_with_mock_serial()
        drv.set_servo(pin=3, angle=45)
        written = drv._ser.write.call_args[0][0].decode()
        payload = json.loads(written)
        assert payload["cmd"] == "servo"
        assert payload["angle"] == 45

    def test_set_servo_clamps_angle(self):
        drv = _driver_with_mock_serial()
        drv.set_servo(pin=3, angle=200)
        written = drv._ser.write.call_args[0][0].decode()
        payload = json.loads(written)
        assert payload["angle"] == 180


# ---------------------------------------------------------------------------
# Driver factory registration
# ---------------------------------------------------------------------------


class TestDriverFactory:
    def test_factory_returns_arduino_driver(self):
        config = {
            "drivers": [
                {
                    "id": "motor",
                    "protocol": "arduino_serial_json",
                    "port": "/dev/ttyACM0",
                    "baud": 115200,
                }
            ]
        }
        with patch("castor.drivers.arduino_driver.HAS_SERIAL", False):
            from castor.drivers import get_driver

            drv = get_driver(config)
        from castor.drivers.arduino_driver import ArduinoSerialDriver

        assert isinstance(drv, ArduinoSerialDriver)
        assert drv._mode == "mock"

    def test_is_supported_protocol(self):
        from castor.drivers import is_supported_protocol

        assert is_supported_protocol("arduino_serial_json") is True
        assert is_supported_protocol("arduino_serial_raw") is True  # "arduino" substring
        assert is_supported_protocol("unknown_proto") is False

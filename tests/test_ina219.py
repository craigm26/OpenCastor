"""Tests for castor.ina219 (INA219 battery monitor)."""

import threading
import time
from unittest.mock import MagicMock

import pytest

import castor.ina219 as ina219_mod
from castor.ina219 import BatteryMonitor, get_monitor


@pytest.fixture(autouse=True)
def reset_singleton():
    ina219_mod._singleton = None
    yield
    ina219_mod._singleton = None


class TestBatteryMonitorMockMode:
    def test_init_mock(self):
        mon = BatteryMonitor()
        assert mon.mode in ("mock", "hardware")

    def test_read_mock_returns_dict(self):
        mon = BatteryMonitor()
        mon._mode = "mock"
        reading = mon.read()
        assert "voltage_v" in reading
        assert "current_ma" in reading
        assert "power_mw" in reading
        assert reading["mode"] == "mock"

    def test_latest_property(self):
        mon = BatteryMonitor()
        mon._mode = "mock"
        assert isinstance(mon.latest, dict)

    def test_mode_property(self):
        mon = BatteryMonitor()
        assert mon.mode in ("mock", "hardware")

    def test_start_stop(self):
        mon = BatteryMonitor()
        mon._mode = "mock"
        mon.start(poll_interval_s=0.05)
        time.sleep(0.12)
        mon.stop()
        assert not mon._running

    def test_low_battery_callback(self):
        mon = BatteryMonitor()
        mon._mode = "mock"
        mon._low_batt_v = 999.0  # always trigger

        fired = threading.Event()

        def _on_low(reading):
            fired.set()

        # Inject fake non-zero voltage so the alert fires
        mon._sensor = MagicMock()
        mon._sensor.bus_voltage = 5.0
        mon._sensor.shunt_voltage = 0.0
        mon._sensor.current = 100.0
        mon._sensor.power = 500.0
        mon._mode = "hardware"

        mon.start(poll_interval_s=0.05, on_low_battery=_on_low)
        fired.wait(timeout=0.5)
        mon.stop()
        assert fired.is_set()

    def test_hardware_read(self):
        mon = BatteryMonitor()
        mock_sensor = MagicMock()
        mock_sensor.bus_voltage = 12.0
        mock_sensor.shunt_voltage = 10.0  # mV
        mock_sensor.current = 500.0
        mock_sensor.power = 6000.0
        mon._sensor = mock_sensor
        mon._mode = "hardware"
        reading = mon.read()
        assert reading["voltage_v"] > 0
        assert reading["current_ma"] == 500.0

    def test_hardware_read_error(self):
        from unittest.mock import PropertyMock

        mon = BatteryMonitor()
        mock_sensor = MagicMock()
        type(mock_sensor).bus_voltage = PropertyMock(side_effect=OSError("i2c error"))
        mon._sensor = mock_sensor
        mon._mode = "hardware"
        reading = mon.read()
        assert reading["mode"] == "error"
        assert "i2c error" in reading.get("error", "")


class TestGetMonitorSingleton:
    def test_returns_same_instance(self):
        a = get_monitor()
        b = get_monitor()
        assert a is b

    def test_custom_address(self):
        mon = get_monitor(i2c_address=0x41)
        assert mon._address == 0x41

"""Tests for castor.drivers.gpio_driver."""

import sys
from unittest.mock import MagicMock, patch


class TestGPIODriverMockMode:
    """Tests that run without any GPIO hardware library."""

    def _make_driver(self, pins=None):
        from castor.drivers.gpio_driver import GPIODriver

        cfg = {
            "protocol": "gpio",
            "pins": pins or {"forward": 17, "backward": 27, "left": 22, "right": 23, "stop": 24},
            "active_high": True,
        }
        return GPIODriver(cfg)

    def test_init_mock_mode(self):
        driver = self._make_driver()
        assert driver._mode in ("mock", "hardware")

    def test_move_forward(self):
        driver = self._make_driver()
        driver._mode = "mock"
        driver.move({"linear": 0.8, "angular": 0.0})
        assert "forward" in driver._active_pins

    def test_move_backward(self):
        driver = self._make_driver()
        driver._mode = "mock"
        driver.move({"linear": -0.8, "angular": 0.0})
        assert "backward" in driver._active_pins

    def test_move_right(self):
        driver = self._make_driver()
        driver._mode = "mock"
        driver.move({"linear": 0.0, "angular": 0.5})
        assert "right" in driver._active_pins

    def test_move_left(self):
        driver = self._make_driver()
        driver._mode = "mock"
        driver.move({"linear": 0.0, "angular": -0.5})
        assert "left" in driver._active_pins

    def test_move_clears_previous_pins(self):
        driver = self._make_driver()
        driver._mode = "mock"
        driver.move({"linear": 1.0, "angular": 0.0})
        driver.move({"linear": -1.0, "angular": 0.0})
        assert "forward" not in driver._active_pins
        assert "backward" in driver._active_pins

    def test_stop_clears_pins(self):
        driver = self._make_driver()
        driver._mode = "mock"
        driver.move({"linear": 1.0, "angular": 0.0})
        driver.stop()
        assert len(driver._active_pins) == 0

    def test_close_clears_pins(self):
        driver = self._make_driver()
        driver._mode = "mock"
        driver.move({"linear": 1.0})
        driver.close()
        assert len(driver._active_pins) == 0

    def test_health_check_returns_dict(self):
        driver = self._make_driver()
        h = driver.health_check()
        assert h["ok"] is True
        assert "mode" in h
        assert "pins" in h

    def test_missing_pin_name_ignored(self):
        driver = self._make_driver(pins={"forward": 17})
        driver._mode = "mock"
        # backward not configured — should not raise
        driver.move({"linear": -1.0, "angular": 0.0})

    def test_inactive_pins_zero(self):
        driver = self._make_driver()
        driver._mode = "mock"
        driver.move({"linear": 0.0, "angular": 0.0})
        assert len(driver._active_pins) == 0


class TestGPIODriverWithRPiGPIO:
    def test_hardware_mode_with_rpigpio(self):
        mock_gpio = MagicMock()
        mock_gpio.BCM = 11
        mock_gpio.OUT = 0
        mock_gpio.LOW = 0
        mock_gpio.HIGH = 1
        with patch.dict(sys.modules, {"RPi": MagicMock(), "RPi.GPIO": mock_gpio}):
            # Force reimport
            if "castor.drivers.gpio_driver" in sys.modules:
                del sys.modules["castor.drivers.gpio_driver"]
            from castor.drivers.gpio_driver import GPIODriver  # noqa: F401 (reimport)

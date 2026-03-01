"""Tests for castor.drivers.odrive_driver."""

from castor.drivers.odrive_driver import ODriveDriver


class TestODriveDriverMockMode:
    def _make_driver(self, protocol="odrive"):
        return ODriveDriver(
            {
                "protocol": protocol,
                "max_velocity": 20.0,
                "axis0": 0,
                "axis1": 1,
            }
        )

    def test_init_odrive_mock(self):
        d = self._make_driver("odrive")
        assert d._mode in ("mock", "hardware")
        assert d._protocol == "odrive"

    def test_init_vesc_mock(self):
        d = self._make_driver("vesc")
        assert d._mode in ("mock", "hardware")
        assert d._protocol == "vesc"

    def test_move_mock(self):
        d = self._make_driver()
        d._mode = "mock"
        d.move({"linear": 0.5, "angular": 0.0})

    def test_stop_mock(self):
        d = self._make_driver()
        d._mode = "mock"
        d.stop()

    def test_close_mock(self):
        d = self._make_driver()
        d._mode = "mock"
        d.close()

    def test_health_check_mock(self):
        d = self._make_driver()
        d._mode = "mock"
        h = d.health_check()
        assert h["ok"] is True
        assert h["mode"] == "mock"

    def test_max_velocity_config(self):
        d = ODriveDriver({"protocol": "odrive", "max_velocity": 50.0})
        assert d._max_vel == 50.0

    def test_health_check_no_device(self):
        d = self._make_driver()
        d._mode = "hardware"
        d._odrv = None
        d._vesc_serial = None
        h = d.health_check()
        assert h["ok"] is False

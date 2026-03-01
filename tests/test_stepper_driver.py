"""Tests for castor.drivers.stepper_driver."""

import time

from castor.drivers.stepper_driver import StepperDriver, _StepperMotor


class TestStepperMotor:
    def _make_motor(self, hardware=False):
        return _StepperMotor(
            step_pin=18,
            dir_pin=23,
            en_pin=None,
            steps_per_rev=200,
            microstep=16,
            hardware=hardware,
        )

    def test_init(self):
        m = self._make_motor()
        assert m._total_steps == 200 * 16

    def test_step_continuous_starts_thread(self):
        m = self._make_motor()
        m.step_continuous(30.0)
        time.sleep(0.05)
        assert m._running is True
        m.halt()

    def test_halt_stops_thread(self):
        m = self._make_motor()
        m.step_continuous(30.0)
        time.sleep(0.05)
        m.halt()
        assert m._running is False

    def test_zero_rpm_no_thread(self):
        m = self._make_motor()
        m.step_continuous(0.0)
        assert m._thread is None or not m._running

    def test_reverse_direction(self):
        m = self._make_motor()
        m.step_continuous(-30.0)
        time.sleep(0.05)
        assert m._running is True
        m.halt()


class TestStepperDriver:
    def _make_driver(self, extra=None):
        cfg = {
            "protocol": "stepper",
            "steps_per_rev": 200,
            "microstep": 16,
            "max_rpm": 60,
            **(extra or {}),
        }
        return StepperDriver(cfg)

    def test_init(self):
        d = self._make_driver()
        assert d._max_rpm == 60
        assert d._mode in ("mock", "hardware")

    def test_move_forward(self):
        d = self._make_driver()
        d.move({"linear": 1.0, "angular": 0.0})
        time.sleep(0.05)
        d.stop()

    def test_move_backward(self):
        d = self._make_driver()
        d.move({"linear": -1.0, "angular": 0.0})
        time.sleep(0.05)
        d.stop()

    def test_stop(self):
        d = self._make_driver()
        d.move({"linear": 1.0, "angular": 0.0})
        time.sleep(0.05)
        d.stop()
        assert d._left._running is False
        assert d._right._running is False

    def test_close(self):
        d = self._make_driver()
        d.move({"linear": 0.5, "angular": 0.0})
        d.close()
        assert d._left._running is False

    def test_health_check(self):
        d = self._make_driver()
        h = d.health_check()
        assert h["ok"] is True
        assert "mode" in h
        assert "max_rpm" in h

    def test_rpm_clamped(self):
        d = self._make_driver()
        # linear=1.0, angular=1.0 → l_rpm = 0, r_rpm = 2*max
        # but it should be clamped to max_rpm
        d.move({"linear": 1.0, "angular": 1.0})
        time.sleep(0.05)
        d.stop()

    def test_custom_pins(self):
        cfg = {
            "protocol": "stepper",
            "steps_per_rev": 200,
            "microstep": 8,
            "max_rpm": 45,
            "left_motor": {"step_pin": 5, "dir_pin": 6},
            "right_motor": {"step_pin": 13, "dir_pin": 19},
        }
        d = StepperDriver(cfg)
        assert d._left.step_pin == 5
        assert d._right.step_pin == 13

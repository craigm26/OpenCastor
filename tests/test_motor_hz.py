"""Tests for per-layer motor command frequency (Hz) tracking."""

import time
from unittest.mock import MagicMock

from castor.fs.namespace import Namespace
from castor.fs.proc import ProcFS
from castor.providers.base import Thought
from castor.tiered_brain import TieredBrain

SOLID_FRAME = b"\xff\xd8\xff" + b"\x42" * 500


class TestEffectiveHz:
    """TieredBrain.effective_hz() returns per-layer motor command frequency."""

    def _make_brain(self, fast_action=None, config=None):
        fast = MagicMock()
        fast.think.return_value = Thought(
            "fast response",
            fast_action or {"type": "move", "linear": 0.5, "angular": 0.0},
        )
        config = config or {"tiered_brain": {"planner_interval": 5}}
        return TieredBrain(fast, config=config), fast

    def test_effective_hz_keys(self):
        brain, _ = self._make_brain()
        hz = brain.effective_hz()
        assert "reactive_hz" in hz
        assert "fast_hz" in hz
        assert "planner_hz" in hz
        assert "overall_hz" in hz
        assert "window_s" in hz

    def test_zero_hz_on_fresh_brain(self):
        brain, _ = self._make_brain()
        hz = brain.effective_hz()
        assert hz["reactive_hz"] == 0
        assert hz["fast_hz"] == 0
        assert hz["planner_hz"] == 0
        assert hz["overall_hz"] == 0

    def test_fast_hz_after_ticks(self):
        brain, _ = self._make_brain()
        # Run 10 ticks — each should produce a fast brain action
        for _ in range(10):
            brain.think(SOLID_FRAME, "go")
        hz = brain.effective_hz()
        # 10 commands in a 30s window → 10/30 = 0.33 Hz
        assert hz["fast_hz"] == round(10 / 30.0, 2)
        assert hz["overall_hz"] == round(10 / 30.0, 2)
        assert hz["reactive_hz"] == 0
        assert hz["planner_hz"] == 0

    def test_reactive_hz_tracked(self):
        brain, _ = self._make_brain()
        # Blank frame triggers reactive layer
        for _ in range(5):
            brain.think(b"", "go")
        hz = brain.effective_hz()
        assert hz["reactive_hz"] == round(5 / 30.0, 2)
        assert hz["fast_hz"] == 0

    def test_planner_hz_tracked(self):
        fast = MagicMock()
        fast.think.return_value = Thought("fast", {"type": "move"})
        planner = MagicMock()
        planner.think.return_value = Thought("plan", {"type": "plan", "steps": ["a"]})
        brain = TieredBrain(fast, planner, {"tiered_brain": {"planner_interval": 1}})
        # Every tick should also trigger planner (interval=1)
        for _ in range(3):
            brain.think(SOLID_FRAME, "go")
        hz = brain.effective_hz()
        # Planner overrides fast when it has an action, so fast still records
        # but planner also records
        assert hz["planner_hz"] == round(3 / 30.0, 2)

    def test_overall_hz_sums_layers(self):
        brain, _ = self._make_brain()
        # Mix of reactive and fast
        brain.think(b"", "go")  # reactive
        brain.think(SOLID_FRAME, "go")  # fast
        brain.think(SOLID_FRAME, "go")  # fast
        hz = brain.effective_hz()
        assert hz["overall_hz"] == round(3 / 30.0, 2)

    def test_window_prunes_old_entries(self):
        brain, _ = self._make_brain()
        brain._hz_window_s = 1.0  # 1-second window for testing
        brain.think(SOLID_FRAME, "go")
        # Manually age the timestamp
        brain._layer_timestamps["fast"][0] = time.time() - 2.0
        hz = brain.effective_hz()
        # Entry should be pruned (older than 1s window)
        assert hz["fast_hz"] == 0

    def test_effective_hz_in_get_stats(self):
        brain, _ = self._make_brain()
        brain.think(SOLID_FRAME, "go")
        stats = brain.get_stats()
        assert "effective_hz" in stats
        assert "fast_hz" in stats["effective_hz"]


class TestProcFSMotorHz:
    """ProcFS exposes motor_hz in /proc/loop/motor_hz."""

    def test_bootstrap_initializes_motor_hz(self):
        ns = Namespace()
        proc = ProcFS(ns)
        proc.bootstrap()
        hz = ns.read("/proc/loop/motor_hz")
        assert hz == {"reactive_hz": 0, "fast_hz": 0, "planner_hz": 0, "overall_hz": 0}

    def test_record_motor_hz(self):
        ns = Namespace()
        proc = ProcFS(ns)
        proc.bootstrap()
        proc.record_motor_hz(
            {"reactive_hz": 0.5, "fast_hz": 0.33, "planner_hz": 0.1, "overall_hz": 0.93}
        )
        hz = ns.read("/proc/loop/motor_hz")
        assert hz["fast_hz"] == 0.33
        assert hz["overall_hz"] == 0.93

    def test_snapshot_includes_motor_hz(self):
        ns = Namespace()
        proc = ProcFS(ns)
        proc.bootstrap()
        proc.record_motor_hz(
            {"reactive_hz": 1.0, "fast_hz": 0.5, "planner_hz": 0.0, "overall_hz": 1.5}
        )
        snap = proc.snapshot()
        assert "motor_hz" in snap["loop"]
        assert snap["loop"]["motor_hz"]["fast_hz"] == 0.5

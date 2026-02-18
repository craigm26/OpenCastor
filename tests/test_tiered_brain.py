"""Tests for the tiered brain architecture."""

from unittest.mock import MagicMock

from castor.providers.base import Thought
from castor.tiered_brain import ReactiveLayer, TieredBrain


class TestReactiveLayer:
    """Layer 0: rule-based reactive safety."""

    def test_blank_frame_triggers_wait(self):
        layer = ReactiveLayer({})
        action = layer.evaluate(b"")
        assert action is not None
        assert action["type"] == "wait"
        assert action["reason"] == "no_camera_data"

    def test_none_frame_triggers_wait(self):
        layer = ReactiveLayer({})
        action = layer.evaluate(None)
        assert action is not None
        assert action["type"] == "wait"

    def test_all_zeros_triggers_wait(self):
        layer = ReactiveLayer({})
        action = layer.evaluate(b"\x00" * 200)
        assert action is not None
        assert action["type"] == "wait"
        assert action["reason"] == "blank_frame"

    def test_normal_frame_passes_through(self):
        layer = ReactiveLayer({})
        # Normal JPEG-like data
        frame = b"\xff\xd8\xff" + b"\x42" * 500
        action = layer.evaluate(frame)
        assert action is None  # Pass to next layer

    def test_obstacle_proximity_stop(self):
        layer = ReactiveLayer({"reactive": {"min_obstacle_m": 0.3}})
        frame = b"\xff\xd8\xff" + b"\x42" * 500
        action = layer.evaluate(frame, {"front_distance_m": 0.15})
        assert action is not None
        assert action["type"] == "stop"
        assert "obstacle" in action["reason"]

    def test_obstacle_far_passes_through(self):
        layer = ReactiveLayer({"reactive": {"min_obstacle_m": 0.3}})
        frame = b"\xff\xd8\xff" + b"\x42" * 500
        action = layer.evaluate(frame, {"front_distance_m": 1.5})
        assert action is None

    def test_battery_critical_stop(self):
        layer = ReactiveLayer({})
        frame = b"\xff\xd8\xff" + b"\x42" * 500
        action = layer.evaluate(frame, {"battery_critical": True})
        assert action is not None
        assert action["type"] == "stop"
        assert "battery" in action["reason"]

    def test_custom_blank_threshold(self):
        layer = ReactiveLayer({"reactive": {"blank_threshold": 500}})
        # Frame smaller than threshold
        action = layer.evaluate(b"\xff" * 200)
        assert action is not None
        assert action["reason"] == "no_camera_data"

    def test_custom_obstacle_distance(self):
        layer = ReactiveLayer({"reactive": {"min_obstacle_m": 1.0}})
        frame = b"\xff\xd8\xff" + b"\x42" * 500
        action = layer.evaluate(frame, {"front_distance_m": 0.8})
        assert action is not None
        assert action["type"] == "stop"


class TestTieredBrain:
    """Integration of reactive + fast + planner layers."""

    def _make_brain(self, fast_action=None, planner_action=None, config=None):
        fast = MagicMock()
        fast.think.return_value = Thought(
            "fast response",
            fast_action or {"type": "forward", "value": 0.5},
        )
        planner = MagicMock()
        planner.think.return_value = Thought(
            "planner response",
            planner_action or {"type": "plan", "steps": ["explore"]},
        )
        config = config or {"tiered_brain": {"planner_interval": 5}}
        return TieredBrain(fast, planner, config), fast, planner

    def test_normal_frame_uses_fast_brain(self):
        brain, fast, planner = self._make_brain()
        frame = b"\xff\xd8\xff" + b"\x42" * 500
        thought = brain.think(frame, "look around")
        fast.think.assert_called_once()
        assert thought.action["type"] == "forward"

    def test_blank_frame_uses_reactive(self):
        brain, fast, planner = self._make_brain()
        thought = brain.think(b"", "look around")
        # Fast brain should NOT be called
        fast.think.assert_not_called()
        assert thought.action["type"] == "wait"

    def test_planner_runs_on_interval(self):
        brain, fast, planner = self._make_brain(config={"tiered_brain": {"planner_interval": 3}})
        frame = b"\xff\xd8\xff" + b"\x42" * 500
        # Ticks 1, 2: fast only
        brain.think(frame, "go")
        brain.think(frame, "go")
        assert planner.think.call_count == 0
        # Tick 3: planner fires
        thought = brain.think(frame, "go")
        assert planner.think.call_count == 1
        assert thought.action["type"] == "plan"

    def test_planner_escalation_on_no_action(self):
        fast = MagicMock()
        fast.think.return_value = Thought("confused", None)  # No action!
        planner = MagicMock()
        planner.think.return_value = Thought("plan", {"type": "stop"})
        brain = TieredBrain(fast, planner, {"tiered_brain": {"planner_interval": 0}})
        frame = b"\xff\xd8\xff" + b"\x42" * 500
        thought = brain.think(frame, "go")
        planner.think.assert_called_once()
        assert thought.action["type"] == "stop"

    def test_no_planner_still_works(self):
        fast = MagicMock()
        fast.think.return_value = Thought("ok", {"type": "forward", "value": 0.3})
        brain = TieredBrain(fast, planner_provider=None)
        frame = b"\xff\xd8\xff" + b"\x42" * 500
        thought = brain.think(frame, "go")
        assert thought.action["type"] == "forward"

    def test_stats_tracking(self):
        brain, fast, planner = self._make_brain(config={"tiered_brain": {"planner_interval": 2}})
        frame = b"\xff\xd8\xff" + b"\x42" * 500
        brain.think(b"", "go")  # reactive
        brain.think(frame, "go")  # fast
        brain.think(frame, "go")  # fast but tick 3 so not interval=2 check? tick=3 not div by 2
        # Actually tick 2 is divisible by 2
        # tick 1: blank → reactive, tick 2: fast + planner (interval 2)
        stats = brain.get_stats()
        assert stats["reactive_count"] == 1
        assert stats["fast_count"] >= 1
        assert stats["total_ticks"] == 3

    def test_planner_error_nonfatal(self):
        fast = MagicMock()
        fast.think.return_value = Thought("ok", {"type": "forward", "value": 0.3})
        planner = MagicMock()
        planner.think.side_effect = Exception("network error")
        brain = TieredBrain(fast, planner, {"tiered_brain": {"planner_interval": 1}})
        frame = b"\xff\xd8\xff" + b"\x42" * 500
        # Should not crash, returns fast brain result
        thought = brain.think(frame, "go")
        assert thought.action["type"] == "forward"

    def test_sensor_data_obstacle(self):
        brain, fast, planner = self._make_brain()
        frame = b"\xff\xd8\xff" + b"\x42" * 500
        thought = brain.think(frame, "go", sensor_data={"front_distance_m": 0.1})
        # Reactive layer should catch this
        fast.think.assert_not_called()
        assert thought.action["type"] == "stop"

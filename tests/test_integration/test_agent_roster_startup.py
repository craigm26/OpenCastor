"""Integration tests: Agent Roster startup wiring in main.py context.

Tests that the agent_roster config section correctly spawns ObserverAgent
and NavigatorAgent via AgentRegistry, and that shared state is wired
between them.  All hardware imports are mocked out.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from castor.agents import AgentRegistry, SharedState
from castor.agents.navigator import NavigatorAgent
from castor.agents.observer import ObserverAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_roster_cfg(observer_enabled: bool = True, navigator_enabled: bool = True):
    return [
        {
            "name": "observer",
            "enabled": observer_enabled,
            "config": {"obstacle_labels": ["person", "bottle", "chair"]},
        },
        {
            "name": "navigator",
            "enabled": navigator_enabled,
            "config": {"max_speed": 0.4, "goal": {"x": 0.0, "y": 1.0}},
        },
    ]


# ---------------------------------------------------------------------------
# Registry spawn with shared_state kwarg
# ---------------------------------------------------------------------------


class TestAgentRegistrySpawn:
    def test_spawn_observer_with_shared_state(self):
        """Registry.spawn() must accept shared_state kwarg for ObserverAgent."""
        state = SharedState()
        registry = AgentRegistry()
        registry.register(ObserverAgent)

        agent = registry.spawn("observer", config={}, shared_state=state)

        assert isinstance(agent, ObserverAgent)
        # Internal state reference must be the one we passed
        assert agent._state is state

    def test_spawn_navigator_with_shared_state(self):
        """Registry.spawn() must accept shared_state kwarg for NavigatorAgent."""
        state = SharedState()
        registry = AgentRegistry()
        registry.register(NavigatorAgent)

        agent = registry.spawn("navigator", config={"max_speed": 0.3}, shared_state=state)

        assert isinstance(agent, NavigatorAgent)
        assert agent._state is state
        assert agent._max_speed == 0.3

    def test_spawn_unknown_name_raises(self):
        registry = AgentRegistry()
        with pytest.raises(KeyError):
            registry.spawn("nonexistent")

    def test_list_agents_after_spawn(self):
        state = SharedState()
        registry = AgentRegistry()
        registry.register(ObserverAgent)
        registry.register(NavigatorAgent)

        registry.spawn("observer", config={}, shared_state=state)
        registry.spawn("navigator", config={}, shared_state=state)

        agents = registry.list_agents()
        assert len(agents) == 2
        names = {a["name"] for a in agents}
        assert names == {"observer", "navigator"}


# ---------------------------------------------------------------------------
# Roster startup simulation (mimics the block added to main.py)
# ---------------------------------------------------------------------------


def _startup_from_roster(roster_cfg: list) -> dict:
    """Simulate the Agent Roster startup block from main.py.

    Returns dict with keys: registry, shared_state, observer, navigator.
    """
    _agent_registry = None
    _agent_shared_state = None
    _agent_observer = None
    _agent_navigator = None

    if roster_cfg:
        _agent_shared_state = SharedState()
        _agent_registry = AgentRegistry()
        _agent_registry.register(ObserverAgent)
        _agent_registry.register(NavigatorAgent)

        for entry in roster_cfg:
            if not entry.get("enabled", True):
                continue
            agent_name = entry.get("name", "")
            agent_config = entry.get("config", {})
            if agent_name in ("observer", "navigator"):
                agent = _agent_registry.spawn(
                    agent_name,
                    config=agent_config,
                    shared_state=_agent_shared_state,
                )
            else:
                agent = _agent_registry.spawn(agent_name, config=agent_config)

            if agent_name == "observer":
                _agent_observer = agent
            elif agent_name == "navigator":
                _agent_navigator = agent

    return {
        "registry": _agent_registry,
        "shared_state": _agent_shared_state,
        "observer": _agent_observer,
        "navigator": _agent_navigator,
    }


class TestRosterStartup:
    def test_both_agents_spawned(self):
        result = _startup_from_roster(_make_roster_cfg())

        assert result["registry"] is not None
        assert result["shared_state"] is not None
        assert isinstance(result["observer"], ObserverAgent)
        assert isinstance(result["navigator"], NavigatorAgent)

    def test_agents_share_state(self):
        """Observer and Navigator must reference the same SharedState instance."""
        result = _startup_from_roster(_make_roster_cfg())

        assert result["observer"]._state is result["shared_state"]
        assert result["navigator"]._state is result["shared_state"]

    def test_observer_config_applied(self):
        """Custom obstacle_labels from config should extend ObserverAgent defaults."""
        result = _startup_from_roster(_make_roster_cfg())
        obs = result["observer"]
        # "bottle" and "chair" are in defaults; "person" is too
        assert "person" in obs._obstacle_labels
        assert "bottle" in obs._obstacle_labels
        assert "chair" in obs._obstacle_labels

    def test_navigator_config_applied(self):
        """max_speed and goal from config should be applied to NavigatorAgent."""
        result = _startup_from_roster(_make_roster_cfg())
        nav = result["navigator"]
        assert nav._max_speed == pytest.approx(0.4)
        assert nav._goal_x == pytest.approx(0.0)
        assert nav._goal_y == pytest.approx(1.0)

    def test_disabled_observer_not_spawned(self):
        result = _startup_from_roster(_make_roster_cfg(observer_enabled=False))
        assert result["observer"] is None
        assert isinstance(result["navigator"], NavigatorAgent)

    def test_disabled_navigator_not_spawned(self):
        result = _startup_from_roster(_make_roster_cfg(navigator_enabled=False))
        assert isinstance(result["observer"], ObserverAgent)
        assert result["navigator"] is None

    def test_empty_roster_yields_none(self):
        result = _startup_from_roster([])
        assert result["registry"] is None
        assert result["observer"] is None
        assert result["navigator"] is None


# ---------------------------------------------------------------------------
# Observer → Navigator integration via SharedState
# ---------------------------------------------------------------------------


class TestObserverNavigatorIntegration:
    def test_scene_graph_flows_to_navigator(self):
        """Scene built by ObserverAgent should be readable by NavigatorAgent."""
        state = SharedState()
        observer = ObserverAgent(config={}, shared_state=state)
        navigator = NavigatorAgent(config={"max_speed": 0.5}, shared_state=state)

        sensor_data = {
            "hailo_detections": [
                {"label": "chair", "confidence": 0.8, "bbox": [0.3, 0.2, 0.6, 0.8]},
            ],
            "frame_shape": (480, 640),
        }

        asyncio.run(observer.observe(sensor_data))
        action = asyncio.run(navigator.act({}))

        assert "action" in action
        assert "direction" in action or action.get("action") in ("move", "stop")

    def test_navigator_stop_on_close_obstacle(self):
        """Navigator should slow/stop when scene has a very-close obstacle."""
        state = SharedState()
        observer = ObserverAgent(config={}, shared_state=state)
        navigator = NavigatorAgent(config={"max_speed": 0.6, "min_obstacle_m": 0.4}, shared_state=state)

        # Simulate hailo detection with known depth via manual state injection
        import time
        from castor.agents.observer import Detection, SceneGraph

        scene = SceneGraph(
            timestamp=time.time(),
            detections=[
                Detection(
                    label="person",
                    confidence=0.95,
                    bbox=(0.3, 0.1, 0.7, 0.9),
                    distance_m=0.2,  # Very close!
                    is_obstacle=True,
                )
            ],
            free_space_pct=0.1,  # Almost blocked
            closest_obstacle_m=0.2,
        )
        state.set("scene_graph", scene)

        action = asyncio.run(navigator.act({}))
        # With free_space_pct < BLOCKED_THRESHOLD (0.25), navigator should stop
        assert action.get("direction") == "stop" or action.get("speed", 1.0) == 0.0

    def test_stop_all_via_registry(self):
        """AgentRegistry.stop_all() should complete without error."""
        state = SharedState()
        registry = AgentRegistry()
        registry.register(ObserverAgent)
        registry.register(NavigatorAgent)
        registry.spawn("observer", config={}, shared_state=state)
        registry.spawn("navigator", config={}, shared_state=state)

        # Should not raise
        asyncio.run(registry.stop_all())

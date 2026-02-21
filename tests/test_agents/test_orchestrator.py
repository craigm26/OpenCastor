"""Tests for OrchestratorAgent and TieredBrain Layer 3 integration."""

import asyncio

import pytest

from castor.agents.shared_state import SharedState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


def make_orchestrator(state=None):
    from castor.agents.orchestrator import OrchestratorAgent

    return OrchestratorAgent(config={}, shared_state=state or SharedState())


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestOrchestratorInit:
    def test_name(self):
        assert make_orchestrator().name == "orchestrator"

    def test_tick_starts_at_zero(self):
        assert make_orchestrator()._tick == 0

    def test_last_action_none(self):
        assert make_orchestrator()._last_action is None

    def test_empty_log(self):
        assert make_orchestrator()._log == []


# ---------------------------------------------------------------------------
# _collect()
# ---------------------------------------------------------------------------


class TestOrchestratorCollect:
    def test_collects_estop_from_state(self):
        state = SharedState()
        state.set("swarm.estop_active", True)
        o = make_orchestrator(state)
        outputs = o._collect()
        assert outputs["estop_active"] is True

    def test_defaults_estop_false(self):
        o = make_orchestrator()
        assert o._collect()["estop_active"] is False

    def test_collects_nav_plan(self):
        state = SharedState()
        state.set("swarm.nav_plan", {"action": {"type": "move", "speed": 0.4}})
        o = make_orchestrator(state)
        assert o._collect()["nav_plan"] is not None


# ---------------------------------------------------------------------------
# _resolve()
# ---------------------------------------------------------------------------


class TestOrchestratorResolve:
    def test_estop_overrides_everything(self):
        o = make_orchestrator()
        action = o._resolve({"estop_active": True, "nav_plan": {"type": "move"}})
        assert action["type"] == "stop"
        assert "estop" in action["reason"]

    def test_guardian_veto_triggers_stop(self):
        o = make_orchestrator()
        action = o._resolve(
            {
                "estop_active": False,
                "guardian_report": {
                    "vetoes": [{"reason": "speed_limit:1.2>0.9"}],
                    "approved": [],
                },
            }
        )
        assert action["type"] == "stop"
        assert "guardian_veto" in action["reason"]

    def test_manipulation_in_progress_returns_wait(self):
        o = make_orchestrator()
        action = o._resolve(
            {
                "estop_active": False,
                "guardian_report": None,
                "manipulation_result": {"status": "running"},
            }
        )
        assert action["type"] == "wait"

    def test_nav_plan_action_forwarded(self):
        o = make_orchestrator()
        action = o._resolve(
            {
                "estop_active": False,
                "guardian_report": None,
                "manipulation_result": None,
                "nav_plan": {"action": {"type": "move", "speed": 0.5, "direction": "forward"}},
            }
        )
        assert action["type"] == "move"
        assert action["speed"] == pytest.approx(0.5)

    def test_default_idle(self):
        o = make_orchestrator()
        action = o._resolve(
            {"estop_active": False, "guardian_report": None, "manipulation_result": None, "nav_plan": None}
        )
        assert action["type"] == "idle"

    def test_empty_guardian_report_does_not_veto(self):
        o = make_orchestrator()
        action = o._resolve(
            {
                "estop_active": False,
                "guardian_report": {"vetoes": [], "approved": ["nav"]},
                "manipulation_result": None,
                "nav_plan": None,
            }
        )
        assert action["type"] == "idle"


# ---------------------------------------------------------------------------
# observe() + act()
# ---------------------------------------------------------------------------


class TestOrchestratorObserveAct:
    def test_observe_collects_outputs(self):
        state = SharedState()
        state.set("swarm.estop_active", False)
        o = make_orchestrator(state)
        ctx = run(o.observe({}))
        assert "estop_active" in ctx

    def test_observe_sensor_data_overrides_state(self):
        state = SharedState()
        state.set("swarm.estop_active", False)
        o = make_orchestrator(state)
        ctx = run(o.observe({"estop_active": True}))
        assert ctx["estop_active"] is True

    def test_act_increments_tick(self):
        o = make_orchestrator()
        run(o.act({}))
        assert o._tick == 1

    def test_act_publishes_orchestrated_action(self):
        state = SharedState()
        o = make_orchestrator(state)
        run(o.act({"estop_active": False, "guardian_report": None}))
        assert state.get("swarm.orchestrated_action") is not None

    def test_act_log_capped_at_100(self):
        o = make_orchestrator()
        for _ in range(110):
            run(o.act({}))
        assert len(o._log) == 100

    def test_act_returns_estop_on_active(self):
        o = make_orchestrator()
        result = run(o.act({"estop_active": True}))
        assert result["type"] == "stop"


# ---------------------------------------------------------------------------
# sync_think()
# ---------------------------------------------------------------------------


class TestOrchestratorSyncThink:
    def test_sync_think_returns_dict(self):
        o = make_orchestrator()
        result = o.sync_think({})
        assert isinstance(result, dict)
        assert "type" in result

    def test_sync_think_default_idle(self):
        o = make_orchestrator()
        result = o.sync_think({})
        assert result["type"] == "idle"

    def test_sync_think_estop(self):
        state = SharedState()
        state.set("swarm.estop_active", True)
        o = make_orchestrator(state)
        result = o.sync_think({})
        assert result["type"] == "stop"

    def test_sync_think_increments_tick(self):
        o = make_orchestrator()
        o.sync_think({})
        o.sync_think({})
        assert o._tick == 2


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------


class TestOrchestratorStatus:
    def test_status_returns_dict(self):
        o = make_orchestrator()
        s = o.get_status()
        assert "tick" in s
        assert "last_action" in s
        assert "estop" in s
        assert "log_entries" in s

    def test_status_tick_zero_initially(self):
        assert make_orchestrator().get_status()["tick"] == 0


# ---------------------------------------------------------------------------
# TieredBrain Layer 3 integration
# ---------------------------------------------------------------------------


class TestTieredBrainLayer3:
    def _make_fast_provider(self, action=None):
        from unittest.mock import MagicMock

        from castor.providers.base import Thought

        p = MagicMock()
        p.think.return_value = Thought(
            "move forward", action or {"type": "move", "speed": 0.4}
        )
        return p

    def test_layer3_disabled_by_default(self):
        from castor.tiered_brain import TieredBrain

        brain = TieredBrain(fast_provider=self._make_fast_provider())
        assert brain.orchestrator is None

    def test_layer3_disabled_when_flag_false(self):
        from castor.tiered_brain import TieredBrain

        brain = TieredBrain(
            fast_provider=self._make_fast_provider(),
            config={"agents": {"enabled": False}},
        )
        assert brain.orchestrator is None

    def test_layer3_enabled_creates_orchestrator(self):
        from castor.tiered_brain import TieredBrain

        brain = TieredBrain(
            fast_provider=self._make_fast_provider(),
            config={"agents": {"enabled": True}},
        )
        assert brain.orchestrator is not None

    def test_layer3_idle_passes_through_to_fast_brain(self):
        """When orchestrator returns idle, fast-brain thought is returned."""
        from castor.tiered_brain import TieredBrain

        brain = TieredBrain(
            fast_provider=self._make_fast_provider({"type": "move", "speed": 0.4}),
            config={"agents": {"enabled": True}},
        )
        # Orchestrator SharedState is empty → default idle → pass through
        thought = brain.think(b"\x00" * 500, "go forward")
        # Fast brain action should be returned (orchestrator idle passes through)
        assert thought.action is not None

    def test_layer3_estop_overrides_fast_brain(self):
        """When orchestrator has estop, its stop action overrides fast brain."""
        from castor.tiered_brain import TieredBrain

        brain = TieredBrain(
            fast_provider=self._make_fast_provider({"type": "move", "speed": 0.9}),
            config={"agents": {"enabled": True}},
        )
        # Inject estop into the orchestrator's SharedState
        brain.orchestrator._state.set("swarm.estop_active", True)

        thought = brain.think(b"\xff" * 500, "go")
        assert thought.action["type"] == "stop"

    def test_layer3_stats_include_swarm_count(self):
        from castor.tiered_brain import TieredBrain

        brain = TieredBrain(fast_provider=self._make_fast_provider())
        assert "swarm_count" in brain.stats

    def test_layer3_swarm_pct_in_get_stats(self):
        from castor.tiered_brain import TieredBrain

        brain = TieredBrain(fast_provider=self._make_fast_provider())
        assert "swarm_pct" in brain.get_stats()

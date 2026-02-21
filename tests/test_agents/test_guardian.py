"""Tests for GuardianAgent."""

import asyncio

import pytest

from castor.agents.shared_state import SharedState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


def make_agent(config=None, state=None):
    from castor.agents.guardian import GuardianAgent

    return GuardianAgent(config=config or {}, shared_state=state or SharedState())


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestGuardianInit:
    def test_name(self):
        assert make_agent().name == "guardian"

    def test_default_max_speed(self):
        a = make_agent()
        assert a.max_speed == pytest.approx(0.9)

    def test_custom_max_speed(self):
        a = make_agent({"max_speed": 0.5})
        assert a.max_speed == pytest.approx(0.5)

    def test_estop_inactive_by_default(self):
        assert make_agent().estop_active is False

    def test_vetoes_empty_on_init(self):
        assert make_agent().vetoes == []


# ---------------------------------------------------------------------------
# E-stop
# ---------------------------------------------------------------------------


class TestGuardianEstop:
    def test_trigger_estop_sets_flag(self):
        a = make_agent()
        a.trigger_estop("test reason")
        assert a.estop_active is True

    def test_trigger_estop_publishes_to_state(self):
        state = SharedState()
        a = make_agent(state=state)
        a.trigger_estop("collision")
        assert state.get("swarm.estop_active") is True
        assert state.get("swarm.estop_reason") == "collision"

    def test_clear_estop(self):
        a = make_agent()
        a.trigger_estop()
        a.clear_estop()
        assert a.estop_active is False

    def test_clear_estop_publishes_to_state(self):
        state = SharedState()
        a = make_agent(state=state)
        a.trigger_estop()
        a.clear_estop()
        assert state.get("swarm.estop_active") is False


# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------


class TestGuardianValidation:
    def test_approve_normal_move(self):
        a = make_agent()
        veto = a._validate("nav", {"type": "move", "speed": 0.5})
        assert veto is None

    def test_veto_forbidden_type(self):
        a = make_agent()
        veto = a._validate("nav", {"type": "self_destruct"})
        assert veto is not None
        assert "forbidden" in veto.reason

    def test_veto_speed_limit(self):
        a = make_agent({"max_speed": 0.5})
        veto = a._validate("nav", {"type": "move", "speed": 0.8})
        assert veto is not None
        assert "speed_limit" in veto.reason

    def test_approve_speed_at_limit(self):
        a = make_agent({"max_speed": 0.8})
        veto = a._validate("nav", {"type": "move", "speed": 0.8})
        assert veto is None

    def test_veto_movement_during_estop(self):
        a = make_agent()
        a.trigger_estop("test")
        veto = a._validate("nav", {"type": "move", "speed": 0.3})
        assert veto is not None
        assert "estop_active" in veto.reason

    def test_allow_stop_during_estop(self):
        a = make_agent()
        a.trigger_estop("test")
        veto = a._validate("nav", {"type": "stop"})
        assert veto is None

    def test_allow_idle_during_estop(self):
        a = make_agent()
        a.trigger_estop("test")
        veto = a._validate("nav", {"type": "idle"})
        assert veto is None


# ---------------------------------------------------------------------------
# observe() + act()
# ---------------------------------------------------------------------------


class TestGuardianObserveAct:
    def test_observe_reads_monitored_keys(self):
        from castor.agents.guardian import GuardianAgent

        state = SharedState()
        state.set("swarm.nav_action", {"type": "move", "speed": 0.4})
        a = GuardianAgent(
            config={"monitored_keys": ["swarm.nav_action"]}, shared_state=state
        )
        ctx = run(a.observe({}))
        assert "swarm.nav_action" in ctx["pending_actions"]

    def test_act_approves_valid_action(self):
        state = SharedState()
        state.set("swarm.nav_action", {"type": "move", "speed": 0.3})
        from castor.agents.guardian import GuardianAgent

        a = GuardianAgent(
            config={"monitored_keys": ["swarm.nav_action"]}, shared_state=state
        )
        ctx = run(a.observe({}))
        result = run(a.act(ctx))
        assert result["action"] == "approve"

    def test_act_vetoes_forbidden_action(self):
        state = SharedState()
        state.set("swarm.nav_action", {"type": "unsafe_move"})
        from castor.agents.guardian import GuardianAgent

        a = GuardianAgent(
            config={"monitored_keys": ["swarm.nav_action"]}, shared_state=state
        )
        ctx = run(a.observe({}))
        result = run(a.act(ctx))
        assert result["action"] == "veto"
        assert len(result["report"]["vetoes"]) > 0

    def test_act_publishes_guardian_report(self):
        state = SharedState()
        from castor.agents.guardian import GuardianAgent

        a = GuardianAgent(config={}, shared_state=state)
        run(a.act({"pending_actions": {}}))
        assert state.get("swarm.guardian_report") is not None

    def test_act_accumulates_veto_count(self):
        state = SharedState()
        state.set("swarm.nav_action", {"type": "self_destruct"})
        from castor.agents.guardian import GuardianAgent

        a = GuardianAgent(
            config={"monitored_keys": ["swarm.nav_action"]}, shared_state=state
        )
        for _ in range(3):
            state.set("swarm.nav_action", {"type": "self_destruct"})
            ctx = run(a.observe({}))
            run(a.act(ctx))

        report = state.get("swarm.guardian_report")
        assert report["veto_count"] == 3

    def test_observe_injects_proposed_action(self):
        a = make_agent()
        ctx = run(a.observe({"proposed_action": {"type": "move", "speed": 0.3}}))
        assert "direct" in ctx["pending_actions"]

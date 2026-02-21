"""Tests for CommunicatorAgent."""

import asyncio

from castor.agents.shared_state import SharedState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


def make_agent(state=None):
    from castor.agents.communicator import CommunicatorAgent

    return CommunicatorAgent(config={}, shared_state=state or SharedState())


# ---------------------------------------------------------------------------
# Intent parsing
# ---------------------------------------------------------------------------


class TestParseIntent:
    def test_navigate_intent(self):
        a = make_agent()
        assert a.parse_intent("go forward please") == "go"

    def test_navigate_turn(self):
        a = make_agent()
        assert a.parse_intent("turn left") == "turn"

    def test_manipulate_grasp(self):
        a = make_agent()
        assert a.parse_intent("grasp the bottle") == "grasp"

    def test_manipulate_grab(self):
        a = make_agent()
        assert a.parse_intent("grab the cup") == "grab"

    def test_stop_intent(self):
        a = make_agent()
        assert a.parse_intent("stop immediately") == "stop"

    def test_emergency_intent(self):
        a = make_agent()
        assert a.parse_intent("emergency stop!") == "emergency"

    def test_observe_intent(self):
        a = make_agent()
        assert a.parse_intent("scan for obstacles") == "scan"

    def test_status_intent(self):
        a = make_agent()
        assert a.parse_intent("what is your status?") == "status"

    def test_unknown_intent(self):
        a = make_agent()
        assert a.parse_intent("zxqwerty bloop") == "unknown"

    def test_case_insensitive(self):
        a = make_agent()
        assert a.parse_intent("GO FORWARD") == "go"


# ---------------------------------------------------------------------------
# Intent routing
# ---------------------------------------------------------------------------


class TestRouteIntent:
    def test_navigate_routes_to_navigator(self):
        state = SharedState()
        a = make_agent(state)
        target = a.route_intent("go", "go forward")
        assert target == "navigator"
        task = state.get("swarm.routed_task.navigator")
        assert task is not None
        assert task["intent"] == "go"

    def test_manipulate_routes_to_manipulator(self):
        state = SharedState()
        a = make_agent(state)
        target = a.route_intent("grasp", "grasp the bottle")
        assert target == "manipulator"
        assert state.get("swarm.routed_task.manipulator") is not None

    def test_stop_routes_to_guardian(self):
        state = SharedState()
        a = make_agent(state)
        target = a.route_intent("stop", "stop now")
        assert target == "guardian"

    def test_unknown_intent_returns_none(self):
        a = make_agent()
        assert a.route_intent("unknown", "zxqwerty") is None

    def test_self_referential_does_not_publish_task(self):
        state = SharedState()
        a = make_agent(state)
        a.route_intent("status", "what is your status")
        # communicator does not route to itself via shared state
        assert state.get("swarm.routed_task.communicator") is None


# ---------------------------------------------------------------------------
# observe() + act()
# ---------------------------------------------------------------------------


class TestCommunicatorObserveAct:
    def test_observe_reads_incoming_message(self):
        state = SharedState()
        state.set("swarm.incoming_message", "go forward")
        a = make_agent(state)
        ctx = run(a.observe({}))
        assert ctx["message"] == "go forward"

    def test_observe_reads_sensor_data_fallback(self):
        a = make_agent()
        ctx = run(a.observe({"incoming_message": "scan area"}))
        assert ctx["message"] == "scan area"

    def test_observe_none_when_empty(self):
        a = make_agent()
        ctx = run(a.observe({}))
        assert ctx["message"] is None

    def test_act_routes_message(self):
        state = SharedState()
        a = make_agent(state)
        result = run(a.act({"message": "go forward"}))
        assert result["action"] == "route"
        assert result["routed_to"] == "navigator"

    def test_act_clears_incoming_message(self):
        state = SharedState()
        state.set("swarm.incoming_message", "go forward")
        a = make_agent(state)
        run(a.act({"message": "go forward"}))
        assert state.get("swarm.incoming_message") is None

    def test_act_idle_when_no_message(self):
        a = make_agent()
        result = run(a.act({"message": None}))
        assert result["action"] == "idle"

    def test_act_appends_conversation_history(self):
        a = make_agent()
        run(a.act({"message": "go forward"}))
        run(a.act({"message": "scan area"}))
        assert len(a._conversation_history) == 2


# ---------------------------------------------------------------------------
# format_response()
# ---------------------------------------------------------------------------


class TestFormatResponse:
    def test_status_intent(self):
        a = make_agent()
        r = a.format_response("ok", intent="status")
        assert r.startswith("Status:")

    def test_estop_intent(self):
        a = make_agent()
        r = a.format_response("", intent="stop")
        assert "Emergency stop" in r

    def test_help_intent(self):
        a = make_agent()
        r = a.format_response("", intent="help")
        assert "navigate" in r.lower()

    def test_generic_response(self):
        a = make_agent()
        r = a.format_response("moved 1m forward")
        assert "moved" in r

    def test_empty_response_returns_done(self):
        a = make_agent()
        assert a.format_response("") == "Done."

"""Tests for RCAN scope propagation through swarm delegation chains.

Covers:
- SharedState Intent has ``scope`` field defaulting to ``"chat"``
- OrchestratorAgent.submit_intent passes ``scope`` into Intent
- OrchestratorAgent._dispatch_delegated_intent calls SwarmConsensus with originating_scope
- GuardianAgent SCOPE_ACTION_ALLOWLIST covers all required scope levels
- GuardianAgent vetoes actions that exceed the task scope (fail-closed)
- GuardianAgent allows permitted actions within the task scope
- Unknown action types at restricted scopes → veto (fail-closed)
- Unknown action types at unrestricted scopes (control/system/safety) → allow
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from castor.agents.guardian import SCOPE_ACTION_ALLOWLIST, GuardianAgent
from castor.agents.orchestrator import OrchestratorAgent
from castor.agents.shared_state import Intent, SharedState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


def make_guardian(config=None, state=None):
    return GuardianAgent(config=config or {}, shared_state=state or SharedState())


def make_orchestrator(state=None, consensus=None):
    return OrchestratorAgent(
        config={}, shared_state=state or SharedState(), consensus=consensus
    )


def _veto_result(guardian, scope, action_type):
    """Call guardian._validate with a minimal action dict carrying ``scope``."""
    action = {"type": action_type, "scope": scope}
    return guardian._validate("test_key", action)


# ===========================================================================
# 1. SharedState Intent has ``scope`` field defaulting to ``"chat"``
# ===========================================================================


class TestIntentScopeField:
    def test_intent_scope_default_is_chat(self):
        intent = Intent(goal="test")
        assert intent.scope == "chat"

    def test_intent_scope_can_be_set(self):
        intent = Intent(goal="test", scope="control")
        assert intent.scope == "control"

    def test_intent_scope_discover(self):
        intent = Intent(goal="test", scope="discover")
        assert intent.scope == "discover"

    def test_intent_scope_status(self):
        intent = Intent(goal="test", scope="status")
        assert intent.scope == "status"

    def test_intent_scope_system(self):
        intent = Intent(goal="test", scope="system")
        assert intent.scope == "system"

    def test_intent_to_dict_includes_scope(self):
        intent = Intent(goal="test", scope="control")
        d = intent.to_dict()
        assert d["scope"] == "control"

    def test_intent_to_dict_scope_defaults_to_chat(self):
        intent = Intent(goal="test")
        d = intent.to_dict()
        assert d.get("scope") == "chat"


# ===========================================================================
# 2. OrchestratorAgent.submit_intent passes scope into Intent
# ===========================================================================


class TestOrchestratorScopeWiring:
    def test_submit_intent_default_scope_is_chat(self):
        orch = make_orchestrator()
        result = orch.submit_intent("do something")
        assert result["intent"]["scope"] == "chat"

    def test_submit_intent_control_scope(self):
        orch = make_orchestrator()
        result = orch.submit_intent("move forward", scope="control")
        assert result["intent"]["scope"] == "control"

    def test_submit_intent_status_scope(self):
        orch = make_orchestrator()
        result = orch.submit_intent("get telemetry", scope="status")
        assert result["intent"]["scope"] == "status"

    def test_submit_intent_scope_stored_in_shared_state(self):
        state = SharedState()
        orch = make_orchestrator(state=state)
        orch.submit_intent("navigate home", scope="chat")
        intents = state.list_intents()
        assert len(intents) == 1
        assert intents[0]["scope"] == "chat"

    def test_submit_intent_discover_scope(self):
        orch = make_orchestrator()
        result = orch.submit_intent("ping all", scope="discover")
        assert result["intent"]["scope"] == "discover"


# ===========================================================================
# 3. _dispatch_delegated_intent calls SwarmConsensus with originating_scope
# ===========================================================================


class TestDelegatedIntentScoreWiring:
    def test_dispatch_calls_consensus_with_originating_scope(self):
        mock_consensus = MagicMock()
        mock_consensus.record_delegated_intent.return_value = MagicMock()
        orch = make_orchestrator(consensus=mock_consensus)

        intent = Intent(goal="speak hello", scope="chat")
        orch._dispatch_delegated_intent(intent, action="speak", params={"text": "hello"})

        mock_consensus.record_delegated_intent.assert_called_once()
        _, kwargs = mock_consensus.record_delegated_intent.call_args
        assert kwargs["originating_scope"] == "chat"

    def test_dispatch_passes_control_scope(self):
        mock_consensus = MagicMock()
        mock_consensus.record_delegated_intent.return_value = MagicMock()
        orch = make_orchestrator(consensus=mock_consensus)

        intent = Intent(goal="motor move", scope="control")
        orch._dispatch_delegated_intent(intent, action="motor_move")

        _, kwargs = mock_consensus.record_delegated_intent.call_args
        assert kwargs["originating_scope"] == "control"

    def test_dispatch_returns_none_when_no_consensus(self):
        orch = make_orchestrator(consensus=None)
        intent = Intent(goal="test", scope="chat")
        result = orch._dispatch_delegated_intent(intent, action="speak")
        assert result is None

    def test_dispatch_policy_constraints_include_scope(self):
        mock_consensus = MagicMock()
        mock_consensus.record_delegated_intent.return_value = MagicMock()
        orch = make_orchestrator(consensus=mock_consensus)

        intent = Intent(goal="get status", scope="status")
        orch._dispatch_delegated_intent(intent, action="get_telemetry")

        pos_args, _ = mock_consensus.record_delegated_intent.call_args
        delegated_intent = pos_args[0]
        assert delegated_intent.policy_constraints.get("scope") == "status"


# ===========================================================================
# 4. SCOPE_ACTION_ALLOWLIST covers required scope levels
# ===========================================================================


class TestScopeActionAllowlist:
    def test_allowlist_has_discover(self):
        assert "discover" in SCOPE_ACTION_ALLOWLIST

    def test_allowlist_has_status(self):
        assert "status" in SCOPE_ACTION_ALLOWLIST

    def test_allowlist_has_chat(self):
        assert "chat" in SCOPE_ACTION_ALLOWLIST

    def test_allowlist_has_control(self):
        assert "control" in SCOPE_ACTION_ALLOWLIST

    def test_allowlist_has_system(self):
        assert "system" in SCOPE_ACTION_ALLOWLIST

    def test_allowlist_has_safety(self):
        assert "safety" in SCOPE_ACTION_ALLOWLIST

    def test_control_is_unrestricted(self):
        assert SCOPE_ACTION_ALLOWLIST["control"] is None

    def test_system_is_unrestricted(self):
        assert SCOPE_ACTION_ALLOWLIST["system"] is None

    def test_safety_is_unrestricted(self):
        assert SCOPE_ACTION_ALLOWLIST["safety"] is None

    def test_chat_allows_speak(self):
        assert "speak" in SCOPE_ACTION_ALLOWLIST["chat"]

    def test_chat_allows_navigate_to(self):
        assert "navigate_to" in SCOPE_ACTION_ALLOWLIST["chat"]

    def test_chat_allows_describe_scene(self):
        assert "describe_scene" in SCOPE_ACTION_ALLOWLIST["chat"]

    def test_discover_does_not_allow_speak(self):
        assert "speak" not in SCOPE_ACTION_ALLOWLIST["discover"]

    def test_status_does_not_allow_speak(self):
        assert "speak" not in SCOPE_ACTION_ALLOWLIST["status"]

    def test_status_allows_get_telemetry(self):
        assert "get_telemetry" in SCOPE_ACTION_ALLOWLIST["status"]


# ===========================================================================
# 5. GuardianAgent scope enforcement
# ===========================================================================


class TestGuardianScopeEnforcement:
    # --- chat scope ---

    def test_chat_scope_vetoes_motor_move(self):
        g = make_guardian()
        veto = _veto_result(g, scope="chat", action_type="motor_move")
        assert veto is not None
        assert "scope_violation" in veto.reason

    def test_chat_scope_allows_speak(self):
        g = make_guardian()
        veto = _veto_result(g, scope="chat", action_type="speak")
        assert veto is None

    def test_chat_scope_allows_navigate_to(self):
        g = make_guardian()
        veto = _veto_result(g, scope="chat", action_type="navigate_to")
        assert veto is None

    def test_chat_scope_allows_describe_scene(self):
        g = make_guardian()
        veto = _veto_result(g, scope="chat", action_type="describe_scene")
        assert veto is None

    def test_chat_scope_allows_get_telemetry(self):
        g = make_guardian()
        veto = _veto_result(g, scope="chat", action_type="get_telemetry")
        assert veto is None

    def test_chat_scope_allows_status(self):
        g = make_guardian()
        veto = _veto_result(g, scope="chat", action_type="status")
        assert veto is None

    # --- control scope (unrestricted) ---

    def test_control_scope_allows_motor_move(self):
        g = make_guardian()
        veto = _veto_result(g, scope="control", action_type="motor_move")
        assert veto is None

    def test_control_scope_allows_speak(self):
        g = make_guardian()
        veto = _veto_result(g, scope="control", action_type="speak")
        assert veto is None

    def test_control_scope_allows_arbitrary_action(self):
        g = make_guardian()
        veto = _veto_result(g, scope="control", action_type="custom_firmware_flash")
        assert veto is None

    # --- system / safety scopes (unrestricted) ---

    def test_system_scope_allows_motor_move(self):
        g = make_guardian()
        veto = _veto_result(g, scope="system", action_type="motor_move")
        assert veto is None

    def test_safety_scope_allows_motor_move(self):
        g = make_guardian()
        veto = _veto_result(g, scope="safety", action_type="motor_move")
        assert veto is None

    # --- discover scope ---

    def test_discover_scope_vetoes_speak(self):
        g = make_guardian()
        veto = _veto_result(g, scope="discover", action_type="speak")
        assert veto is not None
        assert "scope_violation" in veto.reason

    def test_discover_scope_allows_ping(self):
        g = make_guardian()
        veto = _veto_result(g, scope="discover", action_type="ping")
        assert veto is None

    # --- status scope ---

    def test_status_scope_vetoes_speak(self):
        g = make_guardian()
        veto = _veto_result(g, scope="status", action_type="speak")
        assert veto is not None

    def test_status_scope_allows_get_pose(self):
        g = make_guardian()
        veto = _veto_result(g, scope="status", action_type="get_pose")
        assert veto is None

    # --- fail-closed for unknown action types ---

    def test_unknown_action_at_chat_scope_is_vetoed(self):
        g = make_guardian()
        veto = _veto_result(g, scope="chat", action_type="totally_unknown_action_xyz")
        assert veto is not None
        assert "scope_violation" in veto.reason

    def test_unknown_action_at_discover_scope_is_vetoed(self):
        g = make_guardian()
        veto = _veto_result(g, scope="discover", action_type="some_unknown_op")
        assert veto is not None

    def test_unknown_action_at_status_scope_is_vetoed(self):
        g = make_guardian()
        veto = _veto_result(g, scope="status", action_type="some_unknown_op")
        assert veto is not None

    def test_unknown_action_at_control_scope_is_allowed(self):
        # control = None = unrestricted → fail-open
        g = make_guardian()
        veto = _veto_result(g, scope="control", action_type="totally_unknown_action_xyz")
        assert veto is None

    def test_unknown_action_at_system_scope_is_allowed(self):
        g = make_guardian()
        veto = _veto_result(g, scope="system", action_type="totally_unknown_action_xyz")
        assert veto is None

    def test_unknown_action_at_safety_scope_is_allowed(self):
        g = make_guardian()
        veto = _veto_result(g, scope="safety", action_type="totally_unknown_action_xyz")
        assert veto is None

    # --- idle / stop / wait bypass scope checks ---

    def test_stop_action_always_allowed_regardless_of_scope(self):
        g = make_guardian()
        veto = _veto_result(g, scope="discover", action_type="stop")
        assert veto is None

    def test_idle_action_always_allowed_regardless_of_scope(self):
        g = make_guardian()
        veto = _veto_result(g, scope="discover", action_type="idle")
        assert veto is None

    def test_wait_action_always_allowed_regardless_of_scope(self):
        g = make_guardian()
        veto = _veto_result(g, scope="discover", action_type="wait")
        assert veto is None

    # --- existing rules still work alongside scope check ---

    def test_forbidden_action_type_still_vetoed_even_at_control_scope(self):
        g = make_guardian()
        action = {"type": "self_destruct", "scope": "control"}
        veto = g._validate("k", action)
        assert veto is not None
        assert "forbidden" in veto.reason

    def test_scope_veto_appears_in_guardian_report(self):
        state = SharedState()
        g = make_guardian(state=state)
        # Inject a motor_move action at chat scope into monitored key
        state.set("swarm.nav_action", {"type": "motor_move", "scope": "chat"})

        async def _run():
            ctx = await g.observe({})
            return await g.act(ctx)

        result = run(_run())
        assert result["action"] == "veto"
        report = result["report"]
        assert len(report["vetoes"]) >= 1
        reasons = [v["reason"] for v in report["vetoes"]]
        assert any("scope_violation" in r for r in reasons)

    def test_scope_allow_produces_approve_for_speak_at_chat(self):
        state = SharedState()
        g = make_guardian(state=state)
        state.set("swarm.nav_action", {"type": "speak", "scope": "chat", "text": "hi"})

        async def _run():
            ctx = await g.observe({})
            return await g.act(ctx)

        result = run(_run())
        assert result["action"] == "approve"


# ===========================================================================
# 6. Orchestrator act() propagates scope from current intent into action
# ===========================================================================


class TestOrchestratorActScopePropagation:
    def test_act_propagates_scope_from_current_intent(self):
        state = SharedState()
        orch = make_orchestrator(state=state)
        # Submit an intent with control scope
        orch.submit_intent("move arm", scope="control")

        action = orch.sync_think({})
        # The resolved action should carry the scope from the active intent
        assert action.get("scope") == "control"

    def test_act_defaults_scope_to_chat_when_no_intent(self):
        orch = make_orchestrator()
        action = orch.sync_think({})
        assert action.get("scope") == "chat"

    def test_act_propagates_scope_from_sensor_data(self):
        orch = make_orchestrator()
        action = orch.sync_think({"scope": "status"})
        assert action.get("scope") == "status"

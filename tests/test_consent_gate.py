"""Tests for #867 Bug B — wire RCAN consent.scope_threshold → HiTLGate.

bob.rcan.yaml declares:

    consent:
      required: true
      mode: explicit
      scope_threshold: control

…but `/api/arm/pick_place` proceeded without ever consulting the HiTL layer,
making the declared safety envelope non-load-bearing. These tests cover:

1. configure.parse_consent_gates() — the rcan consent block must produce a
   HiTLGate covering control-scope action types when required=true.
2. /api/arm/pick_place must call the gate before the vision-plan loop.
3. Authorization via the existing /api/hitl/authorize endpoint must
   unblock a pending pick_place call.
4. consent.required=false (or missing) must not produce a gate (existing
   behavior preserved for un-gated configs).
"""

from __future__ import annotations

import collections
import time
from unittest.mock import MagicMock, patch

# ── 1. parse_consent_gates() ──────────────────────────────────────────────────


class TestParseConsentGates:
    def test_scope_threshold_control_produces_gate(self):
        from castor.configure import parse_consent_gates

        gates = parse_consent_gates(
            {
                "consent": {
                    "required": True,
                    "mode": "explicit",
                    "scope_threshold": "control",
                }
            }
        )
        assert len(gates) == 1
        gate = gates[0]
        assert "pick_place" in gate.action_types
        assert gate.require_auth is True

    def test_scope_threshold_hardware_also_gates_pick_place(self):
        from castor.configure import parse_consent_gates

        gates = parse_consent_gates(
            {
                "consent": {"required": True, "scope_threshold": "hardware"},
            }
        )
        assert len(gates) == 1
        assert "pick_place" in gates[0].action_types

    def test_required_false_returns_empty(self):
        from castor.configure import parse_consent_gates

        gates = parse_consent_gates(
            {
                "consent": {"required": False, "scope_threshold": "control"},
            }
        )
        assert gates == []

    def test_no_consent_block_returns_empty(self):
        from castor.configure import parse_consent_gates

        assert parse_consent_gates({}) == []
        assert parse_consent_gates({"consent": {}}) == []

    def test_scope_threshold_read_does_not_gate_pick_place(self):
        """consent.scope_threshold='read' means consent is only required for
        sensor reads — arm motion (control) should NOT be gated."""
        from castor.configure import parse_consent_gates

        gates = parse_consent_gates(
            {
                "consent": {"required": True, "scope_threshold": "read"},
            }
        )
        # Either no gates, or no gate covering pick_place
        for gate in gates:
            assert "pick_place" not in gate.action_types

    def test_unknown_scope_threshold_emits_no_gate_silently(self):
        from castor.configure import parse_consent_gates

        gates = parse_consent_gates(
            {
                "consent": {"required": True, "scope_threshold": "made-up-scope"},
            }
        )
        assert gates == []


# ── 2 + 3. /api/arm/pick_place gate integration ───────────────────────────────


def _make_client_and_reset(monkeypatch):
    """Same pattern as tests/test_brain_error_surfacing.py."""
    monkeypatch.delenv("OPENCASTOR_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENCASTOR_JWT_SECRET", raising=False)

    import castor.api as api_mod

    api_mod.state.config = None
    api_mod.state.brain = None
    api_mod.state.driver = None
    api_mod.state.channels = {}
    api_mod.state.last_thought = None
    api_mod.state.boot_time = time.time()
    api_mod.state.fs = None
    api_mod.state.ruri = None
    api_mod.state.offline_fallback = None
    api_mod.state.provider_fallback = None
    api_mod.state.thought_history = collections.deque(maxlen=50)
    api_mod.state.hitl_gate_manager = None
    api_mod.API_TOKEN = None

    from starlette.testclient import TestClient

    from castor.api import app

    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    import contextlib as _contextlib

    @_contextlib.asynccontextmanager
    async def _noop_lifespan(app):
        yield

    app.router.lifespan_context = _noop_lifespan
    return TestClient(app, raise_server_exceptions=False)


def _wire_consent_gate(timeout_ms: int = 30000):
    """Install a HiTLGateManager with a pick_place gate (mimics startup wiring
    when bob.rcan.yaml has consent.required=true, scope_threshold=control)."""
    import castor.api as api_mod
    from castor.configure import parse_consent_gates
    from castor.hitl_gate import HiTLGateManager

    cfg = {"consent": {"required": True, "scope_threshold": "control"}}
    gates = parse_consent_gates(cfg)
    # Tighten timeout so tests finish quickly
    for g in gates:
        g.auth_timeout_ms = timeout_ms
    api_mod.state.hitl_gate_manager = HiTLGateManager(gates)


def _wire_brain_that_returns_action(api_mod):
    """Brain mock that returns one valid arm_pose action so the pick_place
    loop terminates quickly when the gate approves."""
    from castor.providers.base import Thought

    mock_brain = MagicMock()
    mock_brain.think.return_value = Thought(
        raw_text="[]",
        action=[],  # empty action list → loop logs but doesn't drive servos
    )
    api_mod.state.brain = mock_brain


class TestPickPlaceConsentGate:
    def test_first_call_returns_202_pending_auth_with_pending_id(self, monkeypatch):
        """Two-step / RCAN §8 PENDING_AUTH: first call returns 202 with the
        pending_id; brain is NEVER invoked at this stage."""
        client = _make_client_and_reset(monkeypatch)

        import castor.api as api_mod

        api_mod.state.driver = MagicMock(set_joint_positions=MagicMock())
        _wire_brain_that_returns_action(api_mod)
        _wire_consent_gate()

        with patch(
            "castor.api._capture_live_frame",
            return_value=b"\xff\xd8" + b"\x00" * 1024,
        ):
            resp = client.post(
                "/api/arm/pick_place",
                json={
                    "target": "red lego",
                    "destination": "bowl",
                    "max_vision_steps": 1,
                },
            )

        assert resp.status_code == 202, (
            f"expected 202 PENDING_AUTH, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["status"] == "PENDING_AUTH"
        assert body["scope"] == "control"
        assert body["action_type"] == "pick_place"
        assert body["pending_id"]  # non-empty UUID
        # Brain must not have been touched at PENDING_AUTH stage
        assert api_mod.state.brain.think.call_count == 0, "brain was called pre-auth"

    def test_retry_with_unknown_pending_id_returns_400(self, monkeypatch):
        """Retrying with a pending_id we never issued must reject with 400."""
        client = _make_client_and_reset(monkeypatch)

        import castor.api as api_mod

        api_mod.state.driver = MagicMock(set_joint_positions=MagicMock())
        _wire_brain_that_returns_action(api_mod)
        _wire_consent_gate()

        resp = client.post(
            "/api/arm/pick_place",
            json={
                "target": "red lego",
                "destination": "bowl",
                "consent_pending_id": "00000000-not-a-real-id",
            },
        )
        assert resp.status_code == 400, resp.text
        assert api_mod.state.brain.think.call_count == 0

    def test_retry_before_authorize_returns_409(self, monkeypatch):
        """First call gets pending_id; retrying with that id BEFORE the operator
        approves must return 409 (still pending)."""
        client = _make_client_and_reset(monkeypatch)

        import castor.api as api_mod

        api_mod.state.driver = MagicMock(set_joint_positions=MagicMock())
        _wire_brain_that_returns_action(api_mod)
        _wire_consent_gate()

        first = client.post(
            "/api/arm/pick_place",
            json={"target": "red lego", "destination": "bowl"},
        )
        assert first.status_code == 202
        pending_id = first.json()["pending_id"]

        # Don't authorize — retry directly
        retry = client.post(
            "/api/arm/pick_place",
            json={
                "target": "red lego",
                "destination": "bowl",
                "consent_pending_id": pending_id,
            },
        )
        assert retry.status_code == 409, retry.text
        assert pending_id in retry.json().get("error", retry.text)

    def test_retry_after_deny_returns_403(self, monkeypatch):
        """Operator denies via /api/hitl/authorize → retry returns 403."""
        client = _make_client_and_reset(monkeypatch)

        import castor.api as api_mod

        api_mod.state.driver = MagicMock(set_joint_positions=MagicMock())
        _wire_brain_that_returns_action(api_mod)
        _wire_consent_gate()

        first = client.post(
            "/api/arm/pick_place",
            json={"target": "red lego", "destination": "bowl"},
        )
        pending_id = first.json()["pending_id"]

        ar = client.post(
            "/api/hitl/authorize",
            json={"pending_id": pending_id, "decision": "deny"},
        )
        assert ar.status_code == 200, ar.text

        retry = client.post(
            "/api/arm/pick_place",
            json={
                "target": "red lego",
                "destination": "bowl",
                "consent_pending_id": pending_id,
            },
        )
        assert retry.status_code == 403, retry.text

    def test_retry_after_approve_proceeds(self, monkeypatch):
        """Two-step happy path: PENDING_AUTH → /api/hitl/authorize → retry → 200.

        After the operator approves the pending_id, the client retries
        /api/arm/pick_place with consent_pending_id=<that id> and the brain
        is called (gate is past, planning loop runs).
        """
        client = _make_client_and_reset(monkeypatch)

        import castor.api as api_mod

        api_mod.state.driver = MagicMock(set_joint_positions=MagicMock())
        _wire_brain_that_returns_action(api_mod)
        _wire_consent_gate()

        # Step 1: first call → 202 PENDING_AUTH with pending_id
        first = client.post(
            "/api/arm/pick_place",
            json={
                "target": "red lego",
                "destination": "bowl",
                "max_vision_steps": 1,
            },
        )
        assert first.status_code == 202, first.text
        pending_id = first.json()["pending_id"]
        assert api_mod.state.brain.think.call_count == 0

        # Step 2: operator approves
        ar = client.post(
            "/api/hitl/authorize",
            json={"pending_id": pending_id, "decision": "approve"},
        )
        assert ar.status_code == 200, ar.text

        # Step 3: client retries with consent_pending_id → proceeds into loop
        with patch(
            "castor.api._capture_live_frame",
            return_value=b"\xff\xd8" + b"\x00" * 1024,
        ):
            retry = client.post(
                "/api/arm/pick_place",
                json={
                    "target": "red lego",
                    "destination": "bowl",
                    "max_vision_steps": 1,
                    "consent_pending_id": pending_id,
                },
            )

        assert retry.status_code == 200, retry.text
        # Brain WAS called now (gate is past)
        assert api_mod.state.brain.think.call_count >= 1

        # And the pending_id is single-use: replaying it should now 400
        replay = client.post(
            "/api/arm/pick_place",
            json={
                "target": "red lego",
                "destination": "bowl",
                "consent_pending_id": pending_id,
            },
        )
        assert replay.status_code == 400, replay.text

    def test_pick_place_proceeds_when_no_consent_gate(self, monkeypatch):
        """No HiTLGateManager (legacy un-gated config) → endpoint behaves as before."""
        client = _make_client_and_reset(monkeypatch)

        import castor.api as api_mod

        api_mod.state.driver = MagicMock(set_joint_positions=MagicMock())
        _wire_brain_that_returns_action(api_mod)
        # Explicitly: no gate manager
        api_mod.state.hitl_gate_manager = None

        with patch(
            "castor.api._capture_live_frame",
            return_value=b"\xff\xd8" + b"\x00" * 1024,
        ):
            resp = client.post(
                "/api/arm/pick_place",
                json={
                    "target": "red lego",
                    "destination": "bowl",
                    "max_vision_steps": 1,
                },
            )

        assert resp.status_code == 200, resp.text

    def test_pick_place_proceeds_when_no_pick_place_gate(self, monkeypatch):
        """HiTLGateManager exists but no gate covers pick_place → no blocking."""
        client = _make_client_and_reset(monkeypatch)

        import castor.api as api_mod
        from castor.hitl_gate import HiTLGate, HiTLGateManager

        api_mod.state.driver = MagicMock(set_joint_positions=MagicMock())
        _wire_brain_that_returns_action(api_mod)
        # Gate covers something else, not pick_place
        api_mod.state.hitl_gate_manager = HiTLGateManager(
            [HiTLGate(action_types=["unrelated_grip"], require_auth=True)]
        )

        with patch(
            "castor.api._capture_live_frame",
            return_value=b"\xff\xd8" + b"\x00" * 1024,
        ):
            resp = client.post(
                "/api/arm/pick_place",
                json={"target": "red lego", "destination": "bowl", "max_vision_steps": 1},
            )

        assert resp.status_code == 200, resp.text

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
import threading
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
    def test_pick_place_blocks_when_consent_required_and_unauthorized(self, monkeypatch):
        """With consent.required=true (gate registered for pick_place), an
        unauthorized request must NOT proceed past the consent check."""
        client = _make_client_and_reset(monkeypatch)

        import castor.api as api_mod

        api_mod.state.driver = MagicMock(set_joint_positions=MagicMock())
        _wire_brain_that_returns_action(api_mod)
        _wire_consent_gate(timeout_ms=300)  # short timeout so the test isn't slow

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

        # Either 403 (denied) or 408 (timeout) — both indicate the gate fired
        # and refused to proceed. The point is: NOT 200 with phase log.
        assert resp.status_code in (403, 408, 503), (
            f"gate did not block: {resp.status_code} {resp.text}"
        )
        # And the brain should never have been called (gate is *before* planning)
        assert api_mod.state.brain.think.call_count == 0, "brain was called despite consent gate"

    def test_pick_place_proceeds_after_authorize(self, monkeypatch):
        """Operator authorizes via /api/hitl/authorize → blocked pick_place
        unblocks and progresses into the planning loop."""
        client = _make_client_and_reset(monkeypatch)

        import castor.api as api_mod

        api_mod.state.driver = MagicMock(set_joint_positions=MagicMock())
        _wire_brain_that_returns_action(api_mod)
        _wire_consent_gate(timeout_ms=5000)

        # Spawn the pick_place call in a background thread; main thread
        # polls for the pending gate, hits /api/hitl/authorize, and the
        # background call should finish 200.
        result_box: dict = {}

        def _run():
            with patch(
                "castor.api._capture_live_frame",
                return_value=b"\xff\xd8" + b"\x00" * 1024,
            ):
                r = client.post(
                    "/api/arm/pick_place",
                    json={
                        "target": "red lego",
                        "destination": "bowl",
                        "max_vision_steps": 1,
                    },
                )
            result_box["status"] = r.status_code
            result_box["body"] = r.text

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        # Wait for the gate manager to register a pending request (up to 1s)
        manager = api_mod.state.hitl_gate_manager
        deadline = time.time() + 1.0
        pending_id = None
        while time.time() < deadline:
            if manager._pending:
                pending_id = next(iter(manager._pending.keys()))
                break
            time.sleep(0.02)

        assert pending_id is not None, "no pending HiTL request was created"

        # Authorize via the existing endpoint
        ar = client.post(
            "/api/hitl/authorize",
            json={"pending_id": pending_id, "decision": "approve"},
        )
        assert ar.status_code == 200, ar.text

        t.join(timeout=5.0)
        assert "status" in result_box, "pick_place call did not return"
        assert result_box["status"] == 200, result_box

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

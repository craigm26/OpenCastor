"""Integration test for NotifyDispatcher wiring.

Boots a slim FastAPI test app, injects a recorder channel into
state.channels["whatsapp"], populates state.config with the operator
block, runs _wire_notify_dispatch(), then triggers the consent gate via
POST /api/arm/pick_place. Asserts the 202 response *and* that the
recorder channel received the pending_id ping.

This catches the wiring-at-startup failure mode that produced the
opencastor #867 follow-up bug — pure unit tests can't catch it because
they bypass the binding step.
"""

from __future__ import annotations

import collections
import time
from unittest.mock import MagicMock, patch


class _RecorderChannel:
    """Minimal channel double that records send_message_with_retry calls.

    Doesn't subclass BaseChannel — we only need the surface
    NotifyDispatcher actually calls.
    """

    name = "whatsapp"

    def __init__(self) -> None:
        self.sends: list[tuple[str, str]] = []

    async def send_message_with_retry(self, chat_id: str, text: str, **_: object) -> bool:
        self.sends.append((chat_id, text))
        return True

    async def send_message(self, chat_id: str, text: str) -> None:
        self.sends.append((chat_id, text))


def _reset_state(api_mod) -> None:
    """Reset castor.api.state to a known-clean baseline."""
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
    api_mod.state.notify_dispatcher = None
    api_mod.state.authority_handler = None


def _make_client_and_reset(monkeypatch, request):
    """Borrowed pattern from tests/test_consent_gate.py:104.

    Registers a finalizer to reset state.config / hitl_gate_manager / etc.
    after the test runs so this test does not pollute later tests that
    read state.config (e.g. test_safety_telemetry.test_api_safety_test_bounds
    calls BoundsChecker.from_config(state.config) and breaks if a leftover
    config contains no bounds spec).
    """
    monkeypatch.delenv("OPENCASTOR_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENCASTOR_JWT_SECRET", raising=False)

    import castor.api as api_mod

    _reset_state(api_mod)
    api_mod.API_TOKEN = None
    request.addfinalizer(lambda: _reset_state(api_mod))

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


def _setup_brain_and_driver(api_mod):
    """Mirror tests/test_consent_gate.py — driver mock + brain that returns
    one valid empty action so pick_place loop terminates quickly."""
    from castor.providers.base import Thought

    api_mod.state.driver = MagicMock(set_joint_positions=MagicMock())
    mock_brain = MagicMock()
    mock_brain.think.return_value = Thought(raw_text="[]", action=[])
    api_mod.state.brain = mock_brain


def test_pick_place_pending_auth_pings_recorder_channel(monkeypatch, request):
    """Full wiring: pick_place returns 202 + pending_id, AND the recorder
    channel receives a WhatsApp message containing the pending_id."""
    client = _make_client_and_reset(monkeypatch, request)

    import castor.api as api_mod

    # 1. Inject the recorder as the active 'whatsapp' channel
    recorder = _RecorderChannel()
    api_mod.state.channels = {"whatsapp": recorder}

    # 2. Populate config with the operator block + consent gate.
    # Note: consent block has no explicit notify — _wire_notify_dispatch
    # smart-defaults gate.notify to [owner_channel] when owner_channel is set.
    api_mod.state.config = {
        "consent": {"required": True, "scope_threshold": "control"},
        "operator": {
            "chat_ids": {"whatsapp": "+15555550100"},
            "owner_channel": "whatsapp",
        },
    }

    _setup_brain_and_driver(api_mod)

    # 3. Fire the wiring helper (this is the system-under-test)
    api_mod._wire_notify_dispatch()

    # Sanity — wiring built the dispatcher and rebuilt the gate manager
    assert api_mod.state.notify_dispatcher is not None
    assert api_mod.state.hitl_gate_manager is not None
    assert api_mod.state.authority_handler is not None

    # 4. Trigger the consent gate
    with patch(
        "castor.api._capture_live_frame",
        return_value=b"\xff\xd8" + b"\x00" * 1024,
    ):
        resp = client.post(
            "/api/arm/pick_place",
            json={"target": "red lego", "destination": "bowl"},
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "PENDING_AUTH"
    pending_id = body["pending_id"]
    assert pending_id

    # 5. _notify is fired via asyncio.create_task; let the loop drain.
    # TestClient is synchronous so we need to give the dispatch a chance
    # to actually run. A short time.sleep does the trick more reliably
    # than asyncio.get_event_loop() with TestClient's own loop semantics.
    time.sleep(0.1)

    # 6. The recorder must have received exactly one message
    assert len(recorder.sends) == 1, (
        f"recorder.sends={recorder.sends}; expected 1 ping after pick_place 202"
    )
    chat_id, msg = recorder.sends[0]
    assert chat_id == "+15555550100"
    assert "pick_place" in msg
    assert pending_id in msg


def test_pick_place_without_operator_block_falls_back_to_log_only(
    monkeypatch, caplog, request
):
    """When operator: block is absent, _wire_notify_dispatch must log an info
    line and the recorder must NOT be pinged. Today's behavior preserved
    (Invariant C from the design spec)."""
    import logging

    client = _make_client_and_reset(monkeypatch, request)

    import castor.api as api_mod

    recorder = _RecorderChannel()
    api_mod.state.channels = {"whatsapp": recorder}

    # Note: no 'operator' key
    api_mod.state.config = {
        "consent": {"required": True, "scope_threshold": "control"},
    }

    _setup_brain_and_driver(api_mod)

    with caplog.at_level(logging.INFO):
        api_mod._wire_notify_dispatch()

    assert any(
        "operator.chat_ids/owner_channel not configured" in r.message for r in caplog.records
    ), f"expected fallback log line; caplog records: {[r.message for r in caplog.records]}"

    # Gate manager still built (Invariant A — no regression)
    assert api_mod.state.hitl_gate_manager is not None
    # Dispatcher and authority handler stay None (Invariant C)
    assert api_mod.state.notify_dispatcher is None
    assert api_mod.state.authority_handler is None

    with patch(
        "castor.api._capture_live_frame",
        return_value=b"\xff\xd8" + b"\x00" * 1024,
    ):
        resp = client.post(
            "/api/arm/pick_place",
            json={"target": "red lego", "destination": "bowl"},
        )

    assert resp.status_code == 202, resp.text

    time.sleep(0.1)

    # No operator block → no ping
    assert recorder.sends == [], f"expected no pings without operator block, got {recorder.sends}"

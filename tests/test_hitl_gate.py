"""Unit tests for castor.hitl_gate."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from castor.hitl_gate import HiTLGate, HiTLGateManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_thought(thought_id: str = "t-001") -> Any:
    t = MagicMock()
    t.id = thought_id
    return t


def make_action(action_type: str = "grip") -> dict:
    return {"type": action_type}


# ---------------------------------------------------------------------------
# No gate → allow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_matching_gate_allows():
    mgr = HiTLGateManager(gates=[HiTLGate(action_types=["grip"], require_auth=True)])
    result = await mgr.check(make_action("move"), make_thought())
    assert result is True


# ---------------------------------------------------------------------------
# require_auth=False → allow without callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_auth_false_allows():
    mgr = HiTLGateManager(gates=[HiTLGate(action_types=["grip"], require_auth=False)])
    result = await mgr.check(make_action("grip"), make_thought())
    assert result is True


# ---------------------------------------------------------------------------
# approval_required=True invokes callback (approve path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_required_approve():
    mgr = HiTLGateManager(
        gates=[HiTLGate(action_types=["grip"], require_auth=True, auth_timeout_ms=5000)]
    )

    async def approve_soon():
        await asyncio.sleep(0.05)
        # Find the pending_id
        pending_id = next(iter(mgr._pending))
        mgr.authorize(pending_id, "approve")

    result_holder = []

    async def run():
        task = asyncio.create_task(mgr.check(make_action("grip"), make_thought()))
        await approve_soon()
        result_holder.append(await task)

    await run()
    assert result_holder[0] is True


# ---------------------------------------------------------------------------
# approval_required=True — deny path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_required_deny():
    mgr = HiTLGateManager(
        gates=[HiTLGate(action_types=["grip"], require_auth=True, auth_timeout_ms=5000)]
    )

    async def deny_soon():
        await asyncio.sleep(0.05)
        pending_id = next(iter(mgr._pending))
        mgr.authorize(pending_id, "deny")

    result_holder = []

    async def run():
        task = asyncio.create_task(mgr.check(make_action("grip"), make_thought()))
        await deny_soon()
        result_holder.append(await task)

    await run()
    assert result_holder[0] is False


# ---------------------------------------------------------------------------
# Timeout path — on_timeout=block → returns False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_blocks():
    mgr = HiTLGateManager(
        gates=[
            HiTLGate(
                action_types=["grip"],
                require_auth=True,
                auth_timeout_ms=50,  # 50 ms timeout
                on_timeout="block",
            )
        ]
    )
    result = await mgr.check(make_action("grip"), make_thought())
    assert result is False


# ---------------------------------------------------------------------------
# Timeout path — on_timeout=allow → returns True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_allows_when_on_timeout_allow():
    mgr = HiTLGateManager(
        gates=[
            HiTLGate(
                action_types=["grip"],
                require_auth=True,
                auth_timeout_ms=50,
                on_timeout="allow",
            )
        ]
    )
    result = await mgr.check(make_action("grip"), make_thought())
    assert result is True


# ---------------------------------------------------------------------------
# authorize() returns False for unknown pending_id
# ---------------------------------------------------------------------------


def test_authorize_unknown_pending_id():
    mgr = HiTLGateManager(gates=[])
    assert mgr.authorize("no-such-id", "approve") is False


# ---------------------------------------------------------------------------
# notify_fn injection — wires HiTL gates to the channel dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_fn_invoked_on_start_pending():
    """When notify_fn is set, start_pending must schedule a call to it
    with (channels, msg)."""
    import asyncio

    from castor.hitl_gate import HiTLGate, HiTLGateManager

    recorded: list[tuple[list[str], str]] = []

    async def recorder(channels: list[str], msg: str) -> None:
        recorded.append((list(channels), msg))

    gates = [HiTLGate(action_types=["pick_place"], notify=["whatsapp"])]
    mgr = HiTLGateManager(gates, notify_fn=recorder)

    pending_id = mgr.start_pending({"type": "pick_place"}, thought=None)
    assert pending_id  # gate matched → non-empty

    # start_pending fires _notify via asyncio.create_task; flush it
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(recorded) == 1
    channels, msg = recorded[0]
    assert channels == ["whatsapp"]
    assert "pick_place" in msg
    assert pending_id in msg


@pytest.mark.asyncio
async def test_notify_fn_none_falls_back_to_log(caplog):
    """When notify_fn is None (today's behavior), _notify just logs."""
    import asyncio
    import logging

    from castor.hitl_gate import HiTLGate, HiTLGateManager

    gates = [HiTLGate(action_types=["pick_place"], notify=["whatsapp"])]
    mgr = HiTLGateManager(gates, notify_fn=None)

    with caplog.at_level(logging.INFO, logger="OpenCastor.HiTLGate"):
        pending_id = mgr.start_pending({"type": "pick_place"}, thought=None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert pending_id
    # The today-behavior log line must still appear
    assert any("HiTL notify channels=" in r.message for r in caplog.records)

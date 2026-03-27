"""P66 Protocol 66 safety invariants — harness integration.

This test class is the P66 contract for the harness.
ALL invariants must pass before any harness PR is merged.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from castor.harness import (
    AgentHarness,
    HarnessContext,
)
from castor.providers.base import Thought
from castor.tools import ToolRegistry


def _make_provider(text="ok", tool_calls=None):
    p = MagicMock()
    t = Thought(raw_text=text, tool_calls=tool_calls or [])
    p.think.return_value = t
    p.think_with_tools.return_value = t
    p.model_name = "test"
    return p


def _harness(provider=None, scope_override=None):
    p = provider or _make_provider()
    cfg = {
        "harness": {
            "enabled": True,
            "max_iterations": 3,
            "p66_audit": False,
            "retry_on_error": False,
        }
    }
    return AgentHarness(provider=p, config=cfg, tool_registry=ToolRegistry())


# ═══════════════════════════════════════════════════════════════════════════════
# P66 INVARIANT 1 — ESTOP bypasses context building
# ═══════════════════════════════════════════════════════════════════════════════


class TestP66_Invariant1_ESTOPBypassesContext:
    @pytest.mark.asyncio
    async def test_estop_scope_bypasses_context_builder(self):
        """scope=safety must skip ContextBuilder entirely."""
        provider = _make_provider("stopped")
        h = _harness(provider)
        ctx = HarnessContext(instruction="stop", scope="safety")
        result = await h.run(ctx)
        assert result.p66_estop_bypassed
        # think() called directly — no tool loop
        provider.think.assert_called_once()

    @pytest.mark.asyncio
    async def test_estop_keyword_bypasses_context(self):
        for kw in ("ESTOP", "EMERGENCY STOP", "E-STOP", "HALT EVERYTHING"):
            provider = _make_provider("stopped")
            h = _harness(provider)
            ctx = HarnessContext(instruction=kw, scope="chat")
            result = await h.run(ctx)
            assert result.p66_estop_bypassed, f"ESTOP bypass failed for: {kw!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# P66 INVARIANT 2 — ESTOP bypasses tool loop
# ═══════════════════════════════════════════════════════════════════════════════


class TestP66_Invariant2_ESTOPBypassesToolLoop:
    @pytest.mark.asyncio
    async def test_estop_no_tool_calls(self):
        provider = _make_provider("halted")
        h = _harness(provider)
        ctx = HarnessContext(instruction="ESTOP", scope="safety")
        result = await h.run(ctx)
        assert result.p66_estop_bypassed
        assert result.tools_called == []

    @pytest.mark.asyncio
    async def test_estop_no_iteration_limit_applied(self):
        """ESTOP completes in one provider call regardless of max_iterations=0."""
        provider = _make_provider("halted")
        cfg = {
            "harness": {
                "enabled": True,
                "max_iterations": 0,
                "p66_audit": False,
                "retry_on_error": False,
            }
        }
        h = AgentHarness(provider=provider, config=cfg, tool_registry=ToolRegistry())
        ctx = HarnessContext(instruction="ESTOP", scope="safety")
        result = await h.run(ctx)
        assert result.p66_estop_bypassed
        assert result.error is None


# ═══════════════════════════════════════════════════════════════════════════════
# P66 INVARIANT 3 — ESTOP bypasses secondary veto (when dual-model is added)
# ═══════════════════════════════════════════════════════════════════════════════


class TestP66_Invariant3_ESTOPBypassesSecondaryVeto:
    @pytest.mark.asyncio
    async def test_estop_no_secondary_veto_flag(self):
        """ESTOP result must NOT have p66_blocked=True."""
        provider = _make_provider("halted")
        h = _harness(provider)
        ctx = HarnessContext(instruction="ESTOP", scope="safety")
        result = await h.run(ctx)
        assert result.p66_estop_bypassed
        assert result.p66_blocked is False


# ═══════════════════════════════════════════════════════════════════════════════
# P66 INVARIANT 4 — ESTOP bypasses consent dialog
# ═══════════════════════════════════════════════════════════════════════════════


class TestP66_Invariant4_ESTOPNeverRequiresConsent:
    @pytest.mark.asyncio
    async def test_estop_consent_false_still_executes(self):
        provider = _make_provider("stopped")
        h = _harness(provider)
        ctx = HarnessContext(instruction="ESTOP", scope="control", consent_granted=False)
        result = await h.run(ctx)
        assert result.p66_estop_bypassed
        assert result.p66_consent_required is False

    @pytest.mark.asyncio
    async def test_estop_in_chat_scope_still_executes(self):
        """ESTOP in chat scope (wrong scope but ESTOP keyword) must still bypass."""
        provider = _make_provider("stopped")
        h = _harness(provider)
        ctx = HarnessContext(instruction="ESTOP NOW", scope="chat", consent_granted=False)
        result = await h.run(ctx)
        assert result.p66_estop_bypassed


# ═══════════════════════════════════════════════════════════════════════════════
# P66 INVARIANT 5 — Physical tool in chat scope → blocked
# ═══════════════════════════════════════════════════════════════════════════════


class TestP66_Invariant5_PhysicalToolBlockedInChatScope:
    @pytest.mark.asyncio
    async def test_move_blocked_in_chat_scope(self):
        """move tool must not execute in chat scope regardless of model output."""
        tool_calls = [{"name": "move", "args": {"linear": 1.0}}]
        provider = _make_provider(tool_calls=tool_calls)
        provider.think_with_tools.return_value = Thought(raw_text="", tool_calls=tool_calls)
        h = _harness(provider)
        ctx = HarnessContext(instruction="go forward", scope="chat", consent_granted=True)
        result = await h.run(ctx)
        # Either blocked record or consent-request response
        blocked_calls = [r for r in result.tools_called if r.p66_blocked]
        is_consent_response = "confirm" in result.thought.raw_text.lower()
        assert blocked_calls or is_consent_response, (
            "Physical tool in chat scope must be blocked or trigger consent request"
        )

    @pytest.mark.asyncio
    async def test_physical_tool_not_in_chat_schema(self):
        """Physical tools must not appear in tool schema for chat scope."""
        reg = ToolRegistry()
        reg.register("move", lambda **kw: None, description="Move")
        h = AgentHarness(
            provider=_make_provider(),
            config={"harness": {"enabled": True, "p66_audit": False, "retry_on_error": False}},
            tool_registry=reg,
        )
        tools = h._get_tools_for_scope("chat")
        names = {t.get("function", {}).get("name", "") for t in tools}
        assert "move" not in names


# ═══════════════════════════════════════════════════════════════════════════════
# P66 INVARIANT 6 — Consent required → consent-request returned before tool exec
# ═══════════════════════════════════════════════════════════════════════════════


class TestP66_Invariant6_ConsentRequiredBeforePhysical:
    @pytest.mark.asyncio
    async def test_physical_tool_consent_not_granted(self):
        tool_calls = [{"name": "grip", "args": {"state": "close"}}]
        provider = _make_provider()
        provider.think_with_tools.return_value = Thought(raw_text="", tool_calls=tool_calls)
        h = _harness(provider)
        ctx = HarnessContext(instruction="grab the brick", scope="control", consent_granted=False)
        result = await h.run(ctx)
        assert result.p66_consent_required is True
        # Must NOT have actually executed grip
        executed = [r for r in result.tools_called if r.tool_name == "grip" and not r.p66_blocked]
        assert len(executed) == 0, "grip must not execute without consent"


# ═══════════════════════════════════════════════════════════════════════════════
# P66 INVARIANT — Trajectory record always has P66 audit fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestP66_TrajectoryAuditFields:
    @pytest.mark.asyncio
    async def test_result_has_p66_fields(self):
        h = _harness()
        ctx = HarnessContext(instruction="hello", scope="chat")
        result = await h.run(ctx)
        # All P66 fields present
        assert hasattr(result, "p66_consent_required")
        assert hasattr(result, "p66_consent_granted")
        assert hasattr(result, "p66_blocked")
        assert hasattr(result, "p66_estop_bypassed")

    @pytest.mark.asyncio
    async def test_estop_result_p66_flags(self):
        h = _harness()
        ctx = HarnessContext(instruction="ESTOP", scope="safety")
        result = await h.run(ctx)
        assert result.p66_estop_bypassed is True
        assert result.p66_blocked is False
        assert result.p66_consent_required is False

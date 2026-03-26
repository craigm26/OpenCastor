"""Tests for castor/harness.py — AgentHarness orchestrator.

Covers:
  - P66 ESTOP bypass (all paths)
  - Physical tool consent gating
  - Scope-based tool filtering
  - Legacy mode fallback
  - Tool execution loop
  - Hook lifecycle
  - Error recovery
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from castor.harness import (
    ESTOP_TOOLS,
    PHYSICAL_TOOLS,
    SCOPE_LEVELS,
    AgentHarness,
    HarnessContext,
    HarnessHook,
    HarnessResult,
)
from castor.providers.base import Thought
from castor.tools import ToolRegistry

# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_provider(response_text="Hello!", tool_calls=None):
    """Create a mock provider that returns a given Thought."""
    provider = MagicMock()
    thought = Thought(raw_text=response_text)
    if tool_calls:
        thought.tool_calls = tool_calls
    else:
        thought.tool_calls = []
    provider.think.return_value = thought
    provider.think_with_tools.return_value = thought
    provider.model_name = "test-model"
    return provider


def _make_harness(provider=None, config=None, enabled=True):
    provider = provider or _make_provider()
    cfg = config or {"harness": {"enabled": enabled, "max_iterations": 3}}
    reg = ToolRegistry()
    # Suppress P66 audit and retry hooks in tests
    cfg.setdefault("harness", {})
    cfg["harness"]["p66_audit"] = False
    cfg["harness"]["retry_on_error"] = False
    return AgentHarness(provider=provider, config=cfg, tool_registry=reg)


# ── P66 ESTOP bypass ──────────────────────────────────────────────────────────

class TestP66ESTOPBypass:
    """P66 invariant: ESTOP bypasses ALL harness steps."""

    @pytest.mark.asyncio
    async def test_safety_scope_bypasses_harness(self):
        provider = _make_provider("STOP executed")
        harness = _make_harness(provider)
        ctx = HarnessContext(instruction="stop", scope="safety")
        result = await harness.run(ctx)
        assert result.p66_estop_bypassed is True
        provider.think.assert_called_once()

    @pytest.mark.asyncio
    async def test_estop_keyword_bypasses_harness(self):
        provider = _make_provider("ESTOP engaged")
        harness = _make_harness(provider)
        for keyword in ("ESTOP now", "E-STOP", "EMERGENCY STOP all motors"):
            ctx = HarnessContext(instruction=keyword, scope="chat")
            result = await harness.run(ctx)
            assert result.p66_estop_bypassed is True, f"Expected bypass for: {keyword!r}"

    @pytest.mark.asyncio
    async def test_estop_ignores_max_iterations(self):
        """ESTOP never hits iteration limit."""
        provider = _make_provider("stopped")
        harness = _make_harness(provider, config={"harness": {"enabled": True, "max_iterations": 0}})
        ctx = HarnessContext(instruction="ESTOP", scope="safety")
        result = await harness.run(ctx)
        assert result.p66_estop_bypassed is True
        assert result.error is None

    @pytest.mark.asyncio
    async def test_estop_never_requires_consent(self):
        provider = _make_provider("halted")
        harness = _make_harness(provider)
        ctx = HarnessContext(instruction="ESTOP", scope="control", consent_granted=False)
        result = await harness.run(ctx)
        assert result.p66_estop_bypassed is True
        assert result.p66_blocked is False

    @pytest.mark.asyncio
    async def test_normal_instruction_not_bypassed(self):
        provider = _make_provider("Here is the weather.")
        harness = _make_harness(provider)
        ctx = HarnessContext(instruction="What is the weather?", scope="chat")
        result = await harness.run(ctx)
        assert result.p66_estop_bypassed is False


# ── Physical tool consent gating ──────────────────────────────────────────────

class TestP66PhysicalToolConsent:
    """P66: physical tools blocked without consent or wrong scope."""

    @pytest.mark.asyncio
    async def test_physical_tool_blocked_in_chat_scope(self):
        """move tool must be blocked when scope=chat."""
        tool_calls = [{"name": "move", "args": {"linear": 0.5}}]
        provider = _make_provider(tool_calls=tool_calls)
        # Second call returns text (no more tool calls)
        provider.think_with_tools.side_effect = [
            Thought(raw_text="", tool_calls=tool_calls),
            Thought(raw_text="Blocked response"),
        ]
        harness = _make_harness(provider)
        ctx = HarnessContext(instruction="go forward", scope="chat", consent_granted=False)
        result = await harness.run(ctx)
        blocked = [r for r in result.tools_called if r.p66_blocked]
        assert len(blocked) > 0 or "confirm" in result.thought.raw_text.lower()

    @pytest.mark.asyncio
    async def test_physical_tool_blocked_without_consent(self):
        """move tool blocked in control scope if consent not granted."""
        tool_calls = [{"name": "move", "args": {"linear": 0.5}}]
        thought_with_tool = Thought(raw_text="", tool_calls=tool_calls)
        provider = _make_provider()
        provider.think_with_tools.return_value = thought_with_tool
        harness = _make_harness(provider)
        ctx = HarnessContext(instruction="go forward", scope="control", consent_granted=False)
        result = await harness.run(ctx)
        assert result.p66_consent_required is True
        # Should see consent-request response
        assert "confirm" in result.thought.raw_text.lower() or result.p66_blocked

    @pytest.mark.asyncio
    async def test_non_physical_tool_auto_approved(self):
        """web_search should not require consent."""
        reg = ToolRegistry()
        reg.register("web_search", lambda query="": [{"title": "test"}],
                     description="Search the web",
                     parameters={"query": {"type": "string", "required": True}})
        tool_calls = [{"name": "web_search", "args": {"query": "lego bricks"}}]
        thought_with_tool = Thought(raw_text="", tool_calls=tool_calls)
        final_thought = Thought(raw_text="Lego bricks are plastic interlocking toys.")
        provider = _make_provider()
        provider.think_with_tools.side_effect = [thought_with_tool, final_thought]
        harness = AgentHarness(provider=provider, config={"harness": {"enabled": True, "max_iterations": 3, "p66_audit": False, "retry_on_error": False}}, tool_registry=reg)
        ctx = HarnessContext(instruction="what are lego bricks?", scope="chat")
        result = await harness.run(ctx)
        # Should NOT be blocked
        blocked = [r for r in result.tools_called if r.p66_blocked]
        assert len(blocked) == 0


# ── Scope-based tool filtering ─────────────────────────────────────────────────

class TestScopeFiltering:
    """P66: physical tools not advertised in non-control scopes."""

    def test_chat_scope_excludes_physical_tools(self):
        harness = _make_harness()
        tools = harness._get_tools_for_scope("chat")
        tool_names = {t.get("function", {}).get("name", "") for t in tools}
        for phys_tool in PHYSICAL_TOOLS:
            if phys_tool in harness._tool_registry.list_tools():
                assert phys_tool not in tool_names, f"{phys_tool} should not appear in chat scope"

    def test_control_scope_includes_physical_tools(self):
        reg = ToolRegistry()
        reg.register("move", lambda **kw: "moved", description="Move robot",
                     parameters={"linear": {"type": "number"}})
        harness = AgentHarness(provider=_make_provider(), tool_registry=reg,
                                config={"harness": {"enabled": True, "p66_audit": False, "retry_on_error": False}})
        tools = harness._get_tools_for_scope("control")
        tool_names = {t.get("function", {}).get("name", "") for t in tools}
        assert "move" in tool_names

    def test_scope_levels_ordered_correctly(self):
        assert SCOPE_LEVELS["discover"] < SCOPE_LEVELS["chat"]
        assert SCOPE_LEVELS["chat"] < SCOPE_LEVELS["control"]
        assert SCOPE_LEVELS["control"] < SCOPE_LEVELS["safety"]


# ── Legacy mode ───────────────────────────────────────────────────────────────

class TestLegacyMode:
    """harness.enabled=false should behave identically to direct provider.think()."""

    @pytest.mark.asyncio
    async def test_legacy_mode_calls_think_directly(self):
        provider = _make_provider("legacy response")
        harness = _make_harness(provider, enabled=False)
        ctx = HarnessContext(instruction="hello", scope="chat")
        result = await harness.run(ctx)
        assert result.thought.raw_text == "legacy response"
        provider.think.assert_called_once()
        assert result.p66_estop_bypassed is False
        assert len(result.tools_called) == 0

    @pytest.mark.asyncio
    async def test_legacy_mode_preserves_estop(self):
        """Even in legacy mode, ESTOP bypass fires first."""
        provider = _make_provider("stopped")
        harness = _make_harness(provider, enabled=False)
        ctx = HarnessContext(instruction="ESTOP all", scope="safety")
        result = await harness.run(ctx)
        assert result.p66_estop_bypassed is True


# ── Hook lifecycle ─────────────────────────────────────────────────────────────

class TestHookLifecycle:
    """Hooks must be called at correct lifecycle points."""

    @pytest.mark.asyncio
    async def test_pre_turn_called(self):
        hook = MagicMock(spec=HarnessHook)
        hook.on_pre_turn = AsyncMock()
        hook.on_post_turn = AsyncMock()
        hook.on_tool_call = AsyncMock()
        provider = _make_provider("hi")
        harness = _make_harness(provider)
        harness.hooks = [hook]
        ctx = HarnessContext(instruction="hello", scope="chat")
        await harness.run(ctx)
        hook.on_pre_turn.assert_called_once()

    @pytest.mark.asyncio
    async def test_post_turn_called(self):
        hook = MagicMock(spec=HarnessHook)
        hook.on_pre_turn = AsyncMock()
        hook.on_post_turn = AsyncMock()
        hook.on_tool_call = AsyncMock()
        provider = _make_provider("response")
        harness = _make_harness(provider)
        harness.hooks = [hook]
        ctx = HarnessContext(instruction="hello", scope="chat")
        await harness.run(ctx)
        hook.on_post_turn.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_hook_recovers(self):
        recovery_thought = Thought(raw_text="recovered")
        recovery_result = HarnessResult(thought=recovery_thought)

        hook = MagicMock(spec=HarnessHook)
        hook.on_error = AsyncMock(return_value=recovery_result)

        provider = _make_provider()
        provider.think.side_effect = RuntimeError("provider down")
        provider.think_with_tools.side_effect = RuntimeError("provider down")
        harness = _make_harness(provider)
        harness.hooks = [hook]
        ctx = HarnessContext(instruction="hello", scope="chat")
        result = await harness.run(ctx)
        # Should either recover via hook or return graceful degradation
        assert result is not None
        assert result.thought.raw_text != ""


# ── Result fields ─────────────────────────────────────────────────────────────

class TestResultFields:
    @pytest.mark.asyncio
    async def test_run_id_unique(self):
        harness = _make_harness()
        ctx = HarnessContext(instruction="hello", scope="chat")
        r1 = await harness.run(ctx)
        r2 = await harness.run(ctx)
        assert r1.run_id != r2.run_id

    @pytest.mark.asyncio
    async def test_latency_positive(self):
        harness = _make_harness()
        ctx = HarnessContext(instruction="hello", scope="chat")
        result = await harness.run(ctx)
        assert result.total_latency_ms >= 0


# ── Constant correctness ──────────────────────────────────────────────────────

class TestConstants:
    def test_physical_tools_frozenset(self):
        assert "move" in PHYSICAL_TOOLS
        assert "grip" in PHYSICAL_TOOLS
        assert "web_search" not in PHYSICAL_TOOLS
        assert "get_status" not in PHYSICAL_TOOLS

    def test_estop_tools_frozenset(self):
        assert "emergency_stop" in ESTOP_TOOLS
        assert "halt" in ESTOP_TOOLS
        assert "move" not in ESTOP_TOOLS

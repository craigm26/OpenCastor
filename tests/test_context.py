"""Tests for castor/context.py — ContextBuilder."""

from __future__ import annotations

import pytest

from castor.context import BuiltContext, ContextBuilder
from castor.harness import HarnessContext
from castor.tools import ToolRegistry


def _builder(config=None, tool_registry=None):
    cfg = config or {
        "name": "TestBot",
        "model": "gemini-2.5-flash",
        "harness": {"auto_rag": False, "auto_telemetry": False, "context_budget": 0.8},
    }
    reg = tool_registry or ToolRegistry()
    return ContextBuilder(config=cfg, tool_registry=reg)


class TestContextBuilder:
    @pytest.mark.asyncio
    async def test_builds_context(self):
        builder = _builder()
        ctx = HarnessContext(instruction="hello", scope="chat")
        built = await builder.build(ctx, history=[])
        assert isinstance(built, BuiltContext)
        assert "TestBot" in built.system_prompt
        assert len(built.messages) >= 1
        assert built.token_estimate > 0

    @pytest.mark.asyncio
    async def test_instruction_in_messages(self):
        builder = _builder()
        ctx = HarnessContext(instruction="what do you see?", scope="chat")
        built = await builder.build(ctx, history=[])
        last_msg = built.messages[-1]
        assert last_msg["role"] == "user"
        assert "what do you see?" in last_msg["content"]

    @pytest.mark.asyncio
    async def test_history_preserved(self):
        builder = _builder()
        history = [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "first response"},
        ]
        ctx = HarnessContext(instruction="follow-up", scope="chat")
        built = await builder.build(ctx, history=history)
        assert len(built.messages) == 3  # 2 history + 1 current
        assert built.messages[0]["content"] == "first message"

    @pytest.mark.asyncio
    async def test_persona_contains_name(self):
        builder = _builder(config={
            "name": "RoboCastor",
            "model": "gemini-2.5-flash",
            "harness": {"auto_rag": False, "auto_telemetry": False},
        })
        ctx = HarnessContext(instruction="hi", scope="chat")
        built = await builder.build(ctx, history=[])
        assert "RoboCastor" in built.system_prompt

    @pytest.mark.asyncio
    async def test_chat_scope_excludes_physical_tools(self):
        """P66: physical tools should not appear in tool section for chat scope."""
        reg = ToolRegistry()
        reg.register("move", lambda **kw: None, description="Move the robot")
        builder = _builder(tool_registry=reg)
        ctx = HarnessContext(instruction="hi", scope="chat")
        built = await builder.build(ctx, history=[])
        assert "move" not in built.system_prompt

    @pytest.mark.asyncio
    async def test_control_scope_includes_physical_tools(self):
        reg = ToolRegistry()
        reg.register("move", lambda **kw: None, description="Move the robot")
        builder = _builder(tool_registry=reg)
        ctx = HarnessContext(instruction="go forward", scope="control")
        built = await builder.build(ctx, history=[])
        assert "move" in built.system_prompt

    @pytest.mark.asyncio
    async def test_token_estimate_positive(self):
        builder = _builder()
        ctx = HarnessContext(instruction="test", scope="chat")
        built = await builder.build(ctx, history=[])
        assert built.token_estimate > 0

    @pytest.mark.asyncio
    async def test_rag_skipped_when_disabled(self):
        builder = _builder(config={
            "name": "TestBot",
            "model": "default",
            "harness": {"auto_rag": False, "auto_telemetry": False},
        })
        ctx = HarnessContext(instruction="hello", scope="chat")
        built = await builder.build(ctx, history=[])
        assert built.rag_chunks == 0
        assert "[MEMORY" not in built.system_prompt

    @pytest.mark.asyncio
    async def test_telemetry_skipped_when_disabled(self):
        builder = _builder(config={
            "name": "TestBot",
            "model": "default",
            "harness": {"auto_rag": False, "auto_telemetry": False},
        })
        ctx = HarnessContext(instruction="hello", scope="chat")
        built = await builder.build(ctx, history=[])
        assert built.telemetry_injected is False
        assert "[ROBOT STATUS]" not in built.system_prompt

    def test_context_limit_gemini(self):
        builder = _builder(config={"model": "gemini-2.5-flash", "harness": {}})
        assert builder._context_limit == 1_000_000

    def test_context_limit_default(self):
        builder = _builder(config={"model": "unknown-model-xyz", "harness": {}})
        assert builder._context_limit == 32_768

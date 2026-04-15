"""Tests for post-compaction continuation injection in the harness pipeline.

Verifies that after ContextBuilder.build() fires compaction, the harness
inserts a system continuation message as the first message in the turn —
preventing the cold-start recap turns described in issue #848.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from castor.context import BuiltContext


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_built_context(was_compacted: bool, compact_summary: str = "") -> BuiltContext:
    return BuiltContext(
        system_prompt="You are a robot.",
        messages=[{"role": "user", "content": "do the thing"}],
        token_estimate=100,
        was_compacted=was_compacted,
        compact_summary=compact_summary,
    )


# ── 1. BuiltContext carries compact_summary ───────────────────────────────────


def test_built_context_has_compact_summary_field():
    ctx = _make_built_context(was_compacted=True, compact_summary="robot drove to waypoint A")
    assert ctx.compact_summary == "robot drove to waypoint A"
    assert ctx.was_compacted is True


def test_built_context_compact_summary_default_empty():
    ctx = _make_built_context(was_compacted=False)
    assert ctx.compact_summary == ""


# ── 2. _compact_history returns summary text ──────────────────────────────────


@pytest.mark.asyncio
async def test_compact_history_returns_summary_text():
    from castor.context import ContextBuilder

    builder = ContextBuilder()

    with patch.object(builder, "_summarise_messages", new=AsyncMock(return_value="test summary")):
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        compacted, was_compacted, summary = await builder._compact_history(
            messages, budget_tokens=1
        )

    assert was_compacted is True
    assert summary == "test summary"
    # Summary message is prepended in the compacted list
    assert compacted[0]["role"] == "system"
    assert "test summary" in compacted[0]["content"]


@pytest.mark.asyncio
async def test_compact_history_fallback_returns_empty_summary():
    from castor.context import ContextBuilder

    builder = ContextBuilder()

    with patch.object(
        builder,
        "_summarise_messages",
        new=AsyncMock(side_effect=RuntimeError("no provider")),
    ):
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        compacted, was_compacted, summary = await builder._compact_history(
            messages, budget_tokens=1
        )

    assert was_compacted is True
    assert summary == ""  # fallback returns empty string


# ── 3. Harness injects continuation when compacted ───────────────────────────


@pytest.mark.asyncio
async def test_harness_injects_continuation_on_compaction():
    """When build() returns was_compacted=True + compact_summary, the harness
    inserts a system continuation message as the first message."""
    from castor.harness.core import AgentHarness, HarnessContext

    harness = AgentHarness.__new__(AgentHarness)
    harness.hooks = []
    harness.span_tracer = None

    built = _make_built_context(was_compacted=True, compact_summary="robot navigated to zone B")

    ctx = HarnessContext(
        instruction="continue patrol",
        mission_state={},
    )

    captured_messages: list = []

    async def fake_tool_loop(ctx, built, **kwargs):
        captured_messages.extend(built.messages)
        mock_thought = MagicMock()
        mock_thought.raw_text = "ok"
        return mock_thought, [], 1

    with (
        patch.object(harness, "_get_context_builder") as mock_builder_factory,
        patch.object(harness, "_tool_loop", new=fake_tool_loop),
        patch.object(harness, "_log_trajectory", new=AsyncMock()),
        patch("castor.harness.core.asyncio.ensure_future"),
    ):
        mock_builder = MagicMock()
        mock_builder.build = AsyncMock(return_value=built)
        mock_builder_factory.return_value = mock_builder

        import time

        await harness._run_pipeline(ctx, run_id="r1", t0=time.perf_counter())

    # Continuation message should be first
    assert len(captured_messages) >= 2
    first = captured_messages[0]
    assert first["role"] == "system"
    assert "<compaction-summary>" in first["content"]
    assert "robot navigated to zone B" in first["content"]


@pytest.mark.asyncio
async def test_harness_no_injection_when_not_compacted():
    """When was_compacted=False, no continuation message is injected."""
    from castor.harness.core import AgentHarness, HarnessContext

    harness = AgentHarness.__new__(AgentHarness)
    harness.hooks = []
    harness.span_tracer = None

    built = _make_built_context(was_compacted=False)

    ctx = HarnessContext(
        instruction="status check",
        mission_state={},
    )

    captured_messages: list = []

    async def fake_tool_loop(ctx, built, **kwargs):
        captured_messages.extend(built.messages)
        mock_thought = MagicMock()
        mock_thought.raw_text = "ok"
        return mock_thought, [], 1

    with (
        patch.object(harness, "_get_context_builder") as mock_builder_factory,
        patch.object(harness, "_tool_loop", new=fake_tool_loop),
        patch.object(harness, "_log_trajectory", new=AsyncMock()),
        patch("castor.harness.core.asyncio.ensure_future"),
    ):
        mock_builder = MagicMock()
        mock_builder.build = AsyncMock(return_value=built)
        mock_builder_factory.return_value = mock_builder

        import time

        await harness._run_pipeline(ctx, run_id="r2", t0=time.perf_counter())

    # No continuation message — only the original user message
    assert len(captured_messages) == 1
    assert captured_messages[0]["role"] == "user"


@pytest.mark.asyncio
async def test_harness_suppress_follow_up_when_autonomous():
    """With mission_state["autonomous"]=True, continuation includes suppress_follow_up."""
    from castor.harness.core import AgentHarness, HarnessContext

    harness = AgentHarness.__new__(AgentHarness)
    harness.hooks = []
    harness.span_tracer = None

    built = _make_built_context(was_compacted=True, compact_summary="completed step 3 of 5")

    ctx = HarnessContext(
        instruction="continue",
        mission_state={"autonomous": True},
    )

    captured_messages: list = []

    async def fake_tool_loop(ctx, built, **kwargs):
        captured_messages.extend(built.messages)
        mock_thought = MagicMock()
        mock_thought.raw_text = "ok"
        return mock_thought, [], 1

    with (
        patch.object(harness, "_get_context_builder") as mock_builder_factory,
        patch.object(harness, "_tool_loop", new=fake_tool_loop),
        patch.object(harness, "_log_trajectory", new=AsyncMock()),
        patch("castor.harness.core.asyncio.ensure_future"),
    ):
        mock_builder = MagicMock()
        mock_builder.build = AsyncMock(return_value=built)
        mock_builder_factory.return_value = mock_builder

        import time

        await harness._run_pipeline(ctx, run_id="r3", t0=time.perf_counter())

    first = captured_messages[0]
    assert first["role"] == "system"
    assert "Do not acknowledge" in first["content"]

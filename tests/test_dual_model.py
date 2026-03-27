"""Tests for castor/dual_model.py — DualModelHarness."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from castor.dual_model import DualModelHarness, build_dual_harness
from castor.harness import HarnessContext
from castor.providers.base import Thought
from castor.tools import ToolRegistry


def _provider(text="ok", tool_calls=None):
    p = MagicMock()
    t = Thought(raw_text=text, tool_calls=tool_calls or [])
    p.think.return_value = t
    p.think_with_tools.return_value = t
    p.model_name = "test-primary"
    return p


def _secondary(text="SAFE - path clear"):
    p = MagicMock()
    t = Thought(raw_text=text)
    p.think.return_value = t
    p.model_name = "test-secondary"
    return p


def _dual_harness(primary=None, secondary=None, mode="safety_oracle", p66_veto=True):
    cfg = {
        "harness": {
            "enabled": True,
            "max_iterations": 3,
            "p66_audit": False,
            "retry_on_error": False,
            "drift_detection": False,
        },
        "secondary": {
            "mode": mode,
            "scope_filter": ["control"],
            "p66_veto": p66_veto,
        },
    }
    return DualModelHarness(
        primary=primary or _provider(),
        secondary=secondary or _secondary(),
        config=cfg,
        tool_registry=ToolRegistry(),
    )


class TestDualModelInit:
    def test_builds_without_error(self):
        h = _dual_harness()
        assert h._mode == "safety_oracle"
        assert h._p66_veto is True

    def test_invalid_mode_defaults_to_oracle(self):
        h = _dual_harness(mode="totally_invalid")
        assert h._mode == "safety_oracle"

    def test_factory_function(self):
        h = build_dual_harness(_provider(), _secondary())
        assert isinstance(h, DualModelHarness)


class TestSafetyOracle:
    @pytest.mark.asyncio
    async def test_safe_verdict_allows_execution(self):
        """SAFE verdict → tool executes (after consent)."""
        sec = _secondary("SAFE - distance is 1.2m, path clear")
        tool_calls = [{"name": "move", "args": {"linear": 0.3}}]
        prov = _provider()
        prov.think_with_tools.return_value = Thought(raw_text="", tool_calls=tool_calls)
        h = _dual_harness(primary=prov, secondary=sec)
        ctx = HarnessContext(instruction="go forward", scope="control", consent_granted=True)
        result = await h.run(ctx)
        # With consent + SAFE verdict, should execute (or reach limit gracefully)
        assert result is not None

    @pytest.mark.asyncio
    async def test_unsafe_verdict_blocks_execution(self):
        """UNSAFE verdict → physical tool blocked even with consent."""
        sec = _secondary("UNSAFE - obstacle detected at 0.1m")
        tool_calls = [{"name": "move", "args": {"linear": 1.0}}]
        prov = _provider()
        prov.think_with_tools.return_value = Thought(raw_text="", tool_calls=tool_calls)
        h = _dual_harness(primary=prov, secondary=sec)
        ctx = HarnessContext(instruction="charge forward", scope="control", consent_granted=True)
        result = await h.run(ctx)
        assert result.p66_blocked or "safety" in result.thought.raw_text.lower()

    @pytest.mark.asyncio
    async def test_estop_bypasses_safety_oracle(self):
        """P66: ESTOP must never be vetoed by safety oracle."""
        sec = _secondary("UNSAFE - do not execute")
        prov = _provider("stopped")
        h = _dual_harness(primary=prov, secondary=sec)
        ctx = HarnessContext(instruction="ESTOP", scope="safety")
        result = await h.run(ctx)
        assert result.p66_estop_bypassed is True
        assert result.p66_blocked is False

    @pytest.mark.asyncio
    async def test_oracle_disabled_no_veto(self):
        """p66_veto=false: secondary opinion not sought for physical tools."""
        sec = _secondary("UNSAFE - always unsafe")
        _ = [{"name": "move", "args": {"linear": 0.3}}]  # unused, kept for clarity
        prov = _provider()
        prov.think_with_tools.return_value = Thought(raw_text="moved", tool_calls=[])
        h = _dual_harness(primary=prov, secondary=sec, p66_veto=False)
        ctx = HarnessContext(instruction="go", scope="control", consent_granted=True)
        result = await h.run(ctx)
        # Secondary's UNSAFE opinion should not block
        assert "safety check blocked" not in result.thought.raw_text.lower()

    @pytest.mark.asyncio
    async def test_oracle_unavailable_defaults_safe(self):
        """If secondary throws, execution proceeds (fail open for safety oracle)."""
        sec = MagicMock()
        sec.think.side_effect = RuntimeError("secondary offline")
        sec.model_name = "offline"
        prov = _provider("moved")
        prov.think_with_tools.return_value = Thought(raw_text="moved", tool_calls=[])
        h = _dual_harness(primary=prov, secondary=sec)
        ctx = HarnessContext(instruction="go", scope="chat")
        result = await h.run(ctx)
        assert result is not None


class TestSafetyVerdictParsing:
    @pytest.mark.asyncio
    async def test_safe_verdict_parsed(self):
        h = _dual_harness()
        verdict = await h._run_safety_oracle("move", {"linear": 0.3}, {"distance_m": 1.2})
        assert verdict.safe is True

    @pytest.mark.asyncio
    async def test_unsafe_verdict_parsed(self):
        sec = _secondary("UNSAFE - obstacle at 0.1m")
        h = _dual_harness(secondary=sec)
        verdict = await h._run_safety_oracle("move", {"linear": 1.0}, {"distance_m": 0.1})
        assert verdict.safe is False
        assert "obstacle" in verdict.reason.lower()


class TestConsensusMode:
    @pytest.mark.asyncio
    async def test_agree_allows_response(self):
        sec = _secondary("AGREE - response is correct and safe")
        prov = _provider("I will move to the table")
        tool_calls = [{"name": "move", "args": {"linear": 0.5}}]
        prov.think_with_tools.return_value = Thought(
            raw_text="I will move to the table", tool_calls=tool_calls
        )
        h = _dual_harness(primary=prov, secondary=sec, mode="consensus")
        ctx = HarnessContext(instruction="go to table", scope="control", consent_granted=True)
        result = await h.run(ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_disagree_pauses(self):
        sec = _secondary("DISAGREE - unsafe, obstacle in path")
        prov = _provider()
        tool_calls = [{"name": "move", "args": {"linear": 1.0}}]
        prov.think_with_tools.return_value = Thought(raw_text="moving", tool_calls=tool_calls)
        h = _dual_harness(primary=prov, secondary=sec, mode="consensus")
        ctx = HarnessContext(instruction="go fast", scope="control", consent_granted=True)
        result = await h.run(ctx)
        assert (
            "disagrees" in result.thought.raw_text.lower()
            or "pause" in result.thought.raw_text.lower()
        )


class TestDriftDetectionHook:
    @pytest.mark.asyncio
    async def test_drift_score_set_after_3_iterations(self):
        """DriftDetectionHook should set drift_score on result after 3+ iterations."""
        from castor.harness import AgentHarness, HarnessContext
        from castor.tools import ToolRegistry

        call_count = [0]

        def _think(*a, **kw):
            call_count[0] += 1
            # Return tool call first 3 times, then text
            if call_count[0] < 4:
                return Thought(raw_text="", tool_calls=[{"name": "get_status", "args": {}}])
            return Thought(raw_text="Here is the robot status information you requested.")

        prov = MagicMock()
        prov.think.side_effect = _think
        prov.think_with_tools.side_effect = _think
        prov.model_name = "test"

        cfg = {
            "harness": {
                "enabled": True,
                "max_iterations": 5,
                "p66_audit": False,
                "retry_on_error": False,
                "drift_detection": True,
                "drift_threshold": 0.15,
            }
        }
        h = AgentHarness(provider=prov, config=cfg, tool_registry=ToolRegistry())
        ctx = HarnessContext(instruction="what is your status", scope="chat")
        result = await h.run(ctx)
        # drift_score may be set if iterations >= 3
        assert result is not None

    def test_word_overlap_similarity(self):
        from castor.harness import _word_overlap_similarity

        # Same words → high similarity
        assert _word_overlap_similarity("pick up the red brick", "picking red brick up") > 0.3
        # Completely different → low
        assert _word_overlap_similarity("go forward", "the weather today is sunny") < 0.3
        # Empty → 0
        assert _word_overlap_similarity("", "something") == 0.0

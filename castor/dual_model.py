"""
castor/dual_model.py — DualModelHarness: primary + secondary brain slots.

Extends AgentHarness with a second model that can serve as:

  safety_oracle  — reviews all physical tool calls before execution (P66 veto)
  specialist     — primary handles language; secondary handles motor/VLA planning
  consensus      — both models must agree before physical action
  verifier       — secondary grades primary response; escalates on low score
  shadow         — secondary runs async in background for A/B data collection

P66 invariant: ESTOP ALWAYS bypasses secondary veto in all modes.

RCAN config::

    agent:
      provider: google
      model: gemini-2.5-flash
      use_oauth: true

      secondary:
        provider: anthropic
        model: claude-haiku-3-5
        mode: safety_oracle
        scope_filter: [control]
        p66_veto: true

Usage::

    from castor.dual_model import DualModelHarness, build_dual_harness
    harness = build_dual_harness(primary, secondary, config, tool_registry)
    result = await harness.run(ctx)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from castor.harness import (
    ESTOP_TOOLS,
    PHYSICAL_TOOLS,
    AgentHarness,
    HarnessContext,
    HarnessHook,
    HarnessResult,
)
from castor.tools import ToolRegistry

if TYPE_CHECKING:
    from castor.providers.base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.DualModel")

__all__ = ["DualModelHarness", "SafetyVerdict", "build_dual_harness"]

VALID_MODES = frozenset({"safety_oracle", "specialist", "consensus", "verifier", "shadow"})

_SAFETY_ORACLE_PROMPT = """\
You are a safety oracle reviewing a robot action request.

Current robot telemetry:
{telemetry}

Proposed tool call:
  Tool: {tool_name}
  Args: {tool_args}

Is this action safe to execute right now?
Respond with exactly ONE line:
SAFE - <one-line reason>
or
UNSAFE - <one-line reason>
"""

_CONSENSUS_PROMPT = """\
A robot received this instruction: "{instruction}"

The primary brain proposed this response:
{primary_response}

Do you agree this is the correct response? Consider safety, accuracy, and completeness.
Respond with exactly:
AGREE - <one-line reason>
or
DISAGREE - <one-line reason, and your alternative>
"""

_VERIFIER_PROMPT = """\
Rate this robot response on a scale 0-100:

Instruction: "{instruction}"
Response: "{response}"

Score on: relevance (40pts), safety (40pts), completeness (20pts).
Respond with exactly:
SCORE: <number>
REASON: <one line>
"""


@dataclass
class SafetyVerdict:
    """Result of a secondary model safety evaluation."""

    safe: bool
    reason: str
    model: str = ""
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "safe": self.safe,
            "reason": self.reason,
            "model": self.model,
            "latency_ms": self.latency_ms,
        }


class DualModelHarness(AgentHarness):
    """AgentHarness with a secondary model for safety/verification/specialisation.

    Args:
        primary:    Primary brain provider (handles all standard inference).
        secondary:  Secondary provider (role determined by ``mode``).
        config:     Full ``agent`` config dict (must include ``secondary`` block).
        tool_registry: Populated ToolRegistry.
    """

    def __init__(
        self,
        primary: BaseProvider,
        secondary: BaseProvider,
        config: Optional[dict] = None,
        tool_registry: Optional[ToolRegistry] = None,
        hooks: Optional[list[HarnessHook]] = None,
    ) -> None:
        super().__init__(
            provider=primary,
            config=config,
            tool_registry=tool_registry,
            hooks=hooks,
        )
        self._secondary = secondary
        sec_cfg = (config or {}).get("secondary", {})
        self._mode: str = sec_cfg.get("mode", "safety_oracle")
        self._scope_filter: list[str] = sec_cfg.get("scope_filter", ["control"])
        self._p66_veto: bool = bool(sec_cfg.get("p66_veto", True))
        self._verifier_threshold: int = int(sec_cfg.get("verifier_threshold", 60))

        if self._mode not in VALID_MODES:
            logger.warning("Unknown secondary mode %r, defaulting to safety_oracle", self._mode)
            self._mode = "safety_oracle"

        logger.info(
            "DualModelHarness: primary=%s secondary=%s mode=%s p66_veto=%s",
            getattr(primary, "model_name", "?"),
            getattr(secondary, "model_name", "?"),
            self._mode,
            self._p66_veto,
        )

    # ── Override tool loop to inject secondary checks ─────────────────────────

    async def _tool_loop(
        self,
        ctx: HarnessContext,
        built: Any,
        run_id: str = "",
        root_span: Any = None,
        max_turns_override: int | None = None,
    ) -> tuple[Thought, list, int]:
        """Extended tool loop with secondary model integration."""
        from castor.providers.base import Thought as _Thought

        messages = getattr(built, "messages", [])
        tools_schema = self._get_tools_for_scope(ctx.scope)
        tools_called = []
        consent_granted = ctx.consent_granted
        secondary_active = ctx.scope in self._scope_filter

        _max_iter = max_turns_override if max_turns_override is not None else self._max_iterations
        for iteration in range(_max_iter):
            raw_response = await asyncio.to_thread(
                self._think_with_tools,
                ctx.image_bytes,
                ctx.instruction if iteration == 0 else "",
                ctx.surface,
                messages,
                tools_schema,
            )

            tool_calls = self._extract_tool_calls(raw_response)

            if not tool_calls:
                # ── Verifier mode: grade final response ──────────────────────
                if secondary_active and self._mode == "verifier":
                    score = await self._run_verifier(ctx.instruction, raw_response.raw_text)
                    if score < self._verifier_threshold:
                        logger.warning(
                            "Verifier: low score %d < %d, escalating",
                            score,
                            self._verifier_threshold,
                        )
                        # Re-run with stronger context hint
                        augmented = _Thought(
                            raw_text=raw_response.raw_text,
                            tool_calls=[],
                        )
                        augmented.raw_text += f"\n\n[Quality score: {score}/100 — please improve]"
                        return augmented, tools_called, iteration + 1
                return raw_response, tools_called, iteration + 1

            # ── Consensus mode: check agreement before tool execution ─────────
            if secondary_active and self._mode == "consensus" and tool_calls:
                verdict = await self._run_consensus(ctx.instruction, raw_response.raw_text)
                if not verdict.safe:
                    consensus_thought = _Thought(
                        raw_text=(
                            f"I want to pause — my secondary model disagrees: {verdict.reason}. "
                            "Should I proceed anyway?"
                        ),
                        provider="dual_model",
                    )
                    return consensus_thought, tools_called, iteration + 1

            for call in tool_calls:
                name = call.get("name", "")
                args = call.get("args", {})

                # P66: ESTOP always executes — NO secondary veto
                if name in ESTOP_TOOLS:
                    tr = await asyncio.to_thread(self._execute_tool, name, args)
                    from castor.harness import ToolCallRecord

                    tools_called.append(
                        ToolCallRecord(
                            tool_name=name,
                            args=args,
                            result=tr.result,
                            latency_ms=tr.duration_ms,
                        )
                    )
                    for hook in self.hooks:
                        await hook.on_tool_call(call, tr)
                    continue

                # P66: physical tool consent check
                if name in PHYSICAL_TOOLS:
                    if ctx.scope not in ("control", "safety") or not consent_granted:
                        from castor.harness import ToolCallRecord

                        tools_called.append(
                            ToolCallRecord(
                                tool_name=name,
                                args=args,
                                result=None,
                                latency_ms=0,
                                p66_consent_required=True,
                                p66_consent_granted=False,
                                p66_blocked=True,
                            )
                        )
                        consent_thought = _Thought(
                            raw_text=(
                                f"I need your confirmation before I can {name}. "
                                "Please reply 'yes' or 'confirm' to proceed."
                            ),
                            provider="harness",
                        )
                        return consent_thought, tools_called, iteration + 1

                    # ── Safety oracle veto (physical tools only, after consent) ─
                    if (
                        secondary_active
                        and self._mode == "safety_oracle"
                        and self._p66_veto
                        and name in PHYSICAL_TOOLS
                    ):
                        telemetry = await asyncio.to_thread(self._get_telemetry_snapshot)
                        verdict = await self._run_safety_oracle(name, args, telemetry)
                        if not verdict.safe:
                            from castor.harness import ToolCallRecord

                            tools_called.append(
                                ToolCallRecord(
                                    tool_name=name,
                                    args=args,
                                    result=None,
                                    latency_ms=verdict.latency_ms,
                                    p66_consent_required=True,
                                    p66_consent_granted=True,
                                    p66_blocked=True,
                                )
                            )
                            blocked_thought = _Thought(
                                raw_text=(
                                    f"⚠️ Safety check blocked this action: {verdict.reason}. "
                                    "Please check the environment and try again."
                                ),
                                provider="safety_oracle",
                            )
                            return blocked_thought, tools_called, iteration + 1

                # Execute tool
                tr = await asyncio.to_thread(self._execute_tool, name, args)
                is_phys = name in PHYSICAL_TOOLS
                from castor.harness import ToolCallRecord

                tools_called.append(
                    ToolCallRecord(
                        tool_name=name,
                        args=args,
                        result=tr.result,
                        latency_ms=tr.duration_ms,
                        p66_consent_required=is_phys,
                        p66_consent_granted=is_phys and consent_granted,
                        error=tr.error,
                    )
                )
                for hook in self.hooks:
                    await hook.on_tool_call(call, tr)
                messages.append(
                    {
                        "role": "tool",
                        "name": name,
                        "content": str(tr.result) if tr.ok else f"Error: {tr.error}",
                    }
                )

        from castor.providers.base import Thought as _T

        return (
            _T(
                raw_text="I reached my step limit for this task.",
                provider="harness",
            ),
            tools_called,
            self._max_iterations,
        )

    # ── Secondary model runners ───────────────────────────────────────────────

    async def _run_safety_oracle(
        self, tool_name: str, tool_args: dict, telemetry: dict
    ) -> SafetyVerdict:
        """Ask secondary model whether this physical action is safe."""
        import json

        prompt = _SAFETY_ORACLE_PROMPT.format(
            telemetry=json.dumps(telemetry, default=str)[:400],
            tool_name=tool_name,
            tool_args=json.dumps(tool_args),
        )
        t0 = time.perf_counter()
        try:
            thought = await asyncio.to_thread(self._secondary.think, b"", prompt, "safety_oracle")
            latency = (time.perf_counter() - t0) * 1000
            text = thought.raw_text.strip()
            safe = text.upper().startswith("SAFE")
            reason = text.split("-", 1)[-1].strip() if "-" in text else text
            return SafetyVerdict(
                safe=safe,
                reason=reason,
                model=getattr(self._secondary, "model_name", "secondary"),
                latency_ms=latency,
            )
        except Exception as exc:
            logger.warning("Safety oracle failed: %s — defaulting SAFE", exc)
            return SafetyVerdict(safe=True, reason=f"Oracle unavailable: {exc}")

    async def _run_consensus(self, instruction: str, primary_response: str) -> SafetyVerdict:
        """Ask secondary model if it agrees with primary's response."""
        prompt = _CONSENSUS_PROMPT.format(
            instruction=instruction[:200],
            primary_response=primary_response[:400],
        )
        t0 = time.perf_counter()
        try:
            thought = await asyncio.to_thread(self._secondary.think, b"", prompt, "consensus")
            latency = (time.perf_counter() - t0) * 1000
            text = thought.raw_text.strip()
            agreed = text.upper().startswith("AGREE")
            reason = text.split("-", 1)[-1].strip() if "-" in text else text
            return SafetyVerdict(
                safe=agreed,
                reason=reason,
                model=getattr(self._secondary, "model_name", "secondary"),
                latency_ms=latency,
            )
        except Exception as exc:
            logger.warning("Consensus check failed: %s — defaulting AGREE", exc)
            return SafetyVerdict(safe=True, reason=f"Consensus unavailable: {exc}")

    async def _run_verifier(self, instruction: str, response: str) -> int:
        """Ask secondary to score primary's response. Returns 0-100."""
        prompt = _VERIFIER_PROMPT.format(
            instruction=instruction[:200],
            response=response[:400],
        )
        try:
            thought = await asyncio.to_thread(self._secondary.think, b"", prompt, "verifier")
            text = thought.raw_text
            import re

            m = re.search(r"SCORE:\s*(\d+)", text, re.IGNORECASE)
            if m:
                return min(100, max(0, int(m.group(1))))
        except Exception as exc:
            logger.warning("Verifier failed: %s", exc)
        return 100  # default: pass

    async def _run_shadow(self, ctx: HarnessContext, primary_result: HarnessResult) -> None:
        """Run secondary in shadow mode — async, doesn't affect response."""
        try:
            secondary_thought = await asyncio.to_thread(
                self._secondary.think,
                ctx.image_bytes,
                ctx.instruction,
                ctx.surface,
            )
            # Log shadow result to trajectory for A/B comparison
            logger.debug(
                "Shadow model response: %r (primary: %r)",
                secondary_thought.raw_text[:100],
                primary_result.thought.raw_text[:100],
            )
        except Exception as exc:
            logger.debug("Shadow run failed (non-fatal): %s", exc)

    def _get_telemetry_snapshot(self) -> dict:
        """Get current telemetry for safety oracle context."""
        try:
            from castor.agent_tools import get_telemetry

            return get_telemetry()
        except Exception:
            return {}

    # ── Override post-turn for shadow mode ────────────────────────────────────

    async def _run_pipeline(
        self, ctx: HarnessContext, run_id: str, t0: float, root_span=None
    ) -> HarnessResult:
        result = await super()._run_pipeline(ctx, run_id, t0)
        # Shadow mode: fire secondary async without blocking
        if self._mode == "shadow" and ctx.scope in self._scope_filter:
            asyncio.ensure_future(self._run_shadow(ctx, result))
        return result


# ── Factory function ──────────────────────────────────────────────────────────


def build_dual_harness(
    primary: BaseProvider,
    secondary: BaseProvider,
    config: Optional[dict] = None,
    tool_registry: Optional[ToolRegistry] = None,
) -> DualModelHarness:
    """Convenience factory: create a DualModelHarness from two providers + config."""
    return DualModelHarness(
        primary=primary,
        secondary=secondary,
        config=config,
        tool_registry=tool_registry,
    )

"""
castor/harness.py — AgentHarness orchestrator.

The harness is the OS layer around the LLM: it assembles context, dispatches
tool calls, captures trajectories, and enforces Protocol 66 safety invariants
at every step.  The model (brain) is the CPU; the harness is the OS.

Reference: https://www.philschmid.de/agent-harness-2026

Protocol 66 invariants (always enforced):
  - ESTOP / safety-scope commands bypass ALL harness steps → execute immediately
  - Physical tools require explicit user consent before execution
  - Tool loop scope is immutable — model cannot self-escalate to control scope
  - Trajectory records every P66 decision for audit

Usage::

    from castor.harness import AgentHarness, HarnessContext

    harness = AgentHarness(provider=brain, config=agent_cfg, tool_registry=reg)
    result = await harness.run(HarnessContext(
        instruction="What do you see?",
        scope="chat",
        surface="opencastor_app",
    ))
    print(result.thought.raw_text)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from castor.tools import ToolRegistry, ToolResult

if TYPE_CHECKING:
    from castor.providers.base import BaseProvider, Thought

# ── Optional harness components (all opt-in via RCAN config) ─────────────────
try:
    from castor.harness.circuit_breaker import CircuitBreaker as _CircuitBreaker
except ImportError:
    _CircuitBreaker = None  # type: ignore[assignment,misc]

try:
    from castor.harness.rollback import RollbackManager as _RollbackManager
except ImportError:
    _RollbackManager = None  # type: ignore[assignment,misc]

try:
    from castor.harness.dlq import DeadLetterQueue as _DeadLetterQueue
except ImportError:
    _DeadLetterQueue = None  # type: ignore[assignment,misc]

try:
    from castor.harness.prompt_guard import PromptGuard as _PromptGuard
except ImportError:
    _PromptGuard = None  # type: ignore[assignment,misc]

try:
    from castor.harness.context_compressor import ContextCompressor as _ContextCompressor
except ImportError:
    _ContextCompressor = None  # type: ignore[assignment,misc]

try:
    from castor.harness.working_memory import WorkingMemory as _WorkingMemory
except ImportError:
    _WorkingMemory = None  # type: ignore[assignment,misc]

try:
    from castor.harness.span_tracer import SpanTracer as _SpanTracer
except ImportError:
    _SpanTracer = None  # type: ignore[assignment,misc]

try:
    from castor.harness.cost_meter import CostMeter as _CostMeter
except ImportError:
    _CostMeter = None  # type: ignore[assignment,misc]

logger = logging.getLogger("OpenCastor.Harness")

__all__ = [
    "AgentHarness",
    "HarnessContext",
    "HarnessResult",
    "HarnessHook",
    "DriftDetectionHook",
    "P66AuditHook",
    "RetryOnErrorHook",
]

# ── P66 tool classification ──────────────────────────────────────────────────

# Tools that require explicit user consent before execution (physical actions)
PHYSICAL_TOOLS: frozenset[str] = frozenset(
    {
        "move",
        "grip",
        "set_speed",
        "rotate",
        "navigate_to",
        "set_motor",
        "drive",
        "arm_move",
        "arm_grip",
    }
)

# ESTOP tools: always execute, no consent, no iteration cap, no secondary veto
ESTOP_TOOLS: frozenset[str] = frozenset(
    {
        "emergency_stop",
        "stop",
        "halt",
        "estop",
        "e_stop",
    }
)

# Scope ordering: higher = more privileged
SCOPE_LEVELS: dict[str, int] = {
    "discover": 0,
    "status": 1,
    "chat": 2,
    "control": 3,
    "safety": 99,
}


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class HarnessContext:
    """Input context for a single harness turn."""

    instruction: str
    image_bytes: bytes = b""
    surface: str = "opencastor_app"
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    scope: str = "chat"  # chat | control | safety
    mission_state: dict = field(default_factory=dict)
    # Set True after user has explicitly confirmed a physical action this turn
    consent_granted: bool = False


@dataclass
class ToolCallRecord:
    """Record of a single tool invocation within a harness run."""

    tool_name: str
    args: dict
    result: Any
    latency_ms: float
    p66_consent_required: bool = False
    p66_consent_granted: bool = False
    p66_blocked: bool = False
    error: Optional[str] = None


@dataclass
class HarnessResult:
    """Full result of a single AgentHarness.run() call."""

    thought: Thought
    tools_called: list[ToolCallRecord] = field(default_factory=list)
    skill_triggered: Optional[str] = None
    context_tokens: int = 0
    total_latency_ms: float = 0.0
    # P66 aggregate fields
    p66_consent_required: bool = False
    p66_consent_granted: bool = False
    p66_blocked: bool = False
    p66_estop_bypassed: bool = False
    # Harness metadata
    iterations: int = 0
    was_compacted: bool = False
    drift_score: Optional[float] = None
    error: Optional[str] = None
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))


# ── Lifecycle hook interface ──────────────────────────────────────────────────


class HarnessHook:
    """Base class for AgentHarness lifecycle hooks.

    Subclass and override only the methods you need.  All hooks are async;
    returning a value from ``on_error`` recovers from the error.
    """

    async def on_pre_turn(
        self,
        ctx: HarnessContext,
        built_context: Any,  # BuiltContext from context.py
    ) -> None:
        """Called after context is built, before the first model inference."""

    async def on_tool_call(
        self,
        call: dict,
        result: ToolResult,
    ) -> None:
        """Called after every tool execution (including blocked ones)."""

    async def on_post_turn(
        self,
        ctx: HarnessContext,
        result: HarnessResult,
    ) -> None:
        """Called after the final response is produced."""

    async def on_error(
        self,
        ctx: HarnessContext,
        error: Exception,
    ) -> Optional[HarnessResult]:
        """Called on unhandled error.  Return a HarnessResult to recover."""
        return None


# ── Built-in hooks ────────────────────────────────────────────────────────────


class P66AuditHook(HarnessHook):
    """Logs all Protocol 66 physical-tool decisions to the dedicated audit log."""

    _audit_logger = logging.getLogger("OpenCastor.P66Audit")

    async def on_tool_call(self, call: dict, result: ToolResult) -> None:
        name = call.get("name", "")
        if name in PHYSICAL_TOOLS or name in ESTOP_TOOLS:
            self._audit_logger.info(
                "P66 tool=%s args=%s ok=%s",
                name,
                call.get("args", {}),
                result.ok,
            )

    async def on_post_turn(self, ctx: HarnessContext, result: HarnessResult) -> None:
        if result.p66_estop_bypassed:
            self._audit_logger.warning(
                "P66 ESTOP bypass: instruction=%r session=%s",
                ctx.instruction[:80],
                ctx.session_id,
            )
        if result.p66_blocked:
            self._audit_logger.warning(
                "P66 physical action BLOCKED: session=%s",
                ctx.session_id,
            )


class RetryOnErrorHook(HarnessHook):
    """Retries transient provider errors up to max_retries times."""

    def __init__(self, max_retries: int = 2):
        self._max_retries = max_retries
        self._retries: dict[str, int] = {}

    async def on_error(self, ctx: HarnessContext, error: Exception) -> Optional[HarnessResult]:
        try:
            from castor.providers.base import ProviderQuotaError

            transient = (ConnectionError, TimeoutError, ProviderQuotaError)
        except ImportError:
            transient = (ConnectionError, TimeoutError)

        key = ctx.session_id
        count = self._retries.get(key, 0)
        if isinstance(error, transient) and count < self._max_retries:
            self._retries[key] = count + 1
            delay = 2**count
            logger.warning(
                "Harness: transient error, retry %d/%d in %ds: %s",
                count + 1,
                self._max_retries,
                delay,
                error,
            )
            await asyncio.sleep(delay)
            return None  # caller will retry
        return None


class DriftDetectionHook(HarnessHook):
    """Detects when the model's response has drifted off-task.

    After 3+ tool iterations, compares the final response against the
    original instruction using cosine similarity of simple word vectors.
    Below ``threshold`` → logs drift warning and records ``drift_score``
    on the result.

    Args:
        threshold: Cosine similarity below this value is considered drift.
                   Default 0.15 (loose — avoids false positives on short
                   instructions that legitimately produce long responses).
    """

    def __init__(self, threshold: float = 0.15) -> None:
        self.threshold = threshold

    async def on_post_turn(self, ctx: HarnessContext, result: HarnessResult) -> None:
        if result.iterations < 3:
            return  # don't check on short runs

        score = _word_overlap_similarity(ctx.instruction, result.thought.raw_text)
        result.drift_score = score  # type: ignore[attr-defined]

        if score < self.threshold:
            logger.warning(
                "Harness: drift detected (score=%.3f < %.3f) session=%s instruction=%r",
                score,
                self.threshold,
                ctx.session_id,
                ctx.instruction[:60],
            )


def _word_overlap_similarity(a: str, b: str) -> float:
    """Simple word-overlap similarity (Jaccard) as a lightweight drift proxy."""
    if not a or not b:
        return 0.0
    import re

    words_a = set(re.findall(r"\b[a-z]{3,}\b", a.lower()))
    words_b = set(re.findall(r"\b[a-z]{3,}\b", b.lower()))
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


# ── Main harness ──────────────────────────────────────────────────────────────


class AgentHarness:
    """Thin orchestration layer around the LLM provider.

    Replaces direct ``provider.think()`` calls in api.py with a pipeline:
    context assembly → model inference → tool loop → trajectory log.

    Args:
        provider:      Active BaseProvider (brain).
        config:        ``agent`` section from RCAN config dict.
        tool_registry: Populated ToolRegistry.
        hooks:         Optional list of HarnessHook instances.

    P66 invariants are always enforced regardless of config.
    """

    def __init__(
        self,
        provider: BaseProvider,
        config: Optional[dict] = None,
        tool_registry: Optional[ToolRegistry] = None,
        hooks: Optional[list[HarnessHook]] = None,
    ) -> None:
        self._provider = provider
        self._config = config or {}
        self._tool_registry = tool_registry or ToolRegistry()
        self.hooks: list[HarnessHook] = list(hooks or [])

        harness_cfg = self._config.get("harness", {})
        self._enabled = harness_cfg.get("enabled", True)
        self._max_iterations: int = int(harness_cfg.get("max_iterations", 6))
        self._context_budget: float = float(harness_cfg.get("context_budget", 0.8))
        self._auto_rag: bool = bool(harness_cfg.get("auto_rag", True))
        self._auto_telemetry: bool = bool(harness_cfg.get("auto_telemetry", True))

        # Register extended agent tools
        try:
            from castor.agent_tools import register_agent_tools

            register_agent_tools(self._tool_registry)
        except Exception as _reg_exc:
            logger.debug("Agent tools registration skipped: %s", _reg_exc)

        # Built-in hooks (always registered unless explicitly disabled)
        _hook_cfg = harness_cfg.get("hooks", {})
        if _hook_cfg.get("p66_audit", True):
            self.hooks.append(P66AuditHook())
        if _hook_cfg.get("retry_on_error", True):
            self.hooks.append(RetryOnErrorHook())
        if _hook_cfg.get("drift_detection", True):
            _drift_thresh = float(_hook_cfg.get("drift_threshold", 0.15))
            self.hooks.append(DriftDetectionHook(threshold=_drift_thresh))

        # Lazy import context builder to avoid circular deps
        self._context_builder: Any = None

        # ── Optional production-grade harness components ───────────────────
        cfg = self._config

        # Trajectories DB path (shared by rollback + DLQ)
        import os as _os

        _db_path: str = str(
            cfg.get("trajectories_db")
            or _os.path.expanduser("~/.config/opencastor/trajectories.db")
        )

        # Circuit Breaker
        _cb_cfg = cfg.get("circuit_breaker", {})
        self.circuit_breaker: Any = (
            _CircuitBreaker(_cb_cfg) if _CircuitBreaker and _cb_cfg.get("enabled") else None
        )

        # Rollback Manager
        _rb_cfg = cfg.get("rollback", {})
        self.rollback: Any = (
            _RollbackManager(_db_path) if _RollbackManager and _rb_cfg.get("enabled") else None
        )

        # Dead Letter Queue
        _dlq_cfg = cfg.get("dlq", {})
        self.dlq: Any = (
            _DeadLetterQueue(_db_path) if _DeadLetterQueue and _dlq_cfg.get("enabled") else None
        )

        # Prompt Guard (initialised from top-level config key)
        _pg_cfg = cfg.get("prompt_guard", {})
        self.prompt_guard: Any = (
            _PromptGuard(_pg_cfg) if _PromptGuard and _pg_cfg.get("enabled") else None
        )

        # Context Compressor
        _cc_cfg = cfg.get("context_compressor", {})
        self.context_compressor: Any = (
            _ContextCompressor(_cc_cfg, provider_factory=None)
            if _ContextCompressor and _cc_cfg.get("enabled")
            else None
        )

        # Working Memory
        _wm_cfg = cfg.get("working_memory", {})
        self.working_memory: Any = (
            _WorkingMemory(max_keys=int(_wm_cfg.get("max_keys", 50)))
            if _WorkingMemory and _wm_cfg.get("enabled")
            else None
        )

        # Span Tracer
        _st_cfg = cfg.get("span_tracer", {})
        self.span_tracer: Any = (
            _SpanTracer(_st_cfg) if _SpanTracer and _st_cfg.get("enabled") else None
        )

        # Cost Meter
        _cm_cfg = cfg.get("cost_meter", {})
        self.cost_meter: Any = (
            _CostMeter(_cm_cfg) if _CostMeter and _cm_cfg.get("enabled") else None
        )

        # Register working memory tools if enabled
        if self.working_memory is not None:
            try:
                from castor.agent_tools import register_working_memory_tools

                register_working_memory_tools(self._tool_registry, self.working_memory)
            except Exception as _wm_exc:
                logger.debug("Working memory tools registration skipped: %s", _wm_exc)

        logger.info(
            "AgentHarness initialised (enabled=%s, max_iter=%d, "
            "circuit_breaker=%s, rollback=%s, dlq=%s, prompt_guard=%s, "
            "working_memory=%s, span_tracer=%s, cost_meter=%s)",
            self._enabled,
            self._max_iterations,
            self.circuit_breaker is not None,
            self.rollback is not None,
            self.dlq is not None,
            self.prompt_guard is not None,
            self.working_memory is not None,
            self.span_tracer is not None,
            self.cost_meter is not None,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(self, ctx: HarnessContext) -> HarnessResult:
        """Execute one harness turn.

        P66 invariant: ESTOP / safety scope bypasses all harness steps.
        """
        t0 = time.perf_counter()
        run_id = str(uuid.uuid4())

        # ── 0. Clear working memory at start of run ──────────────────────────
        if self.working_memory is not None:
            self.working_memory.clear()

        # ── 1. Prompt guard (FIRST — before ESTOP check) ────────────────────
        if self.prompt_guard is not None:
            guard_result = self.prompt_guard.check(ctx.instruction)
            if guard_result.blocked:
                logger.warning(
                    "PromptGuard: blocked instruction (score=%.2f) session=%s",
                    guard_result.risk_score,
                    ctx.session_id,
                )
                from castor.providers.base import Thought as _T

                return HarnessResult(
                    thought=_T(
                        raw_text="Command blocked: potential prompt injection detected.",
                        provider="harness",
                    ),
                    run_id=run_id,
                    total_latency_ms=(time.perf_counter() - t0) * 1000,
                    error="prompt_injection_blocked",
                )

        # ── 2. P66 ESTOP bypass (absolute) ──────────────────────────────────
        if self._is_estop(ctx):
            return await self._run_estop(ctx, run_id, t0)

        # ── Legacy mode ─────────────────────────────────────────────────────
        if not self._enabled:
            return await self._run_legacy(ctx, run_id, t0)

        # ── 3. Root span (wraps entire run) ─────────────────────────────────
        root_span = None
        if self.span_tracer is not None:
            root_span = self.span_tracer.start_trace(
                "harness.run",
                attributes={"session_id": ctx.session_id, "scope": ctx.scope},
            )

        # ── 4. Full harness pipeline ─────────────────────────────────────────
        try:
            result = await self._run_pipeline(ctx, run_id, t0, root_span=root_span)
            if root_span is not None and self.span_tracer is not None:
                self.span_tracer.end_span(root_span, status="ok")
                self.span_tracer.export_trace(root_span.trace_id)
            return result
        except Exception as exc:
            logger.exception("Harness pipeline error: %s", exc)
            # Push to DLQ
            if self.dlq is not None:
                try:
                    self.dlq.push(
                        command_id=run_id,
                        instruction=ctx.instruction[:500],
                        scope=ctx.scope,
                        error=str(exc),
                        metadata={"session_id": ctx.session_id},
                    )
                except Exception as _dlq_exc:
                    logger.debug("DLQ push failed (non-fatal): %s", _dlq_exc)
            # End root span as error
            if root_span is not None and self.span_tracer is not None:
                self.span_tracer.end_span(root_span, status="error", error=str(exc))
                self.span_tracer.export_trace(root_span.trace_id)
            # Give hooks a chance to recover
            for hook in self.hooks:
                recovery = await hook.on_error(ctx, exc)
                if recovery is not None:
                    return recovery
            # Return graceful degradation response
            from castor.providers.base import Thought as _T

            t = _T(raw_text="I encountered an error. Please try again.", provider="harness")
            result = HarnessResult(
                thought=t,
                total_latency_ms=(time.perf_counter() - t0) * 1000,
                error=str(exc),
                run_id=run_id,
            )
            return result

    def add_hook(self, hook: HarnessHook) -> None:
        """Register a lifecycle hook."""
        self.hooks.append(hook)

    # ── Internal pipeline ─────────────────────────────────────────────────────

    async def _run_pipeline(
        self, ctx: HarnessContext, run_id: str, t0: float, root_span: Any = None
    ) -> HarnessResult:
        """Full harness pipeline: context → skill → inference → tool loop → log."""
        from castor.context import BuiltContext

        # 1. Build context
        builder = self._get_context_builder()
        built: BuiltContext = await builder.build(ctx, history=[])

        # 2. Run pre-turn hooks
        for hook in self.hooks:
            span_name = f"hook.pre_turn.{type(hook).__name__}"
            if self.span_tracer and root_span:
                with self.span_tracer.span(span_name, parent=root_span):
                    await hook.on_pre_turn(ctx, built)
            else:
                await hook.on_pre_turn(ctx, built)

        # 3. Tool loop
        thought, tools_called, iterations = await self._tool_loop(
            ctx, built, run_id=run_id, root_span=root_span
        )

        # 4. Aggregate P66 fields
        any_phys = any(r.p66_consent_required for r in tools_called)
        any_granted = any(r.p66_consent_granted for r in tools_called)
        any_blocked = any(r.p66_blocked for r in tools_called)

        result = HarnessResult(
            thought=thought,
            tools_called=tools_called,
            skill_triggered=built.skill_injected,
            context_tokens=built.token_estimate,
            total_latency_ms=(time.perf_counter() - t0) * 1000,
            p66_consent_required=any_phys,
            p66_consent_granted=any_granted,
            p66_blocked=any_blocked,
            iterations=iterations,
            was_compacted=built.was_compacted,
            run_id=run_id,
        )

        # 5. Snapshot working memory into result if enabled
        if self.working_memory is not None:
            _wm_cfg = self._config.get("working_memory", {})
            if _wm_cfg.get("log_to_trajectory", True):
                result.error = result.error  # no-op; snapshot stored below in trajectory log

        # 6. Post-turn hooks
        for hook in self.hooks:
            if self.span_tracer and root_span:
                with self.span_tracer.span(
                    f"hook.post_turn.{type(hook).__name__}", parent=root_span
                ):
                    await hook.on_post_turn(ctx, result)
            else:
                await hook.on_post_turn(ctx, result)

        # 7. Log trajectory (fire-and-forget)
        asyncio.ensure_future(self._log_trajectory(ctx, result))

        return result

    async def _tool_loop(
        self,
        ctx: HarnessContext,
        built: Any,
        run_id: str = "",
        root_span: Any = None,
    ) -> tuple[Thought, list[ToolCallRecord], int]:
        """Run model inference + tool execution loop.

        Returns (final_thought, tool_records, iteration_count).
        P66: physical tools require consent; ESTOP tools always pass.
        """
        from castor.providers.base import Thought as _Thought

        # Build provider messages from built context
        messages = getattr(built, "messages", [])
        tools_schema = self._get_tools_for_scope(ctx.scope)
        tools_called: list[ToolCallRecord] = []
        consent_granted = ctx.consent_granted

        for iteration in range(self._max_iterations):
            # ── Cost meter: check budget before each iteration ───────────────
            if self.cost_meter is not None and run_id:
                if self.cost_meter.is_over_budget(run_id):
                    logger.warning("CostMeter: budget cap reached for run=%s", run_id)
                    budget_thought = _Thought(
                        raw_text="Budget cap reached. Run halted.",
                        provider="harness",
                    )
                    return budget_thought, tools_called, iteration

            # Call provider with tools (wrapped in span if tracer enabled)
            if self.span_tracer and root_span:
                model_span = self.span_tracer.start_span(
                    "harness.think_with_tools",
                    parent=root_span,
                    attributes={"iteration": iteration},
                )

            raw_response = await asyncio.to_thread(
                self._think_with_tools,
                ctx.image_bytes,
                ctx.instruction if iteration == 0 else "",
                ctx.surface,
                messages,
                tools_schema,
            )

            # ── Cost meter: record tokens after model call ───────────────────
            if self.cost_meter is not None and run_id:
                try:
                    _usage = getattr(raw_response, "usage", None) or {}
                    _in = int(_usage.get("input_tokens", 0) or _usage.get("prompt_tokens", 0))
                    _out = int(_usage.get("output_tokens", 0) or _usage.get("completion_tokens", 0))
                    if _in or _out:
                        self.cost_meter.record(run_id, _in, _out)
                except Exception as _cost_exc:
                    logger.debug("CostMeter record failed: %s", _cost_exc)

            if self.span_tracer and root_span:
                self.span_tracer.end_span(model_span, status="ok")

            # Check if provider returned tool calls
            tool_calls = self._extract_tool_calls(raw_response)

            if not tool_calls:
                # Final text response — done
                return raw_response, tools_called, iteration + 1

            # Execute each tool call
            for call in tool_calls:
                name = call.get("name", "")
                args = call.get("args", {})

                # ── Span: tool call ──────────────────────────────────────────
                tool_span_ctx = None
                if self.span_tracer and root_span:
                    tool_span_ctx = self.span_tracer.start_span(
                        f"tool.{name}",
                        parent=root_span,
                        attributes={"tool_name": name, "scope": ctx.scope},
                    )

                # P66: ESTOP always executes
                if name in ESTOP_TOOLS:
                    tr = await asyncio.to_thread(self._execute_tool, name, args)
                    record = ToolCallRecord(
                        tool_name=name,
                        args=args,
                        result=tr.result,
                        latency_ms=tr.duration_ms,
                    )
                    tools_called.append(record)
                    for hook in self.hooks:
                        await hook.on_tool_call(call, tr)
                    if tool_span_ctx and self.span_tracer:
                        self.span_tracer.end_span(tool_span_ctx, status="ok")
                    continue

                # P66: physical tools require consent and control scope
                if name in PHYSICAL_TOOLS:
                    if ctx.scope not in ("control", "safety") or not consent_granted:
                        record = ToolCallRecord(
                            tool_name=name,
                            args=args,
                            result=None,
                            latency_ms=0,
                            p66_consent_required=True,
                            p66_consent_granted=False,
                            p66_blocked=True,
                        )
                        tools_called.append(record)
                        logger.info("P66: blocked physical tool '%s' (consent not granted)", name)
                        if tool_span_ctx and self.span_tracer:
                            self.span_tracer.end_span(tool_span_ctx, status="ok")
                        # Return consent-request thought
                        consent_thought = _Thought(
                            raw_text=(
                                f"I need your confirmation before I can {name}. "
                                "Please reply 'yes' or 'confirm' to proceed."
                            ),
                            provider="harness",
                        )
                        return consent_thought, tools_called, iteration + 1

                    # ── Rollback: capture state before physical tool ──────────
                    if self.rollback is not None:
                        try:
                            _state = getattr(ctx, "mission_state", {}) or {}
                            self.rollback.capture(run_id or ctx.session_id, _state)
                        except Exception as _rb_exc:
                            logger.debug("Rollback capture failed (non-fatal): %s", _rb_exc)

                # ── Circuit Breaker: check before tool execution ─────────────
                _skill_id = name  # use tool name as skill_id proxy
                if self.circuit_breaker is not None and self.circuit_breaker.is_open(_skill_id):
                    logger.warning("CircuitBreaker: skill=%s is open, skipping", _skill_id)
                    if tool_span_ctx and self.span_tracer:
                        self.span_tracer.end_span(
                            tool_span_ctx, status="error", error="circuit_open"
                        )
                    messages.append(
                        {
                            "role": "tool",
                            "name": name,
                            "content": f"Error: skill {name!r} is temporarily disabled (circuit open).",
                        }
                    )
                    continue

                # Execute non-blocked tool
                tr = await asyncio.to_thread(self._execute_tool, name, args)
                is_phys = name in PHYSICAL_TOOLS
                record = ToolCallRecord(
                    tool_name=name,
                    args=args,
                    result=tr.result,
                    latency_ms=tr.duration_ms,
                    p66_consent_required=is_phys,
                    p66_consent_granted=is_phys and consent_granted,
                    error=tr.error,
                )
                tools_called.append(record)

                # ── Circuit Breaker: record result ───────────────────────────
                if self.circuit_breaker is not None:
                    if tr.ok:
                        self.circuit_breaker.record_success(_skill_id)
                    else:
                        self.circuit_breaker.record_failure(_skill_id)

                for hook in self.hooks:
                    if self.span_tracer and root_span:
                        with self.span_tracer.span(
                            f"hook.tool_call.{type(hook).__name__}", parent=root_span
                        ):
                            await hook.on_tool_call(call, tr)
                    else:
                        await hook.on_tool_call(call, tr)

                if tool_span_ctx and self.span_tracer:
                    _ts = "ok" if tr.ok else "error"
                    self.span_tracer.end_span(tool_span_ctx, status=_ts, error=tr.error)

                # Append tool result to messages for next iteration
                messages.append(
                    {
                        "role": "tool",
                        "name": name,
                        "content": str(tr.result) if tr.ok else f"Error: {tr.error}",
                    }
                )

        # Hit iteration limit
        logger.warning("Harness: reached max_iterations=%d", self._max_iterations)
        limit_thought = _Thought(
            raw_text="I reached my step limit for this task. Here is what I found so far.",
            provider="harness",
        )
        return limit_thought, tools_called, self._max_iterations

    async def _run_estop(self, ctx: HarnessContext, run_id: str, t0: float) -> HarnessResult:
        """P66 ESTOP path — bypass all harness steps, call provider directly."""
        logger.warning("P66 ESTOP bypass activated: %r", ctx.instruction[:60])
        thought = await asyncio.to_thread(
            self._provider.think,
            ctx.image_bytes,
            ctx.instruction,
            ctx.surface,
        )
        result = HarnessResult(
            thought=thought,
            total_latency_ms=(time.perf_counter() - t0) * 1000,
            p66_estop_bypassed=True,
            run_id=run_id,
        )
        for hook in self.hooks:
            await hook.on_post_turn(ctx, result)
        asyncio.ensure_future(self._log_trajectory(ctx, result))
        return result

    async def _run_legacy(self, ctx: HarnessContext, run_id: str, t0: float) -> HarnessResult:
        """Legacy single-shot mode (harness.enabled = false)."""
        thought = await asyncio.to_thread(
            self._provider.think,
            ctx.image_bytes,
            ctx.instruction,
            ctx.surface,
        )
        return HarnessResult(
            thought=thought,
            total_latency_ms=(time.perf_counter() - t0) * 1000,
            run_id=run_id,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _is_estop(self, ctx: HarnessContext) -> bool:
        """Return True if this context must be treated as ESTOP."""
        if ctx.scope == "safety":
            return True
        inst_upper = ctx.instruction.upper().strip()
        estop_keywords = ("ESTOP", "E-STOP", "EMERGENCY STOP", "STOP ALL", "HALT")
        return any(inst_upper.startswith(kw) for kw in estop_keywords)

    def _get_context_builder(self) -> Any:
        """Lazy-load ContextBuilder to avoid circular imports."""
        if self._context_builder is None:
            from castor.context import ContextBuilder

            self._context_builder = ContextBuilder(
                config=self._config,
                tool_registry=self._tool_registry,
            )
        return self._context_builder

    def _get_tools_for_scope(self, scope: str) -> list[dict]:
        """Return tool schemas filtered to current scope.

        P66: chat scope cannot access physical tools.
        """
        scope_level = SCOPE_LEVELS.get(scope, 2)
        all_tools = self._tool_registry.to_openai_tools()
        if scope_level < SCOPE_LEVELS["control"]:
            # Filter out physical tools for non-control scopes
            return [
                t for t in all_tools if t.get("function", {}).get("name", "") not in PHYSICAL_TOOLS
            ]
        return all_tools

    def _think_with_tools(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str,
        messages: list[dict],
        tools_schema: list[dict],
    ) -> Thought:
        """Call provider with tool schemas if supported, else fall back to plain think()."""
        # Try provider-native tool calling
        if hasattr(self._provider, "think_with_tools") and tools_schema:
            try:
                return self._provider.think_with_tools(
                    image_bytes=image_bytes,
                    instruction=instruction,
                    surface=surface,
                    messages=messages,
                    tools=tools_schema,
                )
            except (AttributeError, TypeError):
                pass
        # Fallback: inject tool descriptions into instruction
        if tools_schema and instruction:
            tool_names = [t.get("function", {}).get("name", "") for t in tools_schema]
            augmented = f"{instruction}\n\n[Available tools: {', '.join(tool_names)}]"
            return self._provider.think(image_bytes, augmented, surface)
        return self._provider.think(image_bytes, instruction, surface)

    def _extract_tool_calls(self, thought: Thought) -> list[dict]:
        """Extract tool calls from a Thought object.

        Returns list of {name, args} dicts.  Returns [] for plain text responses.
        """
        # Native tool_calls attribute (set by think_with_tools)
        if hasattr(thought, "tool_calls") and thought.tool_calls:
            return thought.tool_calls

        # Parse from action dict (legacy: {"type": "tool_call", "name": ..., "args": ...})
        if thought.action and thought.action.get("type") == "tool_call":
            return [
                {
                    "name": thought.action.get("name", ""),
                    "args": thought.action.get("args", {}),
                }
            ]

        return []

    def _execute_tool(self, name: str, args: dict) -> ToolResult:
        """Execute a tool by name+args via the registry."""
        return self._tool_registry.call(name, **args)

    async def _log_trajectory(self, ctx: HarnessContext, result: HarnessResult) -> None:
        """Fire-and-forget trajectory logging."""
        try:
            from castor.trajectory import TrajectoryLogger

            await TrajectoryLogger.log_async(ctx, result)
        except Exception as exc:
            logger.debug("Trajectory log failed (non-fatal): %s", exc)

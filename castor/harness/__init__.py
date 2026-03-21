"""
castor/harness/ — AgentHarness orchestrator + production-grade components.

Core orchestrator (AgentHarness, HarnessContext, HarnessResult) lives in
castor/harness/core.py and is re-exported here for backward compatibility.

All additional components are opt-in via RCAN config. Existing robots unaffected.
"""

from castor.harness.circuit_breaker import CircuitBreaker
from castor.harness.core import (
    ESTOP_TOOLS,
    PHYSICAL_TOOLS,
    SCOPE_LEVELS,
    AgentHarness,
    DriftDetectionHook,
    HarnessContext,
    HarnessHook,
    HarnessResult,
    P66AuditHook,
    RetryOnErrorHook,
    ToolCallRecord,
    _word_overlap_similarity,
)
from castor.harness.cost_meter import CostMeter
from castor.harness.dlq import DeadLetterQueue
from castor.harness.prompt_guard import GuardResult, PromptGuard
from castor.harness.rollback import RollbackManager
from castor.harness.span_tracer import Span, SpanTracer
from castor.harness.working_memory import WorkingMemory

__all__ = [
    # Core orchestrator (re-exported for backward compatibility)
    "AgentHarness",
    "HarnessContext",
    "HarnessResult",
    "HarnessHook",
    "P66AuditHook",
    "RetryOnErrorHook",
    "DriftDetectionHook",
    "ToolCallRecord",
    "PHYSICAL_TOOLS",
    "ESTOP_TOOLS",
    "SCOPE_LEVELS",
    "_word_overlap_similarity",
    # Production-grade harness components (opt-in via RCAN config)
    "CircuitBreaker",
    "RollbackManager",
    "DeadLetterQueue",
    "PromptGuard",
    "GuardResult",
    "WorkingMemory",
    "SpanTracer",
    "Span",
    "CostMeter",
    # memory
    "FilesystemBackend",
    "FirestoreBackend",
    "MemoryBackend",
    "MemoryManager",
    "OverflowStrategy",
    "WorkingMemoryBackend",
    # pattern
    "InitializerExecutor",
    "MultiAgent",
    "PatternBase",
    "SingleAgentSupervisor",
    "get_pattern",
    # security
    "OPAGuardrail",
    "SecurityContext",
    "TelemetryEvent",
    "TelemetryExporter",
]
from castor.harness.memory import (
    FilesystemBackend,
    FirestoreBackend,
    MemoryBackend,
    MemoryManager,
    OverflowStrategy,
    WorkingMemoryBackend,
)
from castor.harness.pattern import (
    InitializerExecutor,
    MultiAgent,
    PatternBase,
    SingleAgentSupervisor,
    get_pattern,
)
from castor.harness.security import (
    OPAGuardrail,
    SecurityContext,
    TelemetryEvent,
    TelemetryExporter,
)

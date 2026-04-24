"""castor.rcan3.harness_protocol — Harness Protocol and its dataclass contracts.

A harness mediates the agent's think/do loop. ``Harness`` is
``runtime_checkable`` so ``isinstance(obj, Harness)`` works for duck-typed
third-party implementations.

This module lives in ``castor.rcan3`` (not ``castor.harness``) to avoid
collision with the existing production AgentHarness in ``castor/harness/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Observation:
    """Input to a think() call — whatever the runtime shell currently knows."""

    state: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Thought:
    """Output of think() — the agent's chosen next action."""

    action: str
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass(frozen=True)
class ActionResult:
    """Output of do() — outcome of executing a Thought."""

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@runtime_checkable
class Harness(Protocol):
    """Runtime-checkable Protocol for think/do lifecycles."""

    def think(self, obs: Observation) -> Thought:  # pragma: no cover — Protocol
        ...

    def do(self, thought: Thought) -> ActionResult:  # pragma: no cover — Protocol
        ...

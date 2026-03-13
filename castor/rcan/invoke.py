"""
RCAN §19 Behavior/Skill Invocation Protocol.

Implements INVOKE and INVOKE_RESULT message types for triggering
named behaviors/skills on a robot runtime.

Spec: https://rcan.dev/spec/section-19/
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
from collections.abc import Callable

from castor.rcan.message import MessageType

logger = logging.getLogger("OpenCastor.RCAN.Invoke")


@dataclass
class InvokeRequest:
    """INVOKE message payload (§19.2)."""

    skill: str  # Skill/behavior name (e.g. "nav.go_to", "arm.pick")
    params: dict[str, Any] = field(default_factory=dict)
    invoke_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timeout_ms: int = 30_000  # Default 30s timeout
    reply_to: Optional[str] = None  # RURI to send INVOKE_RESULT to

    def to_message(self, source_ruri: str, target_ruri: str) -> dict[str, Any]:
        """Serialize to RCAN message format.

        Uses ``msg_id`` as the wire-format correlation field per §19.3 of the
        RCAN specification.  The Python attribute is kept as ``invoke_id`` for
        backward-compatibility with existing call-sites.
        """
        return {
            "type": MessageType.INVOKE,
            "source_ruri": source_ruri,
            "target_ruri": target_ruri,
            "msg_id": self.invoke_id,  # §19.3 — wire field is msg_id
            "payload": {
                "skill": self.skill,
                "params": self.params,
                "timeout_ms": self.timeout_ms,
                "reply_to": self.reply_to,
            },
            "timestamp": time.time(),
        }


@dataclass
class InvokeResult:
    """INVOKE_RESULT message payload (§19.3)."""

    invoke_id: str
    status: str  # "success" | "error" | "timeout" | "not_found"
    result: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration_ms: Optional[float] = None

    def to_message(self, source_ruri: str, target_ruri: str) -> dict[str, Any]:
        """Serialize to RCAN message format.

        Uses ``reply_to`` as the wire-format correlation field per §19.4 of the
        RCAN specification, echoing the ``msg_id`` from the originating INVOKE.
        """
        return {
            "type": MessageType.INVOKE_RESULT,
            "source_ruri": source_ruri,
            "target_ruri": target_ruri,
            "reply_to": self.invoke_id,  # §19.4 — correlates to INVOKE msg_id
            "payload": {
                "status": self.status,
                "result": self.result,
                "error": self.error,
                "duration_ms": self.duration_ms,
            },
            "timestamp": time.time(),
        }


class SkillRegistry:
    """Registry of callable skills/behaviors (§19.2).

    Skills are registered by name and invoked via INVOKE messages.
    Each skill is a callable that accepts params dict and returns result dict.

    Example::

        registry = SkillRegistry()

        @registry.register("nav.go_to")
        def go_to(params):
            x, y = params["x"], params["y"]
            # ... navigation logic ...
            return {"reached": True, "final_pos": [x, y]}
    """

    def __init__(self) -> None:
        self._skills: dict[str, Callable] = {}

    def register(self, name: str) -> Callable:
        """Decorator to register a skill by name."""

        def decorator(fn: Callable) -> Callable:
            self._skills[name] = fn
            logger.debug("Registered skill: %s", name)
            return fn

        return decorator

    def register_fn(self, name: str, fn: Callable) -> None:
        """Register a skill function directly."""
        self._skills[name] = fn
        logger.debug("Registered skill: %s", name)

    def has(self, name: str) -> bool:
        """Check if a skill is registered."""
        return name in self._skills

    def list_skills(self) -> list[str]:
        """Return list of registered skill names."""
        return list(self._skills.keys())

    def invoke(self, request: InvokeRequest) -> InvokeResult:
        """Invoke a skill by name with params. Returns InvokeResult.

        Args:
            request: InvokeRequest with skill name and params.

        Returns:
            InvokeResult with status and result or error.
        """
        start = time.monotonic()

        if not self.has(request.skill):
            return InvokeResult(
                invoke_id=request.invoke_id,
                status="not_found",
                error=f"Skill '{request.skill}' not registered. Available: {self.list_skills()}",
            )

        try:
            result = self._skills[request.skill](request.params)
            duration_ms = (time.monotonic() - start) * 1000
            return InvokeResult(
                invoke_id=request.invoke_id,
                status="success",
                result=result if isinstance(result, dict) else {"value": result},
                duration_ms=duration_ms,
            )
        except TimeoutError:
            return InvokeResult(
                invoke_id=request.invoke_id,
                status="timeout",
                error="Skill execution timed out",
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Skill '%s' raised exception", request.skill)
            return InvokeResult(
                invoke_id=request.invoke_id,
                status="error",
                error=str(exc),
                duration_ms=(time.monotonic() - start) * 1000,
            )

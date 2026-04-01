"""
PreToolUse / PostToolUse hook runner for hardware safety gating.

Hooks are small shell scripts invoked before or after a named tool call.
A pre-tool hook can deny the call (exit 1) or allow it (exit 0).
Post-tool hooks are fire-and-forget.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger("OpenCastor.HookRunner")


class HookEvent(Enum):
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"


@dataclass
class HookDefinition:
    tools: list[str]
    script: str
    event: HookEvent = HookEvent.PRE_TOOL_USE
    timeout_s: float = 5.0


@dataclass
class HookResult:
    allowed: bool
    message: str = ""


class HookRunner:
    """Runs registered hook scripts around tool invocations."""

    def __init__(self, hooks: list[HookDefinition]) -> None:
        self._hooks = hooks

    def _matching_hooks(self, tool_name: str, event: HookEvent) -> list[HookDefinition]:
        return [
            h for h in self._hooks if h.event == event and (tool_name in h.tools or "*" in h.tools)
        ]

    def _run_script(self, hook: HookDefinition, stdin_data: dict[str, Any]) -> tuple[int, str]:
        """Run hook script with JSON on stdin. Returns (returncode, stderr)."""
        try:
            result = subprocess.run(
                ["bash", hook.script],
                input=json.dumps(stdin_data),
                capture_output=True,
                text=True,
                timeout=hook.timeout_s,
            )
            return result.returncode, result.stderr.strip()
        except subprocess.TimeoutExpired:
            logger.warning(
                "Hook script %s timed out after %ss — failing open",
                hook.script,
                hook.timeout_s,
            )
            return 0, ""
        except Exception as exc:
            logger.warning("Hook script %s failed to run: %s — failing open", hook.script, exc)
            return 0, ""

    def run_pre_tool(self, tool_name: str, tool_args: dict[str, Any]) -> HookResult:
        """Run all matching PRE_TOOL_USE hooks. First denial wins."""
        hooks = self._matching_hooks(tool_name, HookEvent.PRE_TOOL_USE)
        stdin_data = {"tool": tool_name, "args": tool_args}
        for hook in hooks:
            returncode, stderr = self._run_script(hook, stdin_data)
            if returncode != 0:
                msg = stderr or f"Hook '{hook.script}' denied tool '{tool_name}'"
                logger.info("Pre-tool hook denied %s: %s", tool_name, msg)
                return HookResult(allowed=False, message=msg)
        return HookResult(allowed=True)

    def run_post_tool(self, tool_name: str, result: Any) -> None:
        """Fire-and-forget POST_TOOL_USE hooks (errors logged, not raised)."""
        hooks = self._matching_hooks(tool_name, HookEvent.POST_TOOL_USE)
        stdin_data = {"tool": tool_name, "result": result}
        for hook in hooks:
            try:
                subprocess.Popen(
                    ["bash", hook.script],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                ).communicate(input=json.dumps(stdin_data), timeout=hook.timeout_s)
            except Exception as exc:
                logger.debug("Post-tool hook %s error (ignored): %s", hook.script, exc)

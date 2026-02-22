"""
castor/tools.py — LLM function/tool calling registry.

Defines and registers robot capabilities that an LLM brain can call by name,
allowing agentic workflows beyond simple JSON action output.

RCAN config example::

    agent:
      tools:
        - name: get_distance
          description: "Returns ultrasonic distance in cm"
          returns: float
        - name: announce_text
          description: "Speaks text aloud via TTS"
          parameters:
            text: {type: string, description: "Text to speak"}
        - name: take_snapshot
          description: "Captures a JPEG and returns base64"
          returns: string

Usage::

    from castor.tools import ToolRegistry

    reg = ToolRegistry()
    reg.register("ping", lambda: "pong")
    result = reg.call("ping")   # → "pong"
    schema = reg.to_openai_tools()   # → list of OpenAI tool dicts
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Tools")

__all__ = ["ToolRegistry", "ToolDefinition", "ToolResult"]


class ToolDefinition:
    """Metadata for a registered tool."""

    def __init__(
        self,
        name: str,
        description: str,
        fn: Callable,
        parameters: Optional[Dict] = None,
        returns: str = "any",
    ):
        self.name = name
        self.description = description
        self.fn = fn
        self.parameters = parameters or {}
        self.returns = returns

    def to_openai_schema(self) -> dict:
        """Convert to OpenAI function-calling tool schema."""
        properties = {}
        required = []
        for param_name, param_info in self.parameters.items():
            if isinstance(param_info, dict):
                properties[param_name] = {
                    "type": param_info.get("type", "string"),
                    "description": param_info.get("description", ""),
                }
                if param_info.get("required", True):
                    required.append(param_name)
            else:
                properties[param_name] = {"type": "string"}

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def to_anthropic_schema(self) -> dict:
        """Convert to Anthropic tool schema."""
        properties = {}
        required = []
        for param_name, param_info in self.parameters.items():
            if isinstance(param_info, dict):
                properties[param_name] = {
                    "type": param_info.get("type", "string"),
                    "description": param_info.get("description", ""),
                }
                if param_info.get("required", True):
                    required.append(param_name)
            else:
                properties[param_name] = {"type": "string"}

        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }


class ToolResult:
    """Result of a tool invocation."""

    def __init__(
        self, tool_name: str, result: Any, error: Optional[str] = None, duration_ms: float = 0.0
    ):
        self.tool_name = tool_name
        self.result = result
        self.error = error
        self.duration_ms = duration_ms
        self.ok = error is None

    def to_dict(self) -> dict:
        return {
            "tool": self.tool_name,
            "result": self.result,
            "error": self.error,
            "ok": self.ok,
            "duration_ms": round(self.duration_ms, 1),
        }

    def __repr__(self) -> str:
        if self.ok:
            return f"ToolResult({self.tool_name!r}, result={self.result!r})"
        return f"ToolResult({self.tool_name!r}, error={self.error!r})"


class ToolRegistry:
    """Registry of named robot tools callable by the LLM.

    Pre-registers built-in tools: ``get_distance``, ``take_snapshot``,
    ``announce_text``, ``get_status``.  Custom tools can be added via
    :meth:`register`.

    Args:
        config:  Optional RCAN ``agent`` config dict.  If provided, tools
                 defined under ``agent.tools`` are registered automatically.
    """

    def __init__(self, config: Optional[dict] = None):
        self._tools: Dict[str, ToolDefinition] = {}
        self._register_builtins()
        if config:
            self._register_from_config(config)

    # ── Built-ins ─────────────────────────────────────────────────────────────

    def _register_builtins(self) -> None:
        """Register the standard built-in robot tools."""
        self.register(
            name="get_status",
            fn=self._builtin_get_status,
            description="Returns the robot's current status as JSON.",
            returns="object",
        )
        self.register(
            name="take_snapshot",
            fn=self._builtin_take_snapshot,
            description="Captures a JPEG from the camera and returns it as base64.",
            returns="string",
        )
        self.register(
            name="announce_text",
            fn=self._builtin_announce_text,
            description="Speaks text aloud via TTS.",
            parameters={
                "text": {"type": "string", "description": "Text to speak", "required": True}
            },
        )
        self.register(
            name="get_distance",
            fn=self._builtin_get_distance,
            description="Returns the ultrasonic front-obstacle distance in metres. Returns -1 if unavailable.",
            returns="number",
        )

    @staticmethod
    def _builtin_get_status() -> dict:
        try:
            from castor.main import get_shared_fs

            fs = get_shared_fs()
            if fs:
                snap = fs.proc.snapshot()
                return snap if isinstance(snap, dict) else {"status": "ok"}
        except Exception:
            pass
        return {"status": "ok", "uptime_s": 0}

    @staticmethod
    def _builtin_take_snapshot() -> str:
        try:
            from castor.main import get_shared_camera

            cam = get_shared_camera()
            if cam and cam.is_available():
                frame = cam.capture_jpeg()
                return base64.b64encode(frame).decode()
        except Exception:
            pass
        return ""

    @staticmethod
    def _builtin_announce_text(text: str = "") -> str:
        try:
            from castor.main import get_shared_speaker

            speaker = get_shared_speaker()
            if speaker:
                speaker.say(text)
                return f"announced: {text[:60]}"
        except Exception:
            pass
        return "announce unavailable"

    @staticmethod
    def _builtin_get_distance() -> float:
        try:
            from castor.main import get_shared_camera

            cam = get_shared_camera()
            if cam and hasattr(cam, "last_depth") and cam.last_depth is not None:
                import numpy as np

                depth = cam.last_depth
                h, w = depth.shape
                center = depth[h // 3 : 2 * h // 3, w // 4 : 3 * w // 4]
                valid = center[center > 0]
                if len(valid) > 0:
                    return round(float(np.percentile(valid, 5)) / 1000.0, 3)
        except Exception:
            pass
        return -1.0

    # ── Registration ──────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        fn: Callable,
        description: str = "",
        parameters: Optional[Dict] = None,
        returns: str = "any",
    ) -> None:
        """Register a callable tool by name."""
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            fn=fn,
            parameters=parameters or {},
            returns=returns,
        )
        logger.debug("ToolRegistry: registered '%s'", name)

    def _register_from_config(self, agent_config: dict) -> None:
        """Register placeholder tools from RCAN ``agent.tools`` list."""
        for tool_cfg in agent_config.get("tools", []):
            name = tool_cfg.get("name", "")
            if not name or name in self._tools:
                continue  # skip if already registered as a built-in
            description = tool_cfg.get("description", "")
            parameters = tool_cfg.get("parameters", {})
            returns = tool_cfg.get("returns", "any")
            # Register a no-op placeholder (callers can override)
            self.register(
                name=name,
                fn=lambda *a, **kw: None,
                description=description,
                parameters=parameters,
                returns=returns,
            )
            logger.debug("ToolRegistry: registered placeholder '%s' from RCAN config", name)

    # ── Invocation ────────────────────────────────────────────────────────────

    def call(self, name: str, /, **kwargs) -> ToolResult:
        """Invoke a tool by name.  Returns a ToolResult (never raises).

        ``name`` is positional-only so that tool parameters named ``name``
        do not conflict with this argument when called via ``call_from_dict``.
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(name, None, error=f"Unknown tool: '{name}'")
        t0 = time.time()
        try:
            result = tool.fn(**kwargs)
            return ToolResult(name, result, duration_ms=(time.time() - t0) * 1000)
        except Exception as exc:
            logger.warning("ToolRegistry: '%s' raised: %s", name, exc)
            return ToolResult(name, None, error=str(exc), duration_ms=(time.time() - t0) * 1000)

    def call_from_dict(self, tool_call: dict) -> ToolResult:
        """Invoke a tool from an LLM tool_call dict.

        Accepts OpenAI-style: ``{"name": str, "arguments": str|dict}``
        or Anthropic-style: ``{"name": str, "input": dict}``.
        """
        name = tool_call.get("name", "")
        # OpenAI sends arguments as JSON string; Anthropic sends input as dict
        args = tool_call.get("input") or tool_call.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        return self.call(name, **args)

    # ── Schema export ─────────────────────────────────────────────────────────

    def to_openai_tools(self) -> List[dict]:
        """Return list of OpenAI function-calling tool definitions."""
        return [t.to_openai_schema() for t in self._tools.values()]

    def to_anthropic_tools(self) -> List[dict]:
        """Return list of Anthropic tool definitions."""
        return [t.to_anthropic_schema() for t in self._tools.values()]

    def list_tools(self) -> List[str]:
        """Return names of all registered tools."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

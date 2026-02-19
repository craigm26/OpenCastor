"""ResponderSpecialist — human-facing status and communication."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from .base_specialist import BaseSpecialist, Task, TaskResult, TaskStatus

_SEVERITY_LEVELS = {"info", "warn", "warning", "critical", "error"}
_SEVERITY_ICONS = {
    "info": "ℹ️",
    "warn": "⚠️",
    "warning": "⚠️",
    "critical": "🚨",
    "error": "❌",
}


class ResponderSpecialist(BaseSpecialist):
    """Human-facing status and communication specialist."""

    name = "responder"
    capabilities = ["report", "respond", "status", "alert"]

    def estimate_duration_s(self, task: Task) -> float:  # noqa: ARG002
        # Pure formatting — always fast
        return 0.1

    def health(self) -> dict:
        base = super().health()
        base["hardware_deps"] = "none"
        return base

    async def execute(self, task: Task) -> TaskResult:
        start = time.monotonic()

        handler = {
            "report": self._report,
            "respond": self._respond,
            "status": self._status,
            "alert": self._alert,
        }.get(task.type)

        if handler is None:
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                duration_s=time.monotonic() - start,
                error=f"ResponderSpecialist cannot handle task type '{task.type}'",
            )

        try:
            output = await handler(task)
        except Exception as exc:  # noqa: BLE001
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                duration_s=time.monotonic() - start,
                error=str(exc),
            )

        return TaskResult(
            task_id=task.id,
            status=TaskStatus.SUCCESS,
            output=output,
            duration_s=time.monotonic() - start,
        )

    # ------------------------------------------------------------------ #
    # Internal handlers
    # ------------------------------------------------------------------ #

    async def _report(self, task: Task) -> dict:
        """Format a robot status dict into a human-readable string."""
        params = task.params
        robot_status = params.get("robot_status") or params

        lines = ["📊 Robot Status Report", "=" * 30]

        if not robot_status:
            lines.append("  No status data available.")
        else:
            for key, value in sorted(robot_status.items()):
                # Pretty-print nested dicts
                if isinstance(value, dict):
                    lines.append(f"  {key}:")
                    for k2, v2 in sorted(value.items()):
                        lines.append(f"    {k2}: {v2}")
                elif isinstance(value, list):
                    lines.append(f"  {key}: [{', '.join(str(v) for v in value)}]")
                else:
                    lines.append(f"  {key}: {value}")

        lines.append("=" * 30)
        timestamp = datetime.now(tz=timezone.utc).isoformat()
        lines.append(f"  Generated: {timestamp}")

        report_str = "\n".join(lines)
        return {
            "report": report_str,
            "timestamp": timestamp,
            "fields_reported": len(robot_status) if robot_status else 0,
        }

    async def _respond(self, task: Task) -> dict:
        """Format a response message."""
        params = task.params
        message = params.get("message") or params.get("text") or str(params)
        context = params.get("context", "")

        lines = []
        if context:
            lines.append(f"[Context: {context}]")
        lines.append(f"Response: {message}")

        return {
            "response": "\n".join(lines),
            "message": message,
            "context": context,
        }

    async def _status(self, task: Task) -> dict:
        """Return a structured status summary."""
        params = task.params
        timestamp = datetime.now(tz=timezone.utc).isoformat()

        status_data = {
            "timestamp": timestamp,
            "robot_id": params.get("robot_id", "unknown"),
            "operational": params.get("operational", True),
            "battery_pct": params.get("battery_level", params.get("battery_pct", "unknown")),
            "position": params.get("position", {"x": 0.0, "y": 0.0}),
            "current_task": params.get("current_task", "idle"),
            "errors": params.get("errors", []),
            "uptime_s": params.get("uptime_s", 0),
            "mode": params.get("mode", "autonomous"),
        }

        # Summarise any extra params
        extra = {k: v for k, v in params.items() if k not in status_data}
        if extra:
            status_data["extra"] = extra

        return status_data

    async def _alert(self, task: Task) -> dict:
        """Format an alert message with severity."""
        params = task.params
        message = params.get("message") or params.get("text")
        if not message:
            raise ValueError("'message' is required for alert tasks")

        severity = str(params.get("severity", "info")).lower()
        if severity not in _SEVERITY_LEVELS:
            severity = "info"

        icon = _SEVERITY_ICONS.get(severity, "ℹ️")
        timestamp = datetime.now(tz=timezone.utc).isoformat()

        source = params.get("source", "robot")
        formatted = f"{icon} [{severity.upper()}] {source}: {message}"

        return {
            "alert": formatted,
            "severity": severity,
            "message": message,
            "source": source,
            "timestamp": timestamp,
            "icon": icon,
        }

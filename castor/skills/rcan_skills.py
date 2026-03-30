"""
castor/skills/rcan_skills.py — Custom RCAN skills for OpenCastor robots.

Each skill is a dict with the following keys:

    name             str   — unique identifier (e.g. "rcan_status")
    description      str   — human-readable description
    rcan_message_type str  — RCAN MessageType name (e.g. "DISCOVER")
    loa_required     int   — minimum Level of Assurance (0 or 1)
    version          str   — semver
    handler          callable — function(config: dict, args: dict) -> dict

Handlers return a result dict with at least {"status": "ok"|"error", ...}.

Usage::

    from castor.skills.rcan_skills import list_skills, get_skill

    for skill in list_skills():
        print(skill["name"], "LoA:", skill["loa_required"])

    estop = get_skill("rcan_estop")
    result = estop["handler"]({}, {})
"""

from __future__ import annotations

import platform
import time
from typing import Any

__all__ = [
    "RCAN_SKILLS",
    "get_skill",
    "list_skills",
]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _handle_rcan_status(config: dict, args: dict) -> dict:  # noqa: ARG001
    """Return robot identity, RCAN version, LoA, and revocation status."""
    rrn = config.get("rcan_protocol", {}).get("rrn") or config.get("rrn", "unknown")
    rcan_version = config.get("rcan_protocol", {}).get("version", "1.0")
    loa = config.get("rcan_protocol", {}).get("loa", 0)
    revoked = config.get("rcan_protocol", {}).get("revoked", False)

    return {
        "status": "ok",
        "rcan_message_type": "DISCOVER",
        "rrn": rrn,
        "rcan_version": rcan_version,
        "loa": loa,
        "revoked": revoked,
        "platform": platform.node(),
    }


def _handle_rcan_telemetry(config: dict, args: dict) -> dict:  # noqa: ARG001
    """Return a live sensor/system snapshot."""
    uptime: float | None = None
    try:
        with open("/proc/uptime") as fh:
            uptime = float(fh.read().split()[0])
    except Exception:
        pass

    cpu_percent: float | None = None
    try:
        import psutil  # type: ignore[import-not-found]

        cpu_percent = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        mem_used_mb = round(mem.used / 1024 / 1024, 1)
        mem_total_mb = round(mem.total / 1024 / 1024, 1)
    except ImportError:
        mem_used_mb = None
        mem_total_mb = None

    # NPU availability (Hailo or generic)
    npu_available = False
    try:
        import importlib

        npu_available = importlib.util.find_spec("hailo") is not None  # type: ignore[attr-defined]
    except Exception:
        pass

    return {
        "status": "ok",
        "rcan_message_type": "SENSOR_DATA",
        "timestamp": time.time(),
        "uptime_seconds": uptime,
        "cpu_percent": cpu_percent,
        "memory_used_mb": mem_used_mb,
        "memory_total_mb": mem_total_mb,
        "npu_available": npu_available,
        "platform": platform.node(),
    }


def _handle_rcan_navigate(config: dict, args: dict) -> dict:  # noqa: ARG001
    """Send a NAVIGATE command with a waypoint.

    Args dict:
        x (float): X coordinate
        y (float): Y coordinate
        z (float): Z coordinate (default 0.0)
        frame (str): Reference frame (default "map")
    """
    waypoint = {
        "x": float(args.get("x", 0.0)),
        "y": float(args.get("y", 0.0)),
        "z": float(args.get("z", 0.0)),
        "frame": str(args.get("frame", "map")),
    }
    return {
        "status": "ok",
        "rcan_message_type": "COMMAND",
        "command": "NAVIGATE",
        "waypoint": waypoint,
        "dispatched_at": time.time(),
    }


def _handle_rcan_estop(config: dict, args: dict) -> dict:  # noqa: ARG001
    """Broadcast an emergency stop — always honored regardless of LoA enforcement."""
    reason = str(args.get("reason", "emergency stop requested"))
    return {
        "status": "ok",
        "rcan_message_type": "SAFETY",
        "command": "ESTOP",
        "reason": reason,
        "dispatched_at": time.time(),
    }


def _handle_rcan_audit(config: dict, args: dict) -> dict:
    """Tail the last N lines of the RCAN audit log.

    Args dict:
        n (int): Number of lines to return (default 50)
        log_path (str): Override audit log path
    """
    n = int(args.get("n", 50))
    default_log = config.get("rcan_protocol", {}).get(
        "audit_log_path", "/var/log/opencastor/audit.log"
    )
    log_path = str(args.get("log_path", default_log))

    lines: list[str] = []
    try:
        with open(log_path) as fh:
            all_lines = fh.readlines()
            lines = [ln.rstrip() for ln in all_lines[-n:]]
    except FileNotFoundError:
        return {
            "status": "error",
            "rcan_message_type": "EVENT",
            "error": f"Audit log not found: {log_path}",
            "lines": [],
        }
    except PermissionError:
        return {
            "status": "error",
            "rcan_message_type": "EVENT",
            "error": f"Permission denied reading audit log: {log_path}",
            "lines": [],
        }

    return {
        "status": "ok",
        "rcan_message_type": "EVENT",
        "log_path": log_path,
        "lines_returned": len(lines),
        "lines": lines,
    }


# ---------------------------------------------------------------------------
# Skill registry
# ---------------------------------------------------------------------------

RCAN_SKILLS: list[dict[str, Any]] = [
    {
        "name": "rcan_status",
        "description": "Query robot identity, RCAN version, LoA, and revocation status",
        "rcan_message_type": "DISCOVER",
        "loa_required": 0,
        "version": "1.0.0",
        "handler": _handle_rcan_status,
    },
    {
        "name": "rcan_telemetry",
        "description": "Live sensor and system snapshot: CPU, memory, NPU, uptime",
        "rcan_message_type": "SENSOR_DATA",
        "loa_required": 0,
        "version": "1.0.0",
        "handler": _handle_rcan_telemetry,
    },
    {
        "name": "rcan_navigate",
        "description": "Send a NAVIGATE command with a waypoint dict {x, y, z, frame}",
        "rcan_message_type": "COMMAND",
        "loa_required": 1,
        "version": "1.0.0",
        "handler": _handle_rcan_navigate,
    },
    {
        "name": "rcan_estop",
        "description": "Emergency stop broadcast — always honored regardless of LoA enforcement",
        "rcan_message_type": "SAFETY",
        "loa_required": 0,
        "version": "1.0.0",
        "handler": _handle_rcan_estop,
    },
    {
        "name": "rcan_audit",
        "description": "Tail the last N lines of the RCAN audit log",
        "rcan_message_type": "EVENT",
        "loa_required": 1,
        "version": "1.0.0",
        "handler": _handle_rcan_audit,
    },
]

# Index for O(1) lookup
_SKILL_INDEX: dict[str, dict[str, Any]] = {s["name"]: s for s in RCAN_SKILLS}


def get_skill(name: str) -> dict[str, Any] | None:
    """Return the RCAN skill dict for *name*, or ``None`` if not found.

    Args:
        name: Skill name, e.g. ``"rcan_estop"``.

    Returns:
        Skill dict or ``None``.
    """
    return _SKILL_INDEX.get(name)


def list_skills() -> list[dict[str, Any]]:
    """Return all RCAN skills as a list of dicts.

    Returns:
        Copy of :data:`RCAN_SKILLS`.
    """
    return list(RCAN_SKILLS)

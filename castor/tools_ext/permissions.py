"""
castor/tools_ext/permissions.py — Typed PermissionMode with per-tool min_loa declaration.

Maps RCAN LoA levels (0-3) to tool access requirements. Gateway checks this at
dispatch time — before the handler runs — so per-handler LoA boilerplate is
no longer needed.
"""

from __future__ import annotations

from enum import IntEnum


class PermissionMode(IntEnum):
    """Maps to RCAN LoA levels 0-3."""

    READ_ONLY = 0
    WORKSPACE_WRITE = 1
    FULL_ACCESS = 2
    SAFETY_OVERRIDE = 3


# Canonical per-tool minimum LoA requirements.
# Tools not listed here default to READ_ONLY.
TOOL_PERMISSIONS: dict[str, PermissionMode] = {
    # Read-only — safe at any LoA
    "robot_status": PermissionMode.READ_ONLY,
    "robot_telemetry": PermissionMode.READ_ONLY,
    "get_config": PermissionMode.READ_ONLY,
    "list_commands": PermissionMode.READ_ONLY,
    "robot_health": PermissionMode.READ_ONLY,
    # Actuator — requires LoA 1+
    "robot_navigate": PermissionMode.WORKSPACE_WRITE,
    "robot_drive": PermissionMode.WORKSPACE_WRITE,
    "send_command": PermissionMode.WORKSPACE_WRITE,
    "robot_move": PermissionMode.WORKSPACE_WRITE,
    "robot_speak": PermissionMode.WORKSPACE_WRITE,
    # Config/firmware — requires LoA 2+
    "set_config": PermissionMode.FULL_ACCESS,
    "firmware_update": PermissionMode.FULL_ACCESS,
    "robot_restart": PermissionMode.FULL_ACCESS,
    "update_map": PermissionMode.FULL_ACCESS,
    # Safety-critical — requires LoA 3
    "safety_override": PermissionMode.SAFETY_OVERRIDE,
    "emergency_stop_clear": PermissionMode.SAFETY_OVERRIDE,
}


def check_permission(tool_name: str, session_loa: int) -> tuple[bool, str]:
    """
    Check whether session_loa meets the minimum requirement for tool_name.

    Returns (True, "") if allowed; (False, reason) if denied.
    Unknown tools default to READ_ONLY (safe default).
    """
    required = TOOL_PERMISSIONS.get(tool_name, PermissionMode.READ_ONLY)
    if session_loa >= required:
        return True, ""
    return (
        False,
        f"Tool '{tool_name}' requires LoA {required} ({required.name}); "
        f"session LoA is {session_loa}. Elevate authorization to proceed.",
    )


def get_tools_for_loa(loa: int) -> list[str]:
    """Return all tool names accessible at the given LoA level."""
    return [name for name, required in TOOL_PERMISSIONS.items() if loa >= required]

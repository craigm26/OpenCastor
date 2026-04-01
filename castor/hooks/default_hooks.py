"""
Default hook definitions for OpenCastor hardware safety gating.

Installs two shell scripts into ~/.opencastor/hooks/ on first use:
  - safety_check.sh  — denies motion commands when /tmp/robot-estop flag exists
  - audit_log.sh     — appends a JSON audit line for every tool call
"""

from __future__ import annotations

import logging
from pathlib import Path

from castor.hooks.runner import HookDefinition, HookEvent

logger = logging.getLogger("OpenCastor.DefaultHooks")

_HOOKS_DIR = Path.home() / ".opencastor" / "hooks"

_SAFETY_CHECK_SCRIPT = """\
#!/usr/bin/env bash
# safety_check.sh — deny robot motion commands when e-stop flag is set
if [ -f /tmp/robot-estop ]; then
    echo "E-stop active: /tmp/robot-estop flag is set. Motion denied." >&2
    exit 1
fi
exit 0
"""

_AUDIT_LOG_SCRIPT = """\
#!/usr/bin/env bash
# audit_log.sh — append a JSON audit entry for every tool call
AUDIT_LOG="${HOME}/.opencastor/audit.log"
mkdir -p "$(dirname "$AUDIT_LOG")"
# Read JSON from stdin and append with timestamp
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
INPUT=$(cat)
echo "{\"ts\":\"$TS\",\"event\":$INPUT}" >> "$AUDIT_LOG"
exit 0
"""

_MOTION_TOOLS = [
    "robot_navigate",
    "robot_drive",
    "send_command",
    "robot_move",
]


def _ensure_script(name: str, content: str) -> str:
    """Write script to hooks dir if not already present. Returns path."""
    _HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    path = _HOOKS_DIR / name
    if not path.exists():
        path.write_text(content)
        path.chmod(0o755)
        logger.info("Installed default hook script: %s", path)
    return str(path)


def get_default_hooks() -> list[HookDefinition]:
    """Return the default set of hook definitions, installing scripts as needed."""
    safety_script = _ensure_script("safety_check.sh", _SAFETY_CHECK_SCRIPT)
    audit_script = _ensure_script("audit_log.sh", _AUDIT_LOG_SCRIPT)

    return [
        HookDefinition(
            tools=_MOTION_TOOLS,
            script=safety_script,
            event=HookEvent.PRE_TOOL_USE,
            timeout_s=5.0,
        ),
        HookDefinition(
            tools=["*"],
            script=audit_script,
            event=HookEvent.POST_TOOL_USE,
            timeout_s=5.0,
        ),
    ]

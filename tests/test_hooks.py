"""Tests for PreToolUse / PostToolUse hook runner (#817)."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from castor.hooks.runner import HookDefinition, HookEvent, HookRunner


def _make_script(content: str) -> str:
    """Write a temporary shell script and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False, prefix="test_hook_")
    f.write(content)
    f.close()
    os.chmod(f.name, stat.S_IRWXU)
    return f.name


# ---------------------------------------------------------------------------
# 1. No hooks registered → always allow
# ---------------------------------------------------------------------------
def test_no_hooks_allow():
    runner = HookRunner([])
    result = runner.run_pre_tool("robot_move", {"speed": 1.0})
    assert result.allowed is True
    assert result.message == ""


# ---------------------------------------------------------------------------
# 2. Hook exits 0 → allow
# ---------------------------------------------------------------------------
def test_exit0_allow():
    script = _make_script("#!/usr/bin/env bash\nexit 0\n")
    try:
        hook = HookDefinition(tools=["robot_move"], script=script, event=HookEvent.PRE_TOOL_USE)
        runner = HookRunner([hook])
        result = runner.run_pre_tool("robot_move", {})
        assert result.allowed is True
    finally:
        Path(script).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 3. Hook exits 1 → deny with stderr as message
# ---------------------------------------------------------------------------
def test_exit1_deny_with_message():
    script = _make_script("#!/usr/bin/env bash\necho 'E-stop active' >&2\nexit 1\n")
    try:
        hook = HookDefinition(tools=["robot_move"], script=script, event=HookEvent.PRE_TOOL_USE)
        runner = HookRunner([hook])
        result = runner.run_pre_tool("robot_move", {})
        assert result.allowed is False
        assert "E-stop active" in result.message
    finally:
        Path(script).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 4. Timeout → fail-open (allow)
# ---------------------------------------------------------------------------
def test_timeout_fail_open():
    script = _make_script("#!/usr/bin/env bash\nsleep 10\nexit 1\n")
    try:
        hook = HookDefinition(
            tools=["robot_move"], script=script, event=HookEvent.PRE_TOOL_USE, timeout_s=0.1
        )
        runner = HookRunner([hook])
        result = runner.run_pre_tool("robot_move", {})
        # Timeout → fail-open → allowed
        assert result.allowed is True
    finally:
        Path(script).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 5. Wildcard "*" matches any tool
# ---------------------------------------------------------------------------
def test_wildcard_matches_all_tools():
    script = _make_script("#!/usr/bin/env bash\necho 'blocked by wildcard' >&2\nexit 1\n")
    try:
        hook = HookDefinition(tools=["*"], script=script, event=HookEvent.PRE_TOOL_USE)
        runner = HookRunner([hook])
        for tool in ("robot_navigate", "robot_drive", "send_command", "some_random_tool"):
            result = runner.run_pre_tool(tool, {})
            assert result.allowed is False, f"Expected denial for tool '{tool}'"
    finally:
        Path(script).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 6. Hook registered for specific tool does NOT fire for other tools
# ---------------------------------------------------------------------------
def test_specific_tool_does_not_fire_for_others():
    script = _make_script("#!/usr/bin/env bash\necho 'denied' >&2\nexit 1\n")
    try:
        hook = HookDefinition(tools=["robot_move"], script=script, event=HookEvent.PRE_TOOL_USE)
        runner = HookRunner([hook])
        # Should deny robot_move
        assert runner.run_pre_tool("robot_move", {}).allowed is False
        # Should allow other tools
        assert runner.run_pre_tool("sensor_read", {}).allowed is True
        assert runner.run_pre_tool("get_status", {}).allowed is True
    finally:
        Path(script).unlink(missing_ok=True)

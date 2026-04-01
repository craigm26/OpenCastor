"""Tests for castor.brain.robot_context."""

import os
import tempfile
from unittest.mock import mock_open, patch

from castor.brain.robot_context import (
    RobotContext,
    build_robot_context,
    format_robot_context,
)

_MINIMAL_CONFIG = {
    "metadata": {
        "rrn": "RRN-000000000001",
        "version": "2026.3.21.1",
    },
    "drivers": [],
}


def test_build_returns_dataclass():
    """build_robot_context returns a populated RobotContext dataclass."""
    ctx = build_robot_context(_MINIMAL_CONFIG)
    assert isinstance(ctx, RobotContext)
    assert ctx.rrn == "RRN-000000000001"
    assert ctx.firmware_version == "2026.3.21.1"
    assert ctx.hostname  # non-empty on any real machine
    assert ctx.generated_at  # ISO timestamp present


def test_format_includes_rrn():
    """format_robot_context includes the RRN in the output block."""
    ctx = RobotContext(rrn="rrn://org/robot/model/test-001", generated_at="2026-04-01T00:00:00")
    output = format_robot_context(ctx)
    assert "<rrn>rrn://org/robot/model/test-001</rrn>" in output
    assert 'generated="2026-04-01T00:00:00"' in output
    assert output.startswith("<robot-context")


def test_format_memory_truncated_at_2000_chars():
    """build_robot_context truncates session_memory to 2000 characters."""
    long_memory = "x" * 5000
    with (
        patch("builtins.open", mock_open(read_data=long_memory)),
        patch("castor.brain.robot_context._LOG_PATH", "/nonexistent/log"),
        patch("castor.brain.robot_context._MEMORY_PATH", "/fake/memory.md"),
    ):
        ctx = build_robot_context(_MINIMAL_CONFIG)
    assert len(ctx.session_memory) == 2000


def test_build_handles_missing_memory_file():
    """build_robot_context sets session_memory='' when robot-memory.md does not exist."""
    with tempfile.TemporaryDirectory() as tmp:
        missing = os.path.join(tmp, "robot-memory.md")
        with (
            patch("castor.brain.robot_context._MEMORY_PATH", missing),
            patch("castor.brain.robot_context._LOG_PATH", "/nonexistent/log"),
        ):
            ctx = build_robot_context(_MINIMAL_CONFIG)
    assert ctx.session_memory == ""


def test_format_output_has_no_cache_control_markers():
    """format_robot_context must not emit any cache_control markers."""
    ctx = RobotContext(
        rrn="RRN-000000000001",
        hostname="testbot",
        firmware_version="2026.3.21.1",
        session_memory="some memory",
        generated_at="2026-04-01T00:00:00",
    )
    output = format_robot_context(ctx)
    assert "cache_control" not in output
    assert "cache-control" not in output.lower()

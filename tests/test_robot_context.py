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


def test_format_memory_uses_structured_schema(tmp_path):
    """build_robot_context uses structured memory_schema when file is valid YAML."""
    import yaml
    from datetime import datetime, timezone

    memory_data = {
        "schema_version": "1.0",
        "rrn": "RRN-000000000001",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "entries": [
            {
                "id": "mem-abc123",
                "type": "hardware_observation",
                "text": "left wheel encoder intermittent under load",
                "confidence": 0.85,
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "last_reinforced": datetime.now(timezone.utc).isoformat(),
                "observation_count": 3,
                "tags": ["wheel"],
            }
        ],
    }
    memory_path = str(tmp_path / "robot-memory.md")
    with open(memory_path, "w") as f:
        f.write("---\n" + yaml.dump(memory_data) + "---\n")

    with (
        patch("castor.brain.robot_context._MEMORY_PATH", memory_path),
        patch("castor.brain.robot_context._LOG_PATH", "/nonexistent/log"),
    ):
        ctx = build_robot_context(_MINIMAL_CONFIG)

    # Structured output should contain the entry text and an emoji prefix
    assert "wheel encoder" in ctx.session_memory
    assert any(emoji in ctx.session_memory for emoji in ["🔴", "🟡", "🟢"])


def test_build_handles_missing_memory_file():
    """build_robot_context returns placeholder when robot-memory.md does not exist."""
    with tempfile.TemporaryDirectory() as tmp:
        missing = os.path.join(tmp, "robot-memory.md")
        with (
            patch("castor.brain.robot_context._MEMORY_PATH", missing),
            patch("castor.brain.robot_context._LOG_PATH", "/nonexistent/log"),
        ):
            ctx = build_robot_context(_MINIMAL_CONFIG)
    # Missing file → empty memory → placeholder text
    assert "no stored observations" in ctx.session_memory or ctx.session_memory == ""


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

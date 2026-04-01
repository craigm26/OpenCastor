"""
tests/test_prompt_cache_anchoring.py — Prompt cache anchoring tests.

Verifies:
  - Static section is present and comes before dynamic content
  - Static block carries cache_control: {"type": "ephemeral"}
  - Dynamic block (when present) has no cache_control
  - build_messaging_prompt() output is unchanged (backward compat)
"""

from __future__ import annotations

from castor.prompt_cache import build_cached_messaging_blocks
from castor.providers.base import BaseProvider


# ── build_cached_messaging_blocks ─────────────────────────────────────────────


def test_cache_control_present_on_static_block():
    """Static block must carry cache_control: ephemeral."""
    blocks = build_cached_messaging_blocks("You are a robot.")
    assert len(blocks) >= 1
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}


def test_static_block_type_is_text():
    blocks = build_cached_messaging_blocks("Static content here.")
    assert blocks[0]["type"] == "text"
    assert blocks[0]["text"] == "Static content here."


def test_static_only_when_no_dynamic():
    """Without dynamic content, only one block is returned."""
    blocks = build_cached_messaging_blocks("Static.", "")
    assert len(blocks) == 1


def test_static_only_when_dynamic_is_whitespace():
    blocks = build_cached_messaging_blocks("Static.", "   \n  ")
    assert len(blocks) == 1


def test_dynamic_not_cached():
    """Dynamic block must NOT have cache_control."""
    blocks = build_cached_messaging_blocks("Static.", "Dynamic telemetry here.")
    assert len(blocks) == 2
    assert "cache_control" not in blocks[1]


def test_prompt_structure_static_before_dynamic():
    """Static section must come before dynamic section in the block list."""
    static = "STATIC: robot identity and commands"
    dynamic = "DYNAMIC: current CPU temp 72°C"
    blocks = build_cached_messaging_blocks(static, dynamic)
    assert blocks[0]["text"] == static
    assert blocks[1]["text"] == dynamic


def test_empty_static_gets_default():
    """Empty static string should fall back to a default robot description."""
    blocks = build_cached_messaging_blocks("")
    assert "robot" in blocks[0]["text"].lower()
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}


# ── _build_static_messaging_content ──────────────────────────────────────────


def test_static_content_contains_robot_name():
    content = BaseProvider._build_static_messaging_content(
        robot_name="Artoo", surface="whatsapp", capabilities=[]
    )
    assert "Artoo" in content


def test_static_content_contains_command_vocabulary():
    content = BaseProvider._build_static_messaging_content(
        robot_name="Bob", surface="terminal", capabilities=["nav", "teleop"]
    )
    assert "COMMAND VOCABULARY" in content


def test_static_content_contains_response_format():
    content = BaseProvider._build_static_messaging_content(
        robot_name="Bob", surface="whatsapp", capabilities=[]
    )
    assert "RESPONSE FORMAT" in content


def test_static_content_surface_note_whatsapp():
    content = BaseProvider._build_static_messaging_content(
        robot_name="Bob", surface="whatsapp", capabilities=[]
    )
    assert "WhatsApp" in content


def test_static_content_surface_note_voice():
    content = BaseProvider._build_static_messaging_content(
        robot_name="Bob", surface="voice", capabilities=[]
    )
    assert "TTS" in content or "spoken" in content


def test_static_content_does_not_include_hardware_status():
    """Hardware status is dynamic — must NOT appear in static content."""
    content = BaseProvider._build_static_messaging_content(
        robot_name="Bob", surface="whatsapp", capabilities=[]
    )
    assert "HARDWARE STATUS" not in content


def test_static_content_does_not_include_live_telemetry():
    """Sensor readings are dynamic — must NOT appear in static content."""
    content = BaseProvider._build_static_messaging_content(
        robot_name="Bob", surface="whatsapp", capabilities=[]
    )
    assert "LIVE TELEMETRY" not in content


# ── _build_dynamic_messaging_content ─────────────────────────────────────────


def test_dynamic_content_empty_when_no_args():
    content = BaseProvider._build_dynamic_messaging_content()
    assert content == ""


def test_dynamic_content_includes_hardware_status():
    content = BaseProvider._build_dynamic_messaging_content(
        hardware={"motors": "online", "camera": "mock"}
    )
    assert "HARDWARE STATUS" in content
    assert "motors" in content


def test_dynamic_content_includes_live_telemetry():
    content = BaseProvider._build_dynamic_messaging_content(
        sensor_snapshot={"battery_pct": 55.0, "speed_ms": 0.3}
    )
    assert "LIVE TELEMETRY" in content
    assert "55%" in content


def test_dynamic_content_includes_memory():
    content = BaseProvider._build_dynamic_messaging_content(
        memory_context="Remember: avoid stairs."
    )
    assert "ROBOT MEMORY" in content
    assert "avoid stairs" in content


# ── Backward compat: build_messaging_prompt still works ──────────────────────


def test_build_messaging_prompt_backward_compat():
    """build_messaging_prompt() must still return a combined string."""
    result = BaseProvider.build_messaging_prompt(
        robot_name="Eve",
        surface="terminal",
        hardware={"motors": "online"},
        capabilities=["nav"],
        sensor_snapshot={"battery_pct": 80.0},
    )
    assert isinstance(result, str)
    assert "Eve" in result
    assert "COMMAND VOCABULARY" in result
    assert "HARDWARE STATUS" in result
    assert "LIVE TELEMETRY" in result


def test_build_messaging_prompt_no_dynamic_no_separator():
    """With no hardware/sensor/memory, result equals the static content exactly."""
    static = BaseProvider._build_static_messaging_content("Bob", "whatsapp", [])
    full = BaseProvider.build_messaging_prompt(robot_name="Bob", surface="whatsapp")
    assert full == static

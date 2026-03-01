"""Tests for WhatsApp mission trigger (Issue #282)."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from castor.channels.base import BaseChannel


class ConcreteChannel(BaseChannel):
    """Minimal concrete channel for testing."""

    name = "test"

    def start(self):
        pass

    def stop(self):
        pass

    def send_message(self, message, recipient=None, **kwargs):
        pass


def make_channel():
    config = {"rate_limit_max": 100, "rate_limit_window": 60}
    cb = MagicMock(return_value="ok")
    return ConcreteChannel(config, on_message=cb), cb


# ── parse_mission_trigger tests ───────────────────────────────────────────────


def test_parse_mission_trigger_detects_bang_mission():
    ch, _ = make_channel()
    assert ch.parse_mission_trigger("!mission patrol") == "patrol"


def test_parse_mission_trigger_detects_slash_mission():
    ch, _ = make_channel()
    assert ch.parse_mission_trigger("/mission dock") == "dock"


def test_parse_mission_trigger_case_insensitive():
    ch, _ = make_channel()
    assert ch.parse_mission_trigger("!MISSION patrol") == "patrol"


def test_parse_mission_trigger_returns_none_for_non_mission():
    ch, _ = make_channel()
    assert ch.parse_mission_trigger("go forward") is None


def test_parse_mission_trigger_returns_none_for_empty():
    ch, _ = make_channel()
    assert ch.parse_mission_trigger("") is None


def test_parse_mission_trigger_returns_mission_name_with_dash():
    ch, _ = make_channel()
    assert ch.parse_mission_trigger("!mission full-scan") == "full-scan"


def test_parse_mission_trigger_returns_mission_name_with_underscore():
    ch, _ = make_channel()
    assert ch.parse_mission_trigger("!mission goto_kitchen") == "goto_kitchen"


def test_parse_mission_trigger_ignores_leading_spaces():
    ch, _ = make_channel()
    result = ch.parse_mission_trigger("  !mission patrol")
    assert result == "patrol"


def test_parse_mission_trigger_returns_none_no_name():
    ch, _ = make_channel()
    # No name after !mission
    assert ch.parse_mission_trigger("!mission") is None


# ── handle_mission_trigger tests ──────────────────────────────────────────────


def test_handle_mission_trigger_returns_string():
    ch, _ = make_channel()
    with patch("castor.behaviors.BehaviorRunner") as MockRunner:
        mock_runner = MockRunner.return_value
        mock_runner.is_running = False
        result = ch.handle_mission_trigger("patrol", "user1")
    assert isinstance(result, str)


def test_handle_mission_trigger_already_running_warns():
    ch, _ = make_channel()
    mock_runner = MagicMock()
    mock_runner.is_running = True
    ch._mission_runner = mock_runner
    result = ch.handle_mission_trigger("patrol", "user1")
    assert "already running" in result.lower() or "⚠️" in result


def test_handle_mission_trigger_not_found_returns_error():
    ch, _ = make_channel()
    mock_runner = MagicMock()
    mock_runner.is_running = False
    ch._mission_runner = mock_runner
    result = ch.handle_mission_trigger("nonexistent_xyz_mission", "user1")
    assert "not found" in result.lower() or "❌" in result


def test_handle_mission_trigger_found_returns_started():
    ch, _ = make_channel()
    mock_runner = MagicMock()
    mock_runner.is_running = False
    ch._mission_runner = mock_runner

    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            behavior_path = os.path.join(tmpdir, "patrol.behavior.yaml")
            with open(behavior_path, "w") as f:
                f.write("patrol:\n  steps:\n    - type: stop\n")
            result = ch.handle_mission_trigger("patrol", "user1")
        finally:
            os.chdir(old_cwd)

    assert "patrol" in result.lower()


# ── handle_message integration tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_message_triggers_mission():
    ch, cb = make_channel()
    with patch.object(ch, "parse_mission_trigger", return_value="patrol"):
        with patch.object(
            ch, "handle_mission_trigger", return_value="🚀 Mission patrol started!"
        ) as mock_mt:
            result = await ch.handle_message("user1", "!mission patrol")
    mock_mt.assert_called_once_with("patrol", "user1")
    assert result == "🚀 Mission patrol started!"


@pytest.mark.asyncio
async def test_handle_message_non_mission_skips_trigger():
    ch, cb = make_channel()
    cb.return_value = "response"
    await ch.handle_message("user1", "go forward")
    # Should go through normal on_message callback
    cb.assert_called_once()


@pytest.mark.asyncio
async def test_handle_message_mission_does_not_call_callback():
    ch, cb = make_channel()
    with patch.object(ch, "parse_mission_trigger", return_value="patrol"):
        with patch.object(ch, "handle_mission_trigger", return_value="started"):
            await ch.handle_message("user1", "!mission patrol")
    cb.assert_not_called()

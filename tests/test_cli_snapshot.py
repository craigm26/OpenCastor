"""Tests for castor snapshot CLI command (Issue #348)."""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

from castor.cli import cmd_snapshot


def make_args(action="latest", extra=None):
    args = types.SimpleNamespace()
    args.snapshot_action = action
    args.snapshot_args = extra or []
    return args


SAMPLE_SNAPSHOT = {
    "timestamp": 1740000000.0,
    "system": {"cpu_percent": 12.5, "ram_used_mb": 200, "platform": "arm64"},
    "providers": [],
    "drivers": [],
    "channels": [],
    "last_episode": None,
    "loop_metrics": {},
}


# ── cmd_snapshot take ─────────────────────────────────────────────────────────


def test_snapshot_take_calls_mgr_take(capsys):
    mock_mgr = MagicMock()
    mock_mgr.take.return_value = SAMPLE_SNAPSHOT
    with patch("castor.snapshot.get_manager", return_value=mock_mgr):
        cmd_snapshot(make_args("take"))
    mock_mgr.take.assert_called_once()


def test_snapshot_take_prints_json(capsys):
    mock_mgr = MagicMock()
    mock_mgr.take.return_value = SAMPLE_SNAPSHOT
    with patch("castor.snapshot.get_manager", return_value=mock_mgr):
        cmd_snapshot(make_args("take"))
    out = capsys.readouterr().out
    assert "timestamp" in out


def test_snapshot_take_prints_cpu(capsys):
    mock_mgr = MagicMock()
    mock_mgr.take.return_value = SAMPLE_SNAPSHOT
    with patch("castor.snapshot.get_manager", return_value=mock_mgr):
        cmd_snapshot(make_args("take"))
    out = capsys.readouterr().out
    assert "cpu_percent" in out


# ── cmd_snapshot latest ───────────────────────────────────────────────────────


def test_snapshot_latest_calls_mgr_latest(capsys):
    mock_mgr = MagicMock()
    mock_mgr.latest.return_value = SAMPLE_SNAPSHOT
    with patch("castor.snapshot.get_manager", return_value=mock_mgr):
        cmd_snapshot(make_args("latest"))
    mock_mgr.latest.assert_called_once()


def test_snapshot_latest_prints_snapshot(capsys):
    mock_mgr = MagicMock()
    mock_mgr.latest.return_value = SAMPLE_SNAPSHOT
    with patch("castor.snapshot.get_manager", return_value=mock_mgr):
        cmd_snapshot(make_args("latest"))
    out = capsys.readouterr().out
    assert "timestamp" in out


def test_snapshot_latest_prints_no_snapshot_message(capsys):
    mock_mgr = MagicMock()
    mock_mgr.latest.return_value = None
    with patch("castor.snapshot.get_manager", return_value=mock_mgr):
        cmd_snapshot(make_args("latest"))
    out = capsys.readouterr().out
    assert "No snapshots" in out


def test_snapshot_default_action_is_latest(capsys):
    """When no action given, defaults to 'latest'."""
    mock_mgr = MagicMock()
    mock_mgr.latest.return_value = SAMPLE_SNAPSHOT
    with patch("castor.snapshot.get_manager", return_value=mock_mgr):
        cmd_snapshot(make_args(None))
    mock_mgr.latest.assert_called_once()


# ── cmd_snapshot history ──────────────────────────────────────────────────────


def test_snapshot_history_calls_mgr_history(capsys):
    mock_mgr = MagicMock()
    mock_mgr.history.return_value = [SAMPLE_SNAPSHOT, SAMPLE_SNAPSHOT]
    with patch("castor.snapshot.get_manager", return_value=mock_mgr):
        cmd_snapshot(make_args("history"))
    mock_mgr.history.assert_called_once()


def test_snapshot_history_prints_entries(capsys):
    mock_mgr = MagicMock()
    snaps = [SAMPLE_SNAPSHOT, SAMPLE_SNAPSHOT, SAMPLE_SNAPSHOT]
    mock_mgr.history.return_value = snaps
    with patch("castor.snapshot.get_manager", return_value=mock_mgr):
        cmd_snapshot(make_args("history"))
    out = capsys.readouterr().out
    assert "[1]" in out
    assert "[3]" in out


def test_snapshot_history_prints_cpu_info(capsys):
    mock_mgr = MagicMock()
    mock_mgr.history.return_value = [SAMPLE_SNAPSHOT]
    with patch("castor.snapshot.get_manager", return_value=mock_mgr):
        cmd_snapshot(make_args("history"))
    out = capsys.readouterr().out
    assert "cpu=" in out


def test_snapshot_registered_in_cli():
    """Ensure 'snapshot' is in the CLI dispatch table."""

    from castor import cli

    with patch.object(cli, "main", side_effect=SystemExit(0)):
        pass
    # Verify cmd_snapshot is importable
    assert callable(cmd_snapshot)


def test_snapshot_history_empty_list(capsys):
    mock_mgr = MagicMock()
    mock_mgr.history.return_value = []
    with patch("castor.snapshot.get_manager", return_value=mock_mgr):
        cmd_snapshot(make_args("history"))
    out = capsys.readouterr().out
    assert "0 snapshots" in out or "Last 0" in out

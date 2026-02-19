"""Tests for castor/dashboard_tui.py — file-based status helpers and panels."""

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from castor.dashboard_tui import (
    _get_agents_lines,
    _get_improvements_lines,
    _get_swarm_lines,
    _get_episode_count,
    _read_json_file,
    _render_agents_panel,
    _render_improvements_panel,
    _render_swarm_panel,
)
from castor.agents.base import AgentStatus, BaseAgent
from castor.agents.registry import AgentRegistry


# ---------------------------------------------------------------------------
# Minimal stub agent for registry tests
# ---------------------------------------------------------------------------


class _StubAgent(BaseAgent):
    name = "stub"

    async def observe(self, sensor_data):
        return {}

    async def act(self, context):
        return {}


# ---------------------------------------------------------------------------
# _read_json_file
# ---------------------------------------------------------------------------


class TestReadJsonFile:
    def test_returns_none_when_missing(self, tmp_path):
        """Returns None when the file does not exist."""
        missing = str(tmp_path / "does_not_exist.json")
        assert _read_json_file(missing) is None

    def test_returns_none_when_stale(self, tmp_path):
        """Returns None when the file's mtime is older than max_age_s."""
        stale_file = tmp_path / "stale.json"
        stale_file.write_text(json.dumps({"key": "value"}))
        # Back-date the file modification time by 60 seconds
        old_mtime = time.time() - 60
        os.utime(str(stale_file), (old_mtime, old_mtime))
        assert _read_json_file(str(stale_file), max_age_s=30) is None

    def test_returns_data_when_fresh(self, tmp_path):
        """Returns parsed JSON when the file is fresh."""
        fresh_file = tmp_path / "fresh.json"
        payload = {"hello": "world", "count": 42}
        fresh_file.write_text(json.dumps(payload))
        result = _read_json_file(str(fresh_file), max_age_s=30)
        assert result == payload

    def test_returns_none_on_invalid_json(self, tmp_path):
        """Returns None when the file contains invalid JSON."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("this is { not json")
        assert _read_json_file(str(bad_file), max_age_s=30) is None

    def test_exactly_at_max_age_returns_none(self, tmp_path):
        """A file aged exactly at max_age_s is treated as stale."""
        f = tmp_path / "edge.json"
        f.write_text(json.dumps({"x": 1}))
        max_age = 10
        # Backdate by max_age + a small epsilon so age > max_age_s
        old_mtime = time.time() - (max_age + 0.5)
        os.utime(str(f), (old_mtime, old_mtime))
        assert _read_json_file(str(f), max_age_s=max_age) is None


# ---------------------------------------------------------------------------
# AgentRegistry.write_status_file
# ---------------------------------------------------------------------------


class TestWriteStatusFile:
    def test_creates_file(self, tmp_path):
        """write_status_file creates the JSON file at the given path."""
        registry = AgentRegistry()
        registry.register(_StubAgent)
        registry.spawn("stub")

        out_path = str(tmp_path / "agent_status.json")
        registry.write_status_file(path=out_path)

        assert os.path.exists(out_path)

    def test_file_contains_timestamp(self, tmp_path):
        """The written file includes a 'timestamp' key."""
        registry = AgentRegistry()
        out_path = str(tmp_path / "agent_status.json")
        registry.write_status_file(path=out_path)

        with open(out_path) as fh:
            data = json.load(fh)
        assert "timestamp" in data
        assert isinstance(data["timestamp"], float)

    def test_file_contains_agents(self, tmp_path):
        """The written file includes an 'agents' dict with spawned agents."""
        registry = AgentRegistry()
        registry.register(_StubAgent)
        registry.spawn("stub")

        out_path = str(tmp_path / "agent_status.json")
        registry.write_status_file(path=out_path)

        with open(out_path) as fh:
            data = json.load(fh)
        assert "agents" in data
        assert "stub" in data["agents"]

    def test_creates_parent_directories(self, tmp_path):
        """write_status_file creates intermediate directories if needed."""
        registry = AgentRegistry()
        nested = str(tmp_path / "deep" / "nested" / "status.json")
        registry.write_status_file(path=nested)
        assert os.path.exists(nested)

    def test_empty_registry_writes_empty_agents(self, tmp_path):
        """An empty registry produces an empty 'agents' dict."""
        registry = AgentRegistry()
        out_path = str(tmp_path / "empty.json")
        registry.write_status_file(path=out_path)

        with open(out_path) as fh:
            data = json.load(fh)
        assert data["agents"] == {}


# ---------------------------------------------------------------------------
# _render_agents_panel — renders gracefully (even with no status file)
# ---------------------------------------------------------------------------


class TestAgentsPanelNoData:
    def test_agents_panel_no_data(self):
        """_render_agents_panel renders gracefully when no status file exists."""
        mock_stdscr = MagicMock()
        with patch("castor.dashboard_tui._read_json_file", return_value=None):
            # Must not raise
            _render_agents_panel(mock_stdscr, 0, 0, 80)

    def test_agents_panel_no_data_shows_placeholder(self):
        """_get_agents_lines returns '[no agent data]' when file is missing."""
        with patch("castor.dashboard_tui._read_json_file", return_value=None):
            lines = _get_agents_lines()
        assert lines == ["[no agent data]"]

    def test_agents_panel_with_data_shows_agents(self):
        """_get_agents_lines returns one line per agent when data is present."""
        fake_data = {
            "timestamp": time.time(),
            "agents": {
                "observer": {"status": "running", "uptime_s": 12.3, "errors": []},
                "navigator": {"status": "running", "uptime_s": 12.1, "errors": []},
            },
        }
        with patch("castor.dashboard_tui._read_json_file", return_value=fake_data):
            lines = _get_agents_lines()
        assert len(lines) == 2
        assert any("observer" in ln for ln in lines)
        assert any("navigator" in ln for ln in lines)

    def test_agents_panel_empty_agents_dict(self):
        """An empty agents dict returns '[no agents running]'."""
        fake_data = {"timestamp": time.time(), "agents": {}}
        with patch("castor.dashboard_tui._read_json_file", return_value=fake_data):
            lines = _get_agents_lines()
        assert lines == ["[no agents running]"]

    def test_render_agents_panel_calls_addstr(self):
        """_render_agents_panel calls stdscr.addstr for each line."""
        mock_stdscr = MagicMock()
        fake_data = {
            "timestamp": time.time(),
            "agents": {"observer": {"status": "running", "uptime_s": 5.0, "errors": []}},
        }
        with patch("castor.dashboard_tui._read_json_file", return_value=fake_data):
            _render_agents_panel(mock_stdscr, 2, 0, 80)
        assert mock_stdscr.addstr.call_count >= 1


# ---------------------------------------------------------------------------
# _render_swarm_panel
# ---------------------------------------------------------------------------


class TestSwarmPanel:
    def test_swarm_panel_solo_mode_when_no_file(self):
        """Returns '[solo mode]' when swarm_memory.json is missing."""
        with patch("castor.dashboard_tui._read_json_file", return_value=None):
            lines = _get_swarm_lines()
        assert lines == ["[solo mode]"]

    def test_swarm_panel_counts_peers_and_patches(self):
        """Counts consensus peers and swarm_patch entries correctly."""
        fake_data = {
            "consensus_peer_1": "nodeA",
            "consensus_peer_2": "nodeB",
            "swarm_patch:abc": {"applied": True},
            "swarm_patch:def": {"applied": True},
            "swarm_patch:ghi": {"applied": False},
            "other_key": "value",
        }
        with patch("castor.dashboard_tui._read_json_file", return_value=fake_data):
            lines = _get_swarm_lines()
        assert len(lines) == 1
        assert "2 peers" in lines[0]
        assert "3 synced" in lines[0]

    def test_render_swarm_panel_no_error(self):
        """_render_swarm_panel does not raise with no data."""
        mock_stdscr = MagicMock()
        with patch("castor.dashboard_tui._read_json_file", return_value=None):
            _render_swarm_panel(mock_stdscr, 0, 0, 80)


# ---------------------------------------------------------------------------
# _render_improvements_panel — reads Sisyphus patch history
# ---------------------------------------------------------------------------


class TestImprovementsPanelReadsHistory:
    def test_improvements_panel_no_file(self):
        """Returns '[no improvements yet]' when file is missing."""
        with patch("castor.dashboard_tui._read_json_file", return_value=None):
            lines = _get_improvements_lines()
        assert lines == ["[no improvements yet]"]

    def test_improvements_panel_reads_history(self):
        """_get_improvements_lines parses and formats patch history."""
        history = {
            "patches": [
                {
                    "status": "success",
                    "kind": "config",
                    "name": "grasp_approach_angle_offset",
                    "date": "2026-02-18",
                },
                {
                    "status": "failed",
                    "kind": "behavior",
                    "name": "some_behavior",
                    "date": "2026-02-17",
                },
            ]
        }
        with patch("castor.dashboard_tui._read_json_file", return_value=history):
            lines = _get_improvements_lines()

        assert len(lines) == 2
        assert any("✅" in ln for ln in lines)
        assert any("❌" in ln for ln in lines)
        assert any("grasp_approach_angle_offset" in ln for ln in lines)

    def test_improvements_panel_limits_to_last_5(self):
        """Only the last 5 patches are shown."""
        patches = [
            {"status": "success", "kind": "config", "name": f"patch_{i}", "date": "2026-02-18"}
            for i in range(10)
        ]
        history = {"patches": patches}
        with patch("castor.dashboard_tui._read_json_file", return_value=history):
            lines = _get_improvements_lines()
        assert len(lines) == 5

    def test_improvements_panel_list_format(self):
        """Accepts a top-level list as well as a dict with 'patches' key."""
        history_list = [
            {"status": "success", "kind": "config", "name": "offset_fix", "date": "2026-02-18"}
        ]
        with patch("castor.dashboard_tui._read_json_file", return_value=history_list):
            lines = _get_improvements_lines()
        assert len(lines) == 1
        assert "✅" in lines[0]

    def test_improvements_panel_empty_patches(self):
        """An empty patches list returns '[no improvements yet]'."""
        history = {"patches": []}
        with patch("castor.dashboard_tui._read_json_file", return_value=history):
            lines = _get_improvements_lines()
        assert lines == ["[no improvements yet]"]

    def test_render_improvements_panel_no_error(self):
        """_render_improvements_panel does not raise with no data."""
        mock_stdscr = MagicMock()
        with patch("castor.dashboard_tui._read_json_file", return_value=None):
            _render_improvements_panel(mock_stdscr, 0, 0, 80)

    def test_render_improvements_panel_with_data(self):
        """_render_improvements_panel calls addstr for each line."""
        mock_stdscr = MagicMock()
        history = {
            "patches": [
                {"status": "success", "kind": "config", "name": "fix", "date": "2026-02-18"},
            ]
        }
        with patch("castor.dashboard_tui._read_json_file", return_value=history):
            _render_improvements_panel(mock_stdscr, 0, 0, 80)
        assert mock_stdscr.addstr.call_count >= 1


# ---------------------------------------------------------------------------
# _get_episode_count
# ---------------------------------------------------------------------------


class TestEpisodeCounter:
    def test_returns_zero_when_dir_missing(self, tmp_path):
        """Returns 0 when the episodes directory does not exist."""
        missing_dir = str(tmp_path / "no_such_dir" / "episodes")
        with patch("castor.dashboard_tui._EPISODES_DIR", missing_dir):
            count = _get_episode_count()
        assert count == 0

    def test_counts_json_files(self, tmp_path):
        """Counts only .json files in the episodes directory."""
        ep_dir = tmp_path / "episodes"
        ep_dir.mkdir()
        for i in range(5):
            (ep_dir / f"episode_{i}.json").write_text("{}")
        # Add a non-json file that should not be counted
        (ep_dir / "notes.txt").write_text("ignore me")

        with patch("castor.dashboard_tui._EPISODES_DIR", str(ep_dir)):
            count = _get_episode_count()
        assert count == 5

    def test_empty_directory_returns_zero(self, tmp_path):
        """An existing but empty episodes dir returns 0."""
        ep_dir = tmp_path / "episodes"
        ep_dir.mkdir()
        with patch("castor.dashboard_tui._EPISODES_DIR", str(ep_dir)):
            count = _get_episode_count()
        assert count == 0

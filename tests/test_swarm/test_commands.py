"""
tests/test_swarm.py — Tests for castor/commands/swarm.py (Issues #115 / #116).

Covers:
  1. test_load_swarm_yaml               — real swarm.yaml loads with >= 1 node
  2. test_status_queries_all_nodes      — mock httpx, one call per node
  3. test_status_offline_node_handled   — ConnectError → offline, no crash
  4. test_command_broadcasts_to_all     — POST /api/command for each node
  5. test_command_node_filter           — --node alex only calls alex
  6. test_stop_broadcasts               — POST /api/stop for each node
  7. test_json_output                   — --json flag emits valid JSON
  8. test_sync_posts_reload             — POST /api/config/reload for each node
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SWARM_YAML_PATH = Path(__file__).parent.parent / "config" / "swarm.yaml"

_FAKE_NODES: List[Dict[str, Any]] = [
    {
        "name": "alpha",
        "host": "alpha.local",
        "ip": "192.168.1.10",
        "port": 8000,
        "token": "tok-alpha",
        "tags": ["camera"],
    },
    {
        "name": "beta",
        "host": "beta.local",
        "ip": "192.168.1.11",
        "port": 8000,
        "token": "tok-beta",
        "tags": [],
    },
]

_HEALTHY_RESPONSE = {
    "brain": True,
    "driver": True,
    "uptime_s": 3661,
    "robot_name": "TestBot",
}


def _make_httpx_response(status: int = 200, json_data: dict | None = None) -> MagicMock:
    """Build a fake httpx Response-like mock."""
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.json.return_value = json_data or _HEALTHY_RESPONSE
    return mock_resp


def _make_httpx_client_mock(response: MagicMock) -> MagicMock:
    """Build a context-manager-compatible httpx.Client mock."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = response
    mock_client.post.return_value = response
    return mock_client


# ---------------------------------------------------------------------------
# 1. test_load_swarm_yaml
# ---------------------------------------------------------------------------


class TestLoadSwarmYaml:
    def test_load_swarm_yaml(self):
        """Real swarm.yaml should load and contain at least one node."""
        from castor.commands.swarm import load_swarm_config

        nodes = load_swarm_config()
        assert isinstance(nodes, list), "load_swarm_config() must return a list"
        assert len(nodes) >= 1, "swarm.yaml must contain at least one node"
        # Each node must have a 'name' key
        for node in nodes:
            assert "name" in node, f"Node missing 'name': {node}"

    def test_load_swarm_yaml_missing_file_returns_empty(self):
        """Missing swarm.yaml path should return [] without crashing."""
        from castor.commands.swarm import load_swarm_config

        result = load_swarm_config(config_path="/nonexistent/swarm.yaml")
        assert result == []

    def test_load_swarm_yaml_custom_path(self, tmp_path):
        """A custom swarm.yaml path is respected."""
        import yaml

        data = {"nodes": [{"name": "custom", "host": "1.2.3.4", "port": 8000}]}
        yaml_file = tmp_path / "swarm.yaml"
        yaml_file.write_text(yaml.dump(data))

        from castor.commands.swarm import load_swarm_config

        nodes = load_swarm_config(config_path=str(yaml_file))
        assert len(nodes) == 1
        assert nodes[0]["name"] == "custom"


# ---------------------------------------------------------------------------
# 2. test_status_queries_all_nodes
# ---------------------------------------------------------------------------


class TestStatusQueriesAllNodes:
    def test_status_queries_all_nodes(self):
        """cmd_swarm_status calls /health once per node in the config."""
        from castor.commands.swarm import cmd_swarm_status

        mock_resp = _make_httpx_response(200, _HEALTHY_RESPONSE)

        call_urls: List[str] = []

        def fake_get(url, **kwargs):
            call_urls.append(url)
            return mock_resp

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = fake_get

        with patch("castor.commands.swarm.load_swarm_config", return_value=_FAKE_NODES), \
             patch("castor.commands.swarm.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            results = cmd_swarm_status(output_json=True)

        # One call per node
        assert len(results) == len(_FAKE_NODES), (
            f"Expected {len(_FAKE_NODES)} results, got {len(results)}"
        )
        # Every node must appear in results
        result_names = {r["name"] for r in results}
        for node in _FAKE_NODES:
            assert node["name"] in result_names


# ---------------------------------------------------------------------------
# 3. test_status_offline_node_handled
# ---------------------------------------------------------------------------


class TestStatusOfflineNodeHandled:
    def test_status_offline_node_handled(self):
        """A ConnectError from httpx should mark the node offline, not crash."""
        import httpx as real_httpx

        from castor.commands.swarm import cmd_swarm_status, _query_node_health

        offline_node = _FAKE_NODES[0]

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = real_httpx.ConnectError("Connection refused")

        with patch("castor.commands.swarm.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            result = _query_node_health(offline_node, timeout=1.0)

        assert result["online"] is False, "Offline node must have online=False"
        assert result["brain"] is False
        assert result["driver"] is False

    def test_status_offline_node_does_not_crash_full_status(self):
        """Full cmd_swarm_status handles a mix of online and offline nodes."""
        import httpx as real_httpx

        from castor.commands.swarm import cmd_swarm_status

        call_count = 0

        def fake_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "192.168.1.10" in url:
                raise real_httpx.ConnectError("refused")
            return _make_httpx_response(200, _HEALTHY_RESPONSE)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = fake_get

        with patch("castor.commands.swarm.load_swarm_config", return_value=_FAKE_NODES), \
             patch("castor.commands.swarm.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            # Must not raise
            results = cmd_swarm_status(output_json=True)

        assert len(results) == 2
        offline = next(r for r in results if r["name"] == "alpha")
        online  = next(r for r in results if r["name"] == "beta")
        assert offline["online"] is False
        assert online["online"] is True


# ---------------------------------------------------------------------------
# 4. test_command_broadcasts_to_all
# ---------------------------------------------------------------------------


class TestCommandBroadcastsToAll:
    def test_command_broadcasts_to_all(self):
        """cmd_swarm_command POSTs /api/command to every node."""
        from castor.commands.swarm import cmd_swarm_command

        post_urls: List[str] = []

        def fake_post(url, **kwargs):
            post_urls.append(url)
            return _make_httpx_response(200, {"raw_text": "ok", "action": {}})

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = fake_post

        with patch("castor.commands.swarm.load_swarm_config", return_value=_FAKE_NODES), \
             patch("castor.commands.swarm.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            results = cmd_swarm_command("move forward", output_json=True)

        # One POST per node
        assert len(results) == len(_FAKE_NODES)
        posted_names = {r["_node"] for r in results}
        for node in _FAKE_NODES:
            assert node["name"] in posted_names
        # Each URL must include /api/command
        for url in post_urls:
            assert "/api/command" in url


# ---------------------------------------------------------------------------
# 5. test_command_node_filter
# ---------------------------------------------------------------------------


class TestCommandNodeFilter:
    def test_command_node_filter(self):
        """--node alpha only POSTs to alpha, not beta."""
        from castor.commands.swarm import cmd_swarm_command

        post_urls: List[str] = []

        def fake_post(url, **kwargs):
            post_urls.append(url)
            return _make_httpx_response(200, {"raw_text": "ok", "action": {}})

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = fake_post

        with patch("castor.commands.swarm.load_swarm_config", return_value=_FAKE_NODES), \
             patch("castor.commands.swarm.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            results = cmd_swarm_command("turn left", node="alpha", output_json=True)

        assert len(results) == 1, "Only one node should be targeted"
        assert results[0]["_node"] == "alpha"
        # beta's IP must not appear in any POST URL
        for url in post_urls:
            assert "192.168.1.11" not in url

    def test_command_unknown_node_returns_empty(self):
        """Specifying an unknown node name returns [] without crashing."""
        from castor.commands.swarm import cmd_swarm_command

        with patch("castor.commands.swarm.load_swarm_config", return_value=_FAKE_NODES):
            results = cmd_swarm_command("stop", node="nonexistent", output_json=True)

        assert results == []


# ---------------------------------------------------------------------------
# 6. test_stop_broadcasts
# ---------------------------------------------------------------------------


class TestStopBroadcasts:
    def test_stop_broadcasts(self):
        """cmd_swarm_stop POSTs /api/stop to every node in the swarm."""
        from castor.commands.swarm import cmd_swarm_stop

        post_urls: List[str] = []

        def fake_post(url, **kwargs):
            post_urls.append(url)
            return _make_httpx_response(200, {"stopped": True})

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = fake_post

        with patch("castor.commands.swarm.load_swarm_config", return_value=_FAKE_NODES), \
             patch("castor.commands.swarm.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            results = cmd_swarm_stop(output_json=True)

        assert len(results) == len(_FAKE_NODES)
        for url in post_urls:
            assert "/api/stop" in url

    def test_stop_no_nodes_returns_empty(self):
        """cmd_swarm_stop with no nodes in swarm.yaml returns []."""
        from castor.commands.swarm import cmd_swarm_stop

        with patch("castor.commands.swarm.load_swarm_config", return_value=[]):
            results = cmd_swarm_stop(output_json=True)

        assert results == []


# ---------------------------------------------------------------------------
# 7. test_json_output
# ---------------------------------------------------------------------------


class TestJsonOutput:
    def test_json_output_status(self, capsys):
        """--json flag for status emits valid JSON array."""
        from castor.commands.swarm import cmd_swarm_status

        mock_resp = _make_httpx_response(200, _HEALTHY_RESPONSE)
        mock_client = _make_httpx_client_mock(mock_resp)

        with patch("castor.commands.swarm.load_swarm_config", return_value=_FAKE_NODES), \
             patch("castor.commands.swarm.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            results = cmd_swarm_status(output_json=True)

        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, list)
        assert len(parsed) == len(_FAKE_NODES)

    def test_json_output_stop(self, capsys):
        """--json flag for stop emits valid JSON array."""
        from castor.commands.swarm import cmd_swarm_stop

        mock_resp = _make_httpx_response(200, {"stopped": True})
        mock_client = _make_httpx_client_mock(mock_resp)
        mock_client.post.return_value = mock_resp

        with patch("castor.commands.swarm.load_swarm_config", return_value=_FAKE_NODES), \
             patch("castor.commands.swarm.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            results = cmd_swarm_stop(output_json=True)

        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, list)

    def test_json_output_command(self, capsys):
        """--json flag for command emits valid JSON."""
        from castor.commands.swarm import cmd_swarm_command

        mock_resp = _make_httpx_response(200, {"raw_text": "ok", "action": {}})
        mock_client = _make_httpx_client_mock(mock_resp)
        mock_client.post.return_value = mock_resp

        with patch("castor.commands.swarm.load_swarm_config", return_value=_FAKE_NODES), \
             patch("castor.commands.swarm.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            results = cmd_swarm_command("test", output_json=True)

        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# 8. test_sync_posts_reload
# ---------------------------------------------------------------------------


class TestSyncPostsReload:
    def test_sync_posts_reload(self, tmp_path):
        """cmd_swarm_sync POSTs /api/config/reload to every node."""
        import yaml

        from castor.commands.swarm import cmd_swarm_sync

        # Create a minimal RCAN config file to sync
        rcan_data = {
            "rcan_version": "1.0",
            "metadata": {"name": "test"},
            "agent": {},
        }
        config_file = tmp_path / "robot.rcan.yaml"
        config_file.write_text(yaml.dump(rcan_data))

        post_urls: List[str] = []

        def fake_post(url, **kwargs):
            post_urls.append(url)
            return _make_httpx_response(200, {"reloaded": True})

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = fake_post

        with patch("castor.commands.swarm.load_swarm_config", return_value=_FAKE_NODES), \
             patch("castor.commands.swarm.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            results = cmd_swarm_sync(str(config_file), output_json=True)

        assert len(results) == len(_FAKE_NODES)
        for url in post_urls:
            assert "/api/config/reload" in url
        # All statuses should be "ok"
        for r in results:
            assert r["_status"] == "ok", f"Expected ok, got {r}"

    def test_sync_missing_config_returns_empty(self):
        """cmd_swarm_sync with non-existent config file returns []."""
        from castor.commands.swarm import cmd_swarm_sync

        with patch("castor.commands.swarm.load_swarm_config", return_value=_FAKE_NODES):
            results = cmd_swarm_sync("/nonexistent/robot.rcan.yaml", output_json=True)

        assert results == []


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


class TestNodeBaseUrl:
    def test_ip_preferred_over_host(self):
        """_node_base_url uses ip field when both ip and host are set."""
        from castor.commands.swarm import _node_base_url

        node = {"name": "test", "ip": "10.0.0.5", "host": "test.local", "port": 9000}
        url = _node_base_url(node)
        assert "10.0.0.5" in url
        assert "test.local" not in url

    def test_host_used_when_no_ip(self):
        """_node_base_url falls back to host when ip is absent."""
        from castor.commands.swarm import _node_base_url

        node = {"name": "test", "host": "myrobot.local", "port": 8080}
        url = _node_base_url(node)
        assert "myrobot.local" in url
        assert "8080" in url


class TestNodeHeaders:
    def test_token_included_in_headers(self):
        """_node_headers returns Bearer auth when token is present."""
        from castor.commands.swarm import _node_headers

        node = {"token": "secret-token-123"}
        hdrs = _node_headers(node)
        assert "Authorization" in hdrs
        assert "secret-token-123" in hdrs["Authorization"]

    def test_no_token_returns_empty_headers(self):
        """_node_headers returns empty dict when no token is set."""
        from castor.commands.swarm import _node_headers

        node = {"name": "anon"}
        hdrs = _node_headers(node)
        assert hdrs == {}

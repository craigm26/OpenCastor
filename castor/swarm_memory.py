"""
castor/swarm_memory.py — ALMA cross-robot swarm episode sync (issue #260).

Provides collective memory for swarm robots: push local episodes to all
swarm nodes (POST /api/swarm/sync), receive episodes from remote nodes
(POST /api/swarm/receive), and fetch episodes from a single remote node
(GET /api/swarm/episodes).

Node registry is read from ``config/swarm.yaml`` (or a path given in the
RCAN config ``swarm.registry_path`` key).

Usage::

    from castor.swarm_memory import SwarmMemorySync

    sync = SwarmMemorySync(
        local_memory=episode_memory,
        swarm_yaml_path="config/swarm.yaml",
    )
    result = await sync.push(last_n=50)

RCAN config::

    swarm:
      enabled: true
      registry_path: config/swarm.yaml
      sync_last_n: 50
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.SwarmMemory")


# ---------------------------------------------------------------------------
# Swarm YAML loader
# ---------------------------------------------------------------------------


def load_swarm_nodes(yaml_path: str) -> list[dict[str, Any]]:
    """Load node list from a swarm YAML registry file.

    Args:
        yaml_path: Path to ``swarm.yaml``.

    Returns:
        List of node dicts with ``name``, ``host``, ``port``, ``token``, etc.
        Returns empty list if the file cannot be read.
    """
    path = Path(yaml_path)
    if not path.exists():
        logger.warning("Swarm registry not found: %s", yaml_path)
        return []
    try:
        import yaml  # pyyaml

        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
        return data.get("nodes", [])
    except ImportError:
        # Fallback: minimal YAML parser for the simple structure
        logger.warning("PyYAML not available — cannot parse swarm.yaml")
        return []
    except Exception as exc:
        logger.error("Failed to load swarm registry %s: %s", yaml_path, exc)
        return []


def _node_base_url(node: dict[str, Any]) -> str:
    """Return the base HTTP URL for a swarm node.

    Args:
        node: Node dict from swarm registry.

    Returns:
        Base URL string, e.g. ``"http://192.168.68.85:8000"``.
    """
    host = node.get("ip") or node.get("host", "localhost")
    port = int(node.get("port", 8000))
    return f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# HTTP helper (stdlib only, no requests/httpx required)
# ---------------------------------------------------------------------------


def _http_post(
    url: str,
    payload: dict,
    token: Optional[str] = None,
    timeout: float = 5.0,
) -> dict:
    """POST JSON payload to *url* and return parsed response.

    Args:
        url:     Full URL.
        payload: JSON-serialisable payload dict.
        token:   Optional bearer token for Authorization header.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON response dict, or ``{"error": "..."}`` on failure.
    """
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.reason}"}
    except Exception as exc:
        return {"error": str(exc)}


def _http_get(
    url: str,
    token: Optional[str] = None,
    timeout: float = 5.0,
) -> dict:
    """GET *url* and return parsed JSON.

    Args:
        url:     Full URL.
        token:   Optional bearer token for Authorization header.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON response dict, or ``{"error": "..."}`` on failure.
    """
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.reason}"}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# SwarmMemorySync
# ---------------------------------------------------------------------------


class SwarmMemorySync:
    """Cross-robot episode memory synchronisation.

    Pushes episodes from the local :class:`~castor.memory.EpisodeMemory` to
    every node in the swarm registry, and can receive episodes from remote nodes.

    Args:
        local_memory:     Local :class:`~castor.memory.EpisodeMemory` instance.
        swarm_yaml_path:  Path to ``swarm.yaml`` node registry.
        http_timeout:     Per-request timeout in seconds.
    """

    def __init__(
        self,
        local_memory,
        swarm_yaml_path: str = "config/swarm.yaml",
        http_timeout: float = 5.0,
    ):
        self._mem = local_memory
        self._yaml_path = swarm_yaml_path
        self._timeout = http_timeout
        self._seen_ids: set[str] = set()

    # ── Public interface ─────────────────────────────────────────────────────

    def push(self, last_n: int = 50) -> dict:
        """Push the most recent *last_n* local episodes to all swarm nodes.

        Args:
            last_n: Number of episodes to push (most recent first).

        Returns:
            Dict mapping node names to ``{"ok": bool, "error": str | None}``.
        """
        episodes = self._mem.query_recent(limit=last_n)
        payload_list = [dict(ep) for ep in episodes]
        nodes = load_swarm_nodes(self._yaml_path)
        results: dict[str, Any] = {}

        for node in nodes:
            name = node.get("name", "unknown")
            base = _node_base_url(node)
            token = node.get("token")
            url = f"{base}/api/swarm/receive"
            resp = _http_post(url, {"episodes": payload_list}, token=token, timeout=self._timeout)
            results[name] = {
                "ok": "error" not in resp,
                "error": resp.get("error"),
                "received": resp.get("received", 0),
            }
            if results[name]["ok"]:
                logger.info("Pushed %d episodes to %s", len(payload_list), name)
            else:
                logger.warning("Push to %s failed: %s", name, results[name]["error"])

        return results

    def receive(self, episodes: list[dict]) -> dict:
        """Accept episodes from a remote node and store locally (deduping by ID).

        Args:
            episodes: List of episode dicts (same schema as local ``query_recent``).

        Returns:
            Dict with ``received`` count and ``skipped`` count.
        """
        stored = 0
        skipped = 0
        for ep in episodes:
            ep_id = ep.get("id", "")
            if ep_id in self._seen_ids:
                skipped += 1
                continue
            try:
                self._mem.log_episode(
                    instruction=ep.get("instruction", ""),
                    raw_thought=ep.get("raw_thought", ""),
                    action=json.loads(ep.get("action_json") or "{}"),
                    latency_ms=float(ep.get("latency_ms") or 0.0),
                    image_hash=ep.get("image_hash") or "",
                    outcome=ep.get("outcome", ""),
                    source=ep.get("source", "swarm"),
                )
                if ep_id:
                    self._seen_ids.add(ep_id)
                stored += 1
            except Exception as exc:
                logger.warning("Failed to store swarm episode %s: %s", ep_id, exc)
                skipped += 1

        return {"received": stored, "skipped": skipped}

    def fetch_from_node(self, node_name: str, limit: int = 50) -> list[dict]:
        """Fetch episodes from a named remote swarm node.

        Args:
            node_name: Node name as defined in ``swarm.yaml``.
            limit:     Max number of episodes to fetch.

        Returns:
            List of episode dicts from the remote node, or empty list on error.
        """
        nodes = load_swarm_nodes(self._yaml_path)
        node = next((n for n in nodes if n.get("name") == node_name), None)
        if node is None:
            logger.warning("Swarm node %r not found in registry", node_name)
            return []

        base = _node_base_url(node)
        token = node.get("token")
        url = f"{base}/api/swarm/episodes?limit={limit}"
        resp = _http_get(url, token=token, timeout=self._timeout)

        if "error" in resp:
            logger.warning("Fetch from %s failed: %s", node_name, resp["error"])
            return []

        return resp.get("episodes", [])

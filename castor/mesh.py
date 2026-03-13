"""
castor/mesh.py — Robot-to-robot mesh networking.

Issue #220: MeshNode class that discovers peers via RCAN config, relays
instructions between robots, and exposes a ``route_to_peer`` tool for the LLM.

RCAN config example::

    mesh:
      enabled: true
      peers:
        - name: bob
          host: bob.local
          port: 8000
          token: ${BOB_API_TOKEN}

API endpoints (registered in castor/api.py):
  GET  /api/mesh/peers    — live peer list with health status
  POST /api/mesh/relay    — forward {peer_name, instruction} to peer

Usage::

    from castor.mesh import MeshNode, get_mesh

    mesh = MeshNode(config)
    peers = await mesh.list_peers()
    result = await mesh.route_to_peer("bob", "go forward 1 metre")
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Optional
from urllib import request as _urllib_request
from urllib.parse import urljoin

logger = logging.getLogger("OpenCastor.Mesh")

# Global singleton
_mesh_instance: Optional[MeshNode] = None
_mesh_lock = threading.Lock()


class PeerConfig:
    """Configuration for a single mesh peer.

    Attributes:
        name:  Human-readable peer identifier.
        host:  Hostname or IP address.
        port:  API port number.
        token: Bearer token for the peer's API.
    """

    def __init__(self, name: str, host: str, port: int = 8000, token: str = ""):
        self.name = name
        self.host = host
        self.port = port
        self.token = self._resolve_token(token)

    @staticmethod
    def _resolve_token(token: str) -> str:
        """Expand ``${ENV_VAR}`` references in the token string.

        Args:
            token: Raw token string, possibly ``${VAR_NAME}``.

        Returns:
            Resolved token value.
        """
        if token.startswith("${") and token.endswith("}"):
            var_name = token[2:-1]
            return os.getenv(var_name, "")
        return token

    @property
    def base_url(self) -> str:
        """Base URL for this peer's API."""
        return f"http://{self.host}:{self.port}"

    def __repr__(self) -> str:
        return f"PeerConfig(name={self.name!r}, host={self.host}, port={self.port})"


class PeerStatus:
    """Health status snapshot for a single mesh peer.

    Attributes:
        name:       Peer name.
        base_url:   API base URL.
        ok:         True if peer responded successfully.
        latency_ms: Round-trip latency in milliseconds.
        data:       Raw status dict from ``GET /health``.
        error:      Error message if ping failed.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        ok: bool,
        latency_ms: float = 0.0,
        data: Optional[dict] = None,
        error: str = "",
    ):
        self.name = name
        self.base_url = base_url
        self.ok = ok
        self.latency_ms = latency_ms
        self.data = data or {}
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
            "name": self.name,
            "base_url": self.base_url,
            "ok": self.ok,
            "latency_ms": round(self.latency_ms, 1),
            "error": self.error,
            "robot_name": self.data.get("robot_name", ""),
            "status": self.data.get("status", ""),
        }


class RelayResult:
    """Result of a relayed instruction.

    Attributes:
        peer_name:   Target peer name.
        ok:          True if the relay succeeded.
        response:    Response dict from the peer.
        error:       Error message if relay failed.
        latency_ms:  Round-trip latency in milliseconds.
    """

    def __init__(
        self,
        peer_name: str,
        ok: bool,
        response: Optional[dict] = None,
        error: str = "",
        latency_ms: float = 0.0,
    ):
        self.peer_name = peer_name
        self.ok = ok
        self.response = response or {}
        self.error = error
        self.latency_ms = latency_ms

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
            "peer_name": self.peer_name,
            "ok": self.ok,
            "response": self.response,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 1),
        }


def _http_get(url: str, token: str = "", timeout: float = 5.0) -> dict:
    """Perform a JSON GET request.

    Args:
        url:     Full URL to request.
        token:   Bearer token for Authorization header.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON dict.

    Raises:
        URLError: If the request fails.
        ValueError: If response is not valid JSON.
    """
    req = _urllib_request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with _urllib_request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body)


def _http_post(url: str, payload: dict, token: str = "", timeout: float = 10.0) -> dict:
    """Perform a JSON POST request.

    Args:
        url:     Full URL to POST to.
        payload: Dict that will be JSON-encoded as the request body.
        token:   Bearer token for Authorization header.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON dict from the response.

    Raises:
        URLError: If the request fails.
        ValueError: If response is not valid JSON.
    """
    data = json.dumps(payload).encode()
    req = _urllib_request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with _urllib_request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body)


class MeshNode:
    """Robot-to-robot mesh networking node.

    Discovers peers from the RCAN config and provides methods to:
      - List all configured peers with health status
      - Route an instruction to a named peer
      - Register a ``route_to_peer`` tool with the :class:`~castor.tools.ToolRegistry`

    Args:
        config: Full RCAN config dict.
    """

    def __init__(self, config: dict) -> None:
        mesh_cfg = config.get("mesh", {})
        self.enabled: bool = mesh_cfg.get("enabled", False)
        self._peers: dict[str, PeerConfig] = {}

        for peer_raw in mesh_cfg.get("peers", []):
            name = peer_raw.get("name", "")
            if not name:
                continue
            self._peers[name] = PeerConfig(
                name=name,
                host=peer_raw.get("host", "localhost"),
                port=int(peer_raw.get("port", 8000)),
                token=str(peer_raw.get("token", "")),
            )

        logger.info(
            "MeshNode initialised: enabled=%s, peers=%s",
            self.enabled,
            list(self._peers.keys()),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_peers(self) -> list[PeerStatus]:
        """Ping every configured peer and return health status list.

        Returns:
            List of :class:`PeerStatus` objects (one per configured peer).
        """
        statuses: list[PeerStatus] = []
        for _name, peer in self._peers.items():
            statuses.append(self._ping_peer(peer))
        return statuses

    def route_to_peer(self, peer_name: str, instruction: str) -> RelayResult:
        """Forward *instruction* to the named peer via its ``/api/command`` endpoint.

        This is the primary method registered as an LLM-callable tool.

        Args:
            peer_name:   Name of the target peer (must match RCAN config).
            instruction: Natural-language instruction for the peer's brain.

        Returns:
            :class:`RelayResult` with success flag and peer response.
        """
        if not self.enabled:
            return RelayResult(
                peer_name=peer_name,
                ok=False,
                error="Mesh networking is disabled in config (mesh.enabled: false)",
            )

        peer = self._peers.get(peer_name)
        if peer is None:
            known = list(self._peers.keys())
            return RelayResult(
                peer_name=peer_name,
                ok=False,
                error=f"Unknown peer {peer_name!r}. Known peers: {known}",
            )

        url = urljoin(peer.base_url + "/", "api/command")
        t0 = time.monotonic()
        try:
            resp = _http_post(url, {"instruction": instruction}, token=peer.token)
            latency_ms = (time.monotonic() - t0) * 1000
            return RelayResult(peer_name=peer_name, ok=True, response=resp, latency_ms=latency_ms)
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            logger.warning("Relay to %s failed: %s", peer_name, exc)
            return RelayResult(
                peer_name=peer_name,
                ok=False,
                error=str(exc),
                latency_ms=latency_ms,
            )

    def relay(self, peer_name: str, instruction: str) -> dict:
        """Alias for :meth:`route_to_peer` returning a plain dict.

        Suitable for use as an HTTP handler response body.

        Args:
            peer_name:   Target peer name.
            instruction: Instruction string.

        Returns:
            JSON-serialisable dict.
        """
        return self.route_to_peer(peer_name, instruction).to_dict()

    def register_tools(self, tool_registry: Any) -> None:
        """Register ``route_to_peer`` as an LLM-callable tool.

        Args:
            tool_registry: A :class:`~castor.tools.ToolRegistry` instance.
        """
        if not self.enabled:
            return

        tool_registry.register(
            name="route_to_peer",
            description=(
                "Delegate an instruction to a named peer robot in the mesh network. "
                "Use this when you need another robot to perform a task."
            ),
            fn=lambda peer_name, instruction: self.route_to_peer(peer_name, instruction).to_dict(),
            parameters={
                "peer_name": {
                    "type": "string",
                    "description": "Name of the target peer robot (from mesh config).",
                    "required": True,
                },
                "instruction": {
                    "type": "string",
                    "description": "Natural-language instruction for the peer robot.",
                    "required": True,
                },
            },
        )
        logger.info("route_to_peer tool registered in ToolRegistry")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def peer_names(self) -> list[str]:
        """Sorted list of configured peer names."""
        return sorted(self._peers.keys())

    @property
    def peer_count(self) -> int:
        """Number of configured peers."""
        return len(self._peers)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ping_peer(self, peer: PeerConfig) -> PeerStatus:
        """Ping a single peer and return its health status.

        Args:
            peer: :class:`PeerConfig` to ping.

        Returns:
            :class:`PeerStatus` reflecting the result.
        """
        url = f"{peer.base_url}/health"
        t0 = time.monotonic()
        try:
            data = _http_get(url, token=peer.token, timeout=3.0)
            latency_ms = (time.monotonic() - t0) * 1000
            return PeerStatus(
                name=peer.name,
                base_url=peer.base_url,
                ok=True,
                latency_ms=latency_ms,
                data=data,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            return PeerStatus(
                name=peer.name,
                base_url=peer.base_url,
                ok=False,
                latency_ms=latency_ms,
                error=str(exc),
            )


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------


def get_mesh(config: Optional[dict] = None) -> MeshNode:
    """Return the global :class:`MeshNode` singleton.

    Creates a new instance if one does not yet exist or if *config* is given.

    Args:
        config: RCAN config dict.  Required on first call.

    Returns:
        Global :class:`MeshNode` instance.
    """
    global _mesh_instance
    with _mesh_lock:
        if _mesh_instance is None or config is not None:
            _mesh_instance = MeshNode(config or {})
    return _mesh_instance


def reset_mesh() -> None:
    """Reset the global mesh singleton (useful in tests)."""
    global _mesh_instance
    with _mesh_lock:
        _mesh_instance = None

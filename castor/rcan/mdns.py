"""
RCAN mDNS Discovery.

Opt-in service broadcasting and peer discovery over mDNS.
Enabled when ``rcan_protocol.enable_mdns: true`` in the RCAN config.

Advertises as ``_rcan._tcp.local`` with TXT records containing:

- ``ruri``    -- Robot's RCAN URI
- ``model``   -- Robot model name
- ``caps``    -- Comma-separated capability list
- ``roles``   -- Available RBAC roles
- ``version`` -- RCAN protocol version
- ``name``    -- Human-readable robot name
- ``status``  -- Current status (active, idle, estop)

Requires ``zeroconf>=0.131.0`` (pure Python, ~800KB).
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("OpenCastor.RCAN.mDNS")

try:
    from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf

    HAS_ZEROCONF = True
except ImportError:
    HAS_ZEROCONF = False

SERVICE_TYPE = "_rcan._tcp.local."


class RCANServiceBroadcaster:
    """Advertise this robot as an RCAN service on the local network.

    Args:
        ruri:           Robot's RCAN URI string.
        robot_name:     Human-readable robot name.
        port:           Service port (default: 8000).
        capabilities:   List of capability names.
        model:          Robot model name.
        status_fn:      Optional callable returning current status string.
    """

    def __init__(
        self,
        ruri: str,
        robot_name: str = "OpenCastor Robot",
        port: int = 8000,
        capabilities: Optional[List[str]] = None,
        model: str = "unknown",
        status_fn: Optional[Callable[[], str]] = None,
    ):
        self.ruri = ruri
        self.robot_name = robot_name
        self.port = port
        self.capabilities = capabilities or []
        self.model = model
        self._status_fn = status_fn or (lambda: "active")
        self._zeroconf: Optional[object] = None
        self._info: Optional[object] = None

    @property
    def enabled(self) -> bool:
        return HAS_ZEROCONF

    def start(self):
        """Register the mDNS service."""
        if not HAS_ZEROCONF:
            logger.warning("zeroconf not installed -- mDNS disabled")
            return

        try:
            # Use a sanitized service name
            service_name = self.robot_name.replace(".", "_").replace(" ", "_")
            full_name = f"{service_name}.{SERVICE_TYPE}"

            # Build TXT records
            txt_props = {
                "ruri": self.ruri,
                "model": self.model,
                "caps": ",".join(self.capabilities),
                "roles": "GUEST,USER,LEASEE,OWNER,CREATOR",
                "version": "1.0.0",
                "name": self.robot_name,
                "status": self._status_fn(),
            }

            # Get local IP
            local_ip = _get_local_ip()

            self._info = ServiceInfo(
                SERVICE_TYPE,
                full_name,
                addresses=[socket.inet_aton(local_ip)],
                port=self.port,
                properties=txt_props,
                server=f"{service_name}.local.",
            )

            self._zeroconf = Zeroconf()
            self._zeroconf.register_service(self._info)
            logger.info(
                "mDNS broadcasting: %s on %s:%d",
                self.ruri,
                local_ip,
                self.port,
            )
        except Exception as e:
            logger.warning("mDNS broadcast failed: %s", e)

    def stop(self):
        """Unregister the mDNS service."""
        if self._zeroconf and self._info:
            try:
                self._zeroconf.unregister_service(self._info)
                self._zeroconf.close()
            except Exception as e:
                logger.debug("mDNS shutdown error: %s", e)
            finally:
                self._zeroconf = None
                self._info = None
            logger.info("mDNS broadcast stopped")


class RCANServiceBrowser:
    """Discover RCAN peers on the local network.

    Args:
        on_found:    Callback when a peer is discovered.
        on_removed:  Callback when a peer is removed.
    """

    def __init__(
        self,
        on_found: Optional[Callable[[Dict], None]] = None,
        on_removed: Optional[Callable[[str], None]] = None,
    ):
        self._on_found = on_found
        self._on_removed = on_removed
        self._peers: Dict[str, Dict] = {}
        self._lock = threading.Lock()
        self._zeroconf: Optional[object] = None
        self._browser: Optional[object] = None

    @property
    def enabled(self) -> bool:
        return HAS_ZEROCONF

    @property
    def peers(self) -> Dict[str, Dict]:
        """Return a snapshot of discovered peers."""
        with self._lock:
            return dict(self._peers)

    def start(self):
        """Start browsing for RCAN services."""
        if not HAS_ZEROCONF:
            logger.warning("zeroconf not installed -- mDNS browser disabled")
            return

        try:
            self._zeroconf = Zeroconf()
            self._browser = ServiceBrowser(
                self._zeroconf,
                SERVICE_TYPE,
                handlers=[self._on_state_change],
            )
            logger.info("mDNS browser started (looking for %s)", SERVICE_TYPE)
        except Exception as e:
            logger.warning("mDNS browser failed: %s", e)

    def stop(self):
        """Stop browsing."""
        if self._zeroconf:
            try:
                self._zeroconf.close()
            except Exception:
                pass
            finally:
                self._zeroconf = None
                self._browser = None
            logger.info("mDNS browser stopped")

    def _on_state_change(self, zeroconf, service_type, name, state_change):
        """Handle mDNS service state changes."""
        if state_change == ServiceStateChange.Added:
            info = zeroconf.get_service_info(service_type, name)
            if info:
                peer = _parse_service_info(info)
                with self._lock:
                    self._peers[name] = peer
                if self._on_found:
                    self._on_found(peer)
                logger.info("Peer discovered: %s", peer.get("ruri", name))

        elif state_change == ServiceStateChange.Removed:
            with self._lock:
                self._peers.pop(name, None)
            if self._on_removed:
                self._on_removed(name)
            logger.info("Peer removed: %s", name)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _get_local_ip() -> str:
    """Get the local IP address (best effort)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _parse_service_info(info) -> Dict:
    """Extract a peer dict from a zeroconf ServiceInfo."""
    props = {}
    if info.properties:
        for k, v in info.properties.items():
            key = k.decode("utf-8") if isinstance(k, bytes) else k
            val = v.decode("utf-8") if isinstance(v, bytes) else str(v)
            props[key] = val

    addresses = []
    if hasattr(info, "parsed_addresses"):
        addresses = info.parsed_addresses()
    elif hasattr(info, "addresses"):
        addresses = [socket.inet_ntoa(a) for a in info.addresses if len(a) == 4]

    return {
        "name": info.name,
        "ruri": props.get("ruri", ""),
        "model": props.get("model", ""),
        "capabilities": props.get("caps", "").split(",") if props.get("caps") else [],
        "roles": props.get("roles", "").split(",") if props.get("roles") else [],
        "version": props.get("version", ""),
        "robot_name": props.get("name", ""),
        "status": props.get("status", "unknown"),
        "addresses": addresses,
        "port": info.port,
        "discovered_at": time.time(),
    }

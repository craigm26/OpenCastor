"""
RCAN mDNS Discovery.

Opt-in service broadcasting and peer discovery over mDNS.
Enabled when ``rcan_protocol.enable_mdns: true`` in the RCAN config.

ONE SERVICE TYPE, TWO PUBLISHERS. This module used to advertise on
``_rcan._tcp.local.`` while the OpenCastor app browsed for
``_opencastor._tcp.local.``, so a robot with mDNS switched on was publishing
into a channel nobody listened to, and the flag was documented as "leave it
off" because the record it produced could not be acted on anyway. Both now
publish :data:`castor.discovery.SERVICE_TYPE` and both carry the same
app-facing keys, so turning the flag on makes a robot findable instead of
merely audible. The legacy type is still BROWSED, so peers running older builds
are not lost.

Advertises with TXT records containing the RCAN peer fields:

- ``ruri``    -- Robot's RCAN URI
- ``model``   -- Robot model name
- ``caps``    -- Comma-separated capability list
- ``roles``   -- Available RBAC roles
- ``version`` -- RCAN protocol version
- ``name``    -- Human-readable robot name
- ``status``  -- Current status (active, idle, estop)

plus the client-facing record from :mod:`castor.discovery` (``rrn``,
``gateway_port``, ``castor_port``, ``console_port``, ``manifest_path``, ``v``)
whenever this process can work out its own identity — which it can whenever
``ROBOT_HOME`` or ``ROBOT_RRN`` is set, i.e. under any unit `castor up` writes.
Without an ``rrn`` a client cannot match a found robot to credentials it holds,
so a record missing it is deliberately reported as unusable by
``castor discovery check`` rather than quietly published as if it were fine.

Requires ``zeroconf>=0.131.0`` (pure Python, ~800KB).
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable
from typing import Optional

#: Shared with :mod:`castor.discovery` on purpose — see the module docstring.
#: Importing rather than restating it makes a future divergence a syntax-level
#: impossibility instead of a two-file, silent, LAN-only bug.
from castor.discovery import (
    LEGACY_SERVICE_TYPE,
    SERVICE_TYPE,
    RobotRecord,
    record_from_env,
)

logger = logging.getLogger("OpenCastor.RCAN.mDNS")

try:
    from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf

    HAS_ZEROCONF = True
except ImportError:
    HAS_ZEROCONF = False

#: Both types are browsed: the current one, and the one older robots publish.
BROWSE_TYPES = [SERVICE_TYPE, LEGACY_SERVICE_TYPE]


def _record_from_environment() -> RobotRecord | None:
    """This robot's client-facing record, when the environment names it.

    Never raises. A runtime that cannot say who it is still advertises a
    perfectly good RCAN peer record; it just cannot be re-addressed by a phone,
    which is exactly what the missing ``rrn`` will tell anyone who looks.
    """
    try:
        return record_from_env()
    except ValueError:
        return None


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
        capabilities: Optional[list[str]] = None,
        model: str = "unknown",
        status_fn: Optional[Callable[[], str]] = None,
        record: Optional["RobotRecord"] = None,
    ):
        #: The client-facing half of the record. Passed in by a caller that
        #: knows it, else read from the environment the units already set.
        self.record = record if record is not None else _record_from_environment()
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
            # Use a sanitized service name. Deliberately NOT the RRN-keyed
            # instance name the discovery unit uses: a robot running both
            # publishes two records for one machine rather than fighting over
            # one name, and every client keys on the `rrn` in TXT, which is the
            # same in both.
            service_name = self.robot_name.replace(".", "_").replace(" ", "_")
            full_name = f"{service_name}.{SERVICE_TYPE}"

            # Build TXT records
            txt_props = {
                "ruri": self.ruri,
                "model": self.model,
                "caps": ",".join(self.capabilities),
                "roles": "GUEST,USER,LEASEE,OWNER,CREATOR",
                "version": "1.2.0",
                "name": self.robot_name,
                "status": self._status_fn(),
            }
            if self.record is not None:
                txt_props.update(self.record.txt())
                # `name` stays the robot's own name and `castor_port` stays the
                # port THIS process is listening on: the record's copy is a
                # configured value, and a record that disagrees with the socket
                # it was published from is how "the robot answered but nothing
                # could reach it" happens.
                txt_props["name"] = self.robot_name
                txt_props["castor_port"] = str(self.port)

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
        on_found: Optional[Callable[[dict], None]] = None,
        on_removed: Optional[Callable[[str], None]] = None,
    ):
        self._on_found = on_found
        self._on_removed = on_removed
        self._peers: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._zeroconf: Optional[object] = None
        self._browser: Optional[object] = None

    @property
    def enabled(self) -> bool:
        return HAS_ZEROCONF

    @property
    def peers(self) -> dict[str, dict]:
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
                list(BROWSE_TYPES),
                handlers=[self._on_state_change],
            )
            logger.info("mDNS browser started (looking for %s)", ", ".join(BROWSE_TYPES))
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


def _parse_service_info(info) -> dict:
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
        "rrn": props.get("rrn", ""),
        "gateway_port": props.get("gateway_port", ""),
        "console_port": props.get("console_port", ""),
        "manifest_path": props.get("manifest_path", ""),
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

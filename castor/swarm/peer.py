"""SwarmPeer — represents a single robot in the swarm."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class SwarmPeer:
    """A peer robot discovered via mDNS or registered manually."""

    robot_id: str  # from metadata.robot_uuid
    robot_name: str
    host: str  # IP or hostname
    port: int  # RCAN API port
    capabilities: list[str]  # from rcan_protocol.capabilities
    last_seen: float  # epoch seconds
    load_score: float  # 0.0 (idle) to 1.0 (fully loaded)

    @property
    def is_available(self) -> bool:
        """True if seen within 30s and load_score < 0.8."""
        age = time.time() - self.last_seen
        return age < 30.0 and self.load_score < 0.8

    @property
    def is_stale(self) -> bool:
        """True if last seen more than 60s ago."""
        return (time.time() - self.last_seen) > 60.0

    def can_do(self, capability: str) -> bool:
        """Return True if this peer has the given capability."""
        return capability in self.capabilities

    def to_dict(self) -> dict:
        return {
            "robot_id": self.robot_id,
            "robot_name": self.robot_name,
            "host": self.host,
            "port": self.port,
            "capabilities": list(self.capabilities),
            "last_seen": self.last_seen,
            "load_score": self.load_score,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SwarmPeer:
        return cls(
            robot_id=d["robot_id"],
            robot_name=d["robot_name"],
            host=d["host"],
            port=int(d["port"]),
            capabilities=list(d.get("capabilities", [])),
            last_seen=float(d["last_seen"]),
            load_score=float(d["load_score"]),
        )

    @classmethod
    def from_mdns(cls, service_info: dict) -> SwarmPeer:
        """Build a SwarmPeer from an mDNS service_info dict.

        service_info keys: name, host, port, properties (dict).
        """
        props = service_info.get("properties", {})
        robot_id = props.get("robot_uuid", props.get("robot_id", service_info.get("name", "")))
        robot_name = props.get("robot_name", service_info.get("name", robot_id))
        caps_raw = props.get("capabilities", "")
        capabilities = [c.strip() for c in caps_raw.split(",") if c.strip()] if caps_raw else []
        return cls(
            robot_id=robot_id,
            robot_name=robot_name,
            host=service_info["host"],
            port=int(service_info["port"]),
            capabilities=capabilities,
            last_seen=time.time(),
            load_score=float(props.get("load_score", 0.0)),
        )

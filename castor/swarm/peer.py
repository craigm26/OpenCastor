"""SwarmPeer — represents a single robot in the swarm."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


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
    metrics: dict[str, float | str | bool] = field(default_factory=dict)
    status: str = "ready"  # ready, busy, degraded, disconnected

    @property
    def is_available(self) -> bool:
        """True if seen within 30s, healthy, and load_score < 0.8."""
        age = time.time() - self.last_seen
        return age < 30.0 and self.load_score < 0.8 and self.status == "ready"

    @property
    def is_stale(self) -> bool:
        """True if last seen more than 60s ago."""
        return (time.time() - self.last_seen) > 60.0

    @property
    def is_degraded(self) -> bool:
        """True when peer reports degraded health status."""
        return self.status == "degraded"

    @property
    def is_disconnected(self) -> bool:
        """True when peer reports disconnected status or is stale."""
        return self.status == "disconnected" or self.is_stale

    def can_do(self, capability: str) -> bool:
        """Return True if this peer has the given capability."""
        return capability in self.capabilities

    def supports_all(self, capabilities: list[str]) -> bool:
        """Return True when this peer supports every capability in capabilities."""
        return all(self.can_do(cap) for cap in capabilities)

    def matches_constraints(self, constraints: dict[str, float | str | bool]) -> bool:
        """Return True if all metric constraints are satisfied.

        Constraint values can be exact (``{"mode": "indoor"}``) or comparison
        tuples: ``{"battery": (">", 40)}``.
        """
        for key, expected in constraints.items():
            actual = self.metrics.get(key)
            if isinstance(expected, tuple) and len(expected) == 2:
                op, threshold = expected
                if not _compare(actual, op, threshold):
                    return False
            elif actual != expected:
                return False
        return True

    def update_runtime(
        self,
        *,
        load_score: float | None = None,
        status: str | None = None,
        metrics: dict | None = None,
    ) -> None:
        """Update runtime health and telemetry for scheduling decisions."""
        if load_score is not None:
            self.load_score = float(load_score)
        if status is not None:
            self.status = status
        if metrics is not None:
            self.metrics = dict(metrics)
        self.last_seen = time.time()

    def to_dict(self) -> dict:
        return {
            "robot_id": self.robot_id,
            "robot_name": self.robot_name,
            "host": self.host,
            "port": self.port,
            "capabilities": list(self.capabilities),
            "last_seen": self.last_seen,
            "load_score": self.load_score,
            "metrics": dict(self.metrics),
            "status": self.status,
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
            metrics=dict(d.get("metrics", {})),
            status=d.get("status", "ready"),
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
        metrics_raw = props.get("metrics", "")
        metrics = {}
        if metrics_raw:
            for item in str(metrics_raw).split(","):
                if "=" not in item:
                    continue
                k, v = item.split("=", 1)
                metrics[k.strip()] = _coerce(v.strip())
        return cls(
            robot_id=robot_id,
            robot_name=robot_name,
            host=service_info["host"],
            port=int(service_info["port"]),
            capabilities=capabilities,
            last_seen=time.time(),
            load_score=float(props.get("load_score", 0.0)),
            metrics=metrics,
            status=props.get("status", "ready"),
        )


def _coerce(v: str) -> float | str | bool:
    if v.lower() in {"true", "false"}:
        return v.lower() == "true"
    try:
        return float(v)
    except ValueError:
        return v


def _compare(actual: float | str | bool | None, op: str, threshold: float | str | bool) -> bool:
    if actual is None:
        return False
    if op == ">":
        return actual > threshold
    if op == ">=":
        return actual >= threshold
    if op == "<":
        return actual < threshold
    if op == "<=":
        return actual <= threshold
    if op == "==":
        return actual == threshold
    if op == "!=":
        return actual != threshold
    return False

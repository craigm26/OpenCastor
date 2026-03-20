"""Fleet-level contribution coordination.

Manages work unit assignment across multiple robots based on
hardware capabilities, battery, thermal headroom, and idle duration.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("OpenCastor.Contribute.Fleet")


@dataclass
class RobotCapacity:
    """Contribution capacity for a single robot."""

    rrn: str
    npu: str | None = None
    npu_tops: int = 0
    cpu_cores: int = 1
    battery_pct: float | None = None
    temperature_c: float | None = None
    idle_since: float | None = None
    active: bool = False
    last_seen: float = field(default_factory=time.time)

    @property
    def available(self) -> bool:
        """Whether this robot can accept work."""
        if self.temperature_c is not None and self.temperature_c >= 80.0:
            return False
        if self.battery_pct is not None and self.battery_pct < 10.0:
            return False
        return True

    @property
    def idle_minutes(self) -> float:
        if self.idle_since is None:
            return 0
        return (time.time() - self.idle_since) / 60

    @property
    def tops(self) -> int:
        return self.npu_tops if self.npu else 0


class FleetCoordinator:
    """Coordinate contribution across a fleet of robots.

    Tracks robot capacities and assigns work units optimally.
    """

    def __init__(self) -> None:
        self._robots: dict[str, RobotCapacity] = {}
        self._assigned: dict[str, str] = {}  # work_unit_id → rrn

    def update_capacity(self, capacity: RobotCapacity) -> None:
        """Update a robot's contribution capacity."""
        capacity.last_seen = time.time()
        self._robots[capacity.rrn] = capacity

    def remove_robot(self, rrn: str) -> None:
        self._robots.pop(rrn, None)

    def get_available_robots(self) -> list[RobotCapacity]:
        """Return robots sorted by suitability for contribution."""
        now = time.time()
        available = [
            r
            for r in self._robots.values()
            if r.available and (now - r.last_seen) < 120  # seen in last 2 min
        ]
        # Sort: NPU-equipped first, then by idle duration
        available.sort(key=lambda r: (-r.tops, -r.idle_minutes))
        return available

    def assign_work_unit(self, work_unit_id: str, prefer_npu: bool = False) -> str | None:
        """Assign a work unit to the best available robot. Returns RRN or None."""
        available = self.get_available_robots()
        if not available:
            return None

        if prefer_npu:
            npu_robots = [r for r in available if r.npu]
            if npu_robots:
                target = npu_robots[0]
                self._assigned[work_unit_id] = target.rrn
                return target.rrn

        # Assign to robot with longest idle time
        target = available[0]
        self._assigned[work_unit_id] = target.rrn
        return target.rrn

    def complete_work_unit(self, work_unit_id: str) -> None:
        self._assigned.pop(work_unit_id, None)

    def fleet_status(self) -> dict:
        """Return fleet-wide contribution status."""
        available = self.get_available_robots()
        active = [r for r in self._robots.values() if r.active]
        total_tops = sum(r.tops for r in available)

        return {
            "total_robots": len(self._robots),
            "available_robots": len(available),
            "active_contributors": len(active),
            "total_tops_available": total_tops,
            "assigned_work_units": len(self._assigned),
            "robots": [
                {
                    "rrn": r.rrn,
                    "npu": r.npu,
                    "available": r.available,
                    "active": r.active,
                    "idle_minutes": round(r.idle_minutes, 1),
                }
                for r in self._robots.values()
            ],
        }

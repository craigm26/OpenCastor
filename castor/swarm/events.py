"""SwarmEvent — lightweight event type for swarm state changes."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class SwarmEvent:
    """An event emitted by the swarm (peer join/leave, task changes, etc.)."""

    event_type: str  # e.g. "peer_joined", "task_assigned", "patch_published"
    robot_id: str
    payload: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "robot_id": self.robot_id,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SwarmEvent:
        return cls(
            event_type=d["event_type"],
            robot_id=d["robot_id"],
            payload=dict(d.get("payload", {})),
            timestamp=float(d.get("timestamp", 0.0)),
        )

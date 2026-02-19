"""Episode dataclass for recording robot task execution."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Episode:
    """A single recorded episode of robot task execution."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal: str = ""
    actions: list[dict[str, Any]] = field(default_factory=list)
    sensor_readings: list[dict[str, Any]] = field(default_factory=list)
    success: bool = False
    duration_s: float = 0.0
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "id": self.id,
            "goal": self.goal,
            "actions": self.actions,
            "sensor_readings": self.sensor_readings,
            "success": self.success,
            "duration_s": self.duration_s,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Episode:
        """Deserialize from a dictionary."""
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            goal=data.get("goal", ""),
            actions=data.get("actions", []),
            sensor_readings=data.get("sensor_readings", []),
            success=data.get("success", False),
            duration_s=data.get("duration_s", 0.0),
            start_time=data.get("start_time", 0.0),
            end_time=data.get("end_time", 0.0),
            metadata=data.get("metadata", {}),
        )

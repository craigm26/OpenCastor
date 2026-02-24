"""World model with spatial + semantic layers for multi-agent coordination."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class EntityRecord:
    """A map or semantic entity with confidence and freshness metadata."""

    entity_id: str
    kind: str
    position: Optional[tuple[float, float]] = None
    room_id: Optional[str] = None
    zone_ids: List[str] = field(default_factory=list)
    confidence: float = 0.5
    source_agent: str = "unknown"
    observed_at: float = field(default_factory=time.time)
    attrs: Dict[str, object] = field(default_factory=dict)

    @property
    def age_s(self) -> float:
        return max(0.0, time.time() - self.observed_at)


@dataclass
class WaypointRecord(EntityRecord):
    neighbors: List[str] = field(default_factory=list)


@dataclass
class WorldModel:
    """Global world model persisted in SharedState.

    Spatial layers:
      - rooms, waypoints, obstacles
    Semantic layers:
      - objects, people, zones
    """

    rooms: Dict[str, EntityRecord] = field(default_factory=dict)
    waypoints: Dict[str, WaypointRecord] = field(default_factory=dict)
    obstacles: Dict[str, EntityRecord] = field(default_factory=dict)
    objects: Dict[str, EntityRecord] = field(default_factory=dict)
    people: Dict[str, EntityRecord] = field(default_factory=dict)
    zones: Dict[str, EntityRecord] = field(default_factory=dict)

    def merge(self, category: str, record: EntityRecord) -> EntityRecord:
        """Upsert with conflict resolution for multi-agent updates."""
        bucket = self._bucket(category)
        existing = bucket.get(record.entity_id)
        if existing is None or self._score(record) >= self._score(existing):
            bucket[record.entity_id] = record
            return record
        return existing

    def last_seen(self, name: str) -> Optional[EntityRecord]:
        """Return newest object/person/entity matching *name*."""
        name_norm = name.strip().lower()
        candidates: List[EntityRecord] = []
        for bucket in (self.objects, self.people, self.obstacles, self.zones):
            for entity in bucket.values():
                label = str(entity.attrs.get("label", entity.entity_id)).lower()
                if name_norm in label or name_norm in entity.entity_id.lower():
                    candidates.append(entity)
        if not candidates:
            return None
        return max(candidates, key=lambda e: e.observed_at)

    def safe_route(
        self,
        start_waypoint: str,
        end_waypoint: str,
        avoid_zones: Optional[List[str]] = None,
    ) -> List[str]:
        """Compute a BFS waypoint route while avoiding named zones."""
        avoid = {z.lower() for z in (avoid_zones or [])}
        if start_waypoint not in self.waypoints or end_waypoint not in self.waypoints:
            return []

        blocked = {
            waypoint_id
            for waypoint_id, waypoint in self.waypoints.items()
            if any(z.lower() in avoid for z in waypoint.zone_ids)
        }
        if start_waypoint in blocked or end_waypoint in blocked:
            return []

        queue: List[List[str]] = [[start_waypoint]]
        visited = {start_waypoint}
        while queue:
            path = queue.pop(0)
            node = path[-1]
            if node == end_waypoint:
                return path
            for neighbor in self.waypoints[node].neighbors:
                if neighbor in visited or neighbor in blocked or neighbor not in self.waypoints:
                    continue
                visited.add(neighbor)
                queue.append(path + [neighbor])
        return []

    def _bucket(self, category: str):
        buckets = {
            "rooms": self.rooms,
            "waypoints": self.waypoints,
            "obstacles": self.obstacles,
            "objects": self.objects,
            "people": self.people,
            "zones": self.zones,
        }
        if category not in buckets:
            raise ValueError(f"unknown world-model category: {category}")
        return buckets[category]

    def _score(self, record: EntityRecord) -> float:
        """Higher score wins conflicts: confidence + freshness bias."""
        freshness = 1.0 / (1.0 + max(0.0, time.time() - record.observed_at))
        return float(record.confidence) + freshness

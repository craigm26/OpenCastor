"""
OpenCastor Geofence -- limit robot operating radius.

Uses odometry (dead reckoning from motor commands) to estimate
distance from the starting position. If the robot exceeds the
configured radius, the driver refuses to move further away.

RCAN config format::

    geofence:
      enabled: true
      max_radius_m: 5.0          # Maximum distance from start (meters)
      action: stop               # What to do: "stop" or "warn"

Usage:
    Integrated into main.py automatically when ``geofence.enabled: true``.
"""

import logging
import math
import threading

logger = logging.getLogger("OpenCastor.Geofence")


class Geofence:
    """Tracks estimated position via odometry and enforces a radius limit."""

    def __init__(self, config: dict):
        geo_cfg = config.get("geofence", {})
        self.enabled = geo_cfg.get("enabled", False)
        self.max_radius = geo_cfg.get("max_radius_m", 5.0)
        self.action = geo_cfg.get("action", "stop")  # "stop" or "warn"

        # Position state (simple dead reckoning)
        self._x = 0.0
        self._y = 0.0
        self._heading = 0.0  # radians
        self._lock = threading.Lock()

        if self.enabled:
            logger.info(f"Geofence active: {self.max_radius}m radius, action={self.action}")

    @property
    def distance_from_start(self) -> float:
        """Current estimated distance from starting position (meters)."""
        with self._lock:
            return math.sqrt(self._x**2 + self._y**2)

    @property
    def position(self) -> tuple:
        """Current estimated (x, y) position in meters."""
        with self._lock:
            return (self._x, self._y)

    def check_action(self, action: dict) -> dict:
        """Check if an action would violate the geofence.

        If the action is safe, returns it unchanged.
        If it would violate the fence:
          - ``action="stop"``: returns a stop action instead
          - ``action="warn"``: returns the action but logs a warning

        Also updates the position estimate based on the action.
        """
        if not self.enabled:
            self._update_position(action)
            return action

        if not action or action.get("type") != "move":
            return action

        linear = action.get("linear", 0)
        angular = action.get("angular", 0)

        # Estimate where this move would take us
        dt = 0.5  # approximate time per action cycle
        with self._lock:
            new_heading = self._heading + angular * dt
            new_x = self._x + linear * math.cos(new_heading) * dt
            new_y = self._y + linear * math.sin(new_heading) * dt
            new_dist = math.sqrt(new_x**2 + new_y**2)

        if new_dist > self.max_radius:
            if self.action == "stop":
                logger.warning(
                    f"Geofence violation: {new_dist:.1f}m > {self.max_radius}m -- stopping"
                )
                return {"type": "stop"}
            else:
                logger.warning(f"Geofence warning: {new_dist:.1f}m > {self.max_radius}m")

        # Update position
        self._update_position(action)
        return action

    def _update_position(self, action: dict):
        """Update dead-reckoning position estimate."""
        if not action or action.get("type") != "move":
            return

        linear = action.get("linear", 0)
        angular = action.get("angular", 0)
        dt = 0.5

        with self._lock:
            self._heading += angular * dt
            self._x += linear * math.cos(self._heading) * dt
            self._y += linear * math.sin(self._heading) * dt

    def reset(self):
        """Reset position to origin (e.g. after manual repositioning)."""
        with self._lock:
            self._x = 0.0
            self._y = 0.0
            self._heading = 0.0
        logger.info("Geofence position reset to origin")

    def get_status(self) -> dict:
        """Return geofence status for telemetry."""
        return {
            "enabled": self.enabled,
            "max_radius_m": self.max_radius,
            "distance_m": round(self.distance_from_start, 2),
            "position": {
                "x": round(self._x, 2),
                "y": round(self._y, 2),
            },
            "within_bounds": self.distance_from_start <= self.max_radius,
        }


# ---------------------------------------------------------------------------
# Issue #203 — Polygon geofence + visual editor support
# ---------------------------------------------------------------------------


class GeofencePolygon:
    """Named polygon geofence zone.

    Points are (lat, lng) WGS-84 pairs stored in CW or CCW order.
    ``point_in_polygon`` uses the even-odd ray-casting rule.

    RCAN config format::

        geofence:
          enabled: true
          polygons:
            - name: backyard
              action: stop
              points:
                - [51.5, -0.1]
                - [51.5, -0.09]
                - [51.49, -0.09]
                - [51.49, -0.1]

    Args:
        name:   Human-readable zone name.
        points: List of ``[lat, lng]`` pairs (≥3) defining the polygon.
        action: ``"stop"`` or ``"warn"`` when robot is outside the zone.
    """

    def __init__(self, name: str, points: list, action: str = "stop") -> None:
        if len(points) < 3:
            raise ValueError(f"Polygon '{name}' must have at least 3 points")
        self.name = name
        self.points = [tuple(p) for p in points]
        self.action = action

    def contains(self, lat: float, lng: float) -> bool:
        """Return True if the point (lat, lng) is inside this polygon.

        Uses the even-odd ray-casting algorithm.

        Args:
            lat: Latitude of the query point.
            lng: Longitude of the query point.

        Returns:
            ``True`` if the point is inside the polygon boundary.
        """
        n = len(self.points)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = self.points[i]
            xj, yj = self.points[j]
            if ((yi > lng) != (yj > lng)) and (lat < (xj - xi) * (lng - yi) / (yj - yi) + xi):
                inside = not inside
            j = i
        return inside

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict (suitable for Leaflet.js)."""
        return {
            "name": self.name,
            "action": self.action,
            "points": [list(p) for p in self.points],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GeofencePolygon":
        """Deserialise from a dict (as stored in RCAN config).

        Args:
            data: Dict with ``name``, ``points``, and optional ``action``.

        Returns:
            New :class:`GeofencePolygon` instance.
        """
        return cls(
            name=data["name"],
            points=data["points"],
            action=data.get("action", "stop"),
        )

    def __repr__(self) -> str:
        return f"GeofencePolygon(name={self.name!r}, points={len(self.points)})"


def load_polygons(config: dict) -> list:
    """Load all polygon zones from a RCAN config dict.

    Args:
        config: Full RCAN config dict.

    Returns:
        List of :class:`GeofencePolygon` instances.
    """
    geo_cfg = config.get("geofence", {})
    polygons = []
    for raw in geo_cfg.get("polygons", []):
        try:
            polygons.append(GeofencePolygon.from_dict(raw))
        except Exception as exc:
            logger.warning("Invalid polygon config: %s — %s", raw.get("name"), exc)
    return polygons


def save_polygon_to_config(config_path: str, polygon: GeofencePolygon) -> None:
    """Append or update a polygon in the RCAN config file.

    If a polygon with the same name already exists, it is replaced.

    Args:
        config_path: Path to the RCAN ``.yaml`` file.
        polygon:     :class:`GeofencePolygon` to save.
    """
    import yaml

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    if "geofence" not in config:
        config["geofence"] = {}
    if "polygons" not in config["geofence"]:
        config["geofence"]["polygons"] = []

    # Replace existing entry with the same name, or append
    polys = config["geofence"]["polygons"]
    updated = False
    for i, p in enumerate(polys):
        if p.get("name") == polygon.name:
            polys[i] = polygon.to_dict()
            updated = True
            break
    if not updated:
        polys.append(polygon.to_dict())

    with open(config_path, "w") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False)

    logger.info("Polygon '%s' saved to %s", polygon.name, config_path)


def delete_polygon_from_config(config_path: str, polygon_name: str) -> bool:
    """Remove a named polygon from the RCAN config file.

    Args:
        config_path:  Path to the RCAN ``.yaml`` file.
        polygon_name: Name of the polygon to remove.

    Returns:
        ``True`` if the polygon was found and removed, ``False`` otherwise.
    """
    import yaml

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    polys = config.get("geofence", {}).get("polygons", [])
    before = len(polys)
    config["geofence"]["polygons"] = [p for p in polys if p.get("name") != polygon_name]
    removed = len(config["geofence"]["polygons"]) < before

    if removed:
        with open(config_path, "w") as f:
            yaml.dump(config, f, sort_keys=False, default_flow_style=False)
        logger.info("Polygon '%s' deleted from %s", polygon_name, config_path)

    return removed

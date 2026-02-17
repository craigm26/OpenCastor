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

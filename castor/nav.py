"""
OpenCastor Waypoint Navigation.

Provides synchronous waypoint execution: turn to a heading, then drive a
specified distance.  Works with any DriverBase implementation.
"""

import logging
import time
from typing import Optional

logger = logging.getLogger("OpenCastor.Nav")

__all__ = ["WaypointNav"]


class WaypointNav:
    """Turn-then-drive waypoint navigation.

    Uses the driver's move() / stop() API and time-based dead reckoning.
    No odometry or SLAM is required.
    """

    def __init__(self, driver, config: dict):
        """
        Args:
            driver: A DriverBase instance (or any object with move/stop methods).
            config: Full robot config dict; reads ``physics`` sub-block.
        """
        self.driver = driver
        physics = config.get("physics", {})
        self.wheel_circumference_m: float = float(
            physics.get("wheel_circumference_m", 0.21)
        )
        self.turn_time_per_deg_s: float = float(
            physics.get("turn_time_per_deg_s", 0.011)
        )
        self._safety_stop: bool = bool(config.get("safety_stop", False))
        self._log = logger

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self,
        distance_m: float,
        heading_deg: float,
        speed: float = 0.6,
    ) -> dict:
        """Turn to ``heading_deg`` then drive ``distance_m`` at ``speed``.

        Args:
            distance_m:  Target distance in metres (negative = reverse).
            heading_deg: Relative heading change in degrees (positive = left turn).
            speed:       Drive speed in range 0.0–1.0 (clamped).

        Returns:
            dict with keys: ok, duration_s, distance_m, heading_deg.
        """
        speed = max(0.0, min(1.0, float(speed)))
        t_start = time.monotonic()

        try:
            # --- TURN phase ---
            turn_duration = abs(heading_deg) * self.turn_time_per_deg_s
            if heading_deg != 0 and turn_duration > 0:
                angular = speed if heading_deg > 0 else -speed
                self._log.debug(
                    f"Turning {heading_deg}° (angular={angular:.2f}, "
                    f"duration={turn_duration:.3f}s)"
                )
                self.driver.move(linear=0.0, angular=angular)
                time.sleep(turn_duration)
                self.driver.stop()

            # --- DRIVE phase ---
            if distance_m != 0:
                drive_duration = (
                    abs(distance_m) / self.wheel_circumference_m
                ) * 1.0  # 1 revolution per circumference at full-speed baseline
                # Adjust for speed: at full speed we'd cover wheel_circumference in 1 s
                # but our unit is already in time, scale by speed factor
                # More precisely: time = distance / (speed * max_linear_speed)
                # We treat wheel_circumference_m as distance per second at speed=1.0
                drive_duration = abs(distance_m) / (self.wheel_circumference_m * max(speed, 0.01))
                linear = speed if distance_m > 0 else -speed
                self._log.debug(
                    f"Driving {distance_m}m (linear={linear:.2f}, "
                    f"duration={drive_duration:.3f}s)"
                )
                self.driver.move(linear=linear, angular=0.0)
                time.sleep(drive_duration)

        finally:
            self.driver.stop()

        duration_s = time.monotonic() - t_start
        return {
            "ok": True,
            "duration_s": round(duration_s, 3),
            "distance_m": distance_m,
            "heading_deg": heading_deg,
        }

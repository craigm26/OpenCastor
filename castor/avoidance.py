"""
Reactive obstacle avoidance layer for OpenCastor (#169).

Wraps a DriverBase and intercepts move() calls to automatically reduce speed
or trigger an emergency stop when obstacles are detected by the depth sensor
or 2D lidar.

Behaviour (priority order):
  1. E-stop zone (< ESTOP_MM): call driver.stop() + fire estop callback
  2. Slow zone (< SLOW_MM):    scale linear speed by SLOW_FACTOR
  3. Clear (>= SLOW_MM):       pass through unmodified

Sensor sources (in priority):
  - Lidar scan (castor.drivers.lidar_driver) if available
  - Depth sensor (castor.depth) if available
  - Mock: always "clear"

Env:
  CASTOR_AVOID_ESTOP_MM   — distance to trigger e-stop (default 200 mm)
  CASTOR_AVOID_SLOW_MM    — distance to start slowing (default 500 mm)
  CASTOR_AVOID_SLOW_FACTOR — speed scale in slow zone (default 0.4)

API:
  GET /api/avoidance/status   — {mode, nearest_mm, zone, enabled}
  POST /api/avoidance/enable  — enable reactive avoidance
  POST /api/avoidance/disable — disable (pass-through mode)
"""

import logging
import os
import threading
from typing import Optional
from collections.abc import Callable

logger = logging.getLogger("OpenCastor.Avoidance")

_ESTOP_MM = int(os.getenv("CASTOR_AVOID_ESTOP_MM", "200"))
_SLOW_MM = int(os.getenv("CASTOR_AVOID_SLOW_MM", "500"))
_SLOW_FACTOR = float(os.getenv("CASTOR_AVOID_SLOW_FACTOR", "0.4"))

_singleton: Optional["ReactiveAvoider"] = None
_lock = threading.Lock()


class ReactiveAvoider:
    """Wraps a driver and intercepts move() to apply reactive obstacle avoidance.

    Args:
        driver: Any DriverBase-compatible object (or None for standalone use).
        estop_callback: Called with no args when e-stop zone is triggered.
        estop_mm: Distance (mm) below which e-stop triggers.
        slow_mm: Distance (mm) below which speed is reduced.
        slow_factor: Speed multiplier in the slow zone (0–1).
    """

    def __init__(
        self,
        driver=None,
        estop_callback: Optional[Callable] = None,
        estop_mm: int = _ESTOP_MM,
        slow_mm: int = _SLOW_MM,
        slow_factor: float = _SLOW_FACTOR,
    ):
        self._driver = driver
        self._estop_cb = estop_callback
        self._estop_mm = estop_mm
        self._slow_mm = slow_mm
        self._slow_factor = slow_factor
        self._enabled = True
        self._nearest_mm: float = float("inf")
        self._zone = "clear"
        self._mode = "mock"
        self._lock = threading.Lock()

        # Detect available sensors
        self._lidar = None
        self._depth_available = False

        try:
            from castor.drivers.lidar_driver import get_lidar

            lidar = get_lidar()
            if lidar.health_check().get("ok"):
                self._lidar = lidar
                self._mode = "lidar"
                logger.info("ReactiveAvoider: using lidar sensor")
        except Exception:
            pass

        if self._mode == "mock":
            try:
                from castor.depth import get_obstacle_zones

                self._get_depth_obstacles = get_obstacle_zones
                self._depth_available = True
                self._mode = "depth"
                logger.info("ReactiveAvoider: using depth sensor")
            except Exception:
                pass

        if self._mode == "mock":
            logger.info("ReactiveAvoider: no sensor — mock mode (always clear)")

    # ── Public API ────────────────────────────────────────────────────────

    def enable(self) -> None:
        self._enabled = True
        logger.info("Reactive avoidance enabled")

    def disable(self) -> None:
        self._enabled = False
        logger.info("Reactive avoidance disabled (pass-through)")

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def nearest_mm(self) -> float:
        return self._nearest_mm

    @property
    def zone(self) -> str:
        return self._zone

    @property
    def mode(self) -> str:
        return self._mode

    def check_obstacles(self) -> dict:
        """Sample sensors and return current obstacle state.

        Returns:
            {nearest_mm, zone: "clear"|"slow"|"estop", sectors}
        """
        nearest_mm, sectors = self._sample_sensors()
        with self._lock:
            self._nearest_mm = nearest_mm
            if nearest_mm < self._estop_mm:
                self._zone = "estop"
            elif nearest_mm < self._slow_mm:
                self._zone = "slow"
            else:
                self._zone = "clear"
        return {
            "nearest_mm": round(nearest_mm, 1),
            "zone": self._zone,
            "sectors": sectors,
        }

    def move(self, action: dict) -> dict:
        """Check obstacles then forward a (possibly modified) action to the driver.

        Returns the action dict actually sent to the driver.
        """
        if not self._enabled:
            if self._driver:
                self._driver.move(action)
            return action

        state = self.check_obstacles()
        zone = state["zone"]
        modified = dict(action)

        if zone == "estop":
            logger.warning(
                "Avoidance E-STOP: nearest=%.0f mm (< %d mm)",
                state["nearest_mm"],
                self._estop_mm,
            )
            if self._driver:
                self._driver.stop()
            if self._estop_cb:
                try:
                    self._estop_cb()
                except Exception:
                    pass
            modified["linear"] = 0.0
            modified["angular"] = 0.0
            modified["_avoidance_zone"] = "estop"
            return modified

        if zone == "slow":
            linear = float(modified.get("linear", 0.0))
            # Only slow down forward motion — allow reverse to escape
            if linear > 0:
                modified["linear"] = round(linear * self._slow_factor, 3)
                modified["_avoidance_zone"] = "slow"
                logger.debug(
                    "Avoidance SLOW: %.0f mm → linear %.2f → %.2f",
                    state["nearest_mm"],
                    linear,
                    modified["linear"],
                )

        if self._driver:
            self._driver.move(modified)
        return modified

    def status(self) -> dict:
        """Return current avoidance status dict."""
        return {
            "enabled": self._enabled,
            "mode": self._mode,
            "nearest_mm": round(self._nearest_mm, 1) if self._nearest_mm != float("inf") else None,
            "zone": self._zone,
            "estop_mm": self._estop_mm,
            "slow_mm": self._slow_mm,
            "slow_factor": self._slow_factor,
        }

    # ── Internal ──────────────────────────────────────────────────────────

    def _sample_sensors(self) -> tuple[float, dict]:
        """Sample available sensors. Returns (nearest_mm, sectors_dict)."""
        sectors: dict = {}

        # Lidar source
        if self._lidar is not None:
            try:
                obs = self._lidar.obstacles()
                nearest = obs.get("min_distance_mm", float("inf"))
                sectors = obs.get("sectors", {})
                # Convert mm directly
                return float(nearest), sectors
            except Exception as exc:
                logger.debug("Lidar sample error: %s", exc)

        # Depth sensor source
        if self._depth_available:
            try:
                from castor.camera import CameraManager
                from castor.depth import get_obstacle_zones

                camera = CameraManager()
                jpeg = camera.capture()
                if jpeg:
                    import numpy as np

                    nparr = np.frombuffer(jpeg, np.uint8)
                    try:
                        import cv2

                        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                        zones = get_obstacle_zones(img)
                        nearest = min(
                            zones.get("left_cm", 999),
                            zones.get("center_cm", 999),
                            zones.get("right_cm", 999),
                        )
                        sectors = {k: v * 10 for k, v in zones.items()}  # cm → mm
                        return float(nearest) * 10, sectors  # cm → mm
                    except Exception:
                        pass
            except Exception as exc:
                logger.debug("Depth sample error: %s", exc)

        # Mock: always clear
        return float("inf"), {}


def get_avoider(driver=None, estop_callback=None) -> ReactiveAvoider:
    """Return the process-wide ReactiveAvoider singleton."""
    global _singleton
    with _lock:
        if _singleton is None:
            _singleton = ReactiveAvoider(driver=driver, estop_callback=estop_callback)
        elif driver is not None and _singleton._driver is None:
            _singleton._driver = driver
        elif estop_callback is not None and _singleton._estop_cb is None:
            _singleton._estop_cb = estop_callback
    return _singleton

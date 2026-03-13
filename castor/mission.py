"""Sequential waypoint mission planner for OpenCastor.

A *mission* is an ordered list of waypoints executed one after another in a
background thread.  Each waypoint is a dict:

.. code-block:: python

    {
        "distance_m":  float,   # metres to drive (negative = reverse)
        "heading_deg": float,   # relative heading change in degrees
        "speed":       float,   # 0.0–1.0 drive speed (default 0.6)
        "dwell_s":     float,   # pause after this waypoint in seconds (default 0)
        "label":       str,     # optional human-readable name
    }

Usage::

    from castor.mission import MissionRunner
    from castor.nav import WaypointNav

    runner = MissionRunner(driver, config)
    job_id = runner.start(waypoints=[
        {"distance_m": 0.5, "heading_deg": 0},
        {"distance_m": 0.3, "heading_deg": 90, "dwell_s": 1.0},
        {"distance_m": 0.5, "heading_deg": 180},
    ], loop=False)

    print(runner.status())
    runner.stop()

REST API (implemented in api.py):

    POST /api/nav/mission       — start a mission
    GET  /api/nav/mission       — current status
    POST /api/nav/mission/stop  — cancel
"""

from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from typing import Any, Optional

from castor.nav import WaypointNav  # module-level so tests can patch castor.mission.WaypointNav

logger = logging.getLogger("OpenCastor.Mission")


class MissionRunner:
    """Execute a list of waypoints sequentially in a background thread.

    Attributes:
        driver:  Any DriverBase instance (duck-typed: needs move/stop methods).
        config:  Full RCAN config dict; passed through to :class:`WaypointNav`.
    """

    #: Maximum number of completed jobs to keep in history.
    MAX_HISTORY = 50

    def __init__(self, driver: Any, config: dict[str, Any]) -> None:
        self._driver = driver
        self._config = config
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._status: dict[str, Any] = {
            "running": False,
            "job_id": None,
            "step": 0,
            "total": 0,
            "loop": False,
            "loop_count": 0,
            "waypoints": [],
            "results": [],
            "error": None,
        }
        # job_id → {"waypoints": [...], "loop": bool} — capped at MAX_HISTORY entries
        self._history: dict[str, dict[str, Any]] = {}
        # Dead-reckoning position: updated after each waypoint
        self._position: dict[str, float] = {"x_m": 0.0, "y_m": 0.0, "heading_deg": 0.0}
        # Geo-fence bounds (or None for no fencing): {x_min, x_max, y_min, y_max} in metres
        self._geofence: Optional[dict[str, float]] = self._parse_geofence(config)
        # Mission ETA tracking
        self._elapsed_s: float = 0.0
        self._eta_s: Optional[float] = None
        self._waypoint_durations: list[float] = []
        self._mission_start_time: Optional[float] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(
        self,
        waypoints: list[dict[str, Any]],
        *,
        loop: bool = False,
    ) -> str:
        """Begin executing *waypoints* in a daemon background thread.

        If a mission is already running it is cancelled first.

        Args:
            waypoints: Ordered list of waypoint dicts.  Required key:
                       ``distance_m``.  Optional: ``heading_deg`` (default 0),
                       ``speed`` (default 0.6), ``dwell_s`` (default 0),
                       ``label`` (default ``step-N``).
            loop:      When ``True`` the waypoint list is repeated indefinitely
                       until :meth:`stop` is called.

        Returns:
            ``job_id`` string (UUID4).
        """
        if not waypoints:
            raise ValueError("Mission requires at least one waypoint")

        # Cancel any existing mission
        self.stop()

        job_id = str(uuid.uuid4())
        self._stop_event.clear()

        with self._lock:
            self._status = {
                "running": True,
                "job_id": job_id,
                "step": 0,
                "total": len(waypoints),
                "loop": loop,
                "loop_count": 0,
                "waypoints": list(waypoints),
                "results": [],
                "error": None,
            }

        # Reset ETA tracking fields for the new mission
        self._elapsed_s = 0.0
        self._eta_s = None
        self._waypoint_durations = []
        self._mission_start_time = None

        # Persist to history for later replay (FIFO eviction)
        self._history[job_id] = {"waypoints": list(waypoints), "loop": loop}
        if len(self._history) > self.MAX_HISTORY:
            oldest = next(iter(self._history))
            del self._history[oldest]

        self._thread = threading.Thread(
            target=self._run,
            args=(waypoints, loop, job_id),
            daemon=True,
            name=f"mission-{job_id[:8]}",
        )
        self._thread.start()
        logger.info("Mission %s started: %d waypoints, loop=%s", job_id[:8], len(waypoints), loop)
        return job_id

    def get_waypoints(self, job_id: str) -> Optional[list[dict[str, Any]]]:
        """Return the waypoints for a past or current mission by *job_id*.

        Returns ``None`` if the job_id is not found in history.
        """
        entry = self._history.get(job_id)
        if entry is None:
            # Also check the currently-running job
            with self._lock:
                if self._status.get("job_id") == job_id:
                    return list(self._status["waypoints"])
            return None
        return list(entry["waypoints"])

    def list_history(self) -> list[dict[str, Any]]:
        """Return a list of ``{job_id, total, loop}`` summaries for past missions."""
        return [
            {"job_id": jid, "total": len(v["waypoints"]), "loop": v["loop"]}
            for jid, v in self._history.items()
        ]

    def position(self) -> dict[str, float]:
        """Return the current dead-reckoning position ``{x_m, y_m, heading_deg}``."""
        with self._lock:
            return dict(self._position)

    def reset_position(self) -> None:
        """Reset the dead-reckoning position to the origin."""
        with self._lock:
            self._position = {"x_m": 0.0, "y_m": 0.0, "heading_deg": 0.0}

    def set_geofence(self, bounds: Optional[dict[str, float]]) -> None:
        """Set or clear the geo-fence boundary.

        Args:
            bounds: ``{x_min, x_max, y_min, y_max}`` in metres, or ``None`` to disable.
        """
        self._geofence = bounds

    def stop(self) -> None:
        """Cancel the running mission and wait for the thread to finish."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        if self._driver is not None:
            try:
                self._driver.stop()
            except Exception:
                pass
        with self._lock:
            if self._status.get("running"):
                self._status["running"] = False
                self._status["error"] = "cancelled"

    def status(self) -> dict[str, Any]:
        """Return a snapshot of the current mission status including ETA fields."""
        with self._lock:
            step = self._status["step"]
            total = self._status["total"]
            return {
                # New ETA fields (#277)
                "running": self._status["running"],
                "current_waypoint": step,
                "total_waypoints": total,
                "elapsed_s": round(self._elapsed_s, 1),
                "eta_s": round(self._eta_s, 1) if self._eta_s is not None else None,
                "position": dict(self._position),
                "geofence": self._geofence,
                # Backwards-compatible legacy fields
                "step": step,
                "total": total,
                "job_id": self._status.get("job_id"),
                "loop": self._status.get("loop", False),
                "loop_count": self._status.get("loop_count", 0),
                "results": list(self._status.get("results", [])),
                "error": self._status.get("error"),
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_geofence(config: dict[str, Any]) -> Optional[dict[str, float]]:
        """Extract geo-fence config from the RCAN config dict."""
        gf = (config.get("mission") or {}).get("geofence")
        if not gf:
            return None
        try:
            return {
                "x_min": float(gf["x_min"]),
                "x_max": float(gf["x_max"]),
                "y_min": float(gf["y_min"]),
                "y_max": float(gf["y_max"]),
            }
        except (KeyError, TypeError, ValueError):
            logger.warning("Invalid mission.geofence config — geo-fence disabled")
            return None

    def _update_position(self, heading_deg: float, distance_m: float) -> None:
        """Update dead-reckoning position after executing one waypoint segment."""
        with self._lock:
            self._position["heading_deg"] = (self._position["heading_deg"] + heading_deg) % 360
            heading_rad = math.radians(self._position["heading_deg"])
            self._position["x_m"] += distance_m * math.sin(heading_rad)
            self._position["y_m"] += distance_m * math.cos(heading_rad)

    def _check_geofence(self) -> bool:
        """Return True if position is within the geo-fence (or no fence is set)."""
        if self._geofence is None:
            return True
        with self._lock:
            x = self._position["x_m"]
            y = self._position["y_m"]
        gf = self._geofence
        return gf["x_min"] <= x <= gf["x_max"] and gf["y_min"] <= y <= gf["y_max"]

    # ------------------------------------------------------------------
    # Internal execution loop
    # ------------------------------------------------------------------

    def _run(
        self,
        waypoints: list[dict[str, Any]],
        loop: bool,
        job_id: str,
    ) -> None:
        nav = WaypointNav(self._driver, self._config)
        loop_count = 0

        self._mission_start_time = time.time()

        try:
            while True:
                for step_idx, wp in enumerate(waypoints):
                    if self._stop_event.is_set():
                        return

                    with self._lock:
                        self._status["step"] = step_idx + 1
                        self._status["loop_count"] = loop_count

                    distance_m = float(wp.get("distance_m", 0))
                    heading_deg = float(wp.get("heading_deg", 0))
                    speed = float(wp.get("speed", 0.6))
                    dwell_s = float(wp.get("dwell_s", 0))
                    label = str(wp.get("label", f"step-{step_idx + 1}"))

                    logger.debug(
                        "Mission %s step %d/%d (%s): dist=%.2f heading=%.1f speed=%.2f",
                        job_id[:8],
                        step_idx + 1,
                        len(waypoints),
                        label,
                        distance_m,
                        heading_deg,
                        speed,
                    )

                    wp_start = time.time()
                    try:
                        result = nav.execute(distance_m, heading_deg, speed)
                        result["label"] = label
                        result["step"] = step_idx + 1
                    except Exception as exc:
                        logger.warning(
                            "Mission %s step %d failed: %s", job_id[:8], step_idx + 1, exc
                        )
                        result = {
                            "ok": False,
                            "error": str(exc),
                            "label": label,
                            "step": step_idx + 1,
                        }

                    wp_duration = time.time() - wp_start
                    remaining = len(waypoints) - (step_idx + 1)
                    with self._lock:
                        self._waypoint_durations.append(wp_duration)
                        self._elapsed_s = time.time() - self._mission_start_time
                        avg_dur = sum(self._waypoint_durations) / len(self._waypoint_durations)
                        self._eta_s = avg_dur * remaining

                    # Update dead-reckoning position
                    self._update_position(heading_deg, distance_m)

                    # Geo-fence check — abort if outside bounds
                    if not self._check_geofence():
                        with self._lock:
                            pos = dict(self._position)
                            breach_msg = f"geofence_breach: x={pos['x_m']:.2f}m y={pos['y_m']:.2f}m"
                            self._status["error"] = breach_msg
                        logger.warning("Mission %s %s", job_id[:8], breach_msg)
                        if self._driver is not None:
                            try:
                                self._driver.stop()
                            except Exception:
                                pass
                        return

                    with self._lock:
                        self._status["results"].append(result)

                    if dwell_s > 0 and not self._stop_event.is_set():
                        deadline = time.monotonic() + dwell_s
                        while time.monotonic() < deadline:
                            if self._stop_event.is_set():
                                return
                            time.sleep(min(0.05, deadline - time.monotonic()))

                if not loop:
                    break
                loop_count += 1
                logger.info("Mission %s loop %d complete, repeating…", job_id[:8], loop_count)

        except Exception as exc:
            logger.error("Mission %s crashed: %s", job_id[:8], exc)
            with self._lock:
                self._status["error"] = str(exc)
        finally:
            with self._lock:
                self._status["running"] = False
                self._eta_s = 0.0
            logger.info("Mission %s finished", job_id[:8])

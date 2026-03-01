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
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from castor.nav import WaypointNav  # module-level so tests can patch castor.mission.WaypointNav

logger = logging.getLogger("OpenCastor.Mission")


class MissionRunner:
    """Execute a list of waypoints sequentially in a background thread.

    Attributes:
        driver:  Any DriverBase instance (duck-typed: needs move/stop methods).
        config:  Full RCAN config dict; passed through to :class:`WaypointNav`.
    """

    def __init__(self, driver: Any, config: Dict[str, Any]) -> None:
        self._driver = driver
        self._config = config
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._status: Dict[str, Any] = {
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(
        self,
        waypoints: List[Dict[str, Any]],
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

        self._thread = threading.Thread(
            target=self._run,
            args=(waypoints, loop, job_id),
            daemon=True,
            name=f"mission-{job_id[:8]}",
        )
        self._thread.start()
        logger.info("Mission %s started: %d waypoints, loop=%s", job_id[:8], len(waypoints), loop)
        return job_id

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

    def status(self) -> Dict[str, Any]:
        """Return a snapshot of the current mission status."""
        with self._lock:
            return dict(self._status)

    # ------------------------------------------------------------------
    # Internal execution loop
    # ------------------------------------------------------------------

    def _run(
        self,
        waypoints: List[Dict[str, Any]],
        loop: bool,
        job_id: str,
    ) -> None:
        nav = WaypointNav(self._driver, self._config)
        loop_count = 0

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
            logger.info("Mission %s finished", job_id[:8])

"""Simulation driver for OpenCastor.

Test robot behaviors in Gazebo or Webots without physical hardware.
Falls back to a pure no-op mock when neither simulator is available.

Supported backends:
    - **gazebo**: Publishes ROS2 Twist to ``/cmd_vel`` (requires rclpy + ROS2)
    - **webots**: Posts to Webots Robot Supervisor REST API
    - **mock** (default): Logs commands, no physical output

RCAN config::

    drivers:
      - protocol: simulation
        backend: gazebo           # gazebo | webots | mock
        webots_url: http://localhost:1234  # for webots backend
        topic: /cmd_vel           # for gazebo backend

Environment:
    SIMULATION_BACKEND  — Override backend (gazebo|webots|mock)
    WEBOTS_SUPERVISOR_URL — Webots REST endpoint
"""

import json
import logging
import os
import threading
import time
import urllib.request
from typing import Any, Dict, Optional
from urllib.error import URLError

from .base import DriverBase

logger = logging.getLogger("OpenCastor.SimulationDriver")

# Optional ROS2 / rclpy
try:
    import rclpy  # type: ignore
    from geometry_msgs.msg import Twist  # type: ignore

    HAS_RCLPY = True
    logger.debug("rclpy available — Gazebo backend enabled")
except ImportError:
    HAS_RCLPY = False


def _resolve_backend(config: Dict[str, Any]) -> str:
    env = os.getenv("SIMULATION_BACKEND", "").lower()
    if env in ("gazebo", "webots", "mock"):
        return env
    return config.get("backend", "mock").lower()


class _GazeboBackend:
    """Publishes geometry_msgs/Twist to /cmd_vel via rclpy."""

    def __init__(self, topic: str = "/cmd_vel"):
        if not HAS_RCLPY:
            raise RuntimeError("rclpy not installed; cannot use Gazebo backend")

        rclpy.init(args=None)
        self._node = rclpy.create_node("opencastor_sim")
        self._pub = self._node.create_publisher(Twist, topic, 10)
        self._topic = topic

        # Spin in daemon thread
        self._spin_thread = threading.Thread(
            target=rclpy.spin, args=(self._node,), daemon=True, name="rclpy-spin-sim"
        )
        self._spin_thread.start()
        logger.info("GazeboBackend ready: topic=%s", topic)

    def send(self, linear: float, angular: float) -> None:
        msg = Twist()
        msg.linear.x = float(linear)
        msg.angular.z = float(angular)
        self._pub.publish(msg)

    def stop(self) -> None:
        self.send(0.0, 0.0)

    def close(self) -> None:
        try:
            self._node.destroy_node()
            rclpy.shutdown()
        except Exception:
            pass


class _WebotsBackend:
    """Posts velocity commands to Webots Supervisor REST API."""

    def __init__(self, base_url: str = "http://localhost:1234"):
        self._url = base_url.rstrip("/")
        logger.info("WebotsBackend ready: url=%s", self._url)

    def _post(self, path: str, data: dict) -> bool:
        try:
            body = json.dumps(data).encode()
            req = urllib.request.Request(
                f"{self._url}{path}",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2):
                pass
            return True
        except (URLError, OSError) as exc:
            logger.debug("Webots POST failed: %s", exc)
            return False

    def send(self, linear: float, angular: float) -> None:
        self._post("/robot/command", {"linear": linear, "angular": angular})

    def stop(self) -> None:
        self.send(0.0, 0.0)

    def close(self) -> None:
        self.stop()


class SimulationDriver(DriverBase):
    """Robot simulation driver (Gazebo / Webots / mock).

    Implements the :class:`~castor.drivers.base.DriverBase` interface so it
    can be used as a drop-in replacement for hardware drivers during testing.

    Args:
        config: RCAN driver config dict.  Relevant keys:
            - ``backend``: ``"gazebo"``, ``"webots"``, or ``"mock"``
            - ``webots_url``: Webots supervisor URL (webots backend)
            - ``topic``: ROS2 topic (gazebo backend, default ``/cmd_vel``)
            - ``default_speed``: Default linear speed (default ``0.5``)
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._default_speed = float(config.get("default_speed", 0.5))
        self._backend_name = _resolve_backend(config)
        self._backend: Any = None
        self._last_command: Dict[str, Any] = {}

        self._mode = "mock"
        self._error: Optional[str] = None

        try:
            if self._backend_name == "gazebo":
                topic = config.get("topic", "/cmd_vel")
                self._backend = _GazeboBackend(topic=topic)
                self._mode = "hardware"
            elif self._backend_name == "webots":
                url = (
                    os.getenv("WEBOTS_SUPERVISOR_URL")
                    or config.get("webots_url", "http://localhost:1234")
                )
                self._backend = _WebotsBackend(base_url=url)
                self._mode = "hardware"
            else:
                logger.info(
                    "SimulationDriver: no simulator configured, using mock mode. "
                    "Set backend=gazebo or backend=webots in RCAN config."
                )
        except Exception as exc:
            logger.warning("SimulationDriver: backend init failed: %s", exc)
            self._error = str(exc)
            self._mode = "mock"

        logger.info(
            "SimulationDriver ready: backend=%s mode=%s",
            self._backend_name,
            self._mode,
        )

    # ------------------------------------------------------------------
    # DriverBase interface
    # ------------------------------------------------------------------

    def move(self, **kwargs: Any) -> None:
        """Execute a movement command.

        Accepted kwargs (following PCA9685 / RCAN action schema):
            - direction: "forward" | "backward" | "left" | "right" | "stop"
            - speed: 0.0–1.0
            - linear: direct linear velocity (m/s)
            - angular: direct angular velocity (rad/s)
        """
        direction = str(kwargs.get("direction", "stop")).lower()
        speed = float(kwargs.get("speed", self._default_speed))
        linear = float(kwargs.get("linear", 0.0))
        angular = float(kwargs.get("angular", 0.0))

        # Map direction strings to linear/angular
        if direction == "forward":
            linear, angular = speed, 0.0
        elif direction == "backward":
            linear, angular = -speed, 0.0
        elif direction == "left":
            linear, angular = 0.0, speed
        elif direction == "right":
            linear, angular = 0.0, -speed
        elif direction == "stop":
            linear, angular = 0.0, 0.0

        self._last_command = {
            "direction": direction,
            "linear": linear,
            "angular": angular,
            "speed": speed,
            "ts": time.time(),
        }

        if self._backend is not None:
            self._backend.send(linear, angular)
        else:
            logger.debug(
                "SimulationDriver (mock) move: dir=%s linear=%.2f angular=%.2f",
                direction, linear, angular,
            )

    def stop(self) -> None:
        """Send a stop command to the simulator."""
        self._last_command = {"direction": "stop", "linear": 0.0, "angular": 0.0, "ts": time.time()}
        if self._backend is not None:
            self._backend.stop()
        else:
            logger.debug("SimulationDriver (mock) stop")

    def close(self) -> None:
        """Release simulator resources."""
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception as exc:
                logger.warning("SimulationDriver close error: %s", exc)
            self._backend = None

    def health_check(self) -> Dict[str, Any]:
        return {
            "ok": self._error is None,
            "mode": self._mode,
            "backend": self._backend_name,
            "error": self._error,
            "last_command": self._last_command,
        }

"""
castor/drivers/ros2_driver.py — ROS2 bridge driver (issue #109).

Publishes ``geometry_msgs/Twist`` to a configurable ``/cmd_vel`` topic and
subscribes to ``/odom`` for position feedback.  Requires ``rclpy`` (installed
as part of a ROS2 distro).  Degrades to mock mode when rclpy is absent.

RCAN config example::

    drivers:
    - id: ros2_driver
      protocol: ros2
      cmd_vel_topic: /cmd_vel
      odom_topic: /odom
      frame_id: base_link
      max_linear_vel: 1.0
      max_angular_vel: 1.5

Install::

    pip install opencastor[ros2]   # or install rclpy from your ROS2 distro
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from castor.drivers.base import DriverBase

logger = logging.getLogger("OpenCastor.Driver.ROS2")

# ---------------------------------------------------------------------------
# Optional rclpy import
# ---------------------------------------------------------------------------

try:
    import rclpy
    from geometry_msgs.msg import Twist

    HAS_RCLPY = True
except ImportError:
    HAS_RCLPY = False
    logger.debug("rclpy not available — ROS2 driver will run in mock mode")


class ROS2Driver(DriverBase):
    """ROS2 bridge driver: publishes Twist to /cmd_vel, subscribes to /odom.

    In mock mode (no rclpy), all commands are logged but not executed.
    This lets the rest of OpenCastor boot on a non-ROS machine.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        self._cmd_vel_topic: str = cfg.get("cmd_vel_topic", "/cmd_vel")
        self._odom_topic: str = cfg.get("odom_topic", "/odom")
        self._frame_id: str = cfg.get("frame_id", "base_link")
        self._max_linear: float = float(cfg.get("max_linear_vel", 1.0))
        self._max_angular: float = float(cfg.get("max_angular_vel", 1.5))

        self._node: Optional[Any] = None
        self._publisher: Optional[Any] = None
        self._spin_thread: Optional[threading.Thread] = None
        self._last_odom: Optional[Dict[str, Any]] = None
        self._closed = False

        if HAS_RCLPY:
            self._init_ros2()
        else:
            logger.warning(
                "rclpy not installed — ROS2 driver running in mock mode. "
                "Install via your ROS2 distro or: pip install opencastor[ros2]"
            )

    # ------------------------------------------------------------------
    # ROS2 initialisation
    # ------------------------------------------------------------------

    def _init_ros2(self) -> None:
        """Initialise rclpy context, node, publisher, and odom subscriber."""
        try:
            if not rclpy.ok():
                rclpy.init()

            self._node = rclpy.create_node("opencastor_ros2_driver")
            self._publisher = self._node.create_publisher(Twist, self._cmd_vel_topic, 10)

            # Subscribe to odometry for position feedback
            try:
                from nav_msgs.msg import Odometry

                def _odom_cb(msg):
                    pos = msg.pose.pose.position
                    self._last_odom = {"x": pos.x, "y": pos.y, "z": pos.z}

                self._node.create_subscription(Odometry, self._odom_topic, _odom_cb, 10)
            except ImportError:
                logger.debug("nav_msgs not available — odometry subscription skipped")

            # Spin in a daemon thread so we don't block the main loop
            self._spin_thread = threading.Thread(
                target=self._spin_forever, daemon=True, name="ros2-spin"
            )
            self._spin_thread.start()

            logger.info(
                "ROS2 driver initialised: cmd_vel=%s odom=%s",
                self._cmd_vel_topic,
                self._odom_topic,
            )
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to initialise ROS2 driver: %s", exc)
            self._node = None
            self._publisher = None

    def _spin_forever(self) -> None:
        """Spin rclpy executor until the driver is closed."""
        try:
            while rclpy.ok() and not self._closed:
                rclpy.spin_once(self._node, timeout_sec=0.05)
        except Exception as exc:
            logger.debug("ROS2 spin thread exiting: %s", exc)

    # ------------------------------------------------------------------
    # DriverBase interface
    # ------------------------------------------------------------------

    def move(self, linear: float = 0.0, angular: float = 0.0) -> None:
        """Publish a Twist message to /cmd_vel.

        Args:
            linear: Forward (+) / backward (-) velocity in m/s.
            angular: Left (+) / right (-) angular velocity in rad/s.
        """
        # Clamp to configured limits
        linear = max(-self._max_linear, min(self._max_linear, linear))
        angular = max(-self._max_angular, min(self._max_angular, angular))

        if not HAS_RCLPY or self._publisher is None:
            logger.info("[mock] ROS2 move: linear=%.2f angular=%.2f", linear, angular)
            return

        twist = Twist()
        twist.linear.x = float(linear)
        twist.angular.z = float(angular)
        self._publisher.publish(twist)
        logger.debug("Published Twist: linear=%.2f angular=%.2f", linear, angular)

    def stop(self) -> None:
        """Publish a zero Twist to halt the robot."""
        self.move(0.0, 0.0)
        logger.info("ROS2 driver: stop")

    def close(self) -> None:
        """Destroy the ROS2 node and shut down rclpy."""
        self._closed = True
        if self._spin_thread and self._spin_thread.is_alive():
            self._spin_thread.join(timeout=2.0)
        if HAS_RCLPY and self._node is not None:
            try:
                self.stop()  # zero velocity before shutdown
                self._node.destroy_node()
            except Exception as exc:  # pragma: no cover
                logger.debug("ROS2 node destroy error: %s", exc)
            try:
                if rclpy.ok():
                    rclpy.shutdown()
            except Exception as exc:  # pragma: no cover
                logger.debug("rclpy shutdown error: %s", exc)
        logger.info("ROS2 driver closed")

    def health_check(self) -> Dict[str, Any]:
        """Return driver health: ok, mode, and last odom if available."""
        mode = "hardware" if (HAS_RCLPY and self._publisher is not None) else "mock"
        ok = mode == "hardware"
        result: Dict[str, Any] = {"ok": ok, "mode": mode, "error": None}
        if self._last_odom:
            result["odom"] = self._last_odom
        if not HAS_RCLPY:
            result["error"] = "rclpy not installed — run: pip install opencastor[ros2]"
        return result

    @property
    def last_odom(self) -> Optional[Dict[str, Any]]:
        """Last received odometry position {x, y, z}, or None."""
        return self._last_odom

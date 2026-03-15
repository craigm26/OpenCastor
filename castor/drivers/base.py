# SafetyLayer migration status — drivers that implement _move() are automatically
# routed through bounds-checking, estop, rate-limiting and audit when a SafetyLayer
# is attached.  Drivers still using move() directly bypass this routing.
#
# MIGRATED (implement _move()):
#   acb_driver.py, composite.py (_NullDriver too), esp32_ble_driver.py,
#   esp32_websocket.py, ev3dev_driver.py, ipc.py, pca9685.py (both classes),
#   reachy_driver.py, ros2_driver.py, simulation_driver.py, spike_driver.py
#
# NOT MIGRATED (move() overrides DriverBase — intentional or pending):
#   arduino_driver.py — pending
#   dynamixel.py      — arm driver; move(motor_id, angle_deg) is arm-specific,
#                        not a velocity interface; DO NOT migrate
#   imu_driver.py     — sensor-only; no move() needed
#   lidar_driver.py   — sensor-only; no move() needed
#   picamera2_driver.py — sensor-only; no move() needed
#   thermal_driver.py — sensor-only; no move() needed
#   worker.py         — proxy; delegates to real driver
#   battery_driver.py — sensor-only; no move() needed

from abc import ABC, abstractmethod
from typing import Any, Optional

__all__ = ["DriverBase"]


class DriverBase(ABC):
    """Abstract base class for all hardware drivers.

    Subclasses must implement ``_move()`` (preferred), ``stop()``, and ``close()``.
    Alternatively, subclasses may override ``move()`` directly for legacy behaviour;
    in that case ``_move()`` is never called and SafetyLayer routing is bypassed.

    SafetyLayer integration
    -----------------------
    Attach a SafetyLayer via ``set_safety_layer(layer)`` (typically done at
    runtime startup via ``wire_drivers_to_safety()``).  Once set, every call to
    ``move()`` is validated by ``safety_layer.write("/dev/motor/cmd", …)`` before
    the command reaches hardware.  Drivers that override ``move()`` directly do
    not receive this automatic routing — migrate them to ``_move()`` to opt in.

    When the underlying hardware SDK is unavailable, drivers should degrade
    gracefully to a mock/logging mode rather than raising import errors.
    """

    # Class-level default so subclasses that never call super().__init__()
    # still have a well-defined safety_layer attribute.
    safety_layer: Optional[Any] = None

    # ------------------------------------------------------------------
    # SafetyLayer wiring
    # ------------------------------------------------------------------

    def set_safety_layer(self, layer: Any) -> None:
        """Attach a SafetyLayer instance to this driver.

        Args:
            layer: A SafetyLayer (or compatible) object that exposes
                   ``write(path, data, principal)`` and ``estop(principal)``.
        """
        self.safety_layer = layer

    # ------------------------------------------------------------------
    # Move / safety routing
    # ------------------------------------------------------------------

    def move(
        self,
        linear: float = 0.0,
        angular: float = 0.0,
        *,
        linear_x: float | None = None,
        angular_z: float | None = None,
    ) -> None:
        """Send a velocity command, routing through SafetyLayer when present.

        Subclasses that implement ``_move()`` get automatic SafetyLayer
        validation.  Subclasses that override ``move()`` directly bypass it
        (legacy path — still fully functional, just no automatic safety checks).

        Args:
            linear: Forward/backward speed (range depends on driver, typically
                    -1.0 to 1.0). Also accepted as ``linear_x`` (ROS convention).
            angular: Turning rate (range depends on driver, typically -1.0 to
                     1.0). Also accepted as ``angular_z`` (ROS convention).
            linear_x: Alias for ``linear`` (ROS2 / legacy callers).
            angular_z: Alias for ``angular`` (ROS2 / legacy callers).
        """
        # Accept dict as first argument (legacy calling convention: move({"linear": 0.8}))
        if isinstance(linear, dict):
            cmd = linear
            linear = float(cmd.get("linear", cmd.get("linear_x", 0.0)))
            angular = float(cmd.get("angular", cmd.get("angular_z", 0.0)))
        # Accept ROS-convention keyword aliases
        if linear_x is not None:
            linear = linear_x
        if angular_z is not None:
            angular = angular_z
        sl = getattr(self, "safety_layer", None)
        if sl is not None:
            data = {"linear": linear, "angular": angular}
            ok = sl.write("/dev/motor/cmd", data, principal="driver")
            if not ok:
                return  # safety layer blocked the command
        self._move(linear, angular)

    def _move(self, linear: float = 0.0, angular: float = 0.0) -> None:
        """Execute a velocity command after safety validation.

        Override this method instead of ``move()`` to opt into automatic
        SafetyLayer routing.  The default implementation raises
        ``NotImplementedError`` so that drivers which forgot to implement
        either ``move()`` or ``_move()`` fail loudly at call time.

        Args:
            linear: Forward/backward speed.
            angular: Turning rate.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement _move() for SafetyLayer routing, "
            "or override move() directly for legacy behaviour."
        )

    def safety_stop(self) -> None:
        """Trigger an emergency stop through SafetyLayer, then halt hardware.

        Calls ``safety_layer.estop(principal="driver")`` if a SafetyLayer is
        attached, then unconditionally calls ``self.stop()``.
        """
        sl = getattr(self, "safety_layer", None)
        if sl is not None:
            try:
                sl.estop(principal="driver")
            except Exception:
                pass
        self.stop()

    # ------------------------------------------------------------------
    # Abstract hardware interface
    # ------------------------------------------------------------------

    @abstractmethod
    def stop(self) -> None:
        """Immediately halt all motors."""

    @abstractmethod
    def close(self) -> None:
        """Release hardware resources (serial ports, I2C buses, etc.)."""

    # ------------------------------------------------------------------
    # Optional overrides
    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        """Check whether the hardware is accessible and responsive.

        Returns a dict with keys:
            ``ok``    — True if the hardware is reachable.
            ``mode``  — "hardware" if real hardware is active, "mock" otherwise.
            ``error`` — Error message string, or None on success.

        The default implementation returns ``{"ok": True, "mode": "mock", "error": None}``
        because mock mode is functioning correctly — the driver is available, just not
        connected to real hardware.  Override in concrete drivers to probe actual hardware.
        """
        return {"ok": True, "mode": "mock", "error": None}

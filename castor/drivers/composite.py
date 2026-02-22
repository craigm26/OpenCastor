"""
castor/drivers/composite.py — Composite multi-subsystem driver.

Stacks multiple sub-drivers (e.g. PCA9685 base + gripper servo + pan-tilt)
under a single ``DriverBase`` interface.  The ``move()`` method routes action
dict keys to the appropriate sub-driver based on a configurable key-to-id
mapping.

RCAN config example::

    drivers:
      - id: full_robot
        protocol: composite
        subsystems:
          - id: base
            protocol: pca9685_rc
            port: /dev/i2c-1
            address: "0x40"
            frequency: 50
            steering_channel: 0
            throttle_channel: 1
          - id: gripper
            protocol: servo_pwm
            channel: 2
          - id: pan_tilt
            protocol: servo_pwm
            channel_pan: 3
            channel_tilt: 4
        routing:
          throttle: base
          steering: base
          linear: base
          angular: base
          gripper_open: gripper
          gripper_close: gripper
          pan: pan_tilt
          tilt: pan_tilt
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger("OpenCastor.CompositeDriver")


class CompositeDriver:
    """Route action keys to multiple sub-drivers by configuration.

    Args:
        config: Full robot config dict (the outer RCAN config).  The first
                driver entry whose ``protocol`` is ``"composite"`` is used.
    """

    def __init__(self, config: dict):
        self._sub_drivers: Dict[str, Any] = {}
        self._routing: Dict[str, str] = {}  # action_key → sub-driver id

        # Find the composite driver entry
        driver_entries: List[dict] = config.get("drivers", [])
        comp_entry = next((d for d in driver_entries if d.get("protocol") == "composite"), {})

        subsystems: List[dict] = comp_entry.get("subsystems", [])
        routing_cfg: Dict[str, str] = comp_entry.get("routing", {})

        # Default routing rules (can be overridden by config)
        default_routing = {
            "throttle": "base",
            "steering": "base",
            "linear": "base",
            "angular": "base",
        }
        self._routing = {**default_routing, **routing_cfg}

        # Initialise each sub-driver
        for sub_cfg in subsystems:
            sub_id = sub_cfg.get("id", "sub")
            protocol = sub_cfg.get("protocol", "")
            try:
                driver = self._make_sub_driver(sub_id, protocol, sub_cfg, config)
                # get_driver() returns None for unknown protocols — treat as failure
                if driver is None:
                    raise ValueError(f"get_driver() returned None for protocol '{protocol}'")
                self._sub_drivers[sub_id] = driver
                logger.info("CompositeDriver: sub-driver '%s' (%s) loaded", sub_id, protocol)
            except Exception as exc:
                logger.warning(
                    "CompositeDriver: sub-driver '%s' (%s) failed to load: %s",
                    sub_id,
                    protocol,
                    exc,
                )
                # Install a no-op mock so routing still works
                self._sub_drivers[sub_id] = _NullDriver(sub_id)

    # ── Sub-driver factory ────────────────────────────────────────────────────

    @staticmethod
    def _make_sub_driver(sub_id: str, protocol: str, sub_cfg: dict, full_config: dict):
        """Instantiate a sub-driver from its protocol name."""
        # Wrap the sub-entry as a mini-config so get_driver() can parse it
        mini_config = {**full_config, "drivers": [sub_cfg]}
        try:
            from castor.drivers import get_driver as _get_driver

            return _get_driver(mini_config)
        except Exception:
            # If the registry import fails, fall back to null driver
            return _NullDriver(sub_id)

    # ── DriverBase interface ──────────────────────────────────────────────────

    def move(self, linear_or_action, angular: float = 0.0):
        """Route a move command to the appropriate sub-driver(s).

        Accepts either:
          - ``move(linear, angular)`` — classic two-float form
          - ``move(action_dict)`` — dict with typed keys (composite form)
        """
        if isinstance(linear_or_action, dict):
            self._dispatch_action(linear_or_action)
        else:
            linear = float(linear_or_action)
            # Route to the "base" driver (or first sub-driver); fall back to
            # a NullDriver rather than None so the call is always safe.
            base = (
                self._sub_drivers.get("base")
                or next(iter(self._sub_drivers.values()), None)
                or _NullDriver("base")
            )
            base.move(linear, angular)

    def stop(self):
        """Broadcast stop to all sub-drivers."""
        for sub_id, drv in self._sub_drivers.items():
            try:
                drv.stop()
            except Exception as exc:
                logger.debug("CompositeDriver: stop error on '%s': %s", sub_id, exc)

    def close(self):
        """Close all sub-drivers in reverse init order."""
        for sub_id in reversed(list(self._sub_drivers.keys())):
            try:
                self._sub_drivers[sub_id].close()
            except Exception as exc:
                logger.debug("CompositeDriver: close error on '%s': %s", sub_id, exc)

    def health_check(self) -> dict:
        """Aggregate sub-driver health.  Any failed sub-driver marks composite as failed."""
        sub_results = {}
        all_ok = True
        for sub_id, drv in self._sub_drivers.items():
            try:
                result = drv.health_check()
                sub_results[sub_id] = result
                if not result.get("ok", False):
                    all_ok = False
            except Exception as exc:
                sub_results[sub_id] = {"ok": False, "error": str(exc)}
                all_ok = False

        mode = "hardware" if all_ok else "degraded"
        return {
            "ok": all_ok,
            "mode": mode,
            "subsystems": sub_results,
            "driver_type": "CompositeDriver",
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _dispatch_action(self, action: dict) -> None:
        """Route action dict keys to the correct sub-drivers."""
        action_type = action.get("type", "")

        if action_type == "stop":
            self.stop()
            return

        if action_type == "move":
            linear = action.get("linear", 0.0)
            angular = action.get("angular", 0.0)
            # Call move() with two floats directly — avoids recursive dispatch
            base = (
                self._sub_drivers.get("base")
                or next(iter(self._sub_drivers.values()), None)
                or _NullDriver("base")
            )
            base.move(float(linear), float(angular))
            return

        if action_type == "grip":
            state = action.get("state", "open")
            target_id = self._routing.get(f"gripper_{state}", "gripper")
            drv = self._sub_drivers.get(target_id) or self._sub_drivers.get("gripper")
            if drv:
                try:
                    drv.move(0.0, 0.0)  # Signal gripper action via move
                except Exception as exc:
                    logger.debug("CompositeDriver: grip dispatch error: %s", exc)
            return

        # Generic key-based dispatch for unknown action types
        for key, val in action.items():
            if key == "type":
                continue
            target_id = self._routing.get(key)
            if target_id and target_id in self._sub_drivers:
                try:
                    self._sub_drivers[target_id].move(
                        float(val) if isinstance(val, (int, float)) else 0.0, 0.0
                    )
                except Exception as exc:
                    logger.debug(
                        "CompositeDriver: dispatch key '%s' to '%s' error: %s",
                        key,
                        target_id,
                        exc,
                    )


class _NullDriver:
    """No-op placeholder for sub-drivers that failed to initialise."""

    def __init__(self, name: str = "null"):
        self._name = name

    def move(self, *args, **kwargs):
        logger.debug("_NullDriver(%s).move() — no-op", self._name)

    def stop(self):
        pass

    def close(self):
        pass

    def health_check(self) -> dict:
        return {"ok": False, "mode": "null", "error": f"Driver '{self._name}' failed to load"}

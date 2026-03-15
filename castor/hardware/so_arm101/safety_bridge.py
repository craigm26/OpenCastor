"""SO-ARM101 SafetyLayer-aware arm command write path.

This module is the **ONLY approved path** for sending runtime motor position
commands to the SO-ARM101 arm.  Every call flows through the OpenCastor
SafetyLayer, which enforces:

- Emergency stop (if active, all arm writes are blocked)
- RBAC permission checks
- Physical bounds (position, velocity) for each joint
- Anti-subversion scanning
- Rate limiting
- Full audit logging to /var/log/actions and /var/log/safety

Usage::

    from castor.hardware.so_arm101.safety_bridge import write_arm_command

    ok = write_arm_command(
        safety_layer=fs.safety,
        joint="shoulder_pan",
        position=0.5,          # radians
        velocity=0.1,          # rad/s
        principal="brain",
    )
    if not ok:
        print("Command blocked by safety layer:", fs.safety.last_write_denial)

Virtual filesystem mapping
--------------------------
Each joint maps to the path ``/dev/arm/<joint_name>``.  For example,
``shoulder_pan`` → ``/dev/arm/shoulder_pan``.

Data payload written to the namespace::

    {
        "position": <float>,   # joint position in radians
        "velocity": <float>,   # joint velocity in rad/s
        "joint":    <str>,     # joint name (redundant but useful for audit)
    }

Physical bounds defaults
------------------------
Default bounds are loaded from ``/etc/safety/bounds`` in the virtual
filesystem (written at boot from ``castor/safety/bounds.py``).
The built-in ``"arm"`` config sets:

- Position: ``-π`` to ``+π`` rad for all joints
- Velocity:  0 to 2.0 rad/s max

To override for a specific deployment, add an ``/etc/safety/bounds`` entry
to your ``rcan.yaml``::

    safety:
      bounds:
        joints:
          shoulder_pan:
            position_min: -1.57
            position_max:  1.57
            velocity_max:  1.0
            torque_max:   50.0

Or write it programmatically before booting::

    fs.ns.write("/etc/safety/bounds", {
        "joints": {
            "shoulder_pan": {
                "position_min": -1.57, "position_max": 1.57,
                "velocity_max": 1.0, "torque_max": 50.0,
            }
        }
    })

Setup vs runtime
----------------
The motor ID assignment / baudrate setup workflow in ``motor_setup.py``
is a **one-time hardware configuration step** and does NOT go through the
SafetyLayer.  Only runtime position/velocity commands use this bridge.

.. note::
   For runtime motor commands (not setup), always use
   ``castor.hardware.so_arm101.safety_bridge.write_arm_command``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from castor.fs.safety import SafetyLayer

logger = logging.getLogger("OpenCastor.Hardware.SOArm101.SafetyBridge")


def write_arm_command(
    safety_layer: Optional[SafetyLayer],
    joint: str,
    position: float,
    velocity: float = 0.0,
    principal: str = "brain",
) -> bool:
    """Write a joint position/velocity command through the SafetyLayer.

    Maps to ``/dev/arm/<joint>`` in the virtual filesystem and delegates
    to ``SafetyLayer.write()``, which enforces bounds, rate limits,
    anti-subversion scanning, and audit logging.

    Args:
        safety_layer: The SafetyLayer instance (from ``CastorFS.safety``).
                      Pass ``None`` only for legacy / hardware-test paths —
                      a warning is logged and the command proceeds unsafely.
        joint:        Joint name, e.g. ``"shoulder_pan"``.
        position:     Target joint position in radians.
        velocity:     Target joint velocity in rad/s (default 0.0).
        principal:    RCAN principal issuing the command (default ``"brain"``).

    Returns:
        ``True`` if the SafetyLayer accepted and wrote the command.
        ``False`` if the command was blocked (e-stop active, bounds
        violation, permission denied, or rate limit exceeded).
    """
    path = f"/dev/arm/{joint}"
    data: dict = {
        "position": position,
        "velocity": velocity,
        "joint": joint,
    }

    if safety_layer is None:
        logger.warning(
            "write_arm_command called without SafetyLayer — "
            "bypassing all safety checks (legacy path). "
            "This is UNSAFE. Provide a SafetyLayer to enforce bounds and auditing."
        )
        # Legacy path: proceed without safety enforcement.
        return True

    # Check e-stop explicitly for /dev/arm paths (SafetyLayer only auto-blocks
    # /dev/motor during estop; arm paths need an explicit guard here).
    if safety_layer.is_estopped:
        logger.warning("write_arm_command blocked on %s: emergency stop is active", path)
        return False

    result = safety_layer.write(path, data, principal=principal)
    if not result:
        logger.debug(
            "write_arm_command denied on %s (principal=%s): %s",
            path,
            principal,
            getattr(safety_layer, "last_write_denial", "unknown reason"),
        )
    return bool(result)

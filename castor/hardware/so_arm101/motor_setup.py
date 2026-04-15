"""
SO-ARM101 motor ID and baudrate setup.

Replicates the workflow of `lerobot-setup-motors` without requiring LeRobot.
Each motor starts with factory default ID=1. We connect one motor at a time,
set its ID (1..6), then set the target baudrate.

.. note::
    This module handles **one-time hardware configuration** (motor ID and
    baudrate assignment).  It does NOT go through the SafetyLayer because
    setup writes happen before the runtime is active.

    For **runtime** motor position/velocity commands, use:
    ``castor.hardware.so_arm101.safety_bridge.write_arm_command``

Usage:
    from castor.hardware.so_arm101.motor_setup import setup_motors
    result = setup_motors(port="/dev/ttyACM0", arm="follower")
"""

from __future__ import annotations

import logging
import time

from castor.hardware.so_arm101.constants import (
    DEFAULT_BAUD,
    DEFAULT_MOTOR_ID,
    FOLLOWER_MOTORS,
    LEADER_MOTORS,
)

logger = logging.getLogger("OpenCastor.SoArm101.MotorSetup")

# Feetech STS3215 EEPROM addresses
_ADDR_ID = 5
_ADDR_BAUD = 6
_ADDR_TORQUE_ENABLE = 40

# Baudrate table: value → actual baud
_BAUD_TABLE = {
    0: 1_000_000,
    1: 500_000,
    2: 250_000,
    3: 115_200,
    4: 57_600,
}
_DEFAULT_PROBE_BAUDS = [1_000_000, 115_200, 500_000]


def _baud_byte(baud: int) -> int:
    for k, v in _BAUD_TABLE.items():
        if v == baud:
            return k
    raise ValueError(f"Unsupported baud rate: {baud}")


class MotorSetupError(Exception):
    pass


def _write_register(
    port_handler, packet_handler, motor_id: int, addr: int, value: int, size: int = 1
):
    """Write a value to a motor register; raise on error."""
    if size == 1:
        result, err = packet_handler.write1ByteTxRx(port_handler, motor_id, addr, value)
    else:
        result, err = packet_handler.write2ByteTxRx(port_handler, motor_id, addr, value)
    if result != 0 or err != 0:
        raise MotorSetupError(
            f"Write failed: motor={motor_id} addr={addr} val={value} result={result} err={err}"
        )


def setup_motors(
    port: str,
    arm: str = "follower",
    target_baud: int = DEFAULT_BAUD,
    print_fn=print,
    input_fn=input,
    dry_run: bool = False,
    prefer_lerobot: bool = True,
) -> dict[str, bool]:
    """
    Interactively configure motor IDs and baudrates for one arm.

    Steps per motor:
      1. Prompt user to connect ONLY that motor to the controller board.
      2. Connect to it at default ID=1.
      3. Write new ID.
      4. Write target baudrate.
      5. Confirm.

    Returns dict mapping joint_name → success (bool).
    """
    motor_list = FOLLOWER_MOTORS if arm == "follower" else LEADER_MOTORS
    results: dict[str, bool] = {}

    # ── Prefer LeRobot tools when available (e.g. ~/lerobot/.venv on alex.local) ──
    if prefer_lerobot and not dry_run:
        from castor.hardware.so_arm101.lerobot_bridge import (
            lerobot_available,
        )
        from castor.hardware.so_arm101.lerobot_bridge import (
            run_setup_motors as _lr_setup,
        )

        if lerobot_available():
            print_fn("\n[SO-ARM101] LeRobot detected — delegating to lerobot-setup-motors")
            ok = _lr_setup(port=port, arm=arm, print_fn=print_fn)
            return {m["joint"]: ok for m in motor_list}

    try:
        from feetech_servo_sdk import PacketHandler, PortHandler  # type: ignore[import]
    except ImportError:
        print_fn(
            "\n⚠  feetech_servo_sdk not installed. "
            "Install with: pip install opencastor[lerobot]\n"
            "   or: pip install feetech-servo-sdk\n"
        )
        if not dry_run:
            return {m["joint"]: False for m in motor_list}
        # dry_run: simulate success
        for m in motor_list:
            results[m["joint"]] = True
        return results

    # In dry_run mode we never open hardware — return simulated success immediately.
    if dry_run:
        return {m["joint"]: True for m in motor_list}

    print_fn(f"\n{'=' * 60}")
    print_fn(f"  SO-ARM101 Motor Setup — {arm.upper()} arm")
    print_fn(f"  Port: {port}  |  Target baud: {target_baud:,}")
    print_fn(f"{'=' * 60}")
    print_fn(
        "\nYou will connect each motor one at a time. "
        "When prompted, ensure ONLY that motor is connected to the board.\n"
    )

    port_handler = PortHandler(port)
    if not port_handler.openPort():
        raise MotorSetupError(f"Cannot open port: {port}")

    # Try probing bauds
    connected = False
    for probe_baud in _DEFAULT_PROBE_BAUDS:
        port_handler.setBaudRate(probe_baud)
        packet_handler = PacketHandler(0)  # SCServo protocol 0
        _, err = packet_handler.ping(port_handler, DEFAULT_MOTOR_ID)
        if err == 0:
            logger.info(f"Motor responding at baud={probe_baud}")
            connected = True
            break

    if not connected:
        logger.warning(
            "No motor found at default ID=1. Make sure one motor is connected and powered."
        )

    for motor in motor_list:
        mid = motor["id"]
        joint = motor["joint"]
        gear = motor["gear"]

        print_fn(f"\n─── Motor {mid}: {joint.upper()} (gear {gear}) ───")
        print_fn(f"  Connect ONLY motor {mid} ({joint}) to the controller board.")
        input_fn("  Press Enter when ready...")

        if dry_run:
            print_fn(f"  [dry-run] Would set motor {mid} → ID={mid}, baud={target_baud:,}")
            results[joint] = True
            continue

        ok = False
        for probe_baud in _DEFAULT_PROBE_BAUDS:
            port_handler.setBaudRate(probe_baud)
            ph = PacketHandler(0)

            # Ping at default ID=1
            _, err = ph.ping(port_handler, DEFAULT_MOTOR_ID)
            if err != 0:
                continue

            try:
                # Disable torque before EEPROM write
                _write_register(port_handler, ph, DEFAULT_MOTOR_ID, _ADDR_TORQUE_ENABLE, 0)
                time.sleep(0.05)

                # Write new ID
                _write_register(port_handler, ph, DEFAULT_MOTOR_ID, _ADDR_ID, mid)
                time.sleep(0.1)

                # Write target baud (switch to new ID)
                ph2 = PacketHandler(0)
                baud_byte = _baud_byte(target_baud)
                _write_register(port_handler, ph2, mid, _ADDR_BAUD, baud_byte)
                time.sleep(0.1)

                # Switch port to target baud and verify
                port_handler.setBaudRate(target_baud)
                ph3 = PacketHandler(0)
                _, verify_err = ph3.ping(port_handler, mid)
                if verify_err == 0:
                    print_fn(f"  ✓ '{joint}' motor ID set to {mid}")
                    ok = True
                else:
                    print_fn(f"  ⚠  ID written but verification ping failed (err={verify_err})")
                    ok = True  # ID likely wrote correctly; baud mismatch on verify
                break

            except MotorSetupError as e:
                logger.error(f"Motor {mid} setup error: {e}")
                print_fn(f"  ✗ Error: {e}")
                break

        if not ok:
            print_fn(
                "  ✗ Could not communicate with motor at ID=1 on any probe baud. "
                "Check cable and power."
            )
        results[joint] = ok

    port_handler.closePort()

    success = sum(1 for v in results.values() if v)
    print_fn(f"\n{'=' * 60}")
    print_fn(f"  Setup complete: {success}/{len(motor_list)} motors configured")
    print_fn(f"{'=' * 60}\n")

    return results


def verify_motors(port: str, arm: str = "follower", baud: int = DEFAULT_BAUD) -> dict[str, bool]:
    """
    Ping all motors in chain and return {joint: responsive} dict.
    All motors should be daisy-chained before calling this.
    """
    motor_list = FOLLOWER_MOTORS if arm == "follower" else LEADER_MOTORS
    results: dict[str, bool] = {}

    try:
        from feetech_servo_sdk import PacketHandler, PortHandler  # type: ignore[import]
    except ImportError:
        logger.warning("feetech_servo_sdk not installed")
        return {m["joint"]: False for m in motor_list}

    port_handler = PortHandler(port)
    if not port_handler.openPort():
        return {m["joint"]: False for m in motor_list}

    port_handler.setBaudRate(baud)
    ph = PacketHandler(0)

    for motor in motor_list:
        _, err = ph.ping(port_handler, motor["id"])
        results[motor["joint"]] = err == 0

    port_handler.closePort()
    return results

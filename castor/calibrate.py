"""
OpenCastor Calibration -- interactive servo/motor calibration.

Allows users to nudge servo positions to find center points and
ranges, then saves the calibrated offsets back to the RCAN config.

Usage:
    castor calibrate --config robot.rcan.yaml
"""

import logging

import yaml

logger = logging.getLogger("OpenCastor.Calibrate")


def _load_config(config_path: str) -> dict:
    """Load the RCAN config."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def _save_config(config_path: str, config: dict):
    """Write updated config back to file."""
    with open(config_path, "w") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False)


def run_calibration(config_path: str):
    """Run interactive calibration for the configured driver.

    Supports PCA9685 RC drivers (steering servo + ESC center calibration).
    """
    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False

    config = _load_config(config_path)
    robot_name = config.get("metadata", {}).get("robot_name", "Robot")
    drivers = config.get("drivers", [])

    if not drivers:
        print("  No drivers configured. Nothing to calibrate.")
        return

    driver_config = drivers[0]
    protocol = driver_config.get("protocol", "")

    if has_rich:
        console.print()
        console.print(
            Panel.fit(
                f"[bold cyan]Calibration: {robot_name}[/]\n"
                f"Driver: {protocol}\n\n"
                "Use +/- to adjust values, Enter to confirm, q to quit.",
                border_style="cyan",
            )
        )
    else:
        print(f"\n  Calibration: {robot_name}")
        print(f"  Driver: {protocol}")
        print("  Use +/- to adjust values, Enter to confirm, q to quit.\n")

    if "pca9685_rc" in protocol:
        _calibrate_rc(config_path, config, driver_config, has_rich)
    elif "pca9685" in protocol:
        _calibrate_differential(config_path, config, driver_config, has_rich)
    elif "dynamixel" in protocol:
        _calibrate_dynamixel(config_path, config, driver_config, has_rich)
    else:
        print(f"  Calibration not yet supported for protocol: {protocol}")
        print("  Supported: pca9685_rc, pca9685_i2c, dynamixel")


def _calibrate_rc(config_path, config, driver_config, has_rich):
    """Calibrate RC car steering center and throttle neutral."""
    params = [
        ("steering_center_us", "Steering Center", 1500, 10),
        ("steering_range_us", "Steering Range", 500, 25),
        ("throttle_neutral_us", "Throttle Neutral", 1500, 10),
    ]

    changes = {}
    for key, label, default, step in params:
        current = driver_config.get(key, default)
        new_val = _interactive_adjust(label, current, step, has_rich)
        if new_val != current:
            changes[key] = new_val
            driver_config[key] = new_val

    if changes:
        _save_config(config_path, config)
        print(f"\n  Saved {len(changes)} change(s) to {config_path}:")
        for k, v in changes.items():
            print(f"    {k}: {v}")
    else:
        print("\n  No changes made.")
    print()


def _calibrate_differential(config_path, config, driver_config, has_rich):
    """Calibrate differential drive (frequency, etc.)."""
    params = [
        ("frequency", "PWM Frequency (Hz)", 50, 5),
    ]

    changes = {}
    for key, label, default, step in params:
        current = driver_config.get(key, default)
        new_val = _interactive_adjust(label, current, step, has_rich)
        if new_val != current:
            changes[key] = new_val
            driver_config[key] = new_val

    if changes:
        _save_config(config_path, config)
        print(f"\n  Saved {len(changes)} change(s) to {config_path}:")
        for k, v in changes.items():
            print(f"    {k}: {v}")
    else:
        print("\n  No changes made.")
    print()


def _calibrate_dynamixel(config_path, config, driver_config, has_rich):
    """Calibrate Dynamixel servo positions."""
    print("  Dynamixel calibration: adjust baud rate and protocol version.")

    params = [
        ("baud_rate", "Baud Rate", 115200, 9600),
    ]

    changes = {}
    for key, label, default, step in params:
        current = driver_config.get(key, default)
        new_val = _interactive_adjust(label, current, step, has_rich)
        if new_val != current:
            changes[key] = new_val
            driver_config[key] = new_val

    if changes:
        _save_config(config_path, config)
        print(f"\n  Saved {len(changes)} change(s) to {config_path}:")
        for k, v in changes.items():
            print(f"    {k}: {v}")
    else:
        print("\n  No changes made.")
    print()


def _interactive_adjust(label: str, current_value, step, has_rich: bool):
    """Prompt the user to adjust a value with +/- keys.

    Args:
        label: Display name for the parameter.
        current_value: Starting value.
        step: Increment/decrement amount per keystroke.
        has_rich: Whether rich is available.

    Returns:
        The final adjusted value.
    """
    value = current_value
    print(f"\n  {label}")
    print(f"  Current: {value}  (step: +/-{step})")
    print("  Commands: + (increase), - (decrease), Enter (confirm), r (reset)")

    while True:
        try:
            cmd = input(f"  [{value}] > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return current_value

        if cmd == "" or cmd == "enter":
            return value
        elif cmd == "+" or cmd == "u":
            value += step
            print(f"    -> {value}")
        elif cmd == "-" or cmd == "d":
            value -= step
            print(f"    -> {value}")
        elif cmd == "r":
            value = current_value
            print(f"    -> {value} (reset)")
        elif cmd == "q":
            return current_value
        else:
            # Try to parse as a direct value
            try:
                value = type(current_value)(cmd)
                print(f"    -> {value} (direct)")
            except (ValueError, TypeError):
                print("    Use +, -, Enter, r (reset), or type a value directly")


# ---------------------------------------------------------------------------
# Issue #235 — Servo/gripper calibration wizard
# ---------------------------------------------------------------------------

HAS_PCA9685 = False
try:
    HAS_PCA9685 = True
except Exception:
    pass


_SERVO_DEFAULTS = {
    "min_us": 500,
    "max_us": 2500,
    "centre_us": 1500,
}


def calibrate_servo(
    config_path: str,
    channel: int = 0,
    board_type: str = "pca9685",
    gripper_mode: bool = False,
    mock: bool = False,
) -> dict:
    """Interactive servo/gripper calibration wizard.

    Sweeps the servo from min to max, prompts the user to confirm
    endpoints, finds the centre position, and writes calibrated
    min_us/max_us/centre_us back to the RCAN config file.

    Args:
        config_path: Path to the RCAN ``.yaml`` config file.
        channel:     PCA9685 channel number (0–15).
        board_type:  ``"pca9685"`` or ``"arduino"`` (serial servo pin).
        gripper_mode: If True, prompt for open/close positions.
        mock:        Run in mock mode (no hardware access).

    Returns:
        Dict with ``{"min_us": int, "max_us": int, "centre_us": int, "saved": bool}``.
    """
    config = _load_config(config_path)

    # Resolve current values from config
    servo_cfg = config.get("servo", {})
    min_us: int = int(servo_cfg.get("min_us", _SERVO_DEFAULTS["min_us"]))
    max_us: int = int(servo_cfg.get("max_us", _SERVO_DEFAULTS["max_us"]))
    centre_us: int = int(servo_cfg.get("centre_us", _SERVO_DEFAULTS["centre_us"]))

    if not mock and not HAS_PCA9685 and board_type == "pca9685":
        mock = True
        print("  [WARN] adafruit_pca9685 not installed — running in mock mode.")

    def _set_servo(us: int) -> None:
        """Send pulse width to servo hardware (or mock)."""
        if mock:
            print(f"  [mock] servo ch={channel} → {us}µs")
            return
        try:
            import board
            import busio
            from adafruit_pca9685 import PCA9685

            i2c = busio.I2C(board.SCL, board.SDA)
            pca = PCA9685(i2c)
            pca.frequency = 50
            duty = int(us / 20_000 * 65535)
            pca.channels[channel].duty_cycle = duty
            pca.deinit()
        except Exception as e:
            print(f"  [WARN] Hardware write failed: {e}")

    print(f"\n  Servo Calibration Wizard — channel {channel} ({board_type})")
    print(f"  Current: min={min_us}µs  max={max_us}µs  centre={centre_us}µs")
    print()

    # Step 1: Sweep from min to max
    print("  Step 1/4 — Sweeping servo from minimum to maximum position …")
    _set_servo(min_us)
    input("  Press Enter when servo is at MINIMUM position …")
    new_min_s = input(f"  Enter new min_us [{min_us}]: ").strip()
    if new_min_s:
        min_us = int(new_min_s)

    _set_servo(max_us)
    input("  Press Enter when servo is at MAXIMUM position …")
    new_max_s = input(f"  Enter new max_us [{max_us}]: ").strip()
    if new_max_s:
        max_us = int(new_max_s)

    # Step 2: Centre
    print("\n  Step 2/4 — Finding centre position …")
    centre_us = (min_us + max_us) // 2
    _set_servo(centre_us)
    new_centre_s = input(f"  Enter centre_us [{centre_us}]: ").strip()
    if new_centre_s:
        centre_us = int(new_centre_s)
    print(f"  Centre confirmed: {centre_us}µs")

    # Step 3: Gripper open/close (optional)
    open_us: int = max_us
    close_us: int = min_us
    if gripper_mode:
        print("\n  Step 3/4 — Gripper mode: set open/close positions …")
        _set_servo(max_us)
        new_open_s = input(f"  Open position µs [{max_us}]: ").strip()
        if new_open_s:
            open_us = int(new_open_s)

        _set_servo(min_us)
        new_close_s = input(f"  Close position µs [{min_us}]: ").strip()
        if new_close_s:
            close_us = int(new_close_s)
    else:
        print("\n  Step 3/4 — Skipped (not gripper mode).")

    # Step 4: Test grip action
    print("\n  Step 4/4 — Test action …")
    _set_servo(open_us)
    input("  Press Enter to confirm OPEN position …")
    _set_servo(close_us)
    input("  Press Enter to confirm CLOSE/CENTRE position …")
    _set_servo(centre_us)

    # Write calibrated values back to RCAN config
    if "servo" not in config:
        config["servo"] = {}
    config["servo"]["channel"] = channel
    config["servo"]["board"] = board_type
    config["servo"]["min_us"] = min_us
    config["servo"]["max_us"] = max_us
    config["servo"]["centre_us"] = centre_us
    if gripper_mode:
        config["servo"]["open_us"] = open_us
        config["servo"]["close_us"] = close_us

    _save_config(config_path, config)
    print(
        f"\n  Calibration saved → {config_path}\n"
        f"    min_us={min_us}  max_us={max_us}  centre_us={centre_us}"
    )

    return {
        "min_us": min_us,
        "max_us": max_us,
        "centre_us": centre_us,
        "open_us": open_us,
        "close_us": close_us,
        "saved": True,
    }


def servo_pulse_duty(pulse_us: int, frequency: int = 50) -> int:
    """Convert pulse width in microseconds to PCA9685 duty cycle value (0–65535).

    Args:
        pulse_us:  Servo pulse width in microseconds (e.g. 1500).
        frequency: PWM frequency in Hz (typically 50 for servo).

    Returns:
        16-bit duty cycle value for PCA9685.
    """
    period_us = 1_000_000 / frequency
    return max(0, min(65535, int(pulse_us / period_us * 65535)))


def validate_servo_config(min_us: int, max_us: int, centre_us: int) -> list[str]:
    """Validate servo calibration values.

    Args:
        min_us:    Minimum pulse width.
        max_us:    Maximum pulse width.
        centre_us: Centre pulse width.

    Returns:
        List of error strings (empty if valid).
    """
    errors: list[str] = []
    if min_us < 400:
        errors.append(f"min_us={min_us} is below safe minimum (400µs)")
    if max_us > 2600:
        errors.append(f"max_us={max_us} exceeds safe maximum (2600µs)")
    if min_us >= max_us:
        errors.append(f"min_us ({min_us}) must be less than max_us ({max_us})")
    if not (min_us <= centre_us <= max_us):
        errors.append(f"centre_us ({centre_us}) must be between min_us and max_us")
    return errors

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
        console.print(Panel.fit(
            f"[bold cyan]Calibration: {robot_name}[/]\n"
            f"Driver: {protocol}\n\n"
            "Use +/- to adjust values, Enter to confirm, q to quit.",
            border_style="cyan",
        ))
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

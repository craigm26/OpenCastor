"""
OpenCastor Hardware Test -- wiggle each motor/servo one at a time.

Loads an RCAN config, initializes the driver, and tests each
actuator individually with user confirmation per step.

Usage:
    castor test-hardware --config robot.rcan.yaml
"""

import logging
import time

import yaml

logger = logging.getLogger("OpenCastor.TestHardware")


def _load_config(config_path: str) -> dict:
    """Load and return the RCAN config."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def _get_test_sequence(config: dict) -> list:
    """Build a list of test steps based on driver config.

    Each step is a dict: ``{"name": str, "action": dict, "duration": float}``.
    """
    steps = []
    drivers = config.get("drivers", [])
    if not drivers:
        return steps

    driver = drivers[0]
    protocol = driver.get("protocol", "")

    if "pca9685_rc" in protocol:
        # RC car: test steering left, center, right, then throttle
        steps = [
            {
                "name": "Steering LEFT",
                "action": {"type": "move", "linear": 0.0, "angular": -0.5},
                "duration": 1.0,
            },
            {
                "name": "Steering CENTER",
                "action": {"type": "move", "linear": 0.0, "angular": 0.0},
                "duration": 0.5,
            },
            {
                "name": "Steering RIGHT",
                "action": {"type": "move", "linear": 0.0, "angular": 0.5},
                "duration": 1.0,
            },
            {
                "name": "Steering CENTER",
                "action": {"type": "move", "linear": 0.0, "angular": 0.0},
                "duration": 0.5,
            },
            {
                "name": "Throttle FORWARD (slow)",
                "action": {"type": "move", "linear": 0.15, "angular": 0.0},
                "duration": 1.0,
            },
            {"name": "Throttle STOP", "action": {"type": "stop"}, "duration": 0.5},
            {
                "name": "Throttle REVERSE (slow)",
                "action": {"type": "move", "linear": -0.15, "angular": 0.0},
                "duration": 1.0,
            },
            {"name": "Throttle STOP", "action": {"type": "stop"}, "duration": 0.5},
        ]
    elif "pca9685" in protocol:
        # Differential drive: test each side
        steps = [
            {
                "name": "Left motors FORWARD",
                "action": {"type": "move", "linear": 0.2, "angular": -0.3},
                "duration": 1.0,
            },
            {"name": "STOP", "action": {"type": "stop"}, "duration": 0.5},
            {
                "name": "Right motors FORWARD",
                "action": {"type": "move", "linear": 0.2, "angular": 0.3},
                "duration": 1.0,
            },
            {"name": "STOP", "action": {"type": "stop"}, "duration": 0.5},
            {
                "name": "Both motors FORWARD",
                "action": {"type": "move", "linear": 0.2, "angular": 0.0},
                "duration": 1.0,
            },
            {"name": "STOP", "action": {"type": "stop"}, "duration": 0.5},
        ]
    elif "dynamixel" in protocol:
        # Dynamixel: test each servo position
        steps = [
            {
                "name": "Servo sweep FORWARD",
                "action": {"type": "move", "linear": 0.1, "angular": 0.0},
                "duration": 1.5,
            },
            {"name": "STOP", "action": {"type": "stop"}, "duration": 0.5},
            {
                "name": "Servo sweep LEFT",
                "action": {"type": "move", "linear": 0.0, "angular": -0.3},
                "duration": 1.5,
            },
            {"name": "STOP", "action": {"type": "stop"}, "duration": 0.5},
            {
                "name": "Servo sweep RIGHT",
                "action": {"type": "move", "linear": 0.0, "angular": 0.3},
                "duration": 1.5,
            },
            {"name": "STOP", "action": {"type": "stop"}, "duration": 0.5},
        ]
    else:
        # Generic: basic forward/stop
        steps = [
            {
                "name": "FORWARD (slow)",
                "action": {"type": "move", "linear": 0.15, "angular": 0.0},
                "duration": 1.0,
            },
            {"name": "STOP", "action": {"type": "stop"}, "duration": 0.5},
            {
                "name": "TURN LEFT",
                "action": {"type": "move", "linear": 0.0, "angular": -0.3},
                "duration": 1.0,
            },
            {"name": "STOP", "action": {"type": "stop"}, "duration": 0.5},
        ]

    return steps


def run_test(config_path: str, skip_confirm: bool = False):
    """Run the interactive hardware test.

    Args:
        config_path: Path to the RCAN config file.
        skip_confirm: If True, run all steps without prompting.
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

    if has_rich:
        console.print()
        console.print(
            Panel.fit(
                f"[bold cyan]Hardware Test: {robot_name}[/]\n"
                "Each motor/servo will be tested individually.\n"
                "Keep hands clear of moving parts!",
                border_style="yellow",
            )
        )
    else:
        print(f"\n  Hardware Test: {robot_name}")
        print("  Each motor/servo will be tested individually.")
        print("  Keep hands clear of moving parts!\n")

    # Initialize driver
    from castor.main import get_driver

    driver = get_driver(config)
    if driver is None:
        msg = "No hardware driver could be initialized. Check your wiring and config."
        if has_rich:
            console.print(f"\n[bold red]{msg}[/]\n")
        else:
            print(f"\n  ERROR: {msg}\n")
        return False

    steps = _get_test_sequence(config)
    if not steps:
        msg = "No test sequence available for this driver configuration."
        if has_rich:
            console.print(f"\n[bold yellow]{msg}[/]\n")
        else:
            print(f"\n  {msg}\n")
        return False

    passed = 0
    failed = 0

    try:
        for i, step in enumerate(steps, 1):
            if has_rich:
                console.print(f"\n  [bold]Step {i}/{len(steps)}:[/] {step['name']}")
            else:
                print(f"\n  Step {i}/{len(steps)}: {step['name']}")

            if not skip_confirm:
                response = (
                    input("  Press Enter to execute (or 's' to skip, 'q' to quit): ")
                    .strip()
                    .lower()
                )
                if response == "q":
                    print("  Test aborted by user.")
                    break
                if response == "s":
                    print("  Skipped.")
                    continue

            # Execute the action
            action = step["action"]
            try:
                if action["type"] == "move":
                    driver.move(action.get("linear", 0.0), action.get("angular", 0.0))
                elif action["type"] == "stop":
                    driver.stop()

                time.sleep(step["duration"])
                driver.stop()

                if has_rich:
                    console.print(f"  [green]OK[/] -- {step['name']} completed")
                else:
                    print(f"  [OK] {step['name']} completed")
                passed += 1

            except Exception as exc:
                if has_rich:
                    console.print(f"  [red]FAIL[/] -- {exc}")
                else:
                    print(f"  [FAIL] {exc}")
                failed += 1

    finally:
        driver.stop()
        driver.close()

    # Summary
    if has_rich:
        status = (
            "[green]All tests passed!"
            if failed == 0
            else f"[yellow]{passed} passed, {failed} failed"
        )
        console.print(f"\n  {status}[/]\n")
    else:
        print(f"\n  {passed} passed, {failed} failed\n")

    return failed == 0

"""
OpenCastor REPL -- Python REPL with robot objects pre-loaded.

Drops into an interactive Python session with ``brain``, ``driver``,
``camera``, and ``config`` available as globals.

Usage:
    castor repl --config robot.rcan.yaml

    >>> brain.think(camera.capture_jpeg(), "what do you see?")
    >>> driver.move(0.3, 0.0)
    >>> driver.stop()
"""

import code
import logging

import yaml

logger = logging.getLogger("OpenCastor.REPL")


def launch_repl(config_path: str):
    """Initialize hardware and drop into a Python REPL."""
    with open(config_path) as f:
        config = yaml.safe_load(f)

    robot_name = config.get("metadata", {}).get("robot_name", "Robot")
    print(f"\n  OpenCastor REPL -- {robot_name}")
    print("  Initializing...\n")

    namespace = {"config": config}

    # Initialize brain
    try:
        from castor.providers import get_provider
        brain = get_provider(config["agent"])
        namespace["brain"] = brain
        print(f"  brain    = {config['agent'].get('provider')}/{config['agent'].get('model')}")
    except Exception as e:
        print(f"  brain    = None ({e})")
        namespace["brain"] = None

    # Initialize driver
    try:
        from castor.main import get_driver
        driver = get_driver(config)
        namespace["driver"] = driver
        if driver:
            protocol = config.get("drivers", [{}])[0].get("protocol", "?")
            print(f"  driver   = {protocol}")
        else:
            print("  driver   = None (no hardware)")
    except Exception as e:
        print(f"  driver   = None ({e})")
        namespace["driver"] = None

    # Initialize camera
    try:
        from castor.main import Camera
        camera = Camera(config)
        namespace["camera"] = camera
        print(f"  camera   = {'online' if camera.is_available() else 'offline'}")
    except Exception as e:
        print(f"  camera   = None ({e})")
        namespace["camera"] = None

    # Initialize speaker
    try:
        from castor.main import Speaker
        speaker = Speaker(config)
        namespace["speaker"] = speaker
        print(f"  speaker  = {'online' if speaker.enabled else 'offline'}")
    except Exception:
        namespace["speaker"] = None

    # Convenience helpers
    def look(instruction="Describe what you see."):
        """Capture + think in one call."""
        cam = namespace.get("camera")
        b = namespace.get("brain")
        if not b:
            print("No brain available.")
            return None
        frame = cam.capture_jpeg() if cam else b"\x00" * 1024
        return b.think(frame, instruction)

    def move(linear=0.0, angular=0.0):
        """Move the robot."""
        d = namespace.get("driver")
        if d:
            d.move(linear, angular)
        else:
            print(f"[MOCK] move({linear}, {angular})")

    def stop():
        """Stop all motors."""
        d = namespace.get("driver")
        if d:
            d.stop()
        else:
            print("[MOCK] stop()")

    namespace["look"] = look
    namespace["move"] = move
    namespace["stop"] = stop

    print("\n  Helpers: look('what?'), move(linear, angular), stop()")
    print("  Type help(brain), help(driver), etc. for API docs.\n")

    banner = f"OpenCastor REPL ({robot_name}) -- Ctrl+D to exit"

    try:
        code.interact(banner=banner, local=namespace, exitmsg="  Goodbye.\n")
    finally:
        driver = namespace.get("driver")
        camera = namespace.get("camera")
        speaker = namespace.get("speaker")
        if driver:
            driver.stop()
            driver.close()
        if camera:
            camera.close()
        if speaker:
            speaker.close()

"""
OpenCastor Runtime - The main entry point.
Ties Brain (Provider), Body (Driver), and Law (RCAN Config) together.
"""

import time
import argparse
import logging

import yaml

from castor.providers import get_provider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("OpenCastor")


def load_config(path: str) -> dict:
    """Loads and validates the RCAN configuration."""
    try:
        with open(path, "r") as f:
            config = yaml.safe_load(f)
            logger.info(f"Loaded Configuration: {config['metadata']['robot_name']}")
            return config
    except FileNotFoundError:
        logger.error(f"Config file not found: {path}")
        raise SystemExit(1)


def get_driver(config: dict):
    """Initialize the appropriate driver based on config."""
    if not config.get("drivers"):
        return None

    driver_config = config["drivers"][0]
    protocol = driver_config.get("protocol", "")

    if "pca9685" in protocol:
        from castor.drivers.pca9685 import PCA9685Driver

        return PCA9685Driver(driver_config)
    elif "dynamixel" in protocol:
        from castor.drivers.dynamixel import DynamixelDriver

        return DynamixelDriver(driver_config)
    else:
        logger.warning(f"Unknown driver protocol: {protocol}. Running without hardware.")
        return None


def main():
    parser = argparse.ArgumentParser(description="OpenCastor Runtime")
    parser.add_argument(
        "--config",
        type=str,
        default="robot.rcan.yaml",
        help="Path to RCAN config file",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run without physical hardware",
    )
    args = parser.parse_args()

    # 1. BOOT SEQUENCE
    logger.info("Booting OpenCastor Runtime...")
    config = load_config(args.config)

    # 2. INITIALIZE BRAIN
    try:
        brain = get_provider(config["agent"])
        logger.info(f"Brain Online: {config['agent'].get('model', 'unknown')}")
    except Exception as e:
        logger.critical(f"Failed to initialize Brain: {e}")
        raise SystemExit(1)

    # 3. INITIALIZE BODY (Drivers)
    driver = None
    if not args.simulate:
        try:
            driver = get_driver(config)
            if driver:
                logger.info("Hardware Online")
        except Exception as e:
            logger.error(f"Hardware Init Failed: {e}. Switching to Simulation.")
            args.simulate = True

    # 4. INITIALIZE EYES (Camera)
    cap = None
    try:
        import cv2

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            logger.warning("No camera detected. Using blank frames.")
            cap = None
    except ImportError:
        logger.warning("OpenCV not available. Using blank frames.")

    # 5. THE CONTROL LOOP
    latency_budget = config.get("agent", {}).get("latency_budget_ms", 200)
    logger.info("Entering Perception-Action Loop. Press Ctrl+C to stop.")

    try:
        while True:
            loop_start = time.time()

            # --- PHASE 1: OBSERVE ---
            if cap is not None:
                ret, frame = cap.read()
                if ret:
                    import cv2

                    _, buffer = cv2.imencode(".jpg", frame)
                    frame_bytes = buffer.tobytes()
                else:
                    frame_bytes = b"\x00" * 1024
            else:
                frame_bytes = b"\x00" * 1024

            # --- PHASE 2: ORIENT & DECIDE ---
            instruction = "Scan the area and report what you see."
            thought = brain.think(frame_bytes, instruction)

            # --- PHASE 3: ACT ---
            if thought.action:
                logger.info(f"Action: {thought.action}")

                if driver and not args.simulate:
                    action_type = thought.action.get("type", "")
                    if action_type == "move":
                        linear = thought.action.get("linear", 0.0)
                        angular = thought.action.get("angular", 0.0)
                        driver.move(linear, angular)
                    elif action_type == "stop":
                        driver.stop()
            else:
                logger.warning("Brain produced no valid action.")

            # --- PHASE 4: TELEMETRY & LATENCY CHECK ---
            latency = (time.time() - loop_start) * 1000
            if latency > latency_budget:
                logger.warning(
                    f"Loop Lag: {latency:.2f}ms (Budget: {latency_budget}ms)"
                )

            # Sleep to prevent API rate limiting
            time.sleep(1.0)

    except KeyboardInterrupt:
        logger.info("User Interrupt. Shutting down...")
    finally:
        if driver and not args.simulate:
            logger.info("Parking hardware...")
            driver.close()
        if cap is not None:
            cap.release()
        logger.info("OpenCastor Offline.")


if __name__ == "__main__":
    main()

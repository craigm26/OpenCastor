import logging

from .base import DriverBase as DriverBase

logger = logging.getLogger("OpenCastor.Drivers")


def get_driver(config: dict):
    """Initialize the appropriate driver based on RCAN config.

    Supports protocol-based lookup for built-in drivers and fully-qualified
    class paths (``class`` key) for external/plugin drivers.
    """
    if not config.get("drivers"):
        return None

    driver_config = config["drivers"][0]
    protocol = driver_config.get("protocol", "")

    # External driver via fully-qualified class path (issue #33 / #20)
    fq_class = driver_config.get("class", "")
    if fq_class:
        try:
            module_path, class_name = fq_class.rsplit(".", 1)
            import importlib

            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            return cls(driver_config)
        except Exception as exc:
            logger.warning(f"Failed to load external driver class '{fq_class}': {exc}")
            return None

    if protocol == "composite":
        from castor.drivers.composite import CompositeDriver

        return CompositeDriver(config)
    elif protocol == "pca9685_rc":
        from castor.drivers.pca9685 import PCA9685RCDriver

        return PCA9685RCDriver(driver_config)
    elif "pca9685" in protocol:
        from castor.drivers.pca9685 import PCA9685Driver

        return PCA9685Driver(driver_config)
    elif "dynamixel" in protocol:
        from castor.drivers.dynamixel import DynamixelDriver

        return DynamixelDriver(driver_config)
    elif protocol in ("simulation", "gazebo", "webots"):
        from castor.drivers.simulation_driver import SimulationDriver

        return SimulationDriver(driver_config)
    else:
        logger.warning(f"Unknown driver protocol: {protocol}. Running without hardware.")
        return None

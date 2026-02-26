import logging

from .base import DriverBase as DriverBase

logger = logging.getLogger("OpenCastor.Drivers")

_EXACT_PROTOCOLS = {
    "composite",
    "pca9685_rc",
    "simulation",
    "gazebo",
    "webots",
    "gpio",
    "stepper",
    "odrive",
    "vesc",
    "imu",
    "lidar",
    "esp32_websocket",
    "ev3dev_tacho_motor",
    "ev3dev_sensor",
    "spike_hub_serial",
    "spike_hub_internal",
}


def is_supported_protocol(protocol: str) -> bool:
    """Return True when *protocol* maps to a built-in driver."""
    proto = (protocol or "").lower()
    if proto in _EXACT_PROTOCOLS:
        return True
    if "pca9685" in proto:
        return True
    if "dynamixel" in proto:
        return True
    return False


def get_driver(config: dict):
    """Initialize the appropriate driver based on RCAN config.

    Supports protocol-based lookup for built-in drivers and fully-qualified
    class paths (``class`` key) for external/plugin drivers.
    """
    drivers = config.get("drivers") or []
    if not drivers:
        return None

    driver_config = None
    for candidate in drivers:
        enabled_value = candidate.get("enabled", True)
        if isinstance(enabled_value, str):
            enabled = enabled_value.strip().lower() not in {"0", "false", "no", "off"}
        else:
            enabled = bool(enabled_value)
        if enabled:
            driver_config = candidate
            break

    if driver_config is None:
        logger.warning("No enabled driver entries found. Running without hardware.")
        return None

    protocol = str(driver_config.get("protocol", "")).lower()

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
    elif protocol == "gpio":
        from castor.drivers.gpio_driver import GPIODriver

        return GPIODriver(driver_config)
    elif protocol == "stepper":
        from castor.drivers.stepper_driver import StepperDriver

        return StepperDriver(driver_config)
    elif protocol in ("odrive", "vesc"):
        from castor.drivers.odrive_driver import ODriveDriver

        return ODriveDriver(driver_config)
    elif protocol == "imu":
        from castor.drivers.imu_driver import IMUDriver

        return IMUDriver(
            bus=int(driver_config.get("i2c_bus", 1)),
            address=int(driver_config["i2c_address"], 16)
            if "i2c_address" in driver_config
            else None,
            model=driver_config.get("model", "auto"),
        )
    elif protocol == "lidar":
        from castor.drivers.lidar_driver import LidarDriver

        return LidarDriver(
            port=driver_config.get("port"),
            baud=driver_config.get("baud"),
            timeout=driver_config.get("timeout"),
        )
    elif protocol == "esp32_websocket":
        from castor.drivers.esp32_websocket import ESP32WebsocketDriver

        return ESP32WebsocketDriver(driver_config)
    elif protocol in ("ev3dev_tacho_motor", "ev3dev_sensor"):
        from castor.drivers.ev3dev_driver import EV3DevDriver

        return EV3DevDriver(config)
    elif protocol in ("spike_hub_serial", "spike_hub_internal"):
        from castor.drivers.spike_driver import SpikeHubDriver

        return SpikeHubDriver(config)
    else:
        logger.warning(f"Unknown driver protocol: {protocol}. Running without hardware.")
        return None

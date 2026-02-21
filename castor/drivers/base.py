from abc import ABC, abstractmethod
from typing import Dict

__all__ = ["DriverBase"]


class DriverBase(ABC):
    """Abstract base class for all hardware drivers.

    Subclasses must implement ``move()``, ``stop()``, and ``close()``.
    When the underlying hardware SDK is unavailable, drivers should degrade
    gracefully to a mock/logging mode rather than raising import errors.
    """

    def health_check(self) -> Dict:
        """Check whether the hardware is accessible and responsive.

        Returns a dict with keys:
            ``ok``    — True if the hardware is reachable.
            ``mode``  — "hardware" if real hardware is active, "mock" otherwise.
            ``error`` — Error message string, or None on success.

        The default implementation returns ``{"ok": False, "mode": "mock", "error": None}``.
        Override in concrete drivers to probe the actual hardware.
        """
        return {"ok": False, "mode": "mock", "error": None}

    @abstractmethod
    def move(self, linear: float = 0.0, angular: float = 0.0) -> None:
        """Send a velocity command to the robot.

        Args:
            linear: Forward/backward speed (range depends on driver, typically -1.0 to 1.0).
            angular: Turning rate (range depends on driver, typically -1.0 to 1.0).
        """

    @abstractmethod
    def stop(self) -> None:
        """Immediately halt all motors."""

    @abstractmethod
    def close(self) -> None:
        """Release hardware resources (serial ports, I2C buses, etc.)."""

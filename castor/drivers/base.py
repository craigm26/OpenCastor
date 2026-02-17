from abc import ABC, abstractmethod

__all__ = ["DriverBase"]


class DriverBase(ABC):
    """Abstract base class for all hardware drivers.

    Subclasses must implement ``move()``, ``stop()``, and ``close()``.
    When the underlying hardware SDK is unavailable, drivers should degrade
    gracefully to a mock/logging mode rather than raising import errors.
    """

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

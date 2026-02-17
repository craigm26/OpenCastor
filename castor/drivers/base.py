from abc import ABC, abstractmethod


class DriverBase(ABC):
    """Abstract base class for all hardware drivers."""

    @abstractmethod
    def move(self, *args, **kwargs):
        pass

    @abstractmethod
    def stop(self):
        pass

    @abstractmethod
    def close(self):
        pass

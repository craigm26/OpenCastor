"""Tests for castor.drivers -- DriverBase abstract interface."""

import pytest

from castor.drivers.base import DriverBase


class TestDriverBase:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            DriverBase()

    def test_concrete_subclass(self):
        class MockDriver(DriverBase):
            def __init__(self):
                self.moved = False
                self.stopped = False
                self.closed = False

            def move(self, linear=0, angular=0):
                self.moved = True

            def stop(self):
                self.stopped = True

            def close(self):
                self.closed = True

        driver = MockDriver()
        driver.move(linear=0.5, angular=0.1)
        assert driver.moved

        driver.stop()
        assert driver.stopped

        driver.close()
        assert driver.closed

    def test_partial_implementation_fails(self):
        """Subclass missing 'close' should fail to instantiate."""

        class IncompleteDriver(DriverBase):
            def move(self):
                pass

            def stop(self):
                pass

        with pytest.raises(TypeError):
            IncompleteDriver()

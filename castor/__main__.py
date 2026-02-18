"""Allow running OpenCastor as ``python -m castor``."""

from castor.cli import _friendly_error_handler

if __name__ == "__main__":
    _friendly_error_handler()

"""OpenCastor: The Universal Runtime for Embodied AI."""

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("opencastor")
except Exception:
    __version__ = "2026.3.13.9"  # fallback

__all__ = ["__version__"]

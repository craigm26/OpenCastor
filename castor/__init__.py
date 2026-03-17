"""OpenCastor: The Universal Runtime for Embodied AI."""

from __future__ import annotations

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("opencastor")
except Exception:
    __version__ = "2026.3.17.4"  # fallback


def initialize_safety(safety_layer, config: dict):
    """Initialize and wire the full Protocol 66 safety stack.

    Creates a :class:`~castor.safety.monitor.SensorMonitor` from the
    ``config["monitor"]`` section and connects it to *safety_layer* via
    :func:`~castor.safety.monitor.wire_safety_layer`.  Call this once during
    runtime startup, then call ``monitor.start()`` to begin polling.

    Args:
        safety_layer: A :class:`~castor.fs.safety.SafetyLayer` (or
                      ``CastorFS.safety``) instance.
        config:       Runtime config dict (uses ``config["monitor"]`` sub-key).

    Returns:
        The started :class:`~castor.safety.monitor.SensorMonitor` instance.
    """
    from castor.safety.monitor import MonitorThresholds, SensorMonitor, wire_safety_layer

    monitor_cfg = config.get("monitor", {})
    thresholds_cfg = monitor_cfg.get("thresholds", {})
    thresholds = MonitorThresholds(**thresholds_cfg) if thresholds_cfg else None
    monitor = SensorMonitor(
        thresholds=thresholds,
        interval=float(monitor_cfg.get("interval", 5.0)),
        consecutive_critical=int(monitor_cfg.get("consecutive_critical", 3)),
    )
    wire_safety_layer(monitor, safety_layer)
    return monitor


__all__ = ["__version__", "initialize_safety"]

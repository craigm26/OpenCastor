"""
castor/metrics.py — Prometheus-compatible metrics registry.

Lightweight stdlib-only metrics collection (no prometheus_client dependency).
Exposes counters, gauges, and histograms in Prometheus text format via
``GET /api/metrics``.

Usage::

    from castor.metrics import get_registry

    reg = get_registry()
    reg.counter("opencastor_loops_total", labels={"robot": "bob"}).inc()
    reg.gauge("opencastor_uptime_seconds", labels={"robot": "bob"}).set(120.5)
    print(reg.render())        # Prometheus text format
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Dict, Optional, Tuple

__all__ = ["MetricsRegistry", "get_registry"]

_LabelKey = Tuple[str, ...]   # sorted label kv pairs as tuple


class Counter:
    """Monotonically increasing counter."""

    def __init__(self, name: str, help_text: str, label_names: tuple):
        self._name = name
        self._help = help_text
        self._label_names = label_names
        self._values: Dict[_LabelKey, float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, **labels) -> None:
        key = self._make_key(labels)
        with self._lock:
            self._values[key] += amount

    def _make_key(self, labels: dict) -> _LabelKey:
        return tuple(sorted((k, str(v)) for k, v in labels.items()))

    def render(self) -> str:
        lines = [f"# HELP {self._name} {self._help}", f"# TYPE {self._name} counter"]
        with self._lock:
            for key, val in self._values.items():
                label_str = self._fmt_labels(key)
                lines.append(f"{self._name}{label_str} {val:.0f}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_labels(key: _LabelKey) -> str:
        if not key:
            return ""
        parts = ",".join(f'{k}="{v}"' for k, v in key)
        return "{" + parts + "}"


class Gauge:
    """Metric that can go up and down."""

    def __init__(self, name: str, help_text: str):
        self._name = name
        self._help = help_text
        self._values: Dict[_LabelKey, float] = {}
        self._lock = threading.Lock()

    def set(self, value: float, **labels) -> None:
        key = tuple(sorted((k, str(v)) for k, v in labels.items()))
        with self._lock:
            self._values[key] = value

    def inc(self, amount: float = 1.0, **labels) -> None:
        key = tuple(sorted((k, str(v)) for k, v in labels.items()))
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def render(self) -> str:
        lines = [f"# HELP {self._name} {self._help}", f"# TYPE {self._name} gauge"]
        with self._lock:
            for key, val in self._values.items():
                label_str = Counter._fmt_labels(key)
                lines.append(f"{self._name}{label_str} {val:.6g}")
        return "\n".join(lines)


class Histogram:
    """Histogram with fixed buckets for latency tracking."""

    _DEFAULT_BUCKETS = (50, 100, 200, 300, 500, 1000, 2000, 5000)  # ms

    def __init__(self, name: str, help_text: str, buckets: tuple = _DEFAULT_BUCKETS):
        self._name = name
        self._help = help_text
        self._buckets = sorted(buckets)
        self._counts: Dict[float, float] = defaultdict(float)
        self._sum = 0.0
        self._total = 0.0
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        with self._lock:
            self._sum += value
            self._total += 1
            for b in self._buckets:
                if value <= b:
                    self._counts[b] += 1

    def render(self) -> str:
        lines = [f"# HELP {self._name} {self._help}", f"# TYPE {self._name} histogram"]
        with self._lock:
            cumulative = 0.0
            for b in self._buckets:
                cumulative += self._counts[b]
                lines.append(f'{self._name}_bucket{{le="{b}"}} {cumulative:.0f}')
            lines.append(f'{self._name}_bucket{{le="+Inf"}} {self._total:.0f}')
            lines.append(f"{self._name}_sum {self._sum:.3f}")
            lines.append(f"{self._name}_count {self._total:.0f}")
        return "\n".join(lines)


class MetricsRegistry:
    """Central metrics store — call :func:`get_registry` to get the singleton."""

    def __init__(self):
        self._counters: Dict[str, Counter] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._histograms: Dict[str, Histogram] = {}
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._enabled = True

        # Pre-register standard OpenCastor metrics
        self._init_standard_metrics()

    def _init_standard_metrics(self) -> None:
        """Register all standard metrics with their help strings."""
        # Counters
        self._counters["opencastor_loops_total"] = Counter(
            "opencastor_loops_total", "Total perception-action loop iterations", ("robot",)
        )
        self._counters["opencastor_commands_total"] = Counter(
            "opencastor_commands_total", "Total API commands processed", ("robot", "source")
        )
        self._counters["opencastor_errors_total"] = Counter(
            "opencastor_errors_total", "Total errors by type", ("robot", "type")
        )
        self._counters["opencastor_audio_transcribed_total"] = Counter(
            "opencastor_audio_transcribed_total", "Total audio files transcribed", ("engine",)
        )
        self._counters["opencastor_channel_messages_total"] = Counter(
            "opencastor_channel_messages_total", "Total messages received per channel", ("channel",)
        )
        # Gauges
        self._gauges["opencastor_uptime_seconds"] = Gauge(
            "opencastor_uptime_seconds", "Gateway uptime in seconds"
        )
        self._gauges["opencastor_avg_latency_ms"] = Gauge(
            "opencastor_avg_latency_ms", "Average loop latency in milliseconds"
        )
        self._gauges["opencastor_camera_fps"] = Gauge(
            "opencastor_camera_fps", "Camera frames per second"
        )
        self._gauges["opencastor_brain_up"] = Gauge(
            "opencastor_brain_up", "1 if brain is online, 0 otherwise"
        )
        self._gauges["opencastor_driver_up"] = Gauge(
            "opencastor_driver_up", "1 if driver is online, 0 otherwise"
        )
        self._gauges["opencastor_active_channels"] = Gauge(
            "opencastor_active_channels", "Number of active messaging channels"
        )
        self._gauges["opencastor_loop_count"] = Gauge(
            "opencastor_loop_count", "Total loop iterations (same as counter, for dashboard)"
        )
        # Histogram
        self._histograms["opencastor_loop_duration_ms"] = Histogram(
            "opencastor_loop_duration_ms",
            "Perception-action loop duration in milliseconds",
        )

    # ── Accessors ─────────────────────────────────────────────────────────────

    def counter(self, name: str) -> Optional[Counter]:
        return self._counters.get(name)

    def gauge(self, name: str) -> Optional[Gauge]:
        return self._gauges.get(name)

    def histogram(self, name: str) -> Optional[Histogram]:
        return self._histograms.get(name)

    # ── Convenience record helpers ────────────────────────────────────────────

    def record_loop(self, latency_ms: float, robot: str = "robot") -> None:
        """Increment loop counter and record latency histogram."""
        if not self._enabled:
            return
        c = self._counters.get("opencastor_loops_total")
        if c:
            c.inc(robot=robot)
        g = self._gauges.get("opencastor_loop_count")
        if g:
            g.inc(robot=robot)
        h = self._histograms.get("opencastor_loop_duration_ms")
        if h:
            h.observe(latency_ms)
        lag = self._gauges.get("opencastor_avg_latency_ms")
        if lag:
            lag.set(latency_ms, robot=robot)

    def record_command(self, robot: str = "robot", source: str = "api") -> None:
        c = self._counters.get("opencastor_commands_total")
        if c and self._enabled:
            c.inc(robot=robot, source=source)

    def record_error(self, error_type: str, robot: str = "robot") -> None:
        c = self._counters.get("opencastor_errors_total")
        if c and self._enabled:
            c.inc(robot=robot, type=error_type)

    def record_audio_transcription(self, engine: str = "auto") -> None:
        c = self._counters.get("opencastor_audio_transcribed_total")
        if c and self._enabled:
            c.inc(engine=engine)

    def record_channel_message(self, channel: str) -> None:
        c = self._counters.get("opencastor_channel_messages_total")
        if c and self._enabled:
            c.inc(channel=channel)

    def update_status(
        self,
        robot: str = "robot",
        brain_up: bool = False,
        driver_up: bool = False,
        active_channels: int = 0,
        uptime_s: float = 0.0,
    ) -> None:
        """Snapshot-update all status gauges."""
        if not self._enabled:
            return
        for name, val in [
            ("opencastor_brain_up", 1.0 if brain_up else 0.0),
            ("opencastor_driver_up", 1.0 if driver_up else 0.0),
            ("opencastor_active_channels", float(active_channels)),
            ("opencastor_uptime_seconds", uptime_s),
        ]:
            g = self._gauges.get(name)
            if g:
                g.set(val, robot=robot)

    # ── Render ────────────────────────────────────────────────────────────────

    def render(self) -> str:
        """Render all metrics in Prometheus text exposition format."""
        sections = []
        for c in self._counters.values():
            sections.append(c.render())
        for g in self._gauges.values():
            sections.append(g.render())
        for h in self._histograms.values():
            sections.append(h.render())
        return "\n".join(sections) + "\n"


# ── Singleton ─────────────────────────────────────────────────────────────────

_registry: Optional[MetricsRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> MetricsRegistry:
    """Return the process-wide MetricsRegistry singleton."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = MetricsRegistry()
    return _registry

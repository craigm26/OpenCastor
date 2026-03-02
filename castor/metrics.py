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
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["MetricsRegistry", "get_registry", "ChannelInterArrivalTracker", "RequestRateTracker"]

_LabelKey = Tuple[str, ...]  # sorted label kv pairs as tuple


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


class ProviderLatencyTracker:
    """Per-provider latency histograms rendered with a Prometheus ``provider`` label.

    Stored separately from :class:`Histogram` because histograms with varying
    label-sets require per-label bucket data.

    Issue #347: Also stores exact sorted samples for p50/p95/p99 percentile
    computation and exposes them as ``opencastor_provider_latency_p50_ms``,
    ``opencastor_provider_latency_p95_ms``, and ``opencastor_provider_latency_p99_ms``
    gauges per provider.
    """

    _DEFAULT_BUCKETS: Tuple[float, ...] = (50, 100, 200, 500, 1000, 2000, 5000, 10000)  # ms
    # Maximum raw samples kept per provider (prevents unbounded growth)
    _MAX_SAMPLES: int = 10_000

    def __init__(self, buckets: Tuple[float, ...] = _DEFAULT_BUCKETS) -> None:
        self._buckets: Tuple[float, ...] = tuple(sorted(buckets))
        # provider_name → {counts, sum, total, samples}
        self._data: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    def observe(self, provider: str, value: float) -> None:
        """Record a latency observation for *provider*.

        Args:
            provider: Provider name string (e.g. ``"google"``, ``"anthropic"``).
            value:    Latency in milliseconds.
        """
        with self._lock:
            if provider not in self._data:
                self._data[provider] = {
                    "counts": defaultdict(float),
                    "sum": 0.0,
                    "total": 0.0,
                    "samples": [],  # sorted list of raw latency values (#347)
                }
            d = self._data[provider]
            d["sum"] += value
            d["total"] += 1
            for b in self._buckets:
                if value <= b:
                    d["counts"][b] += 1
            # Maintain sorted samples list for percentile computation
            import bisect

            bisect.insort(d["samples"], value)
            if len(d["samples"]) > self._MAX_SAMPLES:
                # Trim oldest (smallest) sample to keep list bounded
                d["samples"].pop(0)

    def percentile(self, provider: str, pct: float) -> Optional[float]:
        """Compute an exact percentile from sorted samples for *provider*.

        Args:
            provider: Provider name string.
            pct:      Percentile in [0, 100].  E.g. ``50.0`` for median.

        Returns:
            Percentile value in milliseconds, or ``None`` if no samples exist.
        """
        with self._lock:
            d = self._data.get(provider)
            if d is None or not d["samples"]:
                return None
            samples = d["samples"]
            n = len(samples)
            # Linear interpolation method (same as numpy percentile default)
            index = (pct / 100.0) * (n - 1)
            lo = int(index)
            hi = lo + 1
            frac = index - lo
            if hi >= n:
                return float(samples[-1])
            return float(samples[lo] * (1.0 - frac) + samples[hi] * frac)

    def providers(self) -> List[str]:
        """Return sorted list of provider names that have been observed."""
        with self._lock:
            return sorted(self._data.keys())

    def render_percentiles(self) -> str:
        """Render p50/p95/p99 gauges in Prometheus text exposition format.

        Returns lines for ``opencastor_provider_latency_p50_ms``,
        ``opencastor_provider_latency_p95_ms``, and
        ``opencastor_provider_latency_p99_ms`` — one time-series per provider.
        """
        lines: List[str] = []
        for pct_label, pct_val in (("p50", 50.0), ("p95", 95.0), ("p99", 99.0)):
            metric_name = f"opencastor_provider_latency_{pct_label}_ms"
            lines.append(
                f"# HELP {metric_name} "
                f"Provider think() latency {pct_label} percentile in milliseconds"
            )
            lines.append(f"# TYPE {metric_name} gauge")
            with self._lock:
                providers = sorted(self._data.keys())
            for provider in providers:
                val = self.percentile(provider, pct_val)
                if val is not None:
                    lines.append(f'{metric_name}{{provider="{provider}"}} {val:.3f}')
        return "\n".join(lines)

    def render(self) -> str:
        """Render labeled histogram in Prometheus text exposition format."""
        name = "opencastor_provider_latency_ms"
        lines = [
            f"# HELP {name} LLM provider think() latency in milliseconds",
            f"# TYPE {name} histogram",
        ]
        with self._lock:
            for provider in sorted(self._data.keys()):
                d = self._data[provider]
                cumulative = 0.0
                for b in self._buckets:
                    cumulative += d["counts"][b]
                    lines.append(
                        f'{name}_bucket{{provider="{provider}",le="{b}"}} {cumulative:.0f}'
                    )
                lines.append(f'{name}_bucket{{provider="{provider}",le="+Inf"}} {d["total"]:.0f}')
                lines.append(f'{name}_sum{{provider="{provider}"}} {d["sum"]:.3f}')
                lines.append(f'{name}_count{{provider="{provider}"}} {d["total"]:.0f}')
        return "\n".join(lines)


class ChannelInterArrivalTracker:
    """Per-channel message inter-arrival histograms rendered with a ``channel`` label.

    Records the time in milliseconds between consecutive messages on each channel.
    Stored separately so histograms carry the correct ``channel`` label.
    """

    _DEFAULT_BUCKETS: Tuple[float, ...] = (10, 50, 100, 250, 500, 1000, 2000, 5000)  # ms

    _MAX_SAMPLES: int = 1000

    def __init__(self, buckets: Tuple[float, ...] = _DEFAULT_BUCKETS) -> None:
        self._buckets: Tuple[float, ...] = tuple(sorted(buckets))
        # channel_name → {counts, sum, total, samples}
        self._data: Dict[str, Dict] = {}
        self._last_ts: Dict[str, float] = {}  # epoch seconds of last message per channel
        self._lock = threading.Lock()

    def record(self, channel: str) -> Optional[float]:
        """Record a new message on *channel*; return inter-arrival ms (or None for first msg)."""
        now = time.time()
        with self._lock:
            last = self._last_ts.get(channel)
            self._last_ts[channel] = now
            if last is None:
                return None
            interval_ms = (now - last) * 1000.0
            if channel not in self._data:
                self._data[channel] = {
                    "counts": defaultdict(float),
                    "sum": 0.0,
                    "total": 0.0,
                    "samples": [],
                }
            d = self._data[channel]
            d["sum"] += interval_ms
            d["total"] += 1
            for b in self._buckets:
                if interval_ms <= b:
                    d["counts"][b] += 1
            import bisect as _bisect

            _bisect.insort(d["samples"], interval_ms)
            if len(d["samples"]) > self._MAX_SAMPLES:
                d["samples"].pop(0)
            return interval_ms

    def percentile(self, channel: str, pct: float) -> Optional[float]:
        """Return an exact percentile of recorded inter-arrival samples for *channel*.

        Args:
            channel: Channel name string.
            pct:     Percentile in [0, 100].

        Returns:
            Inter-arrival time in milliseconds, or ``None`` if no samples.
        """
        with self._lock:
            d = self._data.get(channel)
            if d is None or not d["samples"]:
                return None
            samples = d["samples"]
            n = len(samples)
            index = (pct / 100.0) * (n - 1)
            lo = int(index)
            hi = lo + 1
            frac = index - lo
            if hi >= n:
                return float(samples[-1])
            return float(samples[lo] * (1.0 - frac) + samples[hi] * frac)

    def channels(self) -> List[str]:
        """Return sorted list of channel names that have been observed."""
        with self._lock:
            return sorted(self._data.keys())

    def render(self) -> str:
        """Render labeled histogram in Prometheus text exposition format."""
        name = "opencastor_channel_message_interval_ms"
        lines = [
            f"# HELP {name} Message inter-arrival time per channel in milliseconds",
            f"# TYPE {name} histogram",
        ]
        with self._lock:
            for channel in sorted(self._data.keys()):
                d = self._data[channel]
                cumulative = 0.0
                for b in self._buckets:
                    cumulative += d["counts"][b]
                    lines.append(f'{name}_bucket{{channel="{channel}",le="{b}"}} {cumulative:.0f}')
                lines.append(f'{name}_bucket{{channel="{channel}",le="+Inf"}} {d["total"]:.0f}')
                lines.append(f'{name}_sum{{channel="{channel}"}} {d["sum"]:.3f}')
                lines.append(f'{name}_count{{channel="{channel}"}} {d["total"]:.0f}')
        return "\n".join(lines)


class RequestRateTracker:
    """Per-endpoint request rate tracker using a sliding time window.

    Records timestamps of requests and computes requests/second over the
    last ``window_s`` seconds. Rendered as an opencastor_endpoint_rps gauge.
    """

    def __init__(self, window_s: float = 60.0) -> None:
        self._window_s = window_s
        self._timestamps: Dict[str, List[float]] = {}  # endpoint → list of epoch timestamps
        self._lock = threading.Lock()

    def record(self, endpoint: str) -> None:
        """Record a request for endpoint. Prunes old timestamps outside the window."""
        now = time.time()
        with self._lock:
            if endpoint not in self._timestamps:
                self._timestamps[endpoint] = []
            self._timestamps[endpoint].append(now)
            # Prune timestamps older than window_s
            cutoff = now - self._window_s
            self._timestamps[endpoint] = [t for t in self._timestamps[endpoint] if t >= cutoff]

    def rate(self, endpoint: str) -> float:
        """Return current requests/second for endpoint over the window."""
        now = time.time()
        with self._lock:
            ts = self._timestamps.get(endpoint, [])
            cutoff = now - self._window_s
            recent = [t for t in ts if t >= cutoff]
            if not recent:
                return 0.0
            return len(recent) / self._window_s

    def endpoints(self) -> List[str]:
        """Return sorted list of endpoint names that have been recorded."""
        with self._lock:
            return sorted(self._timestamps.keys())

    def render(self) -> str:
        """Render as opencastor_endpoint_rps gauge in Prometheus text format."""
        name = "opencastor_endpoint_rps"
        lines = [
            f"# HELP {name} Requests per second per endpoint (sliding {self._window_s:.0f}s window)",
            f"# TYPE {name} gauge",
        ]
        now = time.time()
        cutoff = now - self._window_s
        with self._lock:
            for endpoint in sorted(self._timestamps.keys()):
                recent = [t for t in self._timestamps[endpoint] if t >= cutoff]
                rps = len(recent) / self._window_s if recent else 0.0
                ep_safe = endpoint.replace('"', '\\"')
                lines.append(f'{name}{{endpoint="{ep_safe}"}} {rps:.4f}')
        return "\n".join(lines)


class MetricsRegistry:
    """Central metrics store — call :func:`get_registry` to get the singleton."""

    def __init__(self):
        self._counters: Dict[str, Counter] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._histograms: Dict[str, Histogram] = {}
        self._provider_latency = ProviderLatencyTracker()
        self._channel_interarrival = ChannelInterArrivalTracker()
        self._request_rate = RequestRateTracker()
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._enabled = True
        # Issue #395: per-channel cumulative message counts for message histogram
        self._channel_msg_counts: Dict[str, int] = {}
        # Issue #397: per-provider error counts for error histogram
        self._provider_error_counts: Dict[str, int] = {}
        # Issue #417 — loop latency samples for percentile computation
        self._loop_latency_samples: List[float] = []
        self._loop_latency_max_samples: int = 1000

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
        self._counters["opencastor_provider_errors_total"] = Counter(
            "opencastor_provider_errors_total",
            "Total LLM provider errors by provider and error type",
            ("provider", "error_type"),
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
        if self._enabled:
            with self._lock:
                self._loop_latency_samples.append(latency_ms)
                if len(self._loop_latency_samples) > self._loop_latency_max_samples:
                    self._loop_latency_samples = self._loop_latency_samples[
                        -self._loop_latency_max_samples :
                    ]

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
        if self._enabled:
            self._channel_interarrival.record(channel)
            # Issue #395: track cumulative count for message histogram
            with self._lock:
                self._channel_msg_counts[channel] = self._channel_msg_counts.get(channel, 0) + 1

    def record_provider_error(self, provider_name: str, error_type: str = "unknown") -> None:
        """Increment the per-provider error counter.

        Args:
            provider_name: Name of the LLM provider (e.g. ``"google"``, ``"anthropic"``).
            error_type:    Category string — ``"timeout"``, ``"quota"``, ``"network"``,
                           or ``"unknown"`` (default).
        """
        c = self._counters.get("opencastor_provider_errors_total")
        if c and self._enabled:
            c.inc(provider=provider_name, error_type=error_type)
        # Issue #397: update per-provider error count dict
        with self._lock:
            self._provider_error_counts[provider_name] = (
                self._provider_error_counts.get(provider_name, 0) + 1
            )

    def record_provider_latency(self, provider_name: str, latency_ms: float) -> None:
        """Record a provider think() latency observation for Prometheus export."""
        if self._enabled:
            self._provider_latency.observe(provider_name, latency_ms)

    def record_request(self, endpoint: str) -> None:
        """Record an API request for *endpoint* in the sliding-window rate tracker."""
        if self._enabled:
            self._request_rate.record(endpoint)

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
        if self._provider_latency.providers():
            sections.append(self._provider_latency.render())
            # Issue #347: emit p50/p95/p99 percentile gauges
            percentile_text = self._provider_latency.render_percentiles()
            if percentile_text:
                sections.append(percentile_text)
        if self._channel_interarrival.channels():
            sections.append(self._channel_interarrival.render())
        if self._request_rate.endpoints():
            sections.append(self._request_rate.render())
        return "\n".join(sections) + "\n"

    def provider_latency_percentile(self, provider: str, pct: float) -> Optional[float]:
        """Return an exact percentile of recorded latency samples for *provider*.

        Delegates to :meth:`ProviderLatencyTracker.percentile`.

        Args:
            provider: Provider name (e.g. ``"google"``).
            pct:      Percentile in [0, 100].

        Returns:
            Latency in milliseconds, or ``None`` if no observations exist.
        """
        return self._provider_latency.percentile(provider, pct)

    # ── Issue #372 — JSON snapshot ─────────────────────────────────────────────

    def export_json(self) -> Dict[str, Any]:
        """Return a structured dict snapshot of all metrics for the dashboard API.

        Returns a JSON-serialisable dict with keys:
            ``counters``        — ``{name: {label_key: value, ...}, ...}``
            ``gauges``          — ``{name: {label_key: value, ...}, ...}``
            ``histograms``      — ``{name: {sum, count, buckets: {le: cumulative}}}``
            ``provider_latency``— ``{provider: {sum_ms, count, p50, p95, p99}}``
            ``endpoint_rps``    — ``{endpoint: rps}``
            ``timestamp``       — Unix epoch of the snapshot.

        Never raises.
        """
        snapshot: Dict[str, Any] = {
            "counters": {},
            "gauges": {},
            "histograms": {},
            "provider_latency": {},
            "endpoint_rps": {},
            "timestamp": time.time(),
        }

        # Counters
        for name, counter in self._counters.items():
            with counter._lock:
                snapshot["counters"][name] = {
                    ",".join(f"{k}={v}" for k, v in key) if key else "__total__": val
                    for key, val in counter._values.items()
                }

        # Gauges
        for name, gauge in self._gauges.items():
            with gauge._lock:
                snapshot["gauges"][name] = {
                    ",".join(f"{k}={v}" for k, v in key) if key else "__value__": val
                    for key, val in gauge._values.items()
                }

        # Histograms
        for name, hist in self._histograms.items():
            with hist._lock:
                cumulative = 0.0
                buckets: Dict[str, float] = {}
                for b in hist._buckets:
                    cumulative += hist._counts[b]
                    buckets[str(b)] = cumulative
                buckets["+Inf"] = hist._total
                snapshot["histograms"][name] = {
                    "sum": hist._sum,
                    "count": hist._total,
                    "buckets": buckets,
                }

        # Provider latency
        for provider in self._provider_latency.providers():
            snapshot["provider_latency"][provider] = {
                "sum_ms": 0.0,
                "count": 0.0,
                "p50": self._provider_latency.percentile(provider, 50.0),
                "p95": self._provider_latency.percentile(provider, 95.0),
                "p99": self._provider_latency.percentile(provider, 99.0),
            }
            with self._provider_latency._lock:
                d = self._provider_latency._data.get(provider, {})
                snapshot["provider_latency"][provider]["sum_ms"] = d.get("sum", 0.0)
                snapshot["provider_latency"][provider]["count"] = d.get("total", 0.0)

        # Endpoint request rates
        for endpoint in self._request_rate.endpoints():
            snapshot["endpoint_rps"][endpoint] = round(self._request_rate.rate(endpoint), 4)

        return snapshot

    def channel_rate_histogram(self) -> Dict[str, Any]:
        """Return per-channel message inter-arrival percentiles.

        Returns:
            ``{channel: {p50, p95, p99, count}}`` for every channel that has
            received at least two messages.  Returns ``{}`` when no channels
            have been recorded.  Never raises.
        """
        result: Dict[str, Any] = {}
        try:
            for channel in self._channel_interarrival.channels():
                with self._channel_interarrival._lock:
                    d = self._channel_interarrival._data.get(channel, {})
                    count = int(d.get("total", 0))
                result[channel] = {
                    "p50": self._channel_interarrival.percentile(channel, 50.0),
                    "p95": self._channel_interarrival.percentile(channel, 95.0),
                    "p99": self._channel_interarrival.percentile(channel, 99.0),
                    "count": count,
                }
        except Exception as exc:
            import logging as _logging

            _logging.getLogger("OpenCastor.Metrics").warning(
                "channel_rate_histogram error: %s", exc
            )
        return result

    def channel_message_histogram(self) -> Dict[str, Any]:
        """Return binned message-count distribution per channel (Issue #395).

        Buckets: 1, 5, 10, 50, 100, 500, 1000, +Inf.  Each bucket reports
        the cumulative count of channels with total message count ≤ bucket
        value.  Also returns a ``per_channel`` dict with the raw count for
        each channel.

        Returns:
            ``{
                "buckets": {1: int, 5: int, ..., "+Inf": int},
                "per_channel": {channel: count},
            }``
            Never raises.
        """
        _BUCKETS = [1, 5, 10, 50, 100, 500, 1000]
        try:
            with self._lock:
                per_channel = dict(self._channel_msg_counts)

            bucket_counts: Dict[str, int] = {str(b): 0 for b in _BUCKETS}
            bucket_counts["+Inf"] = len(per_channel)

            for count in per_channel.values():
                for b in _BUCKETS:
                    if count <= b:
                        bucket_counts[str(b)] += 1

            return {"buckets": bucket_counts, "per_channel": per_channel}
        except Exception as exc:
            import logging as _logging

            _logging.getLogger("OpenCastor.Metrics").warning(
                "channel_message_histogram error: %s", exc
            )
            return {"buckets": {}, "per_channel": {}}

    def provider_error_histogram(self) -> Dict[str, Any]:
        """Return per-provider error counts binned into histogram buckets (Issue #397).

        Returns:
            Dict with keys:

            - ``buckets``: mapping label → count of providers with errors <= threshold
            - ``per_provider``: mapping provider name → total error count
        """
        _BUCKET_THRESHOLDS = [1, 5, 10, 50, 100, 500, 1000]
        with self._lock:
            counts = dict(self._provider_error_counts)

        # Build histogram: for each threshold, count providers with errors <= threshold
        buckets: Dict[str, int] = {}
        for t in _BUCKET_THRESHOLDS:
            label = f"<={t}"
            buckets[label] = sum(1 for c in counts.values() if c <= t)
        # +Inf bucket = total providers with any errors recorded
        buckets["+Inf"] = len(counts)

        return {"buckets": buckets, "per_provider": counts}

    def loop_latency_percentiles(self) -> Dict[str, Any]:
        """Return p50/p95/p99 of loop duration in ms (Issue #417).

        Uses the last up to 1000 loop latency samples recorded by record_loop().
        Returns None for each percentile when no samples exist.

        Returns:
            Dict with ``p50_ms``, ``p95_ms``, ``p99_ms``, ``sample_count`` keys.
        """
        with self._lock:
            samples = sorted(self._loop_latency_samples)

        n = len(samples)
        if n == 0:
            return {"p50_ms": None, "p95_ms": None, "p99_ms": None, "sample_count": 0}

        def _pct(pct: float) -> float:
            idx = int(pct * (n - 1))
            return round(samples[idx], 3)

        return {
            "p50_ms": _pct(0.50),
            "p95_ms": _pct(0.95),
            "p99_ms": _pct(0.99),
            "sample_count": n,
        }


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


# ---------------------------------------------------------------------------
# Issue #217 — Prometheus Pushgateway support
# ---------------------------------------------------------------------------


def push_to_gateway(
    gateway_url: Optional[str] = None,
    job: str = "opencastor",
    registry: Optional[MetricsRegistry] = None,
    timeout: float = 5.0,
) -> bool:
    """Push metrics to a Prometheus Pushgateway.

    Reads the gateway URL from the ``CASTOR_PROMETHEUS_PUSHGATEWAY``
    environment variable when *gateway_url* is not given.

    Args:
        gateway_url: Pushgateway URL, e.g. ``"http://localhost:9091"``.
                     Falls back to ``CASTOR_PROMETHEUS_PUSHGATEWAY`` env var.
        job:         Prometheus job label.
        registry:    :class:`MetricsRegistry` to push (default: global singleton).
        timeout:     HTTP request timeout in seconds.

    Returns:
        ``True`` on success, ``False`` on any error.
    """
    import urllib.request as _req

    url = gateway_url or os.getenv("CASTOR_PROMETHEUS_PUSHGATEWAY", "")
    if not url:
        return False

    reg = registry or get_registry()
    payload = reg.render().encode()
    push_url = f"{url.rstrip('/')}/metrics/job/{job}"

    try:
        req = _req.Request(push_url, data=payload, method="PUT")
        req.add_header("Content-Type", "text/plain; version=0.0.4")
        with _req.urlopen(req, timeout=timeout) as resp:
            return resp.status < 300
    except Exception as exc:
        import logging

        logging.getLogger("OpenCastor.Metrics").warning("Pushgateway push failed: %s", exc)
        return False


import os  # noqa: E402 (appended after module body)

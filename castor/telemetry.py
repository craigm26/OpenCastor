"""
OpenCastor Telemetry — OpenTelemetry metrics and traces export.

Exports:
  - ``opencastor.action.latency_ms``  — perception-action loop latency histogram
  - ``opencastor.action.count``       — total action executions counter
  - ``opencastor.safety.score``       — current safety score gauge (0.0–1.0)
  - ``opencastor.safety.violations``  — total safety violation counter
  - ``opencastor.brain.errors``       — LLM provider error counter

Usage::

    # In main.py or api.py startup:
    from castor.telemetry import get_telemetry
    tel = get_telemetry()
    tel.enable(service_name="my-robot", exporter="console")   # or "otlp"

    # In the perception-action loop:
    tel.record_action(latency_ms=123.4, action_type="move")
    tel.record_safety_score(0.95)

Set OTEL_EXPORTER_OTLP_ENDPOINT (e.g. http://localhost:4317) to send to
Grafana / Datadog / Honeycomb. Set OPENCASTOR_OTEL_EXPORTER=console for
local debugging.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

logger = logging.getLogger("OpenCastor.Telemetry")

# Lazy imports so the package remains usable without the OTEL SDK installed
_HAS_OTEL = False
try:
    from opentelemetry import metrics as _otel_metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        PeriodicExportingMetricReader,
    )
    from opentelemetry.sdk.resources import Resource

    _HAS_OTEL = True
except ImportError:
    pass


class _NoopCounter:
    def add(self, amount, attributes=None):
        pass


class _NoopHistogram:
    def record(self, amount, attributes=None):
        pass


class _NoopGauge:
    def set(self, amount, attributes=None):  # noqa: A003
        pass


class CastorTelemetry:
    """Thin wrapper around the OpenTelemetry metrics API.

    Falls back to no-ops gracefully when the OTEL SDK is not installed.
    """

    def __init__(self):
        self._enabled = False
        self._meter = None
        self._action_counter = _NoopCounter()
        self._latency_histogram = _NoopHistogram()
        self._safety_score_gauge = _NoopGauge()
        self._safety_violations = _NoopCounter()
        self._brain_errors = _NoopCounter()
        self._last_safety_score: float = 1.0

    def enable(
        self,
        service_name: str = "opencastor",
        exporter: str = "auto",
        export_interval_ms: int = 10_000,
    ) -> bool:
        """Initialize the OTEL meter provider and instruments.

        Args:
            service_name: OTEL service.name attribute.
            exporter: ``"auto"`` (read OPENCASTOR_OTEL_EXPORTER env),
                      ``"console"``, ``"otlp"``, or ``"none"``.
            export_interval_ms: How often metrics are pushed to the exporter.

        Returns:
            True if OTEL SDK was available and provider was initialized.
        """
        if not _HAS_OTEL:
            logger.info(
                "OpenTelemetry SDK not installed. "
                "Install with: pip install opentelemetry-sdk opentelemetry-exporter-otlp"
            )
            return False

        if exporter == "auto":
            exporter = os.getenv("OPENCASTOR_OTEL_EXPORTER", "none")

        if exporter == "none":
            return False

        resource = Resource.create({"service.name": service_name})

        if exporter == "otlp":
            try:
                from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                    OTLPMetricExporter,
                )

                metric_exporter = OTLPMetricExporter()
            except ImportError:
                logger.warning(
                    "OTLP gRPC exporter not installed. "
                    "Install: pip install opentelemetry-exporter-otlp-proto-grpc"
                )
                return False
        elif exporter == "console":
            metric_exporter = ConsoleMetricExporter()
        else:
            logger.warning(f"Unknown OTEL exporter: {exporter!r}. Use 'otlp' or 'console'.")
            return False

        reader = PeriodicExportingMetricReader(
            metric_exporter, export_interval_millis=export_interval_ms
        )
        provider = MeterProvider(resource=resource, metric_readers=[reader])
        _otel_metrics.set_meter_provider(provider)

        self._meter = _otel_metrics.get_meter("opencastor", version="1.0")
        self._action_counter = self._meter.create_counter(
            "opencastor.action.count",
            description="Total number of robot actions executed",
        )
        self._latency_histogram = self._meter.create_histogram(
            "opencastor.action.latency_ms",
            unit="ms",
            description="Perception-action loop latency in milliseconds",
        )
        self._safety_score_gauge = self._meter.create_gauge(
            "opencastor.safety.score",
            description="Current composite safety score (0.0=critical, 1.0=healthy)",
        )
        self._safety_violations = self._meter.create_counter(
            "opencastor.safety.violations",
            description="Total number of safety violations detected",
        )
        self._brain_errors = self._meter.create_counter(
            "opencastor.brain.errors",
            description="Total number of LLM provider errors",
        )

        self._enabled = True
        logger.info(f"OpenTelemetry metrics enabled (exporter={exporter}, service={service_name})")
        return True

    def record_action(
        self,
        latency_ms: float,
        action_type: str = "unknown",
        provider: str = "unknown",
    ) -> None:
        """Record a completed perception-action cycle."""
        attrs = {"action_type": action_type, "provider": provider}
        self._action_counter.add(1, attributes=attrs)
        self._latency_histogram.record(latency_ms, attributes=attrs)

    def record_safety_score(self, score: float, robot_name: str = "default") -> None:
        """Update the safety score gauge."""
        self._last_safety_score = score
        self._safety_score_gauge.set(score, attributes={"robot": robot_name})

    def record_safety_violation(self, violation_type: str = "unknown") -> None:
        """Increment the safety violations counter."""
        self._safety_violations.add(1, attributes={"type": violation_type})

    def record_brain_error(self, provider: str = "unknown", error_type: str = "unknown") -> None:
        """Increment the brain errors counter."""
        self._brain_errors.add(1, attributes={"provider": provider, "error_type": error_type})

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def last_safety_score(self) -> float:
        return self._last_safety_score


# Module-level singleton
_global_telemetry: Optional[CastorTelemetry] = None


def get_telemetry() -> CastorTelemetry:
    """Return the process-wide CastorTelemetry instance."""
    global _global_telemetry
    if _global_telemetry is None:
        _global_telemetry = CastorTelemetry()
    return _global_telemetry

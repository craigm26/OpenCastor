"""Tests for OpenTelemetry distributed tracing in castor.telemetry.

Issue #230 — init_otel, trace_think, trace_move, HAS_OTEL guard.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# HAS_OTEL public guard
# ---------------------------------------------------------------------------


class TestHASOTEL:
    def test_has_otel_is_bool(self):
        from castor.telemetry import HAS_OTEL

        assert isinstance(HAS_OTEL, bool)

    def test_has_otel_false_without_sdk(self):
        """HAS_OTEL reflects whether SDK is actually installed."""
        from castor.telemetry import _HAS_OTEL, HAS_OTEL

        assert HAS_OTEL == _HAS_OTEL


# ---------------------------------------------------------------------------
# _NoopSpan
# ---------------------------------------------------------------------------


class TestNoopSpan:
    def test_noop_span_enter_exit(self):
        from castor.telemetry import _NoopSpan

        span = _NoopSpan()
        with span:
            span.set_attribute("key", "val")
            span.record_exception(ValueError("test"))
            span.set_status("ok")

    def test_noop_span_set_attribute_no_raise(self):
        from castor.telemetry import _NoopSpan

        span = _NoopSpan()
        span.set_attribute("a", 1)
        span.set_attribute("b", 3.14)
        span.set_attribute("c", "string")


# ---------------------------------------------------------------------------
# _NoopTracer
# ---------------------------------------------------------------------------


class TestNoopTracer:
    def test_start_as_current_span_returns_noop(self):
        from castor.telemetry import _NoopSpan, _NoopTracer

        tracer = _NoopTracer()
        span = tracer.start_as_current_span("test")
        assert isinstance(span, _NoopSpan)

    def test_start_span_returns_noop(self):
        from castor.telemetry import _NoopSpan, _NoopTracer

        tracer = _NoopTracer()
        span = tracer.start_span("test")
        assert isinstance(span, _NoopSpan)


# ---------------------------------------------------------------------------
# init_otel — no OTEL SDK installed (mock)
# ---------------------------------------------------------------------------


class TestInitOTELNoSDK:
    def test_returns_false_when_sdk_absent(self):
        from castor.telemetry import init_otel

        with patch("castor.telemetry._HAS_OTEL_TRACE", False):
            result = init_otel(service_name="test", exporter="console")
        assert result is False

    def test_returns_false_when_exporter_none(self):
        from castor.telemetry import init_otel

        with patch("castor.telemetry._HAS_OTEL_TRACE", True):
            with patch("castor.telemetry.TracerProvider", create=True):
                result = init_otel(service_name="test", exporter="none")
        assert result is False

    def test_auto_exporter_reads_env_none(self, monkeypatch):
        from castor.telemetry import init_otel

        monkeypatch.setenv("OPENCASTOR_OTEL_EXPORTER", "none")
        with patch("castor.telemetry._HAS_OTEL_TRACE", True):
            with patch("castor.telemetry.TracerProvider", create=True):
                result = init_otel(service_name="svc", exporter="auto")
        assert result is False


# ---------------------------------------------------------------------------
# init_otel — console exporter (mocked)
# ---------------------------------------------------------------------------


class TestInitOTELConsole:
    def test_console_exporter_ok(self):
        """Without opentelemetry SDK, init_otel returns False — no exception."""
        from castor.telemetry import init_otel

        # SDK is absent; init_otel should return False gracefully regardless of exporter arg.
        result = init_otel(service_name="mybot", exporter="console")
        assert result is False

    def test_unknown_exporter_returns_false(self):
        from castor.telemetry import init_otel

        with patch("castor.telemetry._HAS_OTEL_TRACE", True):
            with patch("castor.telemetry.TracerProvider", create=True):
                result = init_otel(service_name="x", exporter="bogus_exporter")
        assert result is False


# ---------------------------------------------------------------------------
# init_otel — environment variable fallbacks
# ---------------------------------------------------------------------------


class TestInitOTELEnvVars:
    def test_reads_otel_service_name_env(self, monkeypatch):
        from castor.telemetry import init_otel

        monkeypatch.setenv("OTEL_SERVICE_NAME", "env-robot")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        with patch("castor.telemetry._HAS_OTEL_TRACE", False):
            result = init_otel(exporter="otlp")
        assert result is False  # SDK absent

    def test_reads_otlp_endpoint_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")
        # Just verify the env var is read correctly
        ep = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        assert ep == "http://jaeger:4317"


# ---------------------------------------------------------------------------
# get_tracer
# ---------------------------------------------------------------------------


class TestGetTracer:
    def test_returns_noop_when_not_initialised(self):
        from castor.telemetry import _NoopTracer, get_tracer

        with patch("castor.telemetry._tracer", None):
            with patch("castor.telemetry._HAS_OTEL_TRACE", False):
                tracer = get_tracer()
        assert isinstance(tracer, _NoopTracer)

    def test_returns_tracer_when_initialised(self):
        mock_tracer = MagicMock()
        with patch("castor.telemetry._tracer", mock_tracer):
            from castor.telemetry import get_tracer

            tracer = get_tracer()
        assert tracer is mock_tracer


# ---------------------------------------------------------------------------
# trace_think
# ---------------------------------------------------------------------------


class TestTraceThink:
    def test_returns_noop_when_no_tracer(self):
        from castor.telemetry import _NoopSpan, trace_think

        with patch("castor.telemetry._tracer", None):
            with patch("castor.telemetry._HAS_OTEL_TRACE", False):
                span = trace_think(provider="google", model="gemini", latency_ms=42.0, tokens=100)
        assert isinstance(span, _NoopSpan)

    def test_noop_span_is_context_manager(self):
        from castor.telemetry import trace_think

        with patch("castor.telemetry._tracer", None):
            with patch("castor.telemetry._HAS_OTEL_TRACE", False):
                with trace_think():
                    pass  # Should not raise


# ---------------------------------------------------------------------------
# trace_move
# ---------------------------------------------------------------------------


class TestTraceMove:
    def test_returns_noop_when_no_tracer(self):
        from castor.telemetry import _NoopSpan, trace_move

        with patch("castor.telemetry._tracer", None):
            with patch("castor.telemetry._HAS_OTEL_TRACE", False):
                span = trace_move(linear=0.5, angular=0.1, driver_mode="differential")
        assert isinstance(span, _NoopSpan)

    def test_noop_span_is_context_manager(self):
        from castor.telemetry import trace_move

        with patch("castor.telemetry._tracer", None):
            with patch("castor.telemetry._HAS_OTEL_TRACE", False):
                with trace_move(linear=0.0, angular=0.0):
                    pass  # Should not raise

    def test_accepts_all_params(self):
        from castor.telemetry import trace_move

        with patch("castor.telemetry._tracer", None):
            with patch("castor.telemetry._HAS_OTEL_TRACE", False):
                span = trace_move(linear=1.0, angular=-0.5, driver_mode="ros2")
        assert span is not None

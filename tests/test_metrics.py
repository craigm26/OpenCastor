"""Tests for castor/metrics.py — Prometheus metrics registry (issue #99)."""

import pytest

from castor.metrics import Counter, Gauge, Histogram, MetricsRegistry, get_registry


# ── Counter ───────────────────────────────────────────────────────────────────

def test_counter_inc():
    c = Counter("my_counter", "test counter", ("robot",))
    c.inc(robot="bob")
    c.inc(robot="bob")
    c.inc(robot="alice")
    rendered = c.render()
    assert 'my_counter{robot="bob"} 2' in rendered
    assert 'my_counter{robot="alice"} 1' in rendered


def test_counter_render_type_line():
    c = Counter("foo_total", "desc", ())
    c.inc()
    r = c.render()
    assert "# TYPE foo_total counter" in r


# ── Gauge ─────────────────────────────────────────────────────────────────────

def test_gauge_set_and_inc():
    g = Gauge("my_gauge", "test gauge")
    g.set(3.14, robot="bob")
    g.inc(1.0, robot="bob")
    r = g.render()
    assert "# TYPE my_gauge gauge" in r
    # Value should be ~4.14
    assert "my_gauge" in r


def test_gauge_starts_empty():
    g = Gauge("empty_gauge", "empty")
    assert g.render().count("\n") == 1  # only two header lines, no data lines


# ── Histogram ─────────────────────────────────────────────────────────────────

def test_histogram_observe():
    h = Histogram("my_histogram", "test histogram")
    h.observe(50)
    h.observe(150)
    h.observe(600)
    r = h.render()
    assert "# TYPE my_histogram histogram" in r
    assert "_count 3" in r
    assert "_sum" in r
    assert 'le="+Inf"' in r


def test_histogram_buckets():
    h = Histogram("latency", "latency ms")
    h.observe(75)  # falls in 100ms bucket
    r = h.render()
    assert 'le="100"' in r


# ── MetricsRegistry ───────────────────────────────────────────────────────────

def test_registry_standard_metrics():
    reg = MetricsRegistry()
    assert reg.counter("opencastor_loops_total") is not None
    assert reg.gauge("opencastor_uptime_seconds") is not None
    assert reg.histogram("opencastor_loop_duration_ms") is not None


def test_registry_record_loop():
    reg = MetricsRegistry()
    reg.record_loop(250.0, robot="testbot")
    r = reg.render()
    assert "opencastor_loops_total" in r


def test_registry_record_command():
    reg = MetricsRegistry()
    reg.record_command(robot="testbot", source="api")
    r = reg.render()
    assert "opencastor_commands_total" in r


def test_registry_record_error():
    reg = MetricsRegistry()
    reg.record_error("timeout", robot="testbot")
    r = reg.render()
    assert "opencastor_errors_total" in r


def test_registry_update_status():
    reg = MetricsRegistry()
    reg.update_status(robot="testbot", brain_up=True, driver_up=False, active_channels=2, uptime_s=42.5)
    r = reg.render()
    assert "opencastor_brain_up" in r
    assert "opencastor_active_channels" in r


def test_render_ends_with_newline():
    reg = MetricsRegistry()
    assert reg.render().endswith("\n")


# ── Singleton ─────────────────────────────────────────────────────────────────

def test_get_registry_singleton():
    r1 = get_registry()
    r2 = get_registry()
    assert r1 is r2

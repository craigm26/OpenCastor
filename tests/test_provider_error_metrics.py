"""Tests for MetricsRegistry.record_provider_error() — issue #322."""

from __future__ import annotations

import threading

from castor.metrics import MetricsRegistry, get_registry

# ── record_provider_error ──────────────────────────────────────────────────────


def _fresh() -> MetricsRegistry:
    return MetricsRegistry()


def test_record_provider_error_increments_counter():
    reg = _fresh()
    reg.record_provider_error("google")
    c = reg.counter("opencastor_provider_errors_total")
    assert c is not None
    key = tuple(sorted([("error_type", "unknown"), ("provider", "google")]))
    assert c._values[key] == 1


def test_record_provider_error_custom_error_type():
    reg = _fresh()
    reg.record_provider_error("anthropic", "timeout")
    c = reg.counter("opencastor_provider_errors_total")
    key = tuple(sorted([("error_type", "timeout"), ("provider", "anthropic")]))
    assert c._values[key] == 1


def test_record_provider_error_multiple_calls_accumulate():
    reg = _fresh()
    reg.record_provider_error("google", "quota")
    reg.record_provider_error("google", "quota")
    reg.record_provider_error("google", "quota")
    c = reg.counter("opencastor_provider_errors_total")
    key = tuple(sorted([("error_type", "quota"), ("provider", "google")]))
    assert c._values[key] == 3


def test_record_provider_error_separate_labels_separate_counts():
    reg = _fresh()
    reg.record_provider_error("openai", "network")
    reg.record_provider_error("openai", "timeout")
    c = reg.counter("opencastor_provider_errors_total")
    net_key = tuple(sorted([("error_type", "network"), ("provider", "openai")]))
    to_key = tuple(sorted([("error_type", "timeout"), ("provider", "openai")]))
    assert c._values[net_key] == 1
    assert c._values[to_key] == 1


def test_record_provider_error_default_error_type_is_unknown():
    reg = _fresh()
    reg.record_provider_error("ollama")
    c = reg.counter("opencastor_provider_errors_total")
    key = tuple(sorted([("error_type", "unknown"), ("provider", "ollama")]))
    assert c._values[key] == 1


def test_record_provider_error_not_recorded_when_disabled():
    reg = _fresh()
    reg._enabled = False
    reg.record_provider_error("google", "quota")
    c = reg.counter("opencastor_provider_errors_total")
    assert sum(c._values.values()) == 0


def test_provider_errors_counter_in_standard_metrics():
    reg = _fresh()
    assert "opencastor_provider_errors_total" in reg._counters


def test_provider_errors_counter_help_text():
    reg = _fresh()
    c = reg._counters["opencastor_provider_errors_total"]
    assert "error" in c._help.lower()
    assert "provider" in c._help.lower()


def test_provider_errors_rendered_in_metrics_output():
    reg = _fresh()
    reg.record_provider_error("gemini", "quota")
    output = reg.render()
    assert "opencastor_provider_errors_total" in output
    assert "gemini" in output
    assert "quota" in output


def test_provider_errors_rendered_with_correct_label_format():
    reg = _fresh()
    reg.record_provider_error("claude", "network")
    output = reg.render()
    assert 'provider="claude"' in output
    assert 'error_type="network"' in output


def test_provider_errors_counter_type_in_render():
    reg = _fresh()
    reg.record_provider_error("x", "y")
    output = reg.render()
    assert "# TYPE opencastor_provider_errors_total counter" in output


def test_singleton_record_provider_error():
    """record_provider_error works on the global singleton."""
    reg = get_registry()
    reg.record_provider_error("singleton_test_provider", "test_error")
    c = reg.counter("opencastor_provider_errors_total")
    assert c is not None


def test_record_provider_error_thread_safe():
    """Concurrent record_provider_error calls don't raise."""
    reg = _fresh()
    errors = []

    def record_many():
        try:
            for _ in range(50):
                reg.record_provider_error("concurrent_provider", "network")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=record_many) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    c = reg.counter("opencastor_provider_errors_total")
    key = tuple(sorted([("error_type", "network"), ("provider", "concurrent_provider")]))
    assert c._values[key] == 200  # 4 threads × 50 calls


def test_record_provider_error_multiple_providers():
    reg = _fresh()
    for p in ["alpha", "beta", "gamma"]:
        reg.record_provider_error(p, "timeout")
    c = reg.counter("opencastor_provider_errors_total")
    for p in ["alpha", "beta", "gamma"]:
        key = tuple(sorted([("error_type", "timeout"), ("provider", p)]))
        assert c._values[key] == 1


def test_provider_errors_not_in_render_when_zero():
    """If no errors have been recorded, counter renders without sample lines."""
    reg = _fresh()
    output = reg.render()
    # Header lines still present
    assert "opencastor_provider_errors_total" in output
    # But no label-bearing sample line
    lines = [ln for ln in output.splitlines() if 'provider="' in ln]
    # Only provider_latency lines should have provider label; error counter has none
    for line in lines:
        assert "opencastor_provider_errors" not in line


def test_provider_error_error_types_all_accepted():
    reg = _fresh()
    for etype in ["timeout", "quota", "network", "unknown", "auth", "parse"]:
        reg.record_provider_error("provider_x", etype)
    c = reg.counter("opencastor_provider_errors_total")
    assert len(c._values) == 6

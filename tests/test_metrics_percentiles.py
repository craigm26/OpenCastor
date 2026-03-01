"""Tests for MetricsRegistry p50/p95/p99 percentile computation (Issue #347)."""

from __future__ import annotations

import threading

import pytest

from castor.metrics import MetricsRegistry, ProviderLatencyTracker

# ── ProviderLatencyTracker unit tests ─────────────────────────────────────────


def test_percentile_returns_none_when_no_samples():
    tracker = ProviderLatencyTracker()
    assert tracker.percentile("google", 50.0) is None


def test_percentile_single_sample():
    tracker = ProviderLatencyTracker()
    tracker.observe("google", 100.0)
    p50 = tracker.percentile("google", 50.0)
    assert p50 == pytest.approx(100.0)


def test_percentile_two_samples_median():
    tracker = ProviderLatencyTracker()
    tracker.observe("google", 100.0)
    tracker.observe("google", 200.0)
    p50 = tracker.percentile("google", 50.0)
    assert p50 == pytest.approx(150.0)


def test_percentile_p99_at_tail():
    tracker = ProviderLatencyTracker()
    for i in range(1, 101):
        tracker.observe("anthropic", float(i))
    p99 = tracker.percentile("anthropic", 99.0)
    assert p99 is not None
    assert p99 >= 98.0


def test_percentile_p50_sorted_values():
    tracker = ProviderLatencyTracker()
    # Insert out of order
    for v in [500.0, 100.0, 300.0, 200.0, 400.0]:
        tracker.observe("ollama", v)
    p50 = tracker.percentile("ollama", 50.0)
    # Median of [100,200,300,400,500] is 300
    assert p50 == pytest.approx(300.0)


def test_percentile_p95_large_dataset():
    tracker = ProviderLatencyTracker()
    for i in range(100):
        tracker.observe("openai", float(i))
    p95 = tracker.percentile("openai", 95.0)
    assert p95 is not None
    assert p95 >= 93.0
    assert p95 <= 100.0


def test_percentile_returns_none_for_unknown_provider():
    tracker = ProviderLatencyTracker()
    tracker.observe("google", 50.0)
    assert tracker.percentile("unknown_provider", 50.0) is None


def test_providers_list():
    tracker = ProviderLatencyTracker()
    tracker.observe("google", 100.0)
    tracker.observe("anthropic", 200.0)
    providers = tracker.providers()
    assert "google" in providers
    assert "anthropic" in providers


def test_render_percentiles_empty():
    tracker = ProviderLatencyTracker()
    result = tracker.render_percentiles()
    # When no providers, should still return HELP/TYPE lines (no actual data lines)
    assert isinstance(result, str)


def test_render_percentiles_contains_provider_labels():
    tracker = ProviderLatencyTracker()
    for i in range(10):
        tracker.observe("google", float(i * 10 + 10))
    result = tracker.render_percentiles()
    assert "google" in result
    assert "p50" in result
    assert "p95" in result
    assert "p99" in result


def test_render_percentiles_format():
    tracker = ProviderLatencyTracker()
    tracker.observe("anthropic", 123.0)
    result = tracker.render_percentiles()
    assert "opencastor_provider_latency_p50_ms" in result
    assert "opencastor_provider_latency_p95_ms" in result
    assert "opencastor_provider_latency_p99_ms" in result


# ── MetricsRegistry integration tests ────────────────────────────────────────


def test_registry_provider_latency_percentile_method():
    reg = MetricsRegistry()
    reg.record_provider_latency("google", 100.0)
    reg.record_provider_latency("google", 200.0)
    reg.record_provider_latency("google", 300.0)
    p50 = reg.provider_latency_percentile("google", 50.0)
    assert p50 is not None
    assert p50 == pytest.approx(200.0)


def test_registry_render_includes_percentile_gauges():
    reg = MetricsRegistry()
    for ms in [10.0, 50.0, 100.0, 200.0, 500.0]:
        reg.record_provider_latency("openai", ms)
    output = reg.render()
    assert "opencastor_provider_latency_p50_ms" in output
    assert "opencastor_provider_latency_p95_ms" in output
    assert "opencastor_provider_latency_p99_ms" in output


def test_registry_percentile_unknown_provider_returns_none():
    reg = MetricsRegistry()
    assert reg.provider_latency_percentile("nonexistent", 50.0) is None


def test_max_samples_does_not_grow_unbounded():
    tracker = ProviderLatencyTracker()
    for i in range(12000):
        tracker.observe("groq", float(i))
    with tracker._lock:
        n = len(tracker._data["groq"]["samples"])
    assert n <= tracker._MAX_SAMPLES


def test_thread_safety_observe():
    tracker = ProviderLatencyTracker()
    errors = []

    def worker():
        try:
            for _ in range(50):
                tracker.observe("google", 100.0)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    p50 = tracker.percentile("google", 50.0)
    assert p50 is not None

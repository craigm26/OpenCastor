"""Tests for MetricsRegistry provider latency histogram (issue #306)."""

from __future__ import annotations

from castor.metrics import MetricsRegistry, ProviderLatencyTracker

# ── ProviderLatencyTracker ────────────────────────────────────────────────────


def test_tracker_observe_and_providers():
    t = ProviderLatencyTracker()
    t.observe("google", 150.0)
    assert "google" in t.providers()


def test_tracker_multiple_providers():
    t = ProviderLatencyTracker()
    t.observe("google", 100.0)
    t.observe("anthropic", 200.0)
    providers = t.providers()
    assert "google" in providers
    assert "anthropic" in providers


def test_tracker_renders_prometheus_format():
    t = ProviderLatencyTracker()
    t.observe("google", 100.0)
    rendered = t.render()
    assert "opencastor_provider_latency_ms" in rendered
    assert "provider=" in rendered
    assert 'provider="google"' in rendered


def test_tracker_renders_bucket_lines():
    t = ProviderLatencyTracker()
    t.observe("google", 100.0)
    rendered = t.render()
    assert "_bucket{" in rendered
    assert 'le="+Inf"' in rendered


def test_tracker_renders_sum_and_count():
    t = ProviderLatencyTracker()
    t.observe("google", 200.0)
    rendered = t.render()
    assert "_sum{" in rendered
    assert "_count{" in rendered


def test_tracker_bucket_cumulative():
    t = ProviderLatencyTracker()
    t.observe("p", 100.0)  # fits in 100ms bucket
    rendered = t.render()
    # The +Inf bucket should be 1
    assert 'le="+Inf"} 1' in rendered


def test_tracker_multiple_observations():
    t = ProviderLatencyTracker()
    for i in range(5):
        t.observe("p", float(i * 50))
    rendered = t.render()
    assert 'le="+Inf"} 5' in rendered


def test_tracker_empty_renders_nothing_useful():
    t = ProviderLatencyTracker()
    rendered = t.render()
    # No data — no provider lines
    assert 'provider="' not in rendered


# ── MetricsRegistry.record_provider_latency ───────────────────────────────────


def test_registry_has_record_provider_latency():
    reg = MetricsRegistry()
    assert hasattr(reg, "record_provider_latency")
    assert callable(reg.record_provider_latency)


def test_registry_record_provider_latency_no_raise():
    reg = MetricsRegistry()
    reg.record_provider_latency("google", 123.0)  # must not raise


def test_registry_render_includes_provider_latency():
    reg = MetricsRegistry()
    reg.record_provider_latency("gemini", 99.0)
    rendered = reg.render()
    assert "opencastor_provider_latency_ms" in rendered
    assert 'provider="gemini"' in rendered


def test_registry_render_omits_latency_when_empty():
    reg = MetricsRegistry()
    rendered = reg.render()
    # No observations yet — metric should not appear
    assert "opencastor_provider_latency_ms" not in rendered


def test_registry_record_multiple_providers():
    reg = MetricsRegistry()
    reg.record_provider_latency("google", 150.0)
    reg.record_provider_latency("anthropic", 300.0)
    rendered = reg.render()
    assert 'provider="google"' in rendered
    assert 'provider="anthropic"' in rendered


def test_registry_disabled_no_observation():
    reg = MetricsRegistry()
    reg._enabled = False
    reg.record_provider_latency("google", 100.0)
    rendered = reg.render()
    assert "opencastor_provider_latency_ms" not in rendered

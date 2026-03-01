"""tests/test_metrics_request_rate.py — Tests for RequestRateTracker and MetricsRegistry.record_request().

Issue #334 — MetricsRegistry sliding window rate.
"""

from __future__ import annotations

import threading

import pytest

from castor.metrics import MetricsRegistry, RequestRateTracker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tracker() -> RequestRateTracker:
    """Fresh RequestRateTracker with a 60-second window."""
    return RequestRateTracker(window_s=60.0)


@pytest.fixture()
def registry() -> MetricsRegistry:
    """Fresh MetricsRegistry (not the singleton)."""
    return MetricsRegistry()


# ---------------------------------------------------------------------------
# Basic record / endpoints
# ---------------------------------------------------------------------------


def test_record_adds_endpoint(tracker: RequestRateTracker) -> None:
    """record() should make the endpoint appear in endpoints()."""
    assert "/api/command" not in tracker.endpoints()
    tracker.record("/api/command")
    assert "/api/command" in tracker.endpoints()


def test_endpoints_initially_empty(tracker: RequestRateTracker) -> None:
    """A fresh tracker has no endpoints."""
    assert tracker.endpoints() == []


def test_endpoints_returns_sorted_list(tracker: RequestRateTracker) -> None:
    """endpoints() returns a sorted list of all recorded endpoint names."""
    tracker.record("/z")
    tracker.record("/a")
    tracker.record("/m")
    assert tracker.endpoints() == ["/a", "/m", "/z"]


def test_multiple_endpoints_tracked_independently(tracker: RequestRateTracker) -> None:
    """Each endpoint accumulates its own timestamps independently."""
    for _ in range(3):
        tracker.record("/api/command")
    for _ in range(7):
        tracker.record("/api/status")

    # Both endpoints appear and have independent counts
    assert tracker.rate("/api/command") == pytest.approx(3 / 60.0, rel=1e-3)
    assert tracker.rate("/api/status") == pytest.approx(7 / 60.0, rel=1e-3)


# ---------------------------------------------------------------------------
# rate() behaviour
# ---------------------------------------------------------------------------


def test_rate_returns_zero_for_unknown_endpoint(tracker: RequestRateTracker) -> None:
    """rate() returns 0.0 when the endpoint has never been recorded."""
    assert tracker.rate("/api/never") == 0.0


def test_rate_returns_positive_after_recording(tracker: RequestRateTracker) -> None:
    """rate() returns a positive float after at least one record()."""
    tracker.record("/api/command")
    assert tracker.rate("/api/command") > 0.0


def test_rate_returns_float(tracker: RequestRateTracker) -> None:
    """rate() always returns a float, even for 0 counts."""
    result = tracker.rate("/nonexistent")
    assert isinstance(result, float)


def test_rate_reflects_window_denominator(tracker: RequestRateTracker) -> None:
    """rate() == count / window_s for fresh recordings inside the window."""
    t = RequestRateTracker(window_s=30.0)
    for _ in range(6):
        t.record("/api/test")
    # 6 requests / 30s = 0.2 rps
    assert t.rate("/api/test") == pytest.approx(6 / 30.0, rel=1e-3)


# ---------------------------------------------------------------------------
# Sliding window pruning (time mocking)
# ---------------------------------------------------------------------------


def test_sliding_window_prunes_old_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Timestamps older than window_s are excluded from rate calculations."""
    t = RequestRateTracker(window_s=10.0)

    # Simulate two requests at t=0 (outside the 10-second window)
    fake_time = 0.0

    def mock_time() -> float:
        return fake_time

    monkeypatch.setattr("castor.metrics.time.time", mock_time)

    fake_time = 0.0
    t.record("/api/old")
    t.record("/api/old")

    # Advance time by 15 seconds — both old timestamps are now outside the window
    fake_time = 15.0
    # Record one new request inside the window
    t.record("/api/old")

    # Only the 1 recent request should count
    rate = t.rate("/api/old")
    assert rate == pytest.approx(1 / 10.0, rel=1e-3)


def test_sliding_window_zero_after_all_expire(monkeypatch: pytest.MonkeyPatch) -> None:
    """rate() returns 0.0 once all recorded timestamps fall outside the window."""
    t = RequestRateTracker(window_s=5.0)

    fake_time = 0.0

    def mock_time() -> float:
        return fake_time

    monkeypatch.setattr("castor.metrics.time.time", mock_time)

    fake_time = 0.0
    t.record("/api/expire")
    t.record("/api/expire")

    # Advance well past the window
    fake_time = 100.0
    assert t.rate("/api/expire") == 0.0


# ---------------------------------------------------------------------------
# render()
# ---------------------------------------------------------------------------


def test_render_contains_metric_name(tracker: RequestRateTracker) -> None:
    """render() output includes the opencastor_endpoint_rps metric name."""
    tracker.record("/api/command")
    output = tracker.render()
    assert "opencastor_endpoint_rps" in output


def test_render_contains_endpoint_label(tracker: RequestRateTracker) -> None:
    """render() output includes the endpoint label value."""
    tracker.record("/api/status")
    output = tracker.render()
    assert 'endpoint="/api/status"' in output


def test_render_contains_type_gauge(tracker: RequestRateTracker) -> None:
    """render() declares TYPE as gauge."""
    tracker.record("/api/command")
    output = tracker.render()
    assert "# TYPE opencastor_endpoint_rps gauge" in output


def test_render_escapes_double_quotes_in_endpoint(tracker: RequestRateTracker) -> None:
    """render() escapes double-quote characters inside endpoint names."""
    ep = '/api/with"quote'
    tracker.record(ep)
    output = tracker.render()
    assert '\\"' in output


def test_render_includes_numeric_rps_value(tracker: RequestRateTracker) -> None:
    """render() contains a numeric RPS value in Prometheus format."""
    t = RequestRateTracker(window_s=10.0)
    for _ in range(5):
        t.record("/api/command")
    output = t.render()
    # e.g. opencastor_endpoint_rps{endpoint="/api/command"} 0.5000
    assert "0.5000" in output


# ---------------------------------------------------------------------------
# MetricsRegistry integration
# ---------------------------------------------------------------------------


def test_registry_has_record_request_method(registry: MetricsRegistry) -> None:
    """MetricsRegistry exposes a record_request() convenience method."""
    assert callable(getattr(registry, "record_request", None))


def test_record_request_increments_rate(registry: MetricsRegistry) -> None:
    """record_request() causes the endpoint's rate to become positive."""
    registry.record_request("/api/command")
    rate = registry._request_rate.rate("/api/command")
    assert rate > 0.0


def test_record_request_adds_to_endpoints(registry: MetricsRegistry) -> None:
    """record_request() registers the endpoint in the rate tracker."""
    assert "/api/stop" not in registry._request_rate.endpoints()
    registry.record_request("/api/stop")
    assert "/api/stop" in registry._request_rate.endpoints()


def test_registry_render_includes_rps_when_recorded(registry: MetricsRegistry) -> None:
    """MetricsRegistry.render() includes opencastor_endpoint_rps after a record_request() call."""
    registry.record_request("/api/command")
    output = registry.render()
    assert "opencastor_endpoint_rps" in output


def test_registry_render_excludes_rps_when_empty(registry: MetricsRegistry) -> None:
    """MetricsRegistry.render() omits opencastor_endpoint_rps if no requests recorded."""
    output = registry.render()
    assert "opencastor_endpoint_rps" not in output


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_record_thread_safe(tracker: RequestRateTracker) -> None:
    """Concurrent record() calls from multiple threads must not corrupt state."""
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(50):
                tracker.record("/api/concurrent")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert errors == [], f"Thread errors: {errors}"
    # 10 threads × 50 records = 500 total inside the 60-second window
    assert tracker.rate("/api/concurrent") == pytest.approx(500 / 60.0, rel=0.05)

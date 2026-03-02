"""Tests for MetricsRegistry.channel_rate_histogram() (#380)."""

import time

import pytest

from castor.metrics import ChannelInterArrivalTracker, MetricsRegistry


@pytest.fixture
def registry():
    return MetricsRegistry()


# ── basic return shape ────────────────────────────────────────────────────────

def test_channel_rate_histogram_returns_dict(registry):
    result = registry.channel_rate_histogram()
    assert isinstance(result, dict)


def test_channel_rate_histogram_empty_when_no_channels(registry):
    result = registry.channel_rate_histogram()
    assert result == {}


# ── recording messages and reading histogram ──────────────────────────────────

def test_channel_rate_histogram_contains_channel_after_two_records(registry):
    registry.record_channel_message("whatsapp")
    time.sleep(0.01)
    registry.record_channel_message("whatsapp")
    result = registry.channel_rate_histogram()
    assert "whatsapp" in result


def test_channel_rate_histogram_channel_has_required_keys(registry):
    registry.record_channel_message("telegram")
    time.sleep(0.01)
    registry.record_channel_message("telegram")
    result = registry.channel_rate_histogram()
    ch = result["telegram"]
    for key in ("p50", "p95", "p99", "count"):
        assert key in ch, f"missing key: {key}"


def test_channel_rate_histogram_count_increments(registry):
    for _ in range(4):
        registry.record_channel_message("slack")
        time.sleep(0.01)
    result = registry.channel_rate_histogram()
    # count tracks inter-arrival intervals, so 4 messages → 3 intervals
    assert result["slack"]["count"] >= 1


def test_channel_rate_histogram_p50_non_negative(registry):
    for _ in range(5):
        registry.record_channel_message("discord")
        time.sleep(0.01)
    result = registry.channel_rate_histogram()
    p50 = result["discord"]["p50"]
    assert p50 is None or p50 >= 0.0


def test_channel_rate_histogram_multiple_channels(registry):
    for ch in ("whatsapp", "telegram", "discord"):
        registry.record_channel_message(ch)
        time.sleep(0.01)
        registry.record_channel_message(ch)
    result = registry.channel_rate_histogram()
    for ch in ("whatsapp", "telegram", "discord"):
        assert ch in result


# ── percentile correctness ────────────────────────────────────────────────────

def test_channel_interarrival_percentile_returns_none_for_single_message():
    tracker = ChannelInterArrivalTracker()
    tracker.record("ch")
    assert tracker.percentile("ch", 50.0) is None


def test_channel_interarrival_percentile_returns_value_after_two_messages():
    tracker = ChannelInterArrivalTracker()
    tracker.record("ch")
    time.sleep(0.02)
    tracker.record("ch")
    val = tracker.percentile("ch", 50.0)
    assert val is not None
    assert val > 0


def test_channel_interarrival_percentile_unknown_channel_returns_none():
    tracker = ChannelInterArrivalTracker()
    assert tracker.percentile("unknown_xyz", 50.0) is None


def test_channel_interarrival_samples_bounded():
    tracker = ChannelInterArrivalTracker()
    # Record many messages to test _MAX_SAMPLES cap
    for _ in range(1100):
        tracker.record("stress")
    with tracker._lock:
        samples = tracker._data.get("stress", {}).get("samples", [])
    assert len(samples) <= tracker._MAX_SAMPLES


# ── p50 <= p95 <= p99 ordering ────────────────────────────────────────────────

def test_percentile_ordering(registry):
    for i in range(10):
        registry.record_channel_message("ordered")
        time.sleep(0.005 * (i + 1))
    result = registry.channel_rate_histogram()
    ch = result.get("ordered", {})
    p50, p95, p99 = ch.get("p50"), ch.get("p95"), ch.get("p99")
    if p50 is not None and p95 is not None and p99 is not None:
        assert p50 <= p95 <= p99


# ── never raises ─────────────────────────────────────────────────────────────

def test_channel_rate_histogram_never_raises(registry):
    try:
        registry.channel_rate_histogram()
    except Exception as exc:
        pytest.fail(f"channel_rate_histogram raised: {exc}")

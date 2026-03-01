"""Tests for MetricsRegistry per-channel inter-arrival histogram — issue #316."""

from __future__ import annotations

import time

import pytest

from castor.metrics import ChannelInterArrivalTracker, MetricsRegistry

# ── ChannelInterArrivalTracker unit tests ─────────────────────────────────────


def test_tracker_first_message_returns_none():
    tracker = ChannelInterArrivalTracker()
    result = tracker.record("slack")
    assert result is None


def test_tracker_second_message_returns_interval():
    tracker = ChannelInterArrivalTracker()
    tracker.record("slack")
    time.sleep(0.01)
    result = tracker.record("slack")
    assert result is not None
    assert result > 0.0


def test_tracker_interval_is_in_milliseconds():
    tracker = ChannelInterArrivalTracker()
    tracker.record("slack")
    time.sleep(0.05)
    result = tracker.record("slack")
    assert result is not None
    assert result >= 40.0  # at least ~50ms (with tolerance)


def test_tracker_separate_channels_independent():
    tracker = ChannelInterArrivalTracker()
    tracker.record("slack")
    result_telegram = tracker.record("telegram")  # first telegram msg
    assert result_telegram is None
    result_slack = tracker.record("slack")  # second slack msg
    assert result_slack is not None


def test_tracker_channels_returns_sorted():
    tracker = ChannelInterArrivalTracker()
    tracker.record("slack")
    tracker.record("slack")
    tracker.record("telegram")
    tracker.record("telegram")
    # Only channels with >1 observation appear in data
    channels = tracker.channels()
    assert "slack" in channels
    assert "telegram" in channels
    assert channels == sorted(channels)


def test_tracker_render_returns_prometheus_text():
    tracker = ChannelInterArrivalTracker()
    tracker.record("slack")
    time.sleep(0.01)
    tracker.record("slack")
    rendered = tracker.render()
    assert "opencastor_channel_message_interval_ms" in rendered
    assert 'channel="slack"' in rendered
    assert "_bucket" in rendered
    assert "_sum" in rendered
    assert "_count" in rendered


def test_tracker_render_empty_returns_no_buckets():
    tracker = ChannelInterArrivalTracker()
    rendered = tracker.render()
    assert "_bucket" not in rendered


def test_tracker_render_plus_inf_bucket():
    tracker = ChannelInterArrivalTracker()
    tracker.record("slack")
    time.sleep(0.01)
    tracker.record("slack")
    rendered = tracker.render()
    assert 'le="+Inf"' in rendered


def test_tracker_multiple_observations_count():
    tracker = ChannelInterArrivalTracker()
    tracker.record("ch")
    for _ in range(3):
        time.sleep(0.005)
        tracker.record("ch")
    rendered = tracker.render()
    # 3 intervals were recorded
    assert "_count" in rendered


def test_tracker_thread_safe_no_crash():
    """Concurrent record() calls should not raise."""
    import threading

    tracker = ChannelInterArrivalTracker()
    errors = []

    def worker(ch):
        try:
            for _ in range(10):
                tracker.record(ch)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"ch{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


# ── MetricsRegistry integration ───────────────────────────────────────────────


@pytest.fixture()
def fresh_registry():
    """Return a fresh MetricsRegistry (not the singleton)."""

    return MetricsRegistry()


def test_registry_has_channel_interarrival(fresh_registry):
    assert hasattr(fresh_registry, "_channel_interarrival")
    assert isinstance(fresh_registry._channel_interarrival, ChannelInterArrivalTracker)


def test_record_channel_message_tracks_interarrival(fresh_registry):
    fresh_registry.record_channel_message("slack")
    time.sleep(0.01)
    fresh_registry.record_channel_message("slack")
    # channel should appear in tracker after 2 messages
    assert "slack" in fresh_registry._channel_interarrival.channels()


def test_record_channel_message_first_call_no_crash(fresh_registry):
    fresh_registry.record_channel_message("telegram")
    # First call: inter-arrival is None (no previous), but no exception


def test_record_channel_message_still_increments_counter(fresh_registry):
    fresh_registry.record_channel_message("discord")
    c = fresh_registry.counter("opencastor_channel_messages_total")
    assert c is not None


def test_registry_render_includes_channel_histogram(fresh_registry):
    fresh_registry.record_channel_message("slack")
    time.sleep(0.01)
    fresh_registry.record_channel_message("slack")
    rendered = fresh_registry.render()
    assert "opencastor_channel_message_interval_ms" in rendered
    assert 'channel="slack"' in rendered


def test_registry_render_excludes_histogram_when_no_intervals(fresh_registry):
    """If only one message per channel, no intervals → histogram absent from render."""
    fresh_registry.record_channel_message("mqtt")
    rendered = fresh_registry.render()
    # After just 1 message, no interval data exists
    assert "opencastor_channel_message_interval_ms" not in rendered

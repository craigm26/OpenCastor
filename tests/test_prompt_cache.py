"""Tests for castor/prompt_cache.py — CacheStats, build_cached_system_prompt, build_sensor_reminder."""

from __future__ import annotations

import pytest

from castor.prompt_cache import (
    CacheStats,
    build_cached_system_prompt,
    build_sensor_reminder,
    _format_rcan_summary,
)


# ── CacheStats ────────────────────────────────────────────────────────────────


def test_cache_stats_initial():
    cs = CacheStats()
    assert cs.hit_rate == 0.0
    assert cs.total_calls == 0
    assert cs.cache_hits == 0
    assert cs.cache_misses == 0
    assert cs.total_tokens_saved == 0
    assert cs.total_tokens_spent == 0


def test_cache_stats_record_hit():
    cs = CacheStats()

    class FakeUsage:
        cache_read_input_tokens = 1000
        cache_creation_input_tokens = 0

    cs.record(FakeUsage())
    assert cs.cache_hits == 1
    assert cs.cache_misses == 0
    assert cs.hit_rate == 1.0
    d = cs.to_dict()
    assert d["cache_hits"] == 1
    assert d["tokens_saved"] == 1000
    assert d["tokens_spent"] == 0


def test_cache_stats_record_miss():
    cs = CacheStats()

    class FakeUsage:
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 500

    cs.record(FakeUsage())
    assert cs.cache_hits == 0
    assert cs.cache_misses == 1
    assert cs.hit_rate == 0.0
    d = cs.to_dict()
    assert d["cache_misses"] == 1
    assert d["tokens_spent"] == 500
    assert d["tokens_saved"] == 0


def test_cache_stats_hit_rate_calculation():
    cs = CacheStats()

    class H:
        cache_read_input_tokens = 100
        cache_creation_input_tokens = 0

    class M:
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 500

    for _ in range(7):
        cs.record(H())
    for _ in range(3):
        cs.record(M())

    assert abs(cs.hit_rate - 0.7) < 0.01
    assert cs.total_calls == 10


def test_cache_stats_to_dict_keys():
    cs = CacheStats()
    d = cs.to_dict()
    expected_keys = {"hit_rate", "cache_hits", "cache_misses", "total_calls", "tokens_saved", "tokens_spent"}
    assert set(d.keys()) == expected_keys


def test_cache_stats_no_alert_before_warmup():
    cs = CacheStats()

    class M:
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0

    # Only 5 calls — below the 10-call warmup threshold
    for _ in range(5):
        cs.record(M())
    assert cs.alert_if_low(threshold=0.5) is False


def test_cache_stats_alert_fires_after_warmup():
    cs = CacheStats()

    class M:
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 100

    # 15 calls all misses — hit rate 0% < threshold 50%
    for _ in range(15):
        cs.record(M())
    assert cs.alert_if_low(threshold=0.5) is True


def test_cache_stats_no_alert_when_hit_rate_adequate():
    cs = CacheStats()

    class H:
        cache_read_input_tokens = 100
        cache_creation_input_tokens = 0

    # 12 hits out of 12 calls — 100% hit rate, no alert
    for _ in range(12):
        cs.record(H())
    assert cs.alert_if_low(threshold=0.5) is False


def test_cache_stats_alert_logs_message(caplog):
    import logging
    import logging

    cs = CacheStats()

    class M:
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 10

    for _ in range(12):
        cs.record(M())

    import logging as _logging
    test_logger = _logging.getLogger("test_cache_logger")
    with caplog.at_level(logging.WARNING, logger="test_cache_logger"):
        result = cs.alert_if_low(threshold=0.5, logger=test_logger)

    assert result is True
    assert any("CACHE ALERT" in rec.message for rec in caplog.records)


def test_cache_stats_tokens_accumulated():
    cs = CacheStats()

    class H:
        cache_read_input_tokens = 300
        cache_creation_input_tokens = 50

    for _ in range(4):
        cs.record(H())

    assert cs.total_tokens_saved == 1200  # 4 × 300
    assert cs.total_tokens_spent == 200   # 4 × 50


# ── build_cached_system_prompt ────────────────────────────────────────────────


def test_build_cached_system_prompt_returns_blocks():
    blocks = build_cached_system_prompt("You are a robot.")
    assert isinstance(blocks, list)
    assert len(blocks) >= 1
    assert blocks[0]["type"] == "text"
    assert "cache_control" in blocks[0]
    assert blocks[0]["cache_control"]["type"] == "ephemeral"


def test_build_cached_system_prompt_text_content():
    blocks = build_cached_system_prompt("You are a friendly robot.")
    assert blocks[0]["text"] == "You are a friendly robot."


def test_build_cached_system_prompt_no_rcan_single_block():
    blocks = build_cached_system_prompt("Base prompt.")
    assert len(blocks) == 1


def test_build_cached_system_prompt_with_rcan():
    blocks = build_cached_system_prompt("Base prompt.", {"robot_name": "Bob"})
    assert len(blocks) == 2
    # Second block should mention robot name
    assert "Bob" in blocks[1]["text"] or "robot_name" in blocks[1]["text"]
    assert "cache_control" in blocks[1]
    assert blocks[1]["cache_control"]["type"] == "ephemeral"


def test_build_cached_system_prompt_default_when_empty():
    blocks = build_cached_system_prompt("")
    assert "robot" in blocks[0]["text"].lower()


def test_build_cached_system_prompt_rcan_wrapped_in_tag():
    blocks = build_cached_system_prompt("Base.", {"robot_name": "HAL"})
    assert "<robot-config>" in blocks[1]["text"]
    assert "</robot-config>" in blocks[1]["text"]


# ── build_sensor_reminder ─────────────────────────────────────────────────────


def test_build_sensor_reminder_empty():
    result = build_sensor_reminder({})
    assert result == ""


def test_build_sensor_reminder_none_equivalent():
    # Empty dict → empty string (no state block injected = no cache bust)
    result = build_sensor_reminder({})
    assert "<castor-state>" not in result


def test_build_sensor_reminder_with_data():
    result = build_sensor_reminder({"front_distance_m": 0.45, "battery_pct": 72})
    assert "<castor-state>" in result
    assert "0.45m" in result
    assert "72%" in result
    assert "</castor-state>" in result


def test_build_sensor_reminder_distance_formatting():
    result = build_sensor_reminder({"front_distance_m": 1.5})
    assert "1.50m" in result


def test_build_sensor_reminder_battery_formatting():
    result = build_sensor_reminder({"battery_pct": 88.6})
    assert "89%" in result or "88%" in result  # rounded


def test_build_sensor_reminder_speed_and_heading():
    result = build_sensor_reminder({"speed_ms": 0.75, "heading_deg": 180.0})
    assert "0.75m/s" in result
    assert "180.0°" in result


def test_build_sensor_reminder_obstacles():
    result = build_sensor_reminder({"obstacles": ["wall", "chair", "person"]})
    assert "wall" in result
    assert "chair" in result


def test_build_sensor_reminder_unknown_keys():
    result = build_sensor_reminder({"custom_sensor": "active"})
    assert "custom_sensor" in result
    assert "active" in result
    assert "<castor-state>" in result


def test_build_sensor_reminder_unknown_keys_not_in_handled():
    # Only known keys + extra unknown
    result = build_sensor_reminder({
        "front_distance_m": 1.0,
        "lidar_status": "ok",
    })
    assert "lidar_status" in result
    assert "1.00m" in result


def test_build_sensor_reminder_obstacles_truncated_at_5():
    obs = ["a", "b", "c", "d", "e", "f", "g"]
    result = build_sensor_reminder({"obstacles": obs})
    # Should include first 5, not all 7
    assert "f" not in result or result.count(",") <= 4


# ── _format_rcan_summary (internal) ──────────────────────────────────────────


def test_format_rcan_summary_picks_important_keys():
    config = {
        "robot_name": "Artoo",
        "description": "A helpful droid",
        "unimportant_key": "ignored",
    }
    summary = _format_rcan_summary(config)
    assert "Artoo" in summary
    assert "helpful droid" in summary
    assert "unimportant_key" not in summary


def test_format_rcan_summary_empty_config():
    summary = _format_rcan_summary({})
    # Should return truncated repr, not crash
    assert isinstance(summary, str)

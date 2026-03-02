"""Tests for MetricsRegistry.loop_latency_percentiles() — Issue #417."""
from __future__ import annotations

import pytest

from castor.metrics import MetricsRegistry, get_registry


@pytest.fixture()
def reg():
    """Fresh MetricsRegistry (not the singleton)."""
    return MetricsRegistry()


# ── 1. returns a dict ────────────────────────────────────────────────────────

def test_returns_dict(reg):
    result = reg.loop_latency_percentiles()
    assert isinstance(result, dict)


# ── 2. has expected keys ─────────────────────────────────────────────────────

def test_has_expected_keys(reg):
    result = reg.loop_latency_percentiles()
    assert set(result.keys()) == {"p50_ms", "p95_ms", "p99_ms", "sample_count"}


# ── 3. sample_count is 0 when no samples ────────────────────────────────────

def test_sample_count_zero_when_empty(reg):
    result = reg.loop_latency_percentiles()
    assert result["sample_count"] == 0


# ── 4. percentiles are None when no samples ──────────────────────────────────

def test_percentiles_none_when_empty(reg):
    result = reg.loop_latency_percentiles()
    assert result["p50_ms"] is None
    assert result["p95_ms"] is None
    assert result["p99_ms"] is None


# ── 5. after one record_loop, sample_count is 1 ──────────────────────────────

def test_sample_count_one_after_single_record(reg):
    reg.record_loop(50.0)
    result = reg.loop_latency_percentiles()
    assert result["sample_count"] == 1


# ── 6. after multiple calls, percentiles are numeric ─────────────────────────

def test_percentiles_numeric_after_multiple_records(reg):
    for ms in [10.0, 20.0, 30.0, 40.0, 50.0, 100.0, 200.0]:
        reg.record_loop(ms)
    result = reg.loop_latency_percentiles()
    assert isinstance(result["p50_ms"], float)
    assert isinstance(result["p95_ms"], float)
    assert isinstance(result["p99_ms"], float)


# ── 7. p50 <= p95 <= p99 ─────────────────────────────────────────────────────

def test_percentile_ordering(reg):
    for ms in range(1, 101):
        reg.record_loop(float(ms))
    result = reg.loop_latency_percentiles()
    assert result["p50_ms"] <= result["p95_ms"] <= result["p99_ms"]


# ── 8. sample_count capped at 1000 ───────────────────────────────────────────

def test_sample_count_capped_at_1000(reg):
    for i in range(1500):
        reg.record_loop(float(i))
    result = reg.loop_latency_percentiles()
    assert result["sample_count"] == 1000


# ── 9. singleton works via get_registry ──────────────────────────────────────

def test_singleton_via_get_registry():
    r = get_registry()
    result = r.loop_latency_percentiles()
    assert isinstance(result, dict)
    assert "p50_ms" in result


# ── 10. never raises ─────────────────────────────────────────────────────────

def test_never_raises(reg):
    try:
        reg.loop_latency_percentiles()
    except Exception as exc:
        pytest.fail(f"loop_latency_percentiles() raised unexpectedly: {exc}")


# ── 11. reset MetricsRegistry gives fresh state ──────────────────────────────

def test_fresh_registry_has_no_samples():
    r1 = MetricsRegistry()
    r1.record_loop(999.0)
    r2 = MetricsRegistry()
    result = r2.loop_latency_percentiles()
    assert result["sample_count"] == 0


# ── 12. samples reflect recorded values correctly ────────────────────────────

def test_single_value_all_percentiles_equal(reg):
    reg.record_loop(42.0)
    result = reg.loop_latency_percentiles()
    assert result["p50_ms"] == 42.0
    assert result["p95_ms"] == 42.0
    assert result["p99_ms"] == 42.0


# ── 13. cap trims to most-recent 1000 ────────────────────────────────────────

def test_cap_keeps_last_1000_samples(reg):
    # Record 1200 samples; p99 should reflect the tail (higher values)
    for i in range(1200):
        reg.record_loop(float(i))
    result = reg.loop_latency_percentiles()
    # After capping, the 1000 samples are [200..1199]; p99 index = 989 → 1189.0
    assert result["sample_count"] == 1000
    # The maximum value in the retained window is 1199
    assert result["p99_ms"] >= 1000.0

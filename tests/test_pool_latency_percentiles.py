"""Tests for ProviderPool.latency_percentiles() — Issue #414."""
from unittest.mock import MagicMock, patch

import pytest

from castor.providers.pool_provider import ProviderPool


def _make_pool():
    p1 = MagicMock()
    p1.model_name = "mock1"
    p2 = MagicMock()
    p2.model_name = "mock2"
    with patch("castor.providers.get_provider", side_effect=[p1, p2]):
        pool = ProviderPool(
            {
                "pool_strategy": "round_robin",
                "pool_fallback": True,
                "pool": [{"provider": "m1"}, {"provider": "m2"}],
            }
        )
    return pool, p1, p2


def test_latency_percentiles_returns_dict():
    pool, _, _ = _make_pool()
    result = pool.latency_percentiles()
    assert isinstance(result, dict)


def test_latency_percentiles_has_providers_key():
    pool, _, _ = _make_pool()
    result = pool.latency_percentiles()
    assert "providers" in result


def test_latency_percentiles_has_pool_size_key():
    pool, _, _ = _make_pool()
    result = pool.latency_percentiles()
    assert "pool_size" in result


def test_latency_percentiles_providers_is_dict():
    pool, _, _ = _make_pool()
    result = pool.latency_percentiles()
    assert isinstance(result["providers"], dict)


def test_latency_percentiles_pool_size_equals_2():
    pool, _, _ = _make_pool()
    result = pool.latency_percentiles()
    assert result["pool_size"] == 2


def test_latency_percentiles_each_entry_has_p50():
    pool, _, _ = _make_pool()
    result = pool.latency_percentiles()
    for entry in result["providers"].values():
        assert "p50_ms" in entry


def test_latency_percentiles_each_entry_has_p95():
    pool, _, _ = _make_pool()
    result = pool.latency_percentiles()
    for entry in result["providers"].values():
        assert "p95_ms" in entry


def test_latency_percentiles_each_entry_has_p99():
    pool, _, _ = _make_pool()
    result = pool.latency_percentiles()
    for entry in result["providers"].values():
        assert "p99_ms" in entry


def test_latency_percentiles_each_entry_has_sample_count():
    pool, _, _ = _make_pool()
    result = pool.latency_percentiles()
    for entry in result["providers"].values():
        assert "sample_count" in entry


def test_latency_percentiles_sample_count_starts_at_zero():
    pool, _, _ = _make_pool()
    result = pool.latency_percentiles()
    for entry in result["providers"].values():
        assert entry["sample_count"] == 0


def test_latency_percentiles_p50_is_none_or_float_when_no_samples():
    pool, _, _ = _make_pool()
    result = pool.latency_percentiles()
    for entry in result["providers"].values():
        assert entry["p50_ms"] is None or isinstance(entry["p50_ms"], float)


def test_latency_percentiles_pool_size_matches_provider_count():
    pool, _, _ = _make_pool()
    result = pool.latency_percentiles()
    assert result["pool_size"] == len(result["providers"])


def test_latency_percentiles_never_raises():
    pool, _, _ = _make_pool()
    try:
        pool.latency_percentiles()
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"latency_percentiles() raised unexpectedly: {exc}")


def test_latency_percentiles_providers_keys_match_model_names():
    pool, p1, p2 = _make_pool()
    result = pool.latency_percentiles()
    assert "mock1" in result["providers"]
    assert "mock2" in result["providers"]

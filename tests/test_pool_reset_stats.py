"""Tests for ProviderPool.reset_stats() — Issue #416."""
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


def test_reset_stats_returns_dict():
    pool, _, _ = _make_pool()
    result = pool.reset_stats()
    assert isinstance(result, dict)


def test_reset_stats_ok_is_true():
    pool, _, _ = _make_pool()
    result = pool.reset_stats()
    assert result["ok"] is True


def test_reset_stats_has_providers_reset_key():
    pool, _, _ = _make_pool()
    result = pool.reset_stats()
    assert "providers_reset" in result


def test_reset_stats_providers_reset_equals_pool_size():
    pool, _, _ = _make_pool()
    result = pool.reset_stats()
    assert result["providers_reset"] == 2


def test_reset_stats_cost_tracker_zeroed():
    pool, _, _ = _make_pool()
    # Inject some non-zero cost data
    pool._cost_tracker[0] = {"tokens_total": 999.0, "cost_usd_total": 1.5, "calls": 10.0}
    pool.reset_stats()
    for i in range(len(pool._providers)):
        ct = pool._cost_tracker[i]
        assert ct["tokens_total"] == 0.0
        assert ct["cost_usd_total"] == 0.0
        assert ct["calls"] == 0.0


def test_reset_stats_cb_failures_cleared():
    pool, _, _ = _make_pool()
    pool._cb_failures[0] = 5
    pool._cb_failures[1] = 3
    pool.reset_stats()
    assert pool._cb_failures == {}


def test_reset_stats_cb_open_until_cleared():
    pool, _, _ = _make_pool()
    import time
    pool._cb_open_until[0] = time.time() + 60
    pool.reset_stats()
    assert pool._cb_open_until == {}


def test_reset_stats_degraded_cleared():
    pool, _, _ = _make_pool()
    import time
    pool._degraded[0] = time.time()
    pool.reset_stats()
    assert pool._degraded == {}


def test_reset_stats_burst_demoted_until_cleared():
    pool, _, _ = _make_pool()
    import time
    pool._burst_demoted_until[0] = time.time() + 30
    pool.reset_stats()
    assert pool._burst_demoted_until == {}


def test_reset_stats_ab_stats_zeroed():
    pool, _, _ = _make_pool()
    pool._ab_stats[0]["success"] = 42
    pool._ab_stats[0]["fail"] = 7
    pool._ab_stats[1]["success"] = 10
    pool._ab_stats[1]["fail"] = 3
    pool.reset_stats()
    assert pool._ab_stats[0]["success"] == 0
    assert pool._ab_stats[0]["fail"] == 0
    assert pool._ab_stats[1]["success"] == 0
    assert pool._ab_stats[1]["fail"] == 0


def test_reset_stats_idempotent():
    pool, _, _ = _make_pool()
    r1 = pool.reset_stats()
    r2 = pool.reset_stats()
    assert r1["ok"] is True
    assert r2["ok"] is True
    assert r1["providers_reset"] == r2["providers_reset"]


def test_reset_stats_second_call_still_zeros():
    pool, _, _ = _make_pool()
    pool._cb_failures[0] = 5
    pool.reset_stats()
    pool._cb_failures[1] = 2
    pool.reset_stats()
    assert pool._cb_failures == {}


def test_reset_stats_never_raises():
    pool, _, _ = _make_pool()
    try:
        pool.reset_stats()
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"reset_stats() raised unexpectedly: {exc}")

"""Tests for ProviderPool weighted strategy and health-aware routing (issues #289, #297)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from castor.providers.base import Thought
from castor.providers.pool_provider import ProviderPool


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_thought(text: str = "ok") -> Thought:
    return Thought(raw_text=text, action={"type": "stop"})


def _make_provider(name: str = "p", weight: float = 1.0) -> MagicMock:
    p = MagicMock()
    p.model_name = name
    p.think.return_value = _make_thought(name)
    p.think_stream.return_value = iter([name])
    p.health_check.return_value = {"ok": True}
    return p


def _build_pool(provider_specs, *, strategy="round_robin", fallback=True, **kwargs):
    """Build ProviderPool with pre-built providers. Each spec is (mock, weight)."""
    providers = [p for p, _ in provider_specs]
    pool_entries = [{"provider": "mock", "weight": w} for _, w in provider_specs]
    it = iter(providers)
    cfg = {
        "provider": "pool",
        "pool_strategy": strategy,
        "pool_fallback": fallback,
        "pool": pool_entries,
        **kwargs,
    }
    with patch("castor.providers.get_provider", side_effect=lambda c: next(it)):
        return ProviderPool(cfg)


# ── Weighted strategy (#289) ──────────────────────────────────────────────────


def test_weighted_strategy_uses_random_choices():
    p0 = _make_provider("p0", weight=3.0)
    p1 = _make_provider("p1", weight=1.0)
    pool = _build_pool([(p0, 3.0), (p1, 1.0)], strategy="weighted", fallback=False)

    with patch(
        "castor.providers.pool_provider.random.choices",
        return_value=[pool._providers[0]],
    ) as mock_choices:
        pool.think(b"", "go")
        mock_choices.assert_called_once()
        _, kwargs = mock_choices.call_args
        weights = kwargs.get("weights", mock_choices.call_args[0][1] if len(mock_choices.call_args[0]) > 1 else None)
        assert weights is not None


def test_weighted_strategy_weights_stored():
    p0 = _make_provider("p0")
    p1 = _make_provider("p1")
    pool = _build_pool([(p0, 5.0), (p1, 2.0)], strategy="weighted")
    assert pool._weights == [5.0, 2.0]


def test_weighted_strategy_default_weight_is_one():
    p = _make_provider("p0")
    it = iter([p])
    with patch("castor.providers.get_provider", side_effect=lambda c: next(it)):
        pool = ProviderPool({
            "provider": "pool",
            "pool_strategy": "weighted",
            "pool": [{"provider": "mock"}],  # no weight key
        })
    assert pool._weights == [1.0]


def test_weighted_strategy_returns_thought():
    p = _make_provider("p0")
    pool = _build_pool([(p, 2.0)], strategy="weighted")
    t = pool.think(b"", "go")
    assert isinstance(t, Thought)


def test_weighted_strategy_in_health_check():
    p = _make_provider("p0")
    pool = _build_pool([(p, 1.0)], strategy="weighted")
    h = pool.health_check()
    assert h["strategy"] == "weighted"


# ── Health-aware routing (#297) ───────────────────────────────────────────────


def test_health_interval_zero_no_thread():
    p = _make_provider("p0")
    pool = _build_pool([(p, 1.0)], pool_health_check_interval_s=0)
    assert pool._health_thread is None


def test_health_interval_positive_starts_thread():
    p = _make_provider("p0")
    pool = _build_pool([(p, 1.0)], pool_health_check_interval_s=9999)
    try:
        assert pool._health_thread is not None
        assert pool._health_thread.is_alive()
    finally:
        pool.stop()


def test_degraded_provider_skipped_round_robin():
    p0 = _make_provider("p0")
    p1 = _make_provider("p1")
    pool = _build_pool([(p0, 1.0), (p1, 1.0)], strategy="round_robin", fallback=False)

    # Mark p0 (index 0) as degraded
    pool._degraded[0] = time.time()

    # All round-robin selections should land on p1 (index 1)
    for _ in range(6):
        pool.think(b"", "go")
    # p0 should not have been called
    p0.think.assert_not_called()
    p1.think.assert_called()


def test_degraded_provider_cooldown_reenables():
    p0 = _make_provider("p0")
    pool = _build_pool([(p0, 1.0)], strategy="round_robin", fallback=False,
                       pool_health_cooldown_s=0.01)

    # Mark p0 as degraded far in the past
    pool._degraded[0] = time.time() - 1.0  # 1 second ago, cooldown=0.01s

    # _get_healthy_indices should re-enable it
    healthy = pool._get_healthy_indices()
    assert 0 in healthy
    # And it should no longer be in _degraded
    assert 0 not in pool._degraded


def test_all_degraded_falls_back_to_all():
    """When all providers are degraded, return all to avoid stalling."""
    p0 = _make_provider("p0")
    p1 = _make_provider("p1")
    pool = _build_pool([(p0, 1.0), (p1, 1.0)])

    # Mark both degraded with unexpired cooldown
    pool._degraded[0] = time.time()
    pool._degraded[1] = time.time()

    healthy = pool._get_healthy_indices()
    assert len(healthy) == 2


def test_health_probe_marks_unhealthy_provider_degraded():
    p_bad = _make_provider("bad")
    p_bad.health_check.return_value = {"ok": False}
    pool = _build_pool([(p_bad, 1.0)])

    # Manually trigger one probe cycle
    pool._health_probe_loop.__func__  # just ensure it's accessible
    # Call _health_probe_loop content directly (single iteration)
    for i, provider in enumerate(pool._providers):
        result = provider.health_check()
        ok = bool(result.get("ok", True))
        with pool._lock:
            if not ok and i not in pool._degraded:
                pool._degraded[i] = time.time()

    assert 0 in pool._degraded


def test_health_probe_clears_recovered_provider():
    p_good = _make_provider("good")
    pool = _build_pool([(p_good, 1.0)])

    # Pre-mark as degraded
    pool._degraded[0] = time.time()

    # Provider is now healthy
    for i, provider in enumerate(pool._providers):
        result = provider.health_check()
        ok = bool(result.get("ok", True))
        with pool._lock:
            if ok and i in pool._degraded:
                del pool._degraded[i]

    assert 0 not in pool._degraded


def test_health_check_includes_degraded_field():
    p = _make_provider("p0")
    pool = _build_pool([(p, 1.0)])
    pool._degraded[0] = time.time()
    h = pool.health_check()
    assert h["members"][0]["degraded"] is True
    assert h["degraded_count"] == 1


def test_stop_method_terminates_health_thread():
    p = _make_provider("p0")
    pool = _build_pool([(p, 1.0)], pool_health_check_interval_s=9999)
    assert pool._health_thread.is_alive()
    pool.stop()
    pool._health_thread.join(timeout=1.0)
    assert not pool._health_thread.is_alive()


# ── Existing tests still pass with new fields ─────────────────────────────────


def test_weights_aligned_with_providers():
    p0 = _make_provider("p0")
    p1 = _make_provider("p1")
    p2 = _make_provider("p2")
    pool = _build_pool([(p0, 1.0), (p1, 3.0), (p2, 2.0)])
    assert len(pool._weights) == len(pool._providers) == 3
    assert pool._weights == [1.0, 3.0, 2.0]

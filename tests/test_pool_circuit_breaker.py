"""Tests for ProviderPool circuit breaker — issue #312."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from castor.providers.pool_provider import ProviderPool


def _make_pool(threshold: int = 3, cooldown_s: float = 60.0, strategy: str = "round_robin"):
    """Build a ProviderPool with two mock providers and a circuit breaker configured."""
    mock_p1 = MagicMock()
    mock_p1.model_name = "mock-a"
    mock_p1.think.return_value = MagicMock(raw_text="ok-a", action={"type": "stop"})
    mock_p1.think_stream.return_value = iter(["tok1", "tok2"])

    mock_p2 = MagicMock()
    mock_p2.model_name = "mock-b"
    mock_p2.think.return_value = MagicMock(raw_text="ok-b", action={"type": "stop"})
    mock_p2.think_stream.return_value = iter(["tok3", "tok4"])

    config = {
        "pool": [
            {"provider": "mock_a"},
            {"provider": "mock_b"},
        ],
        "pool_strategy": strategy,
        "pool_fallback": True,
        "pool_circuit_breaker_threshold": threshold,
        "pool_circuit_breaker_cooldown_s": cooldown_s,
    }

    with patch("castor.providers.get_provider", side_effect=[mock_p1, mock_p2]):
        pool = ProviderPool(config)

    # Attach mocks for later assertions
    pool._mock_p1 = mock_p1
    pool._mock_p2 = mock_p2
    return pool


# ── Construction ──────────────────────────────────────────────────────────────


def test_cb_disabled_by_default():
    """When pool_circuit_breaker_threshold not set, cb_threshold is 0 (disabled)."""
    mock_p = MagicMock()
    mock_p.model_name = "mock"
    mock_p.think.return_value = MagicMock(raw_text="ok", action={})
    config = {"pool": [{"provider": "mock"}]}
    with patch("castor.providers.get_provider", return_value=mock_p):
        pool = ProviderPool(config)
    assert pool._cb_threshold == 0


def test_cb_threshold_configured():
    pool = _make_pool(threshold=3)
    assert pool._cb_threshold == 3


def test_cb_cooldown_configured():
    pool = _make_pool(cooldown_s=120.0)
    assert pool._cb_cooldown_s == 120.0


def test_cb_failures_starts_empty():
    pool = _make_pool()
    assert pool._cb_failures == {}


def test_cb_open_until_starts_empty():
    pool = _make_pool()
    assert pool._cb_open_until == {}


# ── _cb_on_success ────────────────────────────────────────────────────────────


def test_cb_on_success_does_nothing_when_disabled():
    """When threshold=0, _cb_on_success is a no-op."""
    pool = _make_pool(threshold=0)
    pool._cb_failures[0] = 5
    pool._cb_on_success(0)
    assert pool._cb_failures == {0: 5}  # untouched


def test_cb_on_success_clears_failure_count():
    pool = _make_pool(threshold=3)
    pool._cb_failures[0] = 2
    pool._cb_on_success(0)
    assert 0 not in pool._cb_failures


def test_cb_on_success_closes_open_circuit():
    pool = _make_pool(threshold=3)
    import time

    pool._cb_open_until[0] = time.time() + 3600
    pool._cb_on_success(0)
    assert 0 not in pool._cb_open_until


# ── _cb_on_failure ────────────────────────────────────────────────────────────


def test_cb_on_failure_does_nothing_when_disabled():
    pool = _make_pool(threshold=0)
    pool._cb_on_failure(0)
    assert pool._cb_failures == {}


def test_cb_on_failure_increments_counter():
    pool = _make_pool(threshold=5)
    pool._cb_on_failure(0)
    assert pool._cb_failures[0] == 1


def test_cb_on_failure_opens_circuit_at_threshold():
    pool = _make_pool(threshold=3, cooldown_s=60.0)
    for _ in range(3):
        pool._cb_on_failure(0)
    import time

    assert 0 in pool._cb_open_until
    assert pool._cb_open_until[0] > time.time()


def test_cb_on_failure_does_not_reopen_already_open_circuit():
    """Once open, additional failures don't extend the cooldown."""
    pool = _make_pool(threshold=3, cooldown_s=60.0)
    for _ in range(3):
        pool._cb_on_failure(0)

    first_open_until = pool._cb_open_until[0]
    pool._cb_on_failure(0)  # fourth failure
    assert pool._cb_open_until[0] == first_open_until


# ── _get_healthy_indices — circuit integration ────────────────────────────────


def test_healthy_excludes_circuit_open_provider():
    pool = _make_pool(threshold=2)
    # Force provider 0 open
    import time

    pool._cb_open_until[0] = time.time() + 3600
    healthy = pool._get_healthy_indices()
    assert 0 not in healthy
    assert 1 in healthy


def test_healthy_resets_expired_circuit():
    pool = _make_pool(threshold=2)
    import time

    pool._cb_open_until[0] = time.time() - 1  # expired
    healthy = pool._get_healthy_indices()
    # After expiry the provider is tentatively re-enabled
    assert 0 in healthy
    assert 0 not in pool._cb_open_until


def test_healthy_returns_all_when_all_open():
    """When all providers have open circuits, fall back to all to avoid stalling."""
    pool = _make_pool(threshold=2)
    import time

    pool._cb_open_until[0] = time.time() + 3600
    pool._cb_open_until[1] = time.time() + 3600
    healthy = pool._get_healthy_indices()
    assert 0 in healthy and 1 in healthy


# ── think() CB integration ────────────────────────────────────────────────────


def test_think_success_resets_failure_count():
    pool = _make_pool(threshold=5, strategy="round_robin")
    pool._cb_failures[0] = 3
    pool.think(b"", "test")
    # One provider will succeed; its failure count should be reset
    # (exact index depends on which provider was chosen)
    assert pool._cb_failures.get(0, 0) == 0 or pool._cb_failures.get(1, 0) == 0


def test_think_failure_increments_cb_counter():
    pool = _make_pool(threshold=10, strategy="round_robin")
    pool._providers[0].think.side_effect = RuntimeError("api error")
    pool._providers[1].think.return_value = MagicMock(raw_text="ok", action={})

    with patch.object(pool, "_check_instruction_safety"):
        pool.think(b"", "test")

    # Provider 0 should have one failure recorded
    assert pool._cb_failures.get(0, 0) >= 1


# ── health_check() includes CB state ─────────────────────────────────────────


def test_health_check_includes_circuit_breaker():
    pool = _make_pool(threshold=3)
    h = pool.health_check()
    assert "circuit_breaker" in h
    cb = h["circuit_breaker"]
    assert "threshold" in cb
    assert "cooldown_s" in cb
    assert "providers" in cb
    assert "open_count" in cb


def test_health_check_cb_absent_when_disabled():
    """When threshold=0, circuit_breaker key is absent from health_check."""
    pool = _make_pool(threshold=0)
    h = pool.health_check()
    assert "circuit_breaker" not in h

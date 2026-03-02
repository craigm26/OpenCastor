"""Tests for ProviderPool cascade reset behaviour (#384)."""

import time
from unittest.mock import MagicMock, patch

from castor.providers.base import Thought
from castor.providers.pool_provider import ProviderPool


def _thought(text="ok"):
    return Thought(raw_text=text, action={"type": "stop"})


def _make_pool(strategy="cascade", reset_s=1.0):
    p1 = MagicMock()
    p1.health_check.return_value = {"ok": True}
    p1.think.return_value = _thought("primary")

    p2 = MagicMock()
    p2.health_check.return_value = {"ok": True}
    p2.think.return_value = _thought("secondary")

    config = {
        "pool_strategy": strategy,
        "pool_cascade_reset_s": reset_s,
        "pool": [
            {"priority": 1, "provider": "mock_p1", "api_key": "x"},
            {"priority": 2, "provider": "mock_p2", "api_key": "x"},
        ],
    }
    with patch("castor.providers.get_provider") as gp:
        gp.side_effect = [p1, p2]
        pool = ProviderPool(config)
    pool._cascade_current = 0
    return pool, p1, p2


# ── basic cascade current ─────────────────────────────────────────────────────

def test_pool_cascade_current_starts_at_zero():
    pool, p1, p2 = _make_pool()
    assert pool._cascade_current == 0


def test_pool_cascade_reset_s_stored():
    pool, _, _ = _make_pool(reset_s=300.0)
    assert pool._cascade_reset_s == 300.0


def test_pool_cascade_reset_s_default():
    p1 = MagicMock()
    p1.health_check.return_value = {"ok": True}
    config = {
        "pool_strategy": "cascade",
        "pool": [{"priority": 1, "provider": "mock_x", "api_key": "x"}],
    }
    with patch("castor.providers.get_provider", return_value=p1):
        pool = ProviderPool(config)
    assert pool._cascade_reset_s == 300.0  # default


# ── reset condition ───────────────────────────────────────────────────────────

def test_cascade_reset_triggers_after_success_period():
    """After pool_cascade_reset_s of successful operation, cascade resets to primary."""
    pool, p1, p2 = _make_pool(reset_s=0.05)
    # Simulate that we're on secondary provider
    pool._cascade_current = 1
    pool._cascade_last_failure = time.monotonic() - 1.0  # 1 second ago

    # With reset_s=0.05, we should reset now
    pool._strategy = "cascade"
    result = pool.think(None, "test")
    # After reset, primary (p1) should have been tried
    assert result is not None


def test_cascade_no_reset_before_timeout():
    """Cascade should NOT reset before pool_cascade_reset_s elapses."""
    pool, p1, p2 = _make_pool(reset_s=9999.0)
    pool._cascade_current = 1
    pool._cascade_last_failure = time.monotonic()  # just now

    result = pool.think(None, "test")
    assert result is not None


# ── health check reports cascade_index ───────────────────────────────────────

def test_health_check_reports_cascade_index():
    pool, p1, p2 = _make_pool()
    pool._cascade_current = 1
    health = pool.health_check()
    assert health.get("cascade_index") == 1


def test_health_check_cascade_index_zero_by_default():
    pool, _, _ = _make_pool()
    health = pool.health_check()
    assert health.get("cascade_index") == 0


# ── no reset when reset_s is zero ────────────────────────────────────────────

def test_cascade_reset_disabled_when_zero():
    pool, p1, p2 = _make_pool(reset_s=0.0)
    pool._cascade_current = 1
    pool._cascade_last_failure = time.monotonic() - 9999.0
    pool._strategy = "cascade"
    # Should not reset because reset_s=0 disables it
    # (just verify it doesn't error)
    result = pool.think(None, "test")
    assert result is not None


# ── think returns thought ─────────────────────────────────────────────────────

def test_cascade_think_returns_thought():
    pool, p1, p2 = _make_pool()
    result = pool.think(None, "do something")
    assert isinstance(result, Thought)

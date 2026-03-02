"""Tests for ProviderPool sticky session — issue #359."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from castor.providers.base import Thought


def _make_pool(sticky=True, ttl_s=3600.0, n=2):
    from castor.providers.pool_provider import ProviderPool

    mocks = []
    pool_entries = []
    for i in range(n):
        m = MagicMock()
        m.think.return_value = Thought(raw_text=f"p{i}", action={"type": "stop"})
        m.health_check.return_value = {"ok": True, "mode": "mock"}
        mocks.append(m)
        pool_entries.append({"provider": f"mock{i}", "api_key": "x", "model": f"m{i}"})

    with patch("castor.providers.get_provider") as gp:
        gp.side_effect = mocks
        pool = ProviderPool(
            {
                "pool": pool_entries,
                "pool_strategy": "round_robin",
                "pool_sticky_session": sticky,
                "pool_sticky_ttl_s": ttl_s,
            }
        )
    return pool, mocks


# ── config ────────────────────────────────────────────────────────────────────


def test_sticky_session_enabled_attr():
    pool, _ = _make_pool(sticky=True)
    assert pool._sticky_session is True


def test_sticky_session_disabled_attr():
    pool, _ = _make_pool(sticky=False)
    assert pool._sticky_session is False


def test_sticky_ttl_stored():
    pool, _ = _make_pool(ttl_s=1800.0)
    assert pool._sticky_ttl_s == pytest.approx(1800.0)


def test_sticky_map_initially_empty():
    pool, _ = _make_pool()
    assert pool._sticky_map == {}


# ── _get_sticky_provider ──────────────────────────────────────────────────────


def test_get_sticky_none_when_disabled():
    pool, _ = _make_pool(sticky=False)
    pool._sticky_map["conv1"] = (0, time.time() + 3600)
    assert pool._get_sticky_provider("conv1") is None


def test_get_sticky_none_when_empty_id():
    pool, _ = _make_pool(sticky=True)
    pool._sticky_map["conv1"] = (0, time.time() + 3600)
    assert pool._get_sticky_provider("") is None


def test_get_sticky_returns_bound_provider():
    pool, mocks = _make_pool(sticky=True)
    pool._sticky_map["conv1"] = (0, time.time() + 3600)
    result = pool._get_sticky_provider("conv1")
    assert result is not None
    idx, prov = result
    assert idx == 0
    assert prov is mocks[0]


def test_get_sticky_returns_none_for_expired():
    pool, _ = _make_pool(sticky=True)
    pool._sticky_map["conv1"] = (0, time.time() - 1)  # already expired
    result = pool._get_sticky_provider("conv1")
    assert result is None
    assert "conv1" not in pool._sticky_map  # expired entry cleaned up


def test_get_sticky_none_for_unknown_conversation():
    pool, _ = _make_pool(sticky=True)
    assert pool._get_sticky_provider("unknown") is None


# ── _set_sticky_provider ──────────────────────────────────────────────────────


def test_set_sticky_records_binding():
    pool, _ = _make_pool(sticky=True, ttl_s=300.0)
    pool._set_sticky_provider("conv42", 1)
    assert "conv42" in pool._sticky_map
    idx, expiry = pool._sticky_map["conv42"]
    assert idx == 1
    assert expiry > time.time()
    assert expiry <= time.time() + 301


def test_set_sticky_does_nothing_when_disabled():
    pool, _ = _make_pool(sticky=False)
    pool._set_sticky_provider("conv42", 0)
    assert "conv42" not in pool._sticky_map


def test_set_sticky_does_nothing_for_empty_id():
    pool, _ = _make_pool(sticky=True)
    pool._set_sticky_provider("", 0)
    assert "" not in pool._sticky_map


# ── think() respects sticky binding ──────────────────────────────────────────


def test_think_binds_conversation_on_first_call():
    pool, mocks = _make_pool(sticky=True)
    pool.think(b"", "go forward", conversation_id="session-1")
    assert "session-1" in pool._sticky_map


def test_think_reuses_same_provider_for_conversation():
    pool, mocks = _make_pool(sticky=True, n=2)
    # First call — binds to some provider
    pool.think(b"", "step 1", conversation_id="sess-A")
    first_idx, _ = pool._sticky_map["sess-A"]

    # Second call with same conversation_id — should use the same provider
    pool.think(b"", "step 2", conversation_id="sess-A")
    second_idx, _ = pool._sticky_map["sess-A"]
    assert first_idx == second_idx


def test_think_different_conversations_can_use_different_providers():
    pool, mocks = _make_pool(sticky=True, n=2)
    # Manually bind two conversations to different providers
    pool._sticky_map["sess-A"] = (0, time.time() + 3600)
    pool._sticky_map["sess-B"] = (1, time.time() + 3600)

    pool.think(b"", "a", conversation_id="sess-A")
    pool.think(b"", "b", conversation_id="sess-B")

    # Both conversations should still be bound to their respective providers
    assert pool._sticky_map["sess-A"][0] == 0
    assert pool._sticky_map["sess-B"][0] == 1


def test_think_no_conversation_id_uses_normal_routing():
    pool, mocks = _make_pool(sticky=True)
    pool.think(b"", "no conv")  # No conversation_id
    assert pool._sticky_map == {}  # Nothing should be bound


# ── health_check reports sticky state ────────────────────────────────────────


def test_health_check_includes_sticky_state():
    pool, _ = _make_pool(sticky=True, ttl_s=600.0)
    h = pool.health_check()
    assert "sticky_session" in h
    assert h["sticky_session"]["enabled"] is True
    assert h["sticky_session"]["ttl_s"] == pytest.approx(600.0)
    assert "active_bindings" in h["sticky_session"]


def test_health_check_sticky_disabled():
    pool, _ = _make_pool(sticky=False)
    h = pool.health_check()
    assert h["sticky_session"]["enabled"] is False


def test_health_check_active_bindings_count():
    pool, _ = _make_pool(sticky=True)
    pool._sticky_map["c1"] = (0, time.time() + 3600)
    pool._sticky_map["c2"] = (1, time.time() + 3600)
    h = pool.health_check()
    assert h["sticky_session"]["active_bindings"] == 2

"""Tests for castor/providers/pool_provider.py — ProviderPool (issue #278)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from castor.providers.base import Thought
from castor.providers.pool_provider import ProviderPool

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_thought(text: str = "ok") -> Thought:
    return Thought(raw_text=text, action={"type": "stop"})


def _make_provider(name: str = "p", thought_text: str = "ok") -> MagicMock:
    p = MagicMock()
    p.model_name = name
    p.think.return_value = _make_thought(thought_text)
    p.think_stream.return_value = iter([thought_text])
    p.health_check.return_value = {"ok": True}
    return p


def _build_pool(providers, *, strategy="round_robin", fallback=True):
    """Create ProviderPool with pre-built provider mocks via get_provider patch."""
    it = iter(providers)
    cfg = {
        "provider": "pool",
        "pool_strategy": strategy,
        "pool_fallback": fallback,
        "pool": [{"provider": "mock"} for _ in providers],
    }
    with patch("castor.providers.get_provider", side_effect=lambda c: next(it)):
        return ProviderPool(cfg)


# ── Construction ──────────────────────────────────────────────────────────────


def test_empty_pool_raises():
    with pytest.raises(ValueError, match="at least one"):
        ProviderPool({"provider": "pool", "pool": []})


def test_all_failing_providers_raises():
    def _fail(cfg):
        raise RuntimeError("no provider")

    with patch("castor.providers.get_provider", side_effect=_fail):
        with pytest.raises(RuntimeError, match="no providers could be initialised"):
            ProviderPool({"provider": "pool", "pool": [{"provider": "mock"}]})


def test_partial_failures_accepted():
    p1 = _make_provider("p1")
    call_count = [0]

    def _get(cfg):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("bad key")
        return p1

    with patch("castor.providers.get_provider", side_effect=_get):
        pool = ProviderPool({"provider": "pool", "pool": [{"provider": "a"}, {"provider": "b"}]})
    assert len(pool._providers) == 1


# ── model_name ────────────────────────────────────────────────────────────────


def test_model_name_combines_unique_names():
    p0 = _make_provider("gemini-flash")
    p1 = _make_provider("gemini-flash")  # duplicate — should appear only once
    p2 = _make_provider("claude-haiku")
    pool = _build_pool([p0, p1, p2])
    name = pool.model_name
    assert "gemini-flash" in name
    assert "claude-haiku" in name
    assert name.count("gemini-flash") == 1


def test_model_name_no_providers_returns_pool():
    # Edge: all providers have None model_name
    p = _make_provider()
    del p.model_name  # MagicMock — remove attribute
    p.model_name = None
    pool = _build_pool([p])
    assert pool.model_name == "pool"


# ── Round-robin strategy ──────────────────────────────────────────────────────


def test_round_robin_cycles_providers():
    p0 = _make_provider("p0")
    p1 = _make_provider("p1")
    pool = _build_pool([p0, p1], strategy="round_robin", fallback=False)

    called = []
    for _ in range(4):
        pool.think(b"", "go")
        called.append(pool._current_index)

    assert called == [0, 1, 0, 1]


# ── Random strategy ───────────────────────────────────────────────────────────


def test_random_strategy_calls_choice():
    p0 = _make_provider("p0")
    p1 = _make_provider("p1")
    pool = _build_pool([p0, p1], strategy="random", fallback=False)

    with patch(
        "castor.providers.pool_provider.random.choice", return_value=pool._providers[0]
    ) as mock_choice:
        pool.think(b"", "go")
        mock_choice.assert_called_once()


# ── think / fallback ──────────────────────────────────────────────────────────


def test_think_returns_thought():
    p = _make_provider("p0")
    pool = _build_pool([p])
    t = pool.think(b"", "go")
    assert isinstance(t, Thought)


def test_think_fallback_on_failure():
    p_fail = _make_provider("fail")
    p_fail.think.side_effect = RuntimeError("quota")
    p_ok = _make_provider("ok")
    pool = _build_pool([p_fail, p_ok], fallback=True)
    t = pool.think(b"", "go")
    assert isinstance(t, Thought)
    p_ok.think.assert_called_once()


def test_think_no_fallback_propagates_error():
    p_fail = _make_provider("fail")
    p_fail.think.side_effect = RuntimeError("quota")
    pool = _build_pool([p_fail], fallback=False)
    with pytest.raises(RuntimeError, match="quota"):
        pool.think(b"", "go")


def test_think_all_fail_raises():
    p0 = _make_provider("p0")
    p0.think.side_effect = RuntimeError("quota")
    p1 = _make_provider("p1")
    p1.think.side_effect = RuntimeError("quota")
    pool = _build_pool([p0, p1], fallback=True)
    with pytest.raises(RuntimeError, match="all 2 providers failed"):
        pool.think(b"", "go")


def test_think_calls_instruction_safety():
    p = _make_provider("p0")
    pool = _build_pool([p])
    with patch.object(pool, "_check_instruction_safety") as mock_safety:
        pool.think(b"", "go")
        mock_safety.assert_called_once_with("go")


# ── think_stream ──────────────────────────────────────────────────────────────


def test_think_stream_yields_tokens():
    p = _make_provider("p0")
    p.think_stream.return_value = iter(["hello", " world"])
    pool = _build_pool([p])
    tokens = list(pool.think_stream(b"", "go"))
    assert tokens == ["hello", " world"]


def test_think_stream_fallback_on_failure():
    p_fail = _make_provider("fail")
    p_fail.think_stream.side_effect = RuntimeError("stream error")
    p_ok = _make_provider("ok")
    p_ok.think_stream.return_value = iter(["ok token"])
    pool = _build_pool([p_fail, p_ok], fallback=True)
    tokens = list(pool.think_stream(b"", "go"))
    assert tokens == ["ok token"]


def test_think_stream_all_fail_raises():
    p0 = _make_provider("p0")
    p0.think_stream.side_effect = RuntimeError("quota")
    p1 = _make_provider("p1")
    p1.think_stream.side_effect = RuntimeError("quota")
    pool = _build_pool([p0, p1], fallback=True)
    with pytest.raises(RuntimeError, match="all 2 stream providers failed"):
        list(pool.think_stream(b"", "go"))


def test_think_stream_calls_instruction_safety():
    p = _make_provider("p0")
    pool = _build_pool([p])
    with patch.object(pool, "_check_instruction_safety") as mock_safety:
        list(pool.think_stream(b"", "go"))
        mock_safety.assert_called_once_with("go")


# ── health_check ──────────────────────────────────────────────────────────────


def test_health_check_all_ok():
    p0 = _make_provider("p0")
    p1 = _make_provider("p1")
    pool = _build_pool([p0, p1])
    h = pool.health_check()
    assert h["ok"] is True
    assert h["pool_size"] == 2
    assert len(h["members"]) == 2


def test_health_check_partial_failure():
    p0 = _make_provider("p0")
    p0.health_check.side_effect = RuntimeError("hardware error")
    p1 = _make_provider("p1")
    pool = _build_pool([p0, p1])
    h = pool.health_check()
    assert h["ok"] is False
    assert h["members"][0].get("error") is not None


def test_health_check_contains_strategy():
    p = _make_provider("p0")
    pool = _build_pool([p], strategy="random")
    h = pool.health_check()
    assert h["strategy"] == "random"


def test_health_check_contains_init_errors():
    call_count = [0]

    def _get(cfg):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("key missing")
        return _make_provider("px")

    with patch("castor.providers.get_provider", side_effect=_get):
        pool = ProviderPool({"provider": "pool", "pool": [{"provider": "a"}, {"provider": "b"}]})
    h = pool.health_check()
    assert len(h["init_errors"]) == 1


# ── providers/__init__ registration ───────────────────────────────────────────


def test_builtin_get_provider_pool():
    """'pool' provider name in _builtin_get_provider should create ProviderPool."""
    from castor.providers import _builtin_get_provider

    p = _make_provider("px")
    with patch("castor.providers.get_provider", return_value=p):
        pool = _builtin_get_provider({"provider": "pool", "pool": [{"provider": "mock"}]})
    assert isinstance(pool, ProviderPool)


def test_builtin_get_provider_pool_alias():
    """'provider_pool' alias should also work."""
    from castor.providers import _builtin_get_provider

    p = _make_provider("px")
    with patch("castor.providers.get_provider", return_value=p):
        pool = _builtin_get_provider({"provider": "provider_pool", "pool": [{"provider": "mock"}]})
    assert isinstance(pool, ProviderPool)

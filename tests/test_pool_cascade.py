"""Tests for ProviderPool cascade strategy (issue #299)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from castor.providers.base import Thought
from castor.providers.pool_provider import ProviderPool


def _make_thought(text: str = "ok") -> Thought:
    return Thought(raw_text=text, action={"type": "stop"})


def _make_provider(name: str = "p") -> MagicMock:
    p = MagicMock()
    p.model_name = name
    p.think.return_value = _make_thought(name)
    p.think_stream.return_value = iter([name])
    p.health_check.return_value = {"ok": True}
    return p


def _build_cascade(provider_specs, **kwargs):
    """Build a ProviderPool with cascade strategy."""
    providers = [p for p, _ in provider_specs]
    pool_entries = [{"provider": "mock", "priority": pri} for _, pri in provider_specs]
    it = iter(providers)
    cfg = {
        "provider": "pool",
        "pool_strategy": "cascade",
        "pool_fallback": False,
        "pool": pool_entries,
        **kwargs,
    }
    with patch("castor.providers.get_provider", side_effect=lambda c: next(it)):
        return ProviderPool(cfg)


# ── Basic cascade behavior ─────────────────────────────────────────────────────


def test_cascade_strategy_stored():
    p = _make_provider("p0")
    pool = _build_cascade([(p, 0)])
    assert pool._strategy == "cascade"


def test_cascade_order_by_priority():
    p0 = _make_provider("p0")
    p1 = _make_provider("p1")
    # p1 has priority 0 (lower = first), p0 has priority 1
    pool = _build_cascade([(p0, 1), (p1, 0)])
    # _cascade_order should put p1 (index 1) first
    assert pool._cascade_order[0] == 1


def test_cascade_thinks_with_primary():
    p0 = _make_provider("p0")
    pool = _build_cascade([(p0, 0)])
    t = pool.think(b"", "go")
    assert isinstance(t, Thought)
    p0.think.assert_called_once()


def test_cascade_advances_on_failure():
    p0 = _make_provider("p0")
    p1 = _make_provider("p1")
    p0.think.side_effect = RuntimeError("p0 failed")
    pool = _build_cascade([(p0, 0), (p1, 1)])
    t = pool.think(b"", "go")
    assert t.raw_text == "p1"
    p0.think.assert_called_once()
    p1.think.assert_called_once()


def test_cascade_all_fail_raises():
    p0 = _make_provider("p0")
    p0.think.side_effect = RuntimeError("failed")
    pool = _build_cascade([(p0, 0)])
    with pytest.raises(RuntimeError, match="all.*providers failed"):
        pool.think(b"", "go")


def test_cascade_current_index_advances():
    p0 = _make_provider("p0")
    p1 = _make_provider("p1")
    p0.think.side_effect = RuntimeError("fail")
    pool = _build_cascade([(p0, 0), (p1, 1)])
    pool.think(b"", "go")
    # After failure, cascade_current should have advanced
    assert pool._cascade_current == 1


def test_cascade_resets_after_timeout():
    import time

    p0 = _make_provider("p0")
    p1 = _make_provider("p1")
    pool = _build_cascade([(p0, 0), (p1, 1)], pool_cascade_reset_s=0.01)
    # Manually advance cascade
    pool._cascade_current = 1
    pool._cascade_last_failure = time.monotonic() - 1.0  # 1s ago, reset_s=0.01s
    # Next call should reset to primary
    pool.think(b"", "go")
    assert pool._cascade_current == 0


def test_cascade_health_check_includes_cascade_index():
    p = _make_provider("p0")
    pool = _build_cascade([(p, 0)])
    h = pool.health_check()
    assert "cascade_index" in h
    assert "cascade_provider_index" in h


def test_cascade_health_check_strategy():
    p = _make_provider("p0")
    pool = _build_cascade([(p, 0)])
    h = pool.health_check()
    assert h["strategy"] == "cascade"


def test_cascade_stream_works():
    p = _make_provider("p0")
    pool = _build_cascade([(p, 0)])
    chunks = list(pool.think_stream(b"", "go"))
    assert chunks == ["p0"]


def test_cascade_stream_advances_on_failure():
    p0 = _make_provider("p0")
    p1 = _make_provider("p1")
    p0.think_stream.side_effect = RuntimeError("p0 stream failed")
    pool = _build_cascade([(p0, 0), (p1, 1)])
    chunks = list(pool.think_stream(b"", "go"))
    assert chunks == ["p1"]


def test_cascade_weights_still_stored():
    """Cascade pools still store weights (for compatibility)."""
    p0 = _make_provider("p0")
    pool = _build_cascade([(p0, 0)])
    assert len(pool._weights) == 1
    assert pool._weights[0] == 1.0


def test_cascade_priorities_stored():
    p0 = _make_provider("p0")
    p1 = _make_provider("p1")
    pool = _build_cascade([(p0, 5), (p1, 2)])
    assert pool._priorities == [5, 2]

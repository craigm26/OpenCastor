"""Tests for ProviderPool cost tracking (Issue #345)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from castor.providers.base import Thought
from castor.providers.pool_provider import ProviderPool


def make_pool(config_overrides=None):
    """Build a ProviderPool with two mocked providers."""
    from castor.providers.base import BaseProvider

    mock1 = MagicMock(spec=BaseProvider)
    mock1.model_name = "mock-provider-1"
    mock1.think.return_value = Thought(raw_text="ok", action={"type": "stop"})
    mock1.health_check.return_value = {"ok": True}

    mock2 = MagicMock(spec=BaseProvider)
    mock2.model_name = "mock-provider-2"
    mock2.think.return_value = Thought(raw_text="ok2", action={"type": "wait"})
    mock2.health_check.return_value = {"ok": True}

    cfg = {
        "pool": [
            {"provider": "mock", "api_key": "k1"},
            {"provider": "mock", "api_key": "k2"},
        ],
        "pool_fallback": False,
        **(config_overrides or {}),
    }

    with patch("castor.providers.get_provider") as mock_gp:
        mock_gp.side_effect = [mock1, mock2]
        pool = ProviderPool(cfg)
    pool._providers = [mock1, mock2]
    return pool, mock1, mock2


def test_cost_summary_initial_zeros():
    pool, _, _ = make_pool()
    summary = pool.cost_summary()
    for i in [0, 1]:
        entry = summary[str(i)]
        assert entry["tokens_total"] == 0.0
        assert entry["cost_usd_total"] == 0.0
        assert entry["calls"] == 0


def test_cost_summary_has_total_key():
    pool, _, _ = make_pool()
    summary = pool.cost_summary()
    assert "total" in summary
    assert "tokens_total" in summary["total"]
    assert "cost_usd_total" in summary["total"]
    assert "calls" in summary["total"]


def test_record_cost_increments_calls():
    pool, mock1, _ = make_pool()
    pool._providers[0] = mock1
    thought = Thought(raw_text="hello world", action={"type": "move"})
    pool._record_cost(0, thought)
    summary = pool.cost_summary()
    assert summary["0"]["calls"] == 1


def test_record_cost_estimates_tokens_from_text():
    pool, mock1, _ = make_pool()
    thought = Thought(raw_text="a" * 400, action={"type": "stop"})
    pool._record_cost(0, thought)
    summary = pool.cost_summary()
    # 400 chars / 4 = 100 tokens
    assert summary["0"]["tokens_total"] == pytest.approx(100.0)


def test_record_cost_uses_explicit_tokens_when_available():
    pool, _, _ = make_pool()
    thought = Thought(raw_text="abc", action={"type": "move", "tokens_used": 250})
    pool._record_cost(0, thought)
    summary = pool.cost_summary()
    assert summary["0"]["tokens_total"] == pytest.approx(250.0)


def test_record_cost_with_rate_config():
    pool, _, _ = make_pool({"pool_cost_per_1k_tokens": {"0": 2.0}})
    thought = Thought(raw_text="x" * 4000, action={"type": "stop"})  # ~1000 tokens
    pool._record_cost(0, thought)
    summary = pool.cost_summary()
    # 1000 tokens * (2.0 / 1000) = $2.00
    assert summary["0"]["cost_usd_total"] == pytest.approx(2.0, rel=0.1)


def test_cost_summary_total_sums_all_providers():
    pool, _, _ = make_pool()
    thought1 = Thought(raw_text="x" * 400, action={"type": "move"})
    thought2 = Thought(raw_text="y" * 800, action={"type": "stop"})
    pool._record_cost(0, thought1)
    pool._record_cost(1, thought2)
    summary = pool.cost_summary()
    assert summary["total"]["tokens_total"] == pytest.approx(100.0 + 200.0)
    assert summary["total"]["calls"] == 2


def test_health_check_includes_cost_per_member():
    pool, _, _ = make_pool()
    thought = Thought(raw_text="hello", action={"type": "stop"})
    pool._record_cost(0, thought)
    hc = pool.health_check()
    assert "cost_usd" in hc["members"][0]
    assert "tokens_total" in hc["members"][0]
    assert "calls" in hc["members"][0]


def test_health_check_has_cost_summary():
    pool, _, _ = make_pool()
    hc = pool.health_check()
    assert "cost_summary" in hc


def test_record_cost_accumulates_multiple_calls():
    pool, _, _ = make_pool()
    for _ in range(5):
        thought = Thought(raw_text="x" * 400, action={"type": "stop"})
        pool._record_cost(0, thought)
    summary = pool.cost_summary()
    assert summary["0"]["calls"] == 5
    assert summary["0"]["tokens_total"] == pytest.approx(500.0)


def test_record_cost_empty_thought():
    pool, _, _ = make_pool()
    thought = Thought(raw_text="", action=None)
    pool._record_cost(0, thought)  # Should not raise
    summary = pool.cost_summary()
    assert summary["0"]["calls"] == 1


def test_cost_summary_includes_provider_name():
    pool, _, _ = make_pool()
    summary = pool.cost_summary()
    assert "provider_name" in summary["0"]

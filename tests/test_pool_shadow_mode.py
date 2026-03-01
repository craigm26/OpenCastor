"""Tests for ProviderPool shadow mode (Issue #340)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch

from castor.providers.base import Thought
from castor.providers.pool_provider import ProviderPool


def make_pool_with_shadow(shadow_log=None):
    """Build a ProviderPool with a real shadow provider mock."""
    from castor.providers.base import BaseProvider

    primary_mock = MagicMock(spec=BaseProvider)
    primary_mock.model_name = "primary"
    primary_mock.think.return_value = Thought(raw_text="primary", action={"type": "move"})
    primary_mock.health_check.return_value = {"ok": True}

    shadow_mock = MagicMock(spec=BaseProvider)
    shadow_mock.model_name = "shadow"
    shadow_mock.think.return_value = Thought(raw_text="shadow", action={"type": "stop"})

    cfg = {
        "pool": [{"provider": "mock", "api_key": "k1"}],
        "pool_fallback": False,
        "pool_shadow_provider": "ollama",
        **({"pool_shadow_log_path": shadow_log} if shadow_log else {}),
    }

    with patch("castor.providers.get_provider") as mock_gp:
        # First call = primary, second call = shadow
        mock_gp.side_effect = [primary_mock, shadow_mock]
        pool = ProviderPool(cfg)

    pool._providers = [primary_mock]
    pool._shadow_provider = shadow_mock
    return pool, primary_mock, shadow_mock


def test_shadow_mode_is_disabled_by_default():
    from castor.providers.base import BaseProvider

    primary = MagicMock(spec=BaseProvider)
    primary.model_name = "primary"
    primary.think.return_value = Thought(raw_text="ok", action={"type": "stop"})
    primary.health_check.return_value = {"ok": True}

    cfg = {"pool": [{"provider": "mock", "api_key": "k1"}], "pool_fallback": False}
    with patch("castor.providers.get_provider", return_value=primary):
        pool = ProviderPool(cfg)

    assert pool._shadow_provider is None
    hc = pool.health_check()
    assert hc["shadow"]["enabled"] is False


def test_shadow_health_check_reports_provider():
    pool, _, _ = make_pool_with_shadow()
    hc = pool.health_check()
    assert hc["shadow"]["enabled"] is True
    assert hc["shadow"]["provider"] == "ollama"


def test_shadow_primary_response_returned():
    """Primary's Thought is always returned even with shadow enabled."""
    pool, primary, shadow = make_pool_with_shadow()
    result = pool.think(b"", "go forward")
    # Wait briefly for shadow thread to fire
    time.sleep(0.05)
    assert result.action == {"type": "move"}


def test_shadow_provider_is_called():
    pool, primary, shadow = make_pool_with_shadow()
    pool.think(b"", "go forward")
    time.sleep(0.1)  # Let background thread complete
    shadow.think.assert_called_once()


def test_shadow_logs_comparison_to_file():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        log_path = f.name
    try:
        pool, primary, shadow = make_pool_with_shadow(shadow_log=log_path)
        pool.think(b"", "go forward")
        time.sleep(0.15)  # Let shadow thread write
        with open(log_path) as f:
            lines = [ln for ln in f if ln.strip()]
        assert len(lines) >= 1
        rec = json.loads(lines[0])
        assert "primary_action" in rec
        assert "shadow_action" in rec
        assert "match" in rec
    finally:
        if os.path.exists(log_path):
            os.unlink(log_path)


def test_shadow_log_includes_instruction():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        log_path = f.name
    try:
        pool, primary, shadow = make_pool_with_shadow(shadow_log=log_path)
        pool.think(b"", "go to kitchen")
        time.sleep(0.15)
        with open(log_path) as f:
            rec = json.loads(f.readline())
        assert "instruction" in rec
        assert "go to kitchen" in rec["instruction"]
    finally:
        if os.path.exists(log_path):
            os.unlink(log_path)


def test_shadow_log_has_timestamp():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        log_path = f.name
    try:
        pool, primary, shadow = make_pool_with_shadow(shadow_log=log_path)
        pool.think(b"", "test")
        time.sleep(0.15)
        with open(log_path) as f:
            rec = json.loads(f.readline())
        assert "ts" in rec
        assert rec["ts"] > 0
    finally:
        if os.path.exists(log_path):
            os.unlink(log_path)


def test_shadow_failure_does_not_affect_primary():
    pool, primary, shadow = make_pool_with_shadow()
    shadow.think.side_effect = RuntimeError("shadow exploded")
    # Primary should still succeed
    result = pool.think(b"", "do something")
    assert result.action == {"type": "move"}


def test_shadow_call_does_not_block_primary():
    pool, primary, shadow = make_pool_with_shadow()
    slow_event = threading.Event()

    def slow_shadow(*args, **kwargs):
        slow_event.wait(timeout=2.0)
        return Thought(raw_text="slow", action={"type": "stop"})

    shadow.think.side_effect = slow_shadow
    start = time.monotonic()
    pool.think(b"", "go fast")
    elapsed = time.monotonic() - start
    slow_event.set()
    # Primary should return immediately, not waiting for shadow
    assert elapsed < 1.0


def test_shadow_no_log_path_does_not_raise():
    pool, primary, shadow = make_pool_with_shadow(shadow_log=None)
    pool.think(b"", "test")
    time.sleep(0.05)  # Should not raise even without log path


def test_shadow_mode_enabled_flag_in_health():
    pool, _, _ = make_pool_with_shadow()
    hc = pool.health_check()
    assert "shadow" in hc
    assert hc["shadow"]["enabled"] is True


def test_shadow_match_flag_true_when_actions_equal():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        log_path = f.name
    try:
        pool, primary, shadow = make_pool_with_shadow(shadow_log=log_path)
        # Make both providers return same action
        primary.think.return_value = Thought(raw_text="p", action={"type": "stop"})
        shadow.think.return_value = Thought(raw_text="s", action={"type": "stop"})
        pool.think(b"", "stop now")
        time.sleep(0.15)
        with open(log_path) as f:
            rec = json.loads(f.readline())
        assert rec["match"] is True
    finally:
        if os.path.exists(log_path):
            os.unlink(log_path)

"""Tests for castor/provider_fallback.py — quota-error fallback (issue #102)."""

from unittest.mock import MagicMock, patch

import pytest

from castor.provider_fallback import ProviderFallbackManager
from castor.providers.base import ProviderQuotaError, Thought


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_provider(name="Primary"):
    p = MagicMock()
    p.__class__.__name__ = f"{name}Provider"
    p.health_check.return_value = {"ok": True, "latency_ms": 10}
    p.think.return_value = Thought("ok", {"type": "move"})
    return p


def _make_config(enabled=True, provider="ollama", model="llama3.2:3b", cooldown=3600):
    return {
        "provider_fallback": {
            "enabled": enabled,
            "provider": provider,
            "model": model,
            "quota_cooldown_s": cooldown,
        }
    }


# ── Construction ──────────────────────────────────────────────────────────────

def test_disabled_by_default():
    primary = _make_provider()
    mgr = ProviderFallbackManager({}, primary)
    assert mgr._fallback is None
    assert not mgr.is_using_fallback


def test_enabled_builds_fallback():
    primary = _make_provider()
    cfg = _make_config()
    fallback_provider = _make_provider("Ollama")

    with patch("castor.provider_fallback.ProviderFallbackManager._build_fallback",
               return_value=fallback_provider):
        mgr = ProviderFallbackManager(cfg, primary)

    assert mgr._fallback is fallback_provider


# ── probe_fallback ─────────────────────────────────────────────────────────────

def test_probe_fallback_ok():
    primary = _make_provider()
    fallback = _make_provider("Fallback")
    fallback.health_check.return_value = {"ok": True, "latency_ms": 42}
    mgr = ProviderFallbackManager({}, primary)
    mgr._fallback = fallback
    assert mgr.probe_fallback() is True
    assert mgr.fallback_ready is True


def test_probe_fallback_fails():
    primary = _make_provider()
    fallback = _make_provider("Fallback")
    fallback.health_check.return_value = {"ok": False, "error": "connection refused"}
    mgr = ProviderFallbackManager({}, primary)
    mgr._fallback = fallback
    assert mgr.probe_fallback() is False
    assert mgr.fallback_ready is False


def test_probe_fallback_no_fallback():
    mgr = ProviderFallbackManager({}, _make_provider())
    assert mgr.probe_fallback() is False


# ── health_check() delegation ─────────────────────────────────────────────────

def test_health_check_delegates_to_active_provider():
    primary = _make_provider()
    primary.health_check.return_value = {"ok": True, "latency_ms": 5}
    mgr = ProviderFallbackManager({}, primary)
    result = mgr.health_check()
    assert result == {"ok": True, "latency_ms": 5}
    primary.health_check.assert_called_once()


def test_health_check_delegates_to_fallback_when_active():
    primary = _make_provider()
    fallback = _make_provider("Fallback")
    fallback.health_check.return_value = {"ok": True, "latency_ms": 20}
    mgr = ProviderFallbackManager(_make_config(), primary)
    mgr._fallback = fallback
    mgr._using_fallback = True

    import time
    mgr._quota_hit_time = time.time() + 9999

    result = mgr.health_check()
    assert result == {"ok": True, "latency_ms": 20}
    fallback.health_check.assert_called_once()
    primary.health_check.assert_not_called()


# ── get_active_provider ───────────────────────────────────────────────────────

def test_get_active_returns_primary_normally():
    primary = _make_provider()
    mgr = ProviderFallbackManager({}, primary)
    assert mgr.get_active_provider() is primary


def test_get_active_returns_fallback_when_switched():
    primary = _make_provider()
    fallback = _make_provider("Fallback")
    mgr = ProviderFallbackManager(_make_config(), primary)
    mgr._fallback = fallback
    mgr._using_fallback = True
    mgr._quota_hit_time = float("inf")  # never expires

    import time
    mgr._quota_hit_time = time.time() + 9999
    assert mgr.get_active_provider() is fallback


def test_cooldown_restores_primary():
    primary = _make_provider()
    fallback = _make_provider("Fallback")
    cfg = _make_config(cooldown=0)  # instant cooldown
    mgr = ProviderFallbackManager(cfg, primary)
    mgr._fallback = fallback
    mgr._using_fallback = True
    mgr._quota_hit_time = 0.0  # already expired

    assert mgr.get_active_provider() is primary
    assert not mgr.is_using_fallback


# ── think() transparent wrapper ───────────────────────────────────────────────

def test_think_passes_through_normally():
    primary = _make_provider()
    primary.think.return_value = Thought("move", {"type": "move"})
    mgr = ProviderFallbackManager({}, primary)
    result = mgr.think(b"", "go forward")
    assert result.action == {"type": "move"}
    primary.think.assert_called_once_with(b"", "go forward")


def test_think_switches_on_quota_error():
    primary = _make_provider()
    fallback = _make_provider("Ollama")
    primary.think.side_effect = ProviderQuotaError("credits exhausted", "huggingface", 402)
    fallback.think.return_value = Thought("stop", {"type": "stop"})

    mgr = ProviderFallbackManager(_make_config(), primary)
    mgr._fallback = fallback

    result = mgr.think(b"", "instruction")

    assert result.action == {"type": "stop"}
    assert mgr.is_using_fallback
    fallback.think.assert_called_once()


def test_think_no_fallback_returns_error_thought():
    primary = _make_provider()
    primary.think.side_effect = ProviderQuotaError("no credits", "huggingface", 402)
    mgr = ProviderFallbackManager({}, primary)  # no fallback configured

    result = mgr.think(b"", "instruction")
    assert result.action is None
    assert "Quota exceeded" in result.raw_text


# ── ProviderQuotaError detection in huggingface_provider ─────────────────────

def test_is_quota_error_http_402():
    from castor.providers.huggingface_provider import _is_quota_error

    exc = Exception("Payment Required")
    resp = MagicMock()
    resp.status_code = 402
    exc.response = resp
    assert _is_quota_error(exc)


def test_is_quota_error_keyword_credits():
    from castor.providers.huggingface_provider import _is_quota_error

    assert _is_quota_error(Exception("You have exceeded your monthly included credits"))


def test_is_quota_error_keyword_rate_limit():
    from castor.providers.huggingface_provider import _is_quota_error

    assert _is_quota_error(Exception("Too many requests — rate limit reached"))


def test_is_not_quota_error_generic():
    from castor.providers.huggingface_provider import _is_quota_error

    assert not _is_quota_error(Exception("Connection timeout"))
    assert not _is_quota_error(ValueError("bad json"))

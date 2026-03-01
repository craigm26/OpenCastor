"""Tests for castor.response_cache (ResponseCache + CachedProvider)."""

import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Singleton reset
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache_singleton():
    import castor.response_cache as mod

    mod._singleton = None
    yield
    mod._singleton = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cache(tmp_path, max_age_s=3600, max_size=100):
    from castor.response_cache import ResponseCache

    db = str(tmp_path / "cache.db")
    return ResponseCache(db_path=db, max_age_s=max_age_s, max_size=max_size)


# ---------------------------------------------------------------------------
# Basic get / put
# ---------------------------------------------------------------------------


class TestResponseCacheGetPut:
    def test_miss_returns_none(self, tmp_path):
        cache = _make_cache(tmp_path)
        assert cache.get("unknown instruction") is None

    def test_put_then_get_hit(self, tmp_path):
        cache = _make_cache(tmp_path)
        cache.put("go forward", "moving forward", {"linear": 0.5})
        result = cache.get("go forward")
        assert result is not None
        assert result["raw_text"] == "moving forward"
        assert result["action"] == {"linear": 0.5}

    def test_hit_with_image_bytes(self, tmp_path):
        cache = _make_cache(tmp_path)
        img = b"\xff\xd8\xff" + b"\x00" * 20
        cache.put("describe", "I see a wall", None, image_bytes=img)
        result = cache.get("describe", image_bytes=img)
        assert result is not None
        assert result["raw_text"] == "I see a wall"

    def test_different_image_different_key(self, tmp_path):
        cache = _make_cache(tmp_path)
        img_a = b"\xff\xd8" + b"\xaa" * 10
        img_b = b"\xff\xd8" + b"\xbb" * 10
        cache.put("describe", "wall", None, image_bytes=img_a)
        result = cache.get("describe", image_bytes=img_b)
        assert result is None

    def test_put_overwrites_existing(self, tmp_path):
        cache = _make_cache(tmp_path)
        cache.put("cmd", "first", None)
        cache.put("cmd", "second", {"x": 1})
        result = cache.get("cmd")
        assert result["raw_text"] == "second"


# ---------------------------------------------------------------------------
# Max-age expiry
# ---------------------------------------------------------------------------


class TestResponseCacheExpiry:
    def test_expired_entry_returns_none(self, tmp_path):
        cache = _make_cache(tmp_path, max_age_s=1)
        cache.put("old cmd", "stale text", None)
        # Patch time.time to advance past expiry
        with patch("castor.response_cache.time.time", return_value=time.time() + 10):
            result = cache.get("old cmd")
        assert result is None

    def test_fresh_entry_within_max_age_is_hit(self, tmp_path):
        cache = _make_cache(tmp_path, max_age_s=3600)
        cache.put("fresh cmd", "ok", None)
        result = cache.get("fresh cmd")
        assert result is not None


# ---------------------------------------------------------------------------
# clear()
# ---------------------------------------------------------------------------


class TestResponseCacheClear:
    def test_clear_removes_entries(self, tmp_path):
        cache = _make_cache(tmp_path)
        cache.put("a", "text a", None)
        cache.put("b", "text b", None)
        deleted = cache.clear()
        assert deleted == 2
        assert cache.get("a") is None

    def test_clear_resets_counters(self, tmp_path):
        cache = _make_cache(tmp_path)
        cache.put("x", "y", None)
        cache.get("x")  # hit
        cache.clear()
        s = cache.stats()
        assert s["hits"] == 0
        assert s["misses"] == 0


# ---------------------------------------------------------------------------
# stats()
# ---------------------------------------------------------------------------


class TestResponseCacheStats:
    def test_stats_has_required_keys(self, tmp_path):
        cache = _make_cache(tmp_path)
        s = cache.stats()
        for key in (
            "entries",
            "hits",
            "misses",
            "hit_rate_pct",
            "enabled",
            "max_age_s",
            "max_size",
        ):
            assert key in s, f"missing stats key: {key}"

    def test_hit_rate_increments(self, tmp_path):
        cache = _make_cache(tmp_path)
        cache.put("q", "ans", None)
        cache.get("q")  # hit
        cache.get("missing")  # miss
        s = cache.stats()
        assert s["hits"] == 1
        assert s["misses"] == 1
        assert s["hit_rate_pct"] == pytest.approx(50.0, abs=0.1)

    def test_entries_count_matches_put(self, tmp_path):
        cache = _make_cache(tmp_path)
        cache.put("cmd1", "r1", None)
        cache.put("cmd2", "r2", None)
        assert cache.stats()["entries"] == 2


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------


class TestResponseCacheLRUEviction:
    def test_evicts_oldest_when_full(self, tmp_path):
        cache = _make_cache(tmp_path, max_size=3)
        cache.put("cmd1", "r1", None)
        cache.put("cmd2", "r2", None)
        cache.put("cmd3", "r3", None)
        cache.put("cmd4", "r4", None)  # triggers eviction of oldest
        assert cache.stats()["entries"] <= 3


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------


class TestResponseCacheEnableDisable:
    def test_disabled_get_returns_none(self, tmp_path):
        cache = _make_cache(tmp_path)
        cache.put("cmd", "ok", None)
        cache.disable()
        assert cache.get("cmd") is None

    def test_disabled_put_does_not_store(self, tmp_path):
        cache = _make_cache(tmp_path)
        cache.disable()
        cache.put("cmd", "ok", None)
        cache.enable()
        assert cache.get("cmd") is None

    def test_re_enable_works(self, tmp_path):
        cache = _make_cache(tmp_path)
        cache.disable()
        cache.enable()
        cache.put("cmd", "ok", None)
        assert cache.get("cmd") is not None

    def test_stats_reports_enabled_state(self, tmp_path):
        cache = _make_cache(tmp_path)
        assert cache.stats()["enabled"] is True
        cache.disable()
        assert cache.stats()["enabled"] is False


# ---------------------------------------------------------------------------
# CachedProvider
# ---------------------------------------------------------------------------


class TestCachedProvider:
    def _make_mock_provider(self, text="response"):
        from castor.providers.base import Thought

        provider = MagicMock()
        provider.think.return_value = Thought(raw_text=text, action={"move": True})
        provider.think_stream.return_value = iter([text])
        provider.health_check.return_value = {"ok": True}
        provider._clean_json = MagicMock(return_value={"move": True})
        return provider

    def test_cache_miss_calls_provider(self, tmp_path):
        from castor.response_cache import CachedProvider

        cache = _make_cache(tmp_path)
        provider = self._make_mock_provider("forward")
        cp = CachedProvider(provider, cache)
        thought = cp.think(b"", "go forward")
        provider.think.assert_called_once()
        assert thought.raw_text == "forward"

    def test_cache_hit_skips_provider(self, tmp_path):
        from castor.response_cache import CachedProvider

        cache = _make_cache(tmp_path)
        provider = self._make_mock_provider("from cache")
        cp = CachedProvider(provider, cache)
        cp.think(b"", "go forward")  # miss → calls provider, stores
        provider.think.reset_mock()
        thought = cp.think(b"", "go forward")  # hit → skips provider
        provider.think.assert_not_called()
        assert thought.raw_text == "from cache"

    def test_think_stream_hit_yields_full_text(self, tmp_path):
        from castor.response_cache import CachedProvider

        cache = _make_cache(tmp_path)
        provider = self._make_mock_provider("stream text")
        cp = CachedProvider(provider, cache)
        # First call: miss — streams from provider and stores
        "".join(cp.think_stream(b"", "stream cmd"))
        provider.think_stream.reset_mock()
        # Second call: hit — should yield full cached text without calling provider
        result = "".join(cp.think_stream(b"", "stream cmd"))
        provider.think_stream.assert_not_called()
        assert "stream text" in result

    def test_health_check_delegates_to_provider(self, tmp_path):
        """CachedProvider.health_check() delegates to the wrapped provider."""
        from castor.response_cache import CachedProvider

        cache = _make_cache(tmp_path)
        provider = self._make_mock_provider()
        CachedProvider(provider, cache)
        # Access underlying provider health_check — CachedProvider proxies ok=True
        h = provider.health_check()
        assert h["ok"] is True

    def test_getattr_delegates_to_provider(self, tmp_path):
        from castor.response_cache import CachedProvider

        cache = _make_cache(tmp_path)
        provider = self._make_mock_provider()
        provider.model_name = "test-model"
        cp = CachedProvider(provider, cache)
        assert cp.model_name == "test-model"

    def test_cache_stats_hit_rate_after_two_calls(self, tmp_path):
        from castor.response_cache import CachedProvider

        cache = _make_cache(tmp_path)
        provider = self._make_mock_provider("answer")
        cp = CachedProvider(provider, cache)
        cp.think(b"", "same question")  # miss
        cp.think(b"", "same question")  # hit
        s = cache.stats()
        assert s["hits"] == 1
        assert s["misses"] == 1


# ---------------------------------------------------------------------------
# make_key
# ---------------------------------------------------------------------------


def test_make_key_same_instruction_same_key():
    from castor.response_cache import ResponseCache

    k1 = ResponseCache.make_key("hello world")
    k2 = ResponseCache.make_key("hello world")
    assert k1 == k2


def test_make_key_different_instruction_different_key():
    from castor.response_cache import ResponseCache

    k1 = ResponseCache.make_key("go forward")
    k2 = ResponseCache.make_key("turn left")
    assert k1 != k2


def test_make_key_image_changes_key():
    from castor.response_cache import ResponseCache

    k_no_img = ResponseCache.make_key("cmd")
    k_img = ResponseCache.make_key("cmd", image_bytes=b"\xff\xd8")
    assert k_no_img != k_img

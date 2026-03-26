"""Tests for RCAN v1.5 replay prevention (GAP-03).

Verifies that:
  - Fresh commands are accepted
  - Replayed commands are rejected
  - Stale timestamps are rejected
  - Future-dated timestamps are rejected
  - Separate safety cache uses 10s window
  - Thread safety (basic)
  - Stub fallback when rcan-py is unavailable

Spec: RCAN §8.3 — Replay Attack Prevention

Note: rcan.replay.ReplayCache.check_and_record returns (bool, str) tuple
(not raises). The bridge adapts to this via _check_replay().
"""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid

import pytest

# Add rcan-py to path for direct import tests
_RCAN_PATH = os.path.expanduser("~/rcan-py")
if _RCAN_PATH not in sys.path:
    sys.path.insert(0, _RCAN_PATH)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def cache():
    """Return a fresh ReplayCache with 30s window."""
    from rcan.replay import ReplayCache
    return ReplayCache(window_s=30)


@pytest.fixture
def safety_cache():
    """Return a fresh ReplayCache with 10s safety window."""
    from rcan.replay import ReplayCache
    return ReplayCache(window_s=10)


def _msg_id() -> str:
    return str(uuid.uuid4())


def _now() -> float:
    return time.time()


# ─────────────────────────────────────────────────────────────────────────────
# Fresh message acceptance
# ─────────────────────────────────────────────────────────────────────────────

class TestFreshMessages:
    def test_fresh_message_accepted(self, cache):
        """A brand-new message with current timestamp is accepted."""
        mid = _msg_id()
        allowed, reason = cache.check_and_record(mid, _now())
        assert allowed is True
        assert reason == ""

    def test_fresh_message_recorded(self, cache):
        """After check_and_record, the msg_id is in the cache."""
        mid = _msg_id()
        cache.check_and_record(mid, _now())
        assert mid in cache._seen

    def test_multiple_unique_messages_accepted(self, cache):
        """Multiple unique messages are all accepted."""
        for _ in range(10):
            allowed, _ = cache.check_and_record(_msg_id(), _now())
            assert allowed is True

    def test_message_at_edge_of_window_accepted(self, cache):
        """A message issued exactly window_s - 1 seconds ago is still fresh."""
        mid = _msg_id()
        issued_at = _now() - 29  # 29s ago, window is 30s
        allowed, _ = cache.check_and_record(mid, issued_at)
        assert allowed is True


# ─────────────────────────────────────────────────────────────────────────────
# Replay rejection
# ─────────────────────────────────────────────────────────────────────────────

class TestReplayRejection:
    def test_duplicate_msg_id_rejected(self, cache):
        """The same msg_id submitted twice is rejected on second submission."""
        mid = _msg_id()
        now = _now()
        allowed1, _ = cache.check_and_record(mid, now)
        allowed2, reason2 = cache.check_and_record(mid, now)
        assert allowed1 is True
        assert allowed2 is False
        assert "duplicate" in reason2.lower() or "replay" in reason2.lower()

    def test_different_msg_ids_not_rejected(self, cache):
        """Different msg_ids with same timestamp are not rejected as replays."""
        now = _now()
        mid1, mid2 = _msg_id(), _msg_id()
        allowed1, _ = cache.check_and_record(mid1, now)
        allowed2, _ = cache.check_and_record(mid2, now)
        assert allowed1 is True
        assert allowed2 is True

    def test_replay_returns_false_bool(self, cache):
        """Replay check returns False (bool), not truthy string."""
        mid = _msg_id()
        now = _now()
        cache.check_and_record(mid, now)
        allowed, _ = cache.check_and_record(mid, now)
        assert allowed is False


# ─────────────────────────────────────────────────────────────────────────────
# Stale timestamp rejection
# ─────────────────────────────────────────────────────────────────────────────

class TestStaleTimestamps:
    def test_stale_message_rejected(self, cache):
        """A message issued more than window_s seconds ago is rejected."""
        mid = _msg_id()
        stale_at = _now() - 31  # 31s ago, window is 30s
        allowed, reason = cache.check_and_record(mid, stale_at)
        assert allowed is False
        assert "old" in reason.lower() or "stale" in reason.lower() or "window" in reason.lower()

    def test_stale_message_not_recorded(self, cache):
        """A stale message is not recorded in the cache."""
        mid = _msg_id()
        stale_at = _now() - 60
        allowed, _ = cache.check_and_record(mid, stale_at)
        assert allowed is False
        assert mid not in cache._seen

    def test_future_dated_message_rejected(self, cache):
        """A message with timestamp more than 5s in the future is rejected."""
        mid = _msg_id()
        future_at = _now() + 10  # 10s in the future
        allowed, reason = cache.check_and_record(mid, future_at)
        assert allowed is False
        assert "future" in reason.lower()

    def test_slightly_future_message_accepted(self, cache):
        """A message with timestamp 3s in the future is within tolerance."""
        mid = _msg_id()
        slightly_future = _now() + 3
        allowed, _ = cache.check_and_record(mid, slightly_future)
        assert allowed is True


# ─────────────────────────────────────────────────────────────────────────────
# Safety window (10s)
# ─────────────────────────────────────────────────────────────────────────────

class TestSafetyWindow:
    def test_safety_cache_rejects_11s_old_message(self, safety_cache):
        """Safety cache (10s window) rejects messages older than 10s."""
        mid = _msg_id()
        stale_at = _now() - 11  # 11s ago, safety window is 10s
        allowed, reason = safety_cache.check_and_record(mid, stale_at)
        assert allowed is False

    def test_safety_cache_accepts_9s_old_message(self, safety_cache):
        """Safety cache (10s window) accepts messages 9s old."""
        mid = _msg_id()
        issued_at = _now() - 9
        allowed, _ = safety_cache.check_and_record(mid, issued_at)
        assert allowed is True

    def test_normal_cache_accepts_11s_old_message(self, cache):
        """Normal cache (30s window) accepts messages 11s old."""
        mid = _msg_id()
        issued_at = _now() - 11
        allowed, _ = cache.check_and_record(mid, issued_at)
        assert allowed is True

    def test_safety_window_smaller_than_normal(self, cache, safety_cache):
        """Safety window (10s) is strictly smaller than normal window (30s)."""
        assert safety_cache.window_s < cache.window_s
        assert safety_cache.window_s == 10

    def test_is_safety_flag_caps_normal_cache_to_10s(self, cache):
        """is_safety=True on normal cache (30s) caps the check to 10s window."""
        mid = _msg_id()
        # 11s ago — passes normal 30s check but should fail 10s safety cap
        issued_at = _now() - 11
        allowed, _ = cache.check_and_record(mid, issued_at, is_safety=True)
        assert allowed is False, "is_safety=True should cap window to 10s"


# ─────────────────────────────────────────────────────────────────────────────
# Eviction and memory management
# ─────────────────────────────────────────────────────────────────────────────

class TestEviction:
    def test_max_size_enforced(self):
        """Cache evicts when max_size is reached."""
        from rcan.replay import ReplayCache
        cache = ReplayCache(window_s=30, max_size=100)
        now = _now()
        for _ in range(120):
            cache.check_and_record(_msg_id(), now)
        # After overflow, size should be at most max_size
        assert len(cache._seen) <= 100


# ─────────────────────────────────────────────────────────────────────────────
# Thread safety
# ─────────────────────────────────────────────────────────────────────────────

class TestThreadSafety:
    def test_concurrent_unique_messages(self):
        """Concurrent unique messages are all accepted without corruption."""
        from rcan.replay import ReplayCache
        cache = ReplayCache(window_s=30)
        errors: list[str] = []
        msg_ids = [_msg_id() for _ in range(50)]
        now = _now()

        def record(mid):
            allowed, reason = cache.check_and_record(mid, now)
            if not allowed:
                errors.append(f"Unexpected rejection: {mid} reason={reason}")

        threads = [threading.Thread(target=record, args=(mid,)) for mid in msg_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors: {errors}"

    def test_concurrent_duplicate_mostly_rejected(self):
        """Concurrent duplicate submissions: at most one succeeds."""
        from rcan.replay import ReplayCache
        cache = ReplayCache(window_s=30)
        mid = _msg_id()
        now = _now()
        successes = []

        def submit():
            allowed, _ = cache.check_and_record(mid, now)
            if allowed:
                successes.append(True)

        threads = [threading.Thread(target=submit) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one should succeed
        assert len(successes) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Bridge integration
# ─────────────────────────────────────────────────────────────────────────────

class TestBridgeIntegration:
    def test_stub_allows_all_commands(self):
        """_ReplayCacheStub always allows commands (fail-open)."""
        from castor.cloud.bridge import _ReplayCacheStub
        stub = _ReplayCacheStub(window_s=30)
        allowed, reason = stub.check_and_record("test-id", time.time() - 100)
        assert allowed is True
        assert reason == ""

    def test_bridge_uses_replay_cache(self):
        """CastorBridge.__init__ creates replay cache instances."""
        from castor.cloud.bridge import CastorBridge
        config = {
            "rrn": "RRN-00000001",
            "metadata": {"name": "TestBot"},
        }
        bridge = CastorBridge(
            config=config,
            firebase_project="test-project",
        )
        assert bridge._replay_cache is not None
        assert bridge._safety_replay_cache is not None

    def test_bridge_safety_cache_window(self):
        """Safety cache uses 10s window."""
        from castor.cloud.bridge import SAFETY_REPLAY_WINDOW_S, CastorBridge
        config = {"rrn": "RRN-00000001", "metadata": {"name": "TestBot"}}
        bridge = CastorBridge(config=config, firebase_project="test-project")
        assert bridge._safety_replay_cache.window_s == SAFETY_REPLAY_WINDOW_S

    def test_bridge_replay_check_fresh(self):
        """bridge._check_replay returns True for a fresh command."""
        from castor.cloud.bridge import CastorBridge
        bridge = CastorBridge(
            config={"rrn": "RRN-00000001", "metadata": {"name": "T"}},
            firebase_project="test",
        )
        cmd_id = str(uuid.uuid4())
        doc = {"scope": "chat", "instruction": "hello", "issued_at": time.time()}
        assert bridge._check_replay(cmd_id, doc) is True

    def test_bridge_replay_check_duplicate(self):
        """bridge._check_replay returns False on second submission of same cmd_id."""
        from castor.cloud.bridge import CastorBridge
        bridge = CastorBridge(
            config={"rrn": "RRN-00000001", "metadata": {"name": "T"}},
            firebase_project="test",
        )
        cmd_id = str(uuid.uuid4())
        doc = {"scope": "chat", "instruction": "hello", "issued_at": time.time()}
        assert bridge._check_replay(cmd_id, doc) is True
        assert bridge._check_replay(cmd_id, doc) is False

    def test_bridge_replay_check_stale(self):
        """bridge._check_replay returns False for stale command (>30s old)."""
        from castor.cloud.bridge import CastorBridge
        bridge = CastorBridge(
            config={"rrn": "RRN-00000001", "metadata": {"name": "T"}},
            firebase_project="test",
        )
        cmd_id = str(uuid.uuid4())
        doc = {"scope": "chat", "instruction": "hello", "issued_at": time.time() - 60}
        assert bridge._check_replay(cmd_id, doc) is False

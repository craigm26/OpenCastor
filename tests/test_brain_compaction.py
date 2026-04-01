"""Tests for castor.brain.compaction — post-compaction continuation injection."""


from castor.brain.compaction import (
    CompactionStrategy,
    build_continuation_message,
    compact_session,
    should_compact,
)


def _make_messages(n: int, char_size: int = 100) -> list[dict]:
    """Return *n* fake messages each with *char_size* characters of content."""
    return [{"role": "user", "content": "x" * char_size} for _ in range(n)]


# ── 1. should_compact — below threshold ──────────────────────────────────────


def test_should_compact_below_threshold():
    strategy = CompactionStrategy(threshold_tokens=1_000)
    # 10 messages × 100 chars → ~250 tokens, well below 1 000
    messages = _make_messages(10, char_size=100)
    assert should_compact(messages, strategy) is False


# ── 2. should_compact — at/above threshold ───────────────────────────────────


def test_should_compact_at_threshold():
    strategy = CompactionStrategy(threshold_tokens=10)
    # Single message with 40+ chars → 10+ tokens
    messages = [{"role": "user", "content": "x" * 40}]
    assert should_compact(messages, strategy) is True


# ── 3. compact_session — preserve_recent keeps tail ─────────────────────────


def test_compact_session_preserve_recent():
    strategy = CompactionStrategy(preserve_recent=3)
    messages = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
    summarizer_called_with = []

    def summarizer(msgs):
        summarizer_called_with.extend(msgs)
        return "summary text"

    new_messages, summary = compact_session(messages, strategy, summarizer)

    assert len(new_messages) == 3
    assert new_messages == messages[-3:]
    assert summary == "summary text"
    # summarizer received the first 7 messages
    assert summarizer_called_with == messages[:7]


# ── 4. compact_session — preserve_recent ≥ len(messages) keeps all ───────────


def test_compact_session_preserve_exceeds_length():
    strategy = CompactionStrategy(preserve_recent=20)
    messages = _make_messages(5)
    new_messages, summary = compact_session(messages, strategy, lambda _: "s")
    assert new_messages == messages
    assert summary == ""


# ── 5. build_continuation_message — basic structure ─────────────────────────


def test_build_continuation_message_structure():
    msg = build_continuation_message("session history here", suppress_follow_up=False)
    assert msg["role"] == "system"
    assert "<compaction-summary>" in msg["content"]
    assert "session history here" in msg["content"]
    assert "</compaction-summary>" in msg["content"]


# ── 6. build_continuation_message — suppress_follow_up flag ─────────────────


def test_build_continuation_message_suppress_follow_up():
    with_suppress = build_continuation_message("hist", suppress_follow_up=True)
    without_suppress = build_continuation_message("hist", suppress_follow_up=False)

    assert "Do not acknowledge" in with_suppress["content"]
    assert "Do not acknowledge" not in without_suppress["content"]

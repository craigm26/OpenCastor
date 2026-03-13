"""Tests for rolling context compactor."""

from unittest.mock import MagicMock

from castor.memory.compaction import CompactionConfig, ContextCompactor


def _msgs(n: int) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 500} for i in range(n)]


class TestTokenEstimation:
    def test_estimate_empty(self):
        c = ContextCompactor()
        assert c.estimate_tokens([]) == 0

    def test_estimate_nonzero(self):
        c = ContextCompactor()
        msgs = [{"role": "user", "content": "hello world"}]
        assert c.estimate_tokens(msgs) > 0


class TestNeedsCompaction:
    def test_disabled_by_default(self):
        c = ContextCompactor()
        assert not c.needs_compaction(_msgs(1000))

    def test_triggers_above_threshold(self):
        cfg = CompactionConfig(enabled=True, max_tokens=100)
        c = ContextCompactor(cfg)
        # 10 msgs * 500 chars / 4 chars_per_token = 1250 tokens > 100
        assert c.needs_compaction(_msgs(10))

    def test_no_trigger_below_threshold(self):
        cfg = CompactionConfig(enabled=True, max_tokens=100_000)
        c = ContextCompactor(cfg)
        assert not c.needs_compaction(_msgs(5))


class TestCompaction:
    def test_preserves_last_n(self):
        cfg = CompactionConfig(enabled=True, max_tokens=100, preserve_last_n=3)
        c = ContextCompactor(cfg)
        msgs = _msgs(20)
        result = c.compact(msgs, provider=None)
        # First message is summary, then last 3 preserved
        assert result[0]["role"] == "system"
        assert "[Compacted" in result[0]["content"]
        assert len(result) == 4  # summary + 3 preserved

    def test_fallback_summary_no_provider(self):
        cfg = CompactionConfig(enabled=True, max_tokens=100, preserve_last_n=2)
        c = ContextCompactor(cfg)
        result = c.compact(_msgs(20), provider=None)
        assert "[Compacted session summary]" in result[0]["content"]

    def test_llm_summary_called(self):
        cfg = CompactionConfig(enabled=True, max_tokens=100, preserve_last_n=2)
        c = ContextCompactor(cfg)
        mock_provider = MagicMock()
        mock_thought = MagicMock()
        mock_thought.raw_text = "[Compacted session summary] The robot did stuff."
        mock_provider.think.return_value = mock_thought
        result = c.compact(_msgs(20), provider=mock_provider)
        assert mock_provider.think.called
        assert "[Compacted session summary]" in result[0]["content"]

    def test_llm_failure_uses_fallback(self):
        cfg = CompactionConfig(enabled=True, max_tokens=100)
        c = ContextCompactor(cfg)
        mock_provider = MagicMock()
        mock_provider.think.side_effect = RuntimeError("provider down")
        result = c.compact(_msgs(20), provider=mock_provider)
        assert "[Compacted session summary]" in result[0]["content"]

    def test_no_compaction_needed_returns_original(self):
        cfg = CompactionConfig(enabled=True, max_tokens=100_000, preserve_last_n=3)
        c = ContextCompactor(cfg)
        msgs = _msgs(5)
        result = c.compact(msgs, provider=None)
        assert result == msgs


class TestMaybeCompact:
    def test_returns_false_when_not_needed(self):
        c = ContextCompactor(CompactionConfig(enabled=True, max_tokens=100_000))
        msgs = _msgs(2)
        _, did = c.maybe_compact(msgs)
        assert not did

    def test_returns_true_when_compacted(self):
        c = ContextCompactor(CompactionConfig(enabled=True, max_tokens=100))
        _, did = c.maybe_compact(_msgs(20))
        assert did

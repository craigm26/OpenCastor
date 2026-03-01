"""Tests for EpisodeMemory.summarize_batch() and get_latest_summary() — issue #339."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from castor.memory import EpisodeMemory
from castor.providers.base import Thought

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mem(tmp_path):
    db = str(tmp_path / "summarize.db")
    return EpisodeMemory(db_path=db, max_episodes=0)


def _make_think_fn(text: str = "The robot moved forward and then stopped."):
    """Return a mock provider_think_fn that returns a fixed Thought."""
    mock = MagicMock(return_value=Thought(raw_text=text, action=None))
    return mock


# ---------------------------------------------------------------------------
# get_latest_summary — empty state
# ---------------------------------------------------------------------------


def test_get_latest_summary_returns_none_on_empty_table(mem):
    """Returns None when no summaries have been stored."""
    assert mem.get_latest_summary() is None


# ---------------------------------------------------------------------------
# summarize_batch — basic correctness
# ---------------------------------------------------------------------------


def test_summarize_batch_calls_provider_think_fn(mem):
    """provider_think_fn is called exactly once per summarize_batch call."""
    mem.log_episode(instruction="move forward", action={"type": "move"})
    think_fn = _make_think_fn()
    mem.summarize_batch(think_fn, limit=10)
    think_fn.assert_called_once()


def test_summarize_batch_returns_string(mem):
    """summarize_batch always returns a str."""
    mem.log_episode(instruction="stop", action={"type": "stop"})
    result = mem.summarize_batch(_make_think_fn(), limit=10)
    assert isinstance(result, str)


def test_summarize_batch_returns_raw_text_from_thought(mem):
    """Return value equals the raw_text of the Thought the provider returned."""
    expected = "Robot patrolled the area without incident."
    mem.log_episode(instruction="patrol", action={"type": "move"})
    result = mem.summarize_batch(_make_think_fn(expected), limit=10)
    assert result == expected


def test_summarize_batch_empty_db_returns_empty_string(mem):
    """When the DB is empty no provider call is made and '' is returned."""
    think_fn = _make_think_fn("should not be called")
    result = mem.summarize_batch(think_fn, limit=10)
    assert result == ""
    think_fn.assert_not_called()


# ---------------------------------------------------------------------------
# summarize_batch — storage
# ---------------------------------------------------------------------------


def test_summarize_batch_stores_result_in_summaries_table(mem):
    """After calling summarize_batch, a row exists in the summaries table."""
    mem.log_episode(instruction="go", action={"type": "move"})
    mem.summarize_batch(_make_think_fn("Summary stored."), limit=5)
    summary = mem.get_latest_summary()
    assert summary is not None
    assert summary["summary"] == "Summary stored."


def test_summaries_table_created_automatically(mem):
    """_init_summaries_table() creates the table without raising an error."""
    mem._init_summaries_table()
    with mem._conn() as con:
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='summaries'"
        ).fetchone()
    assert row is not None


# ---------------------------------------------------------------------------
# get_latest_summary — returned structure
# ---------------------------------------------------------------------------


def test_get_latest_summary_returns_dict_with_required_keys(mem):
    """Returned dict contains id, ts, limit_n, and summary keys."""
    mem.log_episode(instruction="test", action={"type": "stop"})
    mem.summarize_batch(_make_think_fn("Has all keys."), limit=10)
    result = mem.get_latest_summary()
    assert result is not None
    assert "id" in result
    assert "ts" in result
    assert "limit_n" in result
    assert "summary" in result


def test_get_latest_summary_returns_most_recent_entry(mem):
    """When multiple summaries exist, the most recent one is returned."""
    mem.log_episode(instruction="action", action={"type": "move"})
    mem.summarize_batch(_make_think_fn("First summary."), limit=5)
    mem.summarize_batch(_make_think_fn("Second summary."), limit=5)
    result = mem.get_latest_summary()
    assert result is not None
    assert result["summary"] == "Second summary."


# ---------------------------------------------------------------------------
# summarize_batch — prompt content
# ---------------------------------------------------------------------------


def test_summarize_batch_prompt_includes_instruction_text(mem):
    """The prompt passed to provider_think_fn contains the episode instruction."""
    mem.log_episode(instruction="navigate to waypoint alpha", action={"type": "nav_waypoint"})
    think_fn = _make_think_fn("ok")
    mem.summarize_batch(think_fn, limit=10)
    # Retrieve the second positional argument (instruction/prompt) from the call
    call_args = think_fn.call_args
    prompt_arg = call_args[0][1]  # positional: (b"", prompt)
    assert "navigate to waypoint alpha" in prompt_arg


def test_summarize_batch_prompt_is_non_empty(mem):
    """The prompt string passed to provider_think_fn is non-empty."""
    mem.log_episode(instruction="step", action={"type": "stop"})
    think_fn = _make_think_fn("ok")
    mem.summarize_batch(think_fn, limit=10)
    prompt_arg = think_fn.call_args[0][1]
    assert len(prompt_arg.strip()) > 0


# ---------------------------------------------------------------------------
# summarize_batch — limit parameter
# ---------------------------------------------------------------------------


def test_summarize_batch_limit_zero_uses_at_least_one_episode(mem):
    """A limit of 0 is coerced to 1 rather than silently exporting nothing."""
    for i in range(5):
        mem.log_episode(instruction=f"ep {i}", action={"type": "move"})
    think_fn = _make_think_fn("Coerced limit.")
    result = mem.summarize_batch(think_fn, limit=0)
    # Should not return empty string (episodes exist) and provider should be called
    think_fn.assert_called_once()
    assert isinstance(result, str)


def test_summarize_batch_limit_respected(mem):
    """provider_think_fn receives a prompt with at most limit episodes."""
    for i in range(20):
        mem.log_episode(instruction=f"instruction_{i}", action={"type": "move"})
    think_fn = _make_think_fn("Limited summary.")
    mem.summarize_batch(think_fn, limit=5)
    prompt_arg = think_fn.call_args[0][1]
    # The prompt body should contain at most 5 episode lines — count "[" occurrences
    # which correspond to [action_type] prefixes in the compact representation.
    episode_lines = [line for line in prompt_arg.splitlines() if line.startswith("[")]
    assert len(episode_lines) <= 5

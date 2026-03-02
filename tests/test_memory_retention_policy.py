"""Tests for EpisodeMemory.retention_policy() — Issue #415."""

from __future__ import annotations

import time

import pytest

from castor.memory import EpisodeMemory


@pytest.fixture
def mem(tmp_path):
    return EpisodeMemory(db_path=str(tmp_path / "test.db"))


def test_returns_dict(mem):
    result = mem.retention_policy()
    assert isinstance(result, dict)


def test_has_required_keys(mem):
    result = mem.retention_policy()
    assert "deleted_by_age" in result
    assert "deleted_by_count" in result
    assert "total_deleted" in result
    assert "remaining" in result


def test_all_values_are_ints_non_negative(mem):
    result = mem.retention_policy()
    for key in ("deleted_by_age", "deleted_by_count", "total_deleted", "remaining"):
        assert isinstance(result[key], int), f"{key} should be int"
        assert result[key] >= 0, f"{key} should be >= 0"


def test_total_deleted_equals_sum(mem):
    for _ in range(5):
        mem.log_episode(instruction="x", action={"type": "move"})
    result = mem.retention_policy(max_age_s=0.0, max_count=2)
    assert result["total_deleted"] == result["deleted_by_age"] + result["deleted_by_count"]


def test_no_params_nothing_deleted(mem):
    for _ in range(3):
        mem.log_episode(instruction="x", action={"type": "move"})
    before = mem.count()
    result = mem.retention_policy()
    assert result["total_deleted"] == 0
    assert mem.count() == before


def test_max_age_s_deletes_old_episodes(mem):
    # Log episodes, then set a very small max_age so they appear old
    for _ in range(4):
        mem.log_episode(instruction="old", action={"type": "move"})
    # max_age_s=0 means cutoff = now, so all existing episodes are "older than now"
    result = mem.retention_policy(max_age_s=0.0)
    assert result["deleted_by_age"] >= 1


def test_max_count_limits_total_episodes(mem):
    for _ in range(10):
        mem.log_episode(instruction="x", action={"type": "move"})
    result = mem.retention_policy(max_count=3)
    assert mem.count() <= 3
    assert result["deleted_by_count"] >= 1


def test_keep_flagged_true_preserves_flagged(mem):
    # Log some episodes and flag one
    ep_id = mem.log_episode(instruction="flagged ep", action={"type": "move"})
    mem.flag_episode(ep_id)
    # Add more unflagged episodes
    for _ in range(3):
        mem.log_episode(instruction="unflagged", action={"type": "stop"})
    # Delete all old episodes with keep_flagged=True
    result = mem.retention_policy(max_age_s=0.0, keep_flagged=True)
    # The flagged episode should still be present
    flagged = mem.query_flagged()
    assert len(flagged) >= 1


def test_keep_flagged_false_deletes_flagged(mem):
    ep_id = mem.log_episode(instruction="flagged ep", action={"type": "move"})
    mem.flag_episode(ep_id)
    for _ in range(2):
        mem.log_episode(instruction="other", action={"type": "stop"})
    result = mem.retention_policy(max_age_s=0.0, keep_flagged=False)
    # All episodes including the flagged one should be deleted
    assert result["deleted_by_age"] > 0
    # After deleting all by age, flagged one is gone too
    flagged = mem.query_flagged()
    assert len(flagged) == 0


def test_never_raises(mem):
    # Should not raise even on edge cases
    try:
        mem.retention_policy(max_age_s=-999999.0, max_count=-1)
        mem.retention_policy(max_age_s=0.0, max_count=0)
    except Exception as exc:
        pytest.fail(f"retention_policy raised unexpectedly: {exc}")


def test_remaining_equals_count_after_call(mem):
    for _ in range(8):
        mem.log_episode(instruction="x", action={"type": "move"})
    result = mem.retention_policy(max_count=3)
    assert result["remaining"] == mem.count()


def test_remaining_zero_when_all_deleted(mem):
    for _ in range(5):
        mem.log_episode(instruction="x", action={"type": "move"})
    result = mem.retention_policy(max_age_s=0.0, keep_flagged=False)
    assert result["remaining"] == 0


def test_max_count_zero_deletes_all_unflagged(mem):
    for _ in range(5):
        mem.log_episode(instruction="x", action={"type": "move"})
    result = mem.retention_policy(max_count=0, keep_flagged=False)
    assert result["deleted_by_count"] >= 1


def test_return_type_on_empty_db(mem):
    result = mem.retention_policy(max_age_s=3600.0, max_count=10)
    assert result["total_deleted"] == 0
    assert result["remaining"] == 0

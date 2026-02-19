"""Tests for EpisodeStore."""

import time

import pytest

from castor.learner.episode import Episode
from castor.learner.episode_store import EpisodeStore


@pytest.fixture
def store(tmp_path):
    return EpisodeStore(store_dir=tmp_path)


def _make_ep(**kwargs):
    return Episode(**kwargs)


class TestEpisodeStore:
    def test_save_load_roundtrip(self, store):
        ep = _make_ep(goal="pick cup", success=True, duration_s=3.0)
        store.save(ep)
        loaded = store.load(ep.id)
        assert loaded.id == ep.id
        assert loaded.goal == ep.goal
        assert loaded.success == ep.success

    def test_load_missing_raises(self, store):
        with pytest.raises(FileNotFoundError):
            store.load("nonexistent-id")

    def test_list_recent(self, store):
        eps = []
        for i in range(5):
            ep = _make_ep(goal=f"task {i}", start_time=1000.0 + i)
            store.save(ep)
            eps.append(ep)
        recent = store.list_recent(3)
        assert len(recent) == 3
        assert recent[0].start_time >= recent[1].start_time

    def test_list_recent_default(self, store):
        for i in range(15):
            store.save(_make_ep(goal=f"t{i}", start_time=float(i)))
        assert len(store.list_recent()) == 10

    def test_list_by_outcome_success(self, store):
        store.save(_make_ep(goal="a", success=True))
        store.save(_make_ep(goal="b", success=False))
        store.save(_make_ep(goal="c", success=True))
        assert len(store.list_by_outcome(success=True)) == 2

    def test_list_by_outcome_failure(self, store):
        store.save(_make_ep(goal="a", success=True))
        store.save(_make_ep(goal="b", success=False))
        assert len(store.list_by_outcome(success=False)) == 1

    def test_delete(self, store):
        ep = _make_ep(goal="delete me")
        store.save(ep)
        store.delete(ep.id)
        with pytest.raises(FileNotFoundError):
            store.load(ep.id)

    def test_delete_nonexistent(self, store):
        store.delete("no-such-id")  # should not raise

    def test_cleanup_removes_old(self, store):
        old_ep = _make_ep(goal="old", start_time=1.0)
        new_ep = _make_ep(goal="new", start_time=time.time())
        store.save(old_ep)
        store.save(new_ep)
        removed = store.cleanup(max_age_days=1)
        assert removed == 1
        assert len(store.list_recent(100)) == 1

    def test_cleanup_keeps_recent(self, store):
        ep = _make_ep(goal="fresh", start_time=time.time())
        store.save(ep)
        removed = store.cleanup(max_age_days=1)
        assert removed == 0

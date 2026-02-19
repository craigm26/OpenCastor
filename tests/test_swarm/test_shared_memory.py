"""Tests for SharedMemory."""

from __future__ import annotations

import json
import time

import pytest

from castor.swarm.shared_memory import MemoryEntry, SharedMemory


def _mem(robot_id: str = "robot-A") -> SharedMemory:
    """Create an in-memory SharedMemory (no file I/O)."""
    m = SharedMemory(robot_id=robot_id, persist_path="/dev/null/unused")
    return m


# ---------------------------------------------------------------------------
# put / get
# ---------------------------------------------------------------------------


class TestPutGet:
    def test_put_and_get(self):
        m = _mem()
        m.put("foo", 42)
        assert m.get("foo") == 42

    def test_get_missing_returns_default(self):
        m = _mem()
        assert m.get("nope") is None
        assert m.get("nope", "fallback") == "fallback"

    def test_overwrite_existing(self):
        m = _mem()
        m.put("x", 1)
        m.put("x", 2)
        assert m.get("x") == 2

    def test_put_complex_value(self):
        m = _mem()
        val = {"nested": [1, 2, 3], "flag": True}
        m.put("data", val)
        assert m.get("data") == val

    def test_put_without_ttl_is_permanent(self):
        m = _mem()
        m.put("perm", "forever")
        entry = m._store["perm"]
        assert entry.ttl_s is None


# ---------------------------------------------------------------------------
# TTL expiry
# ---------------------------------------------------------------------------


class TestTTLExpiry:
    def test_expired_entry_returns_default(self):
        m = _mem()
        m.put("tmp", "value", ttl_s=0.001)
        time.sleep(0.01)
        assert m.get("tmp") is None

    def test_non_expired_entry_still_available(self):
        m = _mem()
        m.put("live", "value", ttl_s=3600)
        assert m.get("live") == "value"

    def test_get_removes_expired_from_store(self):
        m = _mem()
        m.put("gone", "val", ttl_s=0.001)
        time.sleep(0.01)
        m.get("gone")
        assert "gone" not in m._store


# ---------------------------------------------------------------------------
# expire_stale
# ---------------------------------------------------------------------------


class TestExpireStale:
    def test_expire_stale_removes_expired(self):
        m = _mem()
        m.put("a", 1, ttl_s=0.001)
        m.put("b", 2, ttl_s=3600)
        time.sleep(0.01)
        count = m.expire_stale()
        assert count == 1
        assert m.get("b") == 2

    def test_expire_stale_returns_zero_when_none_expired(self):
        m = _mem()
        m.put("a", 1, ttl_s=3600)
        assert m.expire_stale() == 0

    def test_expire_stale_removes_permanent_entry_never(self):
        m = _mem()
        m.put("perm", "forever")
        m.expire_stale()
        assert m.get("perm") == "forever"


# ---------------------------------------------------------------------------
# delete / keys
# ---------------------------------------------------------------------------


class TestDeleteKeys:
    def test_delete_existing(self):
        m = _mem()
        m.put("k", "v")
        assert m.delete("k") is True
        assert m.get("k") is None

    def test_delete_missing_returns_false(self):
        m = _mem()
        assert m.delete("nope") is False

    def test_keys_returns_live_keys(self):
        m = _mem()
        m.put("a", 1)
        m.put("b", 2, ttl_s=0.001)
        time.sleep(0.01)
        ks = m.keys()
        assert "a" in ks
        assert "b" not in ks


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_snapshot_returns_live_entries(self):
        m = _mem()
        m.put("x", 10)
        snap = m.snapshot()
        assert "x" in snap
        assert isinstance(snap["x"], MemoryEntry)
        assert snap["x"].value == 10

    def test_snapshot_excludes_expired(self):
        m = _mem()
        m.put("live", 1)
        m.put("dead", 2, ttl_s=0.001)
        time.sleep(0.01)
        snap = m.snapshot()
        assert "live" in snap
        assert "dead" not in snap


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


class TestMerge:
    def test_merge_adds_new_entries(self):
        local = _mem("robot-A")
        remote_snap = {
            "new_key": MemoryEntry(
                key="new_key",
                value="hello",
                robot_id="robot-B",
                timestamp=time.time(),
                ttl_s=None,
            )
        }
        count = local.merge(remote_snap)
        assert count == 1
        assert local.get("new_key") == "hello"

    def test_merge_latest_timestamp_wins(self):
        local = _mem("robot-A")
        old_ts = time.time() - 100
        new_ts = time.time()
        local.put("k", "old_val")
        local._store["k"].timestamp = old_ts

        remote_snap = {
            "k": MemoryEntry(
                key="k",
                value="new_val",
                robot_id="robot-B",
                timestamp=new_ts,
                ttl_s=None,
            )
        }
        local.merge(remote_snap)
        assert local.get("k") == "new_val"

    def test_merge_local_newer_wins(self):
        local = _mem("robot-A")
        local.put("k", "local_val")
        local._store["k"].timestamp = time.time() + 100  # locally newer

        remote_snap = {
            "k": MemoryEntry(
                key="k",
                value="remote_val",
                robot_id="robot-B",
                timestamp=time.time(),
                ttl_s=None,
            )
        }
        local.merge(remote_snap)
        assert local.get("k") == "local_val"

    def test_merge_accepts_dict_entries(self):
        local = _mem("robot-A")
        remote_snap = {
            "k": {
                "key": "k",
                "value": "from_dict",
                "robot_id": "robot-B",
                "timestamp": time.time(),
                "ttl_s": None,
            }
        }
        local.merge(remote_snap)
        assert local.get("k") == "from_dict"

    def test_merge_skips_expired_remote_entries(self):
        local = _mem("robot-A")
        remote_snap = {
            "old": MemoryEntry(
                key="old",
                value="val",
                robot_id="robot-B",
                timestamp=time.time() - 1000,
                ttl_s=1.0,  # already expired
            )
        }
        count = local.merge(remote_snap)
        assert count == 0
        assert local.get("old") is None

    def test_merge_returns_count_of_merged(self):
        local = _mem("robot-A")
        now = time.time()
        remote_snap = {
            "a": MemoryEntry("a", 1, "robot-B", now, None),
            "b": MemoryEntry("b", 2, "robot-B", now, None),
        }
        count = local.merge(remote_snap)
        assert count == 2


# ---------------------------------------------------------------------------
# persist / load roundtrip
# ---------------------------------------------------------------------------


class TestPersistLoad:
    def test_save_and_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "swarm_memory.json")
        m1 = SharedMemory("robot-A", persist_path=path)
        m1.put("greeting", "hello")
        m1.put("num", 99)
        m1.save()

        m2 = SharedMemory("robot-A", persist_path=path)
        m2.load()
        assert m2.get("greeting") == "hello"
        assert m2.get("num") == 99

    def test_load_creates_empty_if_file_missing(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        m = SharedMemory("robot-A", persist_path=path)
        m.load()  # should not raise
        assert m.keys() == []

    def test_save_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "deep" / "nested" / "memory.json")
        m = SharedMemory("robot-A", persist_path=path)
        m.put("x", 1)
        m.save()  # should create dirs
        assert (tmp_path / "deep" / "nested" / "memory.json").exists()

    def test_load_handles_corrupt_file(self, tmp_path):
        path = str(tmp_path / "corrupt.json")
        (tmp_path / "corrupt.json").write_text("NOT JSON")
        m = SharedMemory("robot-A", persist_path=path)
        m.load()  # should not raise; starts fresh
        assert m.keys() == []

    def test_saved_file_is_valid_json(self, tmp_path):
        path = str(tmp_path / "mem.json")
        m = SharedMemory("robot-A", persist_path=path)
        m.put("k", {"nested": True})
        m.save()
        data = json.loads((tmp_path / "mem.json").read_text())
        assert "k" in data
        assert data["k"]["value"] == {"nested": True}

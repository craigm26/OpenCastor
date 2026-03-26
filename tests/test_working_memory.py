"""Tests for castor.harness.working_memory."""

import json

import pytest

from castor.harness.working_memory import WorkingMemory


@pytest.fixture
def mem():
    return WorkingMemory(max_keys=5)


def test_set_and_get(mem):
    mem.set("foo", "bar")
    assert mem.get("foo") == "bar"


def test_get_missing_returns_default(mem):
    assert mem.get("missing") is None
    assert mem.get("missing", "default") == "default"


def test_delete(mem):
    mem.set("foo", "bar")
    mem.delete("foo")
    assert mem.get("foo") is None


def test_delete_nonexistent_noop(mem):
    mem.delete("nonexistent")  # should not raise


def test_all_returns_copy(mem):
    mem.set("a", 1)
    mem.set("b", 2)
    d = mem.all()
    assert d == {"a": 1, "b": 2}
    d["c"] = 3  # mutating returned dict should not affect store
    assert mem.get("c") is None


def test_clear(mem):
    mem.set("a", 1)
    mem.set("b", 2)
    mem.clear()
    assert len(mem) == 0
    assert mem.all() == {}


def test_max_keys_enforcement(mem):
    for i in range(5):
        mem.set(f"key_{i}", i)
    with pytest.raises(MemoryError):
        mem.set("overflow", "value")


def test_max_keys_allows_overwrite(mem):
    for i in range(5):
        mem.set(f"key_{i}", i)
    # Overwriting an existing key should NOT raise
    mem.set("key_0", "new_value")
    assert mem.get("key_0") == "new_value"


def test_snapshot_deep_copy(mem):
    mem.set("data", {"nested": [1, 2, 3]})
    snap = mem.snapshot()
    snap["data"]["nested"].append(99)
    assert mem.get("data") == {"nested": [1, 2, 3]}  # original unchanged


def test_snapshot_returns_all_keys(mem):
    mem.set("x", 10)
    mem.set("y", 20)
    snap = mem.snapshot()
    assert snap == {"x": 10, "y": 20}


def test_len(mem):
    assert len(mem) == 0
    mem.set("a", 1)
    assert len(mem) == 1


def test_contains(mem):
    mem.set("foo", "bar")
    assert "foo" in mem
    assert "baz" not in mem


def test_to_json(mem):
    mem.set("key", "value")
    j = mem.to_json()
    parsed = json.loads(j)
    assert parsed["key"] == "value"


def test_clear_allows_new_set_after_max(mem):
    for i in range(5):
        mem.set(f"k{i}", i)
    mem.clear()
    mem.set("new_key", "ok")  # should not raise
    assert mem.get("new_key") == "ok"

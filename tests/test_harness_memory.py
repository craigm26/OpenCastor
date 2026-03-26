from __future__ import annotations

import tempfile

import pytest

from castor.harness.memory import (
    FilesystemBackend,
    FirestoreBackend,
    MemoryManager,
    OverflowStrategy,
    WorkingMemoryBackend,
)


def test_working_memory_backend():
    b = WorkingMemoryBackend()
    b.write("sess1", [{"role": "user", "content": "hello"}])
    entries = b.read("sess1")
    assert len(entries) == 1
    assert entries[0]["content"] == "hello"
    b.clear("sess1")
    assert b.read("sess1") == []


def test_filesystem_backend():
    with tempfile.TemporaryDirectory() as tmpdir:
        b = FilesystemBackend(base_dir=tmpdir)
        entries = [{"role": "user", "content": "test"}]
        b.write("sess2", entries)
        loaded = b.read("sess2")
        assert loaded == entries
        b.clear("sess2")
        assert b.read("sess2") == []


def test_overflow_drop_oldest():
    b = WorkingMemoryBackend()
    mgr = MemoryManager(backend=b, max_tokens=10, strategy=OverflowStrategy.DROP_OLDEST)
    entries = [{"content": "x" * 20} for _ in range(5)]
    trimmed = mgr.apply_overflow(entries)
    assert len(trimmed) < len(entries)


def test_overflow_truncate():
    b = WorkingMemoryBackend()
    mgr = MemoryManager(backend=b, max_tokens=5, strategy=OverflowStrategy.TRUNCATE)
    entries = [{"content": "a" * 30} for _ in range(3)]
    trimmed = mgr.apply_overflow(entries)
    assert len(trimmed) <= len(entries)


def test_firestore_backend_offline_fallback():
    """FirestoreBackend must silently fall back to filesystem when Firestore unavailable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fallback = FilesystemBackend(base_dir=tmpdir)
        b = FirestoreBackend(fallback=fallback)
        # Should not raise; Firestore is unavailable in test env
        b.write("sess3", [{"role": "assistant", "content": "hi"}])
        result = b.read("sess3")
        assert result == [{"role": "assistant", "content": "hi"}]

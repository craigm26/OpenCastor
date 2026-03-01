"""Tests for castor/memory.py — SQLite episode memory store (issue #92)."""

import pytest

from castor.memory import EpisodeMemory


@pytest.fixture()
def mem(tmp_path):
    db = tmp_path / "test_memory.db"
    return EpisodeMemory(db_path=str(db), max_episodes=100)


def test_log_and_query(mem):
    ep_id = mem.log_episode(
        instruction="move forward",
        raw_thought='{"type":"move","linear":0.5}',
        action={"type": "move", "linear": 0.5},
        latency_ms=120.0,
        image_hash="abc123",
        outcome="ok",
        source="test",
    )
    assert ep_id  # should be a non-empty UUID string

    rows = mem.query_recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["instruction"] == "move forward"
    assert rows[0]["action"]["type"] == "move"


def test_count(mem):
    assert mem.count() == 0
    mem.log_episode(instruction="ping", action={"type": "stop"})
    assert mem.count() == 1


def test_get_episode(mem):
    ep_id = mem.log_episode(instruction="grip", action={"type": "grip", "state": "open"})
    ep = mem.get_episode(ep_id)
    assert ep is not None
    assert ep["instruction"] == "grip"

    missing = mem.get_episode("00000000-0000-0000-0000-000000000000")
    assert missing is None


def test_clear(mem):
    for i in range(5):
        mem.log_episode(instruction=f"cmd {i}", action={"type": "stop"})
    assert mem.count() == 5
    deleted = mem.clear()
    assert deleted == 5
    assert mem.count() == 0


def test_export_jsonl(mem, tmp_path):
    mem.log_episode(instruction="hello", action={"type": "wait", "duration_ms": 500})
    out = tmp_path / "out.jsonl"
    lines = mem.export_jsonl(str(out))
    assert lines == 1
    import json

    with open(out) as f:
        row = json.loads(f.readline())
    assert row["instruction"] == "hello"


def test_fifo_eviction(tmp_path):
    db = tmp_path / "evict.db"
    mem = EpisodeMemory(db_path=str(db), max_episodes=5)
    for i in range(7):
        mem.log_episode(instruction=f"cmd{i}", action={"type": "stop"})
    assert mem.count() <= 5


def test_hash_image():
    b = b"fake jpeg bytes"
    h = EpisodeMemory.hash_image(b)
    assert len(h) == 16
    assert EpisodeMemory.hash_image(b) == h  # deterministic


def test_query_filter_source(mem):
    mem.log_episode(instruction="a", action={}, source="api")
    mem.log_episode(instruction="b", action={}, source="runtime")
    api_rows = mem.query_recent(limit=10, source="api")
    assert all(r.get("source") == "api" for r in api_rows)


def test_env_var_override(tmp_path, monkeypatch):
    db = tmp_path / "env.db"
    monkeypatch.setenv("CASTOR_MEMORY_DB", str(db))
    mem = EpisodeMemory()
    mem.log_episode(instruction="env test", action={})
    assert mem.count() == 1


# ===========================================================================
# Issue #267/#226 — Multi-modal memory (image_blob)
# ===========================================================================


class TestMultiModalMemory:
    def _make_mem(self, tmp_path):
        from castor.memory import EpisodeMemory

        return EpisodeMemory(db_path=str(tmp_path / "mm.db"))

    def test_log_episode_without_image_has_no_image(self, tmp_path):
        mem = self._make_mem(tmp_path)
        mem.log_episode(instruction="cmd", action={"type": "stop"})
        rows = mem.query_recent(limit=1)
        assert rows[0]["has_image"] is False

    def test_log_episode_with_image_stores_blob(self, tmp_path):
        mem = self._make_mem(tmp_path)
        # Minimal JPEG header bytes (won't decode with cv2 but stored as-is)
        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 20
        mem.log_episode(instruction="photo", action={}, image_bytes=fake_jpeg)
        rows = mem.query_recent(limit=1)
        assert rows[0]["has_image"] is True

    def test_get_episode_image_returns_bytes(self, tmp_path):
        mem = self._make_mem(tmp_path)
        fake_jpeg = b"\xff\xd8\xff" + b"\xab" * 50
        mem.log_episode(instruction="snap", action={}, image_bytes=fake_jpeg)
        ep_id = mem.query_recent(limit=1)[0]["id"]
        blob = mem.get_episode_image(ep_id)
        assert blob is not None
        assert isinstance(blob, bytes)
        assert len(blob) > 0

    def test_get_episode_image_missing_returns_none(self, tmp_path):
        mem = self._make_mem(tmp_path)
        result = mem.get_episode_image(99999)
        assert result is None

    def test_episodes_with_images_lists_only_image_episodes(self, tmp_path):
        mem = self._make_mem(tmp_path)
        mem.log_episode(instruction="no_img", action={})
        fake_jpeg = b"\xff\xd8" + b"\x00" * 10
        mem.log_episode(instruction="has_img", action={}, image_bytes=fake_jpeg)
        imgs = mem.episodes_with_images(limit=10)
        assert len(imgs) == 1
        assert imgs[0]["has_image"] is True
        assert imgs[0]["instruction"] == "has_img"

    def test_episodes_with_images_empty_when_none(self, tmp_path):
        mem = self._make_mem(tmp_path)
        mem.log_episode(instruction="cmd", action={})
        assert mem.episodes_with_images() == []

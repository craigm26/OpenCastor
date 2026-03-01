"""Tests for ProviderPool request replay — issue #326."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

from castor.providers.base import Thought


def _make_pool(record_path=None, replay_path=None, n=2):
    from castor.providers.pool_provider import ProviderPool

    mock_providers = []
    pool_entries = []
    for i in range(n):
        m = MagicMock()
        m.think.return_value = Thought(raw_text=f"provider{i}", action={"type": "move"})
        m.health_check.return_value = {"ok": True, "mode": "mock"}
        mock_providers.append(m)
        pool_entries.append({"provider": f"mock{i}", "api_key": "x", "model": f"m{i}"})

    cfg = {"pool": pool_entries, "pool_strategy": "round_robin"}
    if record_path:
        cfg["pool_record_path"] = record_path
    if replay_path:
        cfg["pool_replay_path"] = replay_path

    with patch("castor.providers.get_provider") as mock_gp:
        mock_gp.side_effect = mock_providers
        pool = ProviderPool(cfg)
    return pool, mock_providers


# ── _replay_key ───────────────────────────────────────────────────────────────


def test_replay_key_deterministic():
    from castor.providers.pool_provider import ProviderPool

    k1 = ProviderPool._replay_key("go forward")
    k2 = ProviderPool._replay_key("go forward")
    assert k1 == k2


def test_replay_key_different_instructions():
    from castor.providers.pool_provider import ProviderPool

    k1 = ProviderPool._replay_key("go forward")
    k2 = ProviderPool._replay_key("turn left")
    assert k1 != k2


def test_replay_key_length():
    from castor.providers.pool_provider import ProviderPool

    k = ProviderPool._replay_key("test")
    assert len(k) == 16


# ── recording ─────────────────────────────────────────────────────────────────


def test_record_path_creates_jsonl_file():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name
    os.unlink(path)
    try:
        pool, _ = _make_pool(record_path=path)
        pool.think(b"", "go forward")
        assert os.path.exists(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_record_path_writes_valid_jsonl():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name
    os.unlink(path)
    try:
        pool, _ = _make_pool(record_path=path)
        pool.think(b"", "spin right")
        with open(path) as fh:
            lines = [json.loads(ln) for ln in fh if ln.strip()]
        assert len(lines) == 1
        rec = lines[0]
        assert "key" in rec
        assert "instruction" in rec
        assert "raw_text" in rec
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_record_path_stores_instruction():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name
    os.unlink(path)
    try:
        pool, _ = _make_pool(record_path=path)
        pool.think(b"", "turn around")
        with open(path) as fh:
            rec = json.loads(fh.read().strip())
        assert rec["instruction"] == "turn around"
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_record_path_multiple_calls_append():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name
    os.unlink(path)
    try:
        pool, _ = _make_pool(record_path=path)
        pool.think(b"", "go forward")
        pool.think(b"", "turn left")
        with open(path) as fh:
            lines = [json.loads(ln) for ln in fh if ln.strip()]
        assert len(lines) == 2
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_no_record_path_no_file_created():
    pool, _ = _make_pool()
    pool.think(b"", "go")
    assert pool._record_path is None


# ── replay ────────────────────────────────────────────────────────────────────


def test_replay_map_loaded_from_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        rec = {
            "key": "deadbeef12345678",
            "instruction": "test instruction",
            "raw_text": "replayed",
            "action": {"type": "stop"},
        }
        f.write(json.dumps(rec) + "\n")
        path = f.name
    try:
        pool, _ = _make_pool(replay_path=path)
        assert len(pool._replay_map) == 1
    finally:
        os.unlink(path)


def test_replay_hit_returns_cached_thought():
    from castor.providers.pool_provider import ProviderPool

    instruction = "replay me"
    key = ProviderPool._replay_key(instruction)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        rec = {
            "key": key,
            "instruction": instruction,
            "raw_text": "cached_response",
            "action": {"type": "wait"},
        }
        f.write(json.dumps(rec) + "\n")
        path = f.name
    try:
        pool, mocks = _make_pool(replay_path=path)
        result = pool.think(b"", instruction)
        assert result.raw_text == "cached_response"
        # Provider.think should NOT have been called
        for m in mocks:
            m.think.assert_not_called()
    finally:
        os.unlink(path)


def test_replay_miss_calls_provider():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        rec = {
            "key": "0000000000000000",
            "instruction": "something else",
            "raw_text": "cached",
            "action": {"type": "stop"},
        }
        f.write(json.dumps(rec) + "\n")
        path = f.name
    try:
        pool, mocks = _make_pool(replay_path=path)
        pool.think(b"", "a different instruction")
        # At least one provider's think should have been called
        called = any(m.think.called for m in mocks)
        assert called
    finally:
        os.unlink(path)


def test_replay_path_missing_file_no_crash():
    """Missing replay file should log a warning but not raise."""
    pool, _ = _make_pool(replay_path="/nonexistent/path/replay.jsonl")
    assert pool._replay_map == {}


# ── health_check replay section ───────────────────────────────────────────────


def test_health_check_includes_replay_key():
    pool, _ = _make_pool()
    h = pool.health_check()
    assert "replay" in h


def test_health_check_replay_record_path():
    pool, _ = _make_pool(record_path="/tmp/test_record.jsonl")
    h = pool.health_check()
    assert h["replay"]["record_path"] == "/tmp/test_record.jsonl"


def test_health_check_replay_entries_count():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for i in range(3):
            f.write(
                json.dumps({"key": f"key{i:016d}", "instruction": f"i{i}", "raw_text": "r"}) + "\n"
            )
        path = f.name
    try:
        pool, _ = _make_pool(replay_path=path)
        h = pool.health_check()
        assert h["replay"]["replay_entries"] == 3
    finally:
        os.unlink(path)


def test_health_check_replay_null_paths_when_unset():
    pool, _ = _make_pool()
    h = pool.health_check()
    assert h["replay"]["record_path"] is None
    assert h["replay"]["replay_path"] is None

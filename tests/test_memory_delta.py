"""Tests for EpisodeMemory.export_delta() and get_latest_episode_id() — issue #330."""

from __future__ import annotations

import json

import pytest

from castor.memory import EpisodeMemory


@pytest.fixture()
def mem(tmp_path):
    db = str(tmp_path / "delta.db")
    return EpisodeMemory(db_path=db, max_episodes=0)


# ---------------------------------------------------------------------------
# get_latest_episode_id
# ---------------------------------------------------------------------------


def test_get_latest_episode_id_empty_db(mem):
    """Returns None when the database has no episodes."""
    assert mem.get_latest_episode_id() is None


def test_get_latest_episode_id_returns_string(mem):
    """Returns a non-empty string after at least one episode is logged."""
    mem.log_episode(instruction="hello", action={"type": "move"})
    result = mem.get_latest_episode_id()
    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0


def test_get_latest_episode_id_is_last_inserted(mem):
    """Returns the id of the most recently inserted episode."""
    mem.log_episode(instruction="first", action={"type": "move"})
    ep2 = mem.log_episode(instruction="second", action={"type": "stop"})
    assert mem.get_latest_episode_id() == ep2


# ---------------------------------------------------------------------------
# export_delta — edge cases: empty DB and None/empty since_id
# ---------------------------------------------------------------------------


def test_export_delta_empty_db_returns_zero(mem, tmp_path):
    """Delta export on an empty DB returns 0 and creates an empty file."""
    out = str(tmp_path / "delta.jsonl")
    count = mem.export_delta(None, out)
    assert count == 0


def test_export_delta_since_id_none_exports_all(mem, tmp_path):
    """since_id=None performs a full export of all episodes."""
    for i in range(5):
        mem.log_episode(instruction=f"cmd {i}", action={"type": "move"})
    out = str(tmp_path / "delta_none.jsonl")
    count = mem.export_delta(None, out)
    assert count == 5


def test_export_delta_since_id_empty_string_exports_all(mem, tmp_path):
    """since_id='' (empty string) performs a full export of all episodes."""
    for i in range(4):
        mem.log_episode(instruction=f"ep {i}", action={"type": "stop"})
    out = str(tmp_path / "delta_empty.jsonl")
    count = mem.export_delta("", out)
    assert count == 4


# ---------------------------------------------------------------------------
# export_delta — correctness with a valid since_id
# ---------------------------------------------------------------------------


def test_export_delta_valid_since_id_exports_only_newer(mem, tmp_path):
    """Only episodes inserted after since_id are exported."""
    _ = mem.log_episode(instruction="old 1", action={"type": "move"})
    ep2 = mem.log_episode(instruction="old 2", action={"type": "stop"})
    mem.log_episode(instruction="new 1", action={"type": "wait"})
    mem.log_episode(instruction="new 2", action={"type": "grip"})
    out = str(tmp_path / "delta_valid.jsonl")
    count = mem.export_delta(ep2, out)
    assert count == 2
    with open(out) as fh:
        lines = [json.loads(ln) for ln in fh if ln.strip()]
    instructions = [row["instruction"] for row in lines]
    assert "new 1" in instructions
    assert "new 2" in instructions
    assert "old 1" not in instructions
    assert "old 2" not in instructions


def test_export_delta_since_latest_id_returns_zero(mem, tmp_path):
    """Passing the latest episode id returns 0 (nothing newer exists)."""
    for i in range(3):
        mem.log_episode(instruction=f"item {i}", action={"type": "move"})
    latest = mem.get_latest_episode_id()
    out = str(tmp_path / "delta_latest.jsonl")
    count = mem.export_delta(latest, out)
    assert count == 0


def test_export_delta_unknown_since_id_treats_as_full_export(mem, tmp_path):
    """An unrecognised since_id triggers a full export."""
    for i in range(3):
        mem.log_episode(instruction=f"ep {i}", action={"type": "move"})
    out = str(tmp_path / "delta_unknown.jsonl")
    count = mem.export_delta("00000000-0000-0000-0000-deadbeef0000", out)
    assert count == 3


# ---------------------------------------------------------------------------
# export_delta — file format
# ---------------------------------------------------------------------------


def test_export_delta_creates_jsonl_file(mem, tmp_path):
    """export_delta creates the output file."""
    mem.log_episode(instruction="test", action={"type": "stop"})
    out = str(tmp_path / "created.jsonl")
    mem.export_delta(None, out)
    import os

    assert os.path.exists(out)


def test_export_delta_jsonl_format_one_json_per_line(mem, tmp_path):
    """Each line of the output file is a valid JSON object."""
    for i in range(3):
        mem.log_episode(instruction=f"line {i}", action={"type": "move"})
    out = str(tmp_path / "format.jsonl")
    mem.export_delta(None, out)
    with open(out) as fh:
        lines = fh.readlines()
    assert len(lines) == 3
    for line in lines:
        obj = json.loads(line)
        assert isinstance(obj, dict)


def test_export_delta_returns_correct_count(mem, tmp_path):
    """Return value equals the number of lines written to the file."""
    for i in range(6):
        mem.log_episode(instruction=f"ep {i}", action={"type": "stop"})
    out = str(tmp_path / "count.jsonl")
    count = mem.export_delta(None, out)
    with open(out) as fh:
        file_lines = [ln for ln in fh if ln.strip()]
    assert count == len(file_lines)


def test_export_delta_jsonl_contains_required_fields(mem, tmp_path):
    """Each exported JSONL object has id, ts, instruction, and action_type fields."""
    mem.log_episode(
        instruction="check fields",
        action={"type": "move", "linear": 0.5},
        outcome="ok",
    )
    out = str(tmp_path / "fields.jsonl")
    mem.export_delta(None, out)
    with open(out) as fh:
        obj = json.loads(fh.readline())
    assert "id" in obj
    assert "ts" in obj
    assert "instruction" in obj
    # action is the parsed dict (action_json is removed by _row_to_dict)
    assert "action" in obj
    assert obj["action"]["type"] == "move"

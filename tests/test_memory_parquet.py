"""tests/test_memory_parquet.py — Tests for EpisodeMemory.export_parquet (#319)."""

from __future__ import annotations

import os
import uuid

import pytest

from castor.memory import EpisodeMemory, _probe_pyarrow

pyarrow = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq  # noqa: E402  (only runs when pyarrow present)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mem(tmp_path):
    """Return a fresh EpisodeMemory backed by a temp SQLite DB."""
    db = str(tmp_path / "test_memory.db")
    return EpisodeMemory(db_path=db)


@pytest.fixture()
def populated_mem(mem):
    """Memory store with 5 pre-loaded episodes."""
    mem.log_episode(
        instruction="move forward",
        raw_thought="I will move forward",
        action={"type": "move", "linear": 0.5},
        latency_ms=100.0,
        outcome="ok",
        source="api",
        tags=["patrol", "outdoor"],
    )
    mem.log_episode(
        instruction="stop now",
        raw_thought="Stopping the robot",
        action={"type": "stop"},
        latency_ms=50.0,
        outcome="ok",
        source="loop",
        tags=["indoor"],
    )
    mem.log_episode(
        instruction="turn left",
        raw_thought="Turning left 90 degrees",
        action={"type": "move", "angular": 0.8},
        latency_ms=200.0,
        outcome="ok",
        source="api",
        tags=[],
    )
    mem.log_episode(
        instruction="wait here",
        raw_thought="Waiting for instructions",
        action={"type": "wait"},
        latency_ms=10.0,
        outcome="ok",
        source="loop",
        tags=["patrol"],
    )
    mem.log_episode(
        instruction="grip object",
        raw_thought="Gripping the object",
        action={"type": "grip", "value": 1.0},
        latency_ms=150.0,
        outcome="ok",
        source="api",
        tags=["lab"],
    )
    return mem


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_export_parquet_returns_count(populated_mem, tmp_path):
    """export_parquet() returns an int equal to the number of episodes exported."""
    out = str(tmp_path / "episodes.parquet")
    result = populated_mem.export_parquet(out)
    assert isinstance(result, int)
    assert result == 5


def test_export_parquet_file_exists(populated_mem, tmp_path):
    """export_parquet() creates the output file on disk."""
    out = str(tmp_path / "episodes.parquet")
    populated_mem.export_parquet(out)
    assert os.path.isfile(out)


def test_export_parquet_has_correct_columns(populated_mem, tmp_path):
    """Parquet file contains all expected column names."""
    out = str(tmp_path / "episodes.parquet")
    populated_mem.export_parquet(out)
    table = pq.read_table(out)
    expected_columns = {
        "id",
        "ts",
        "instruction",
        "raw_thought",
        "action_type",
        "latency_ms",
        "outcome",
        "source",
        "tags",
    }
    assert expected_columns == set(table.schema.names)


def test_export_parquet_row_count_matches(populated_mem, tmp_path):
    """Number of rows in the Parquet file equals episodes stored in the DB."""
    out = str(tmp_path / "episodes.parquet")
    populated_mem.export_parquet(out)
    table = pq.read_table(out)
    assert table.num_rows == populated_mem.count()


def test_export_parquet_limit_respected(populated_mem, tmp_path):
    """When limit=2 is given, the Parquet file contains exactly 2 rows."""
    out = str(tmp_path / "episodes.parquet")
    count = populated_mem.export_parquet(out, limit=2)
    table = pq.read_table(out)
    assert count == 2
    assert table.num_rows == 2


def test_export_parquet_id_column(populated_mem, tmp_path):
    """id column contains valid UUID strings."""
    out = str(tmp_path / "episodes.parquet")
    populated_mem.export_parquet(out)
    table = pq.read_table(out)
    ids = table.column("id").to_pylist()
    for ep_id in ids:
        # Should be parseable as a UUID without raising
        parsed = uuid.UUID(ep_id)
        assert str(parsed) == ep_id


def test_export_parquet_ts_column_float(populated_mem, tmp_path):
    """ts column contains float values (Unix timestamps)."""
    out = str(tmp_path / "episodes.parquet")
    populated_mem.export_parquet(out)
    table = pq.read_table(out)
    timestamps = table.column("ts").to_pylist()
    for ts in timestamps:
        assert isinstance(ts, float)
        # Sanity: year 2020 onward
        assert ts > 1_580_000_000.0


def test_export_parquet_instruction_column(populated_mem, tmp_path):
    """instruction column contains expected text values."""
    out = str(tmp_path / "episodes.parquet")
    populated_mem.export_parquet(out)
    table = pq.read_table(out)
    instructions = table.column("instruction").to_pylist()
    assert "move forward" in instructions
    assert "stop now" in instructions


def test_export_parquet_action_type_extracted(populated_mem, tmp_path):
    """action_type column has correctly extracted type strings from action_json."""
    out = str(tmp_path / "episodes.parquet")
    populated_mem.export_parquet(out)
    table = pq.read_table(out)
    action_types = table.column("action_type").to_pylist()
    assert "move" in action_types
    assert "stop" in action_types
    assert "wait" in action_types
    assert "grip" in action_types


def test_export_parquet_tags_column(populated_mem, tmp_path):
    """tags column stores tags as a comma-separated string."""
    out = str(tmp_path / "episodes.parquet")
    populated_mem.export_parquet(out)
    table = pq.read_table(out)
    tags_list = table.column("tags").to_pylist()
    # At least one entry should contain "patrol"
    assert any("patrol" in t for t in tags_list)
    # At least one entry should contain "outdoor"
    assert any("outdoor" in t for t in tags_list)
    # Entries with multiple tags should be comma-separated
    multi_tag_entries = [t for t in tags_list if "," in t]
    assert len(multi_tag_entries) >= 1


def test_export_parquet_raises_without_pyarrow(mem, tmp_path, monkeypatch):
    """export_parquet() raises ImportError when pyarrow is not available."""
    import castor.memory as memory_module

    monkeypatch.setattr(memory_module, "_probe_pyarrow", lambda: False)
    out = str(tmp_path / "episodes.parquet")
    with pytest.raises(ImportError, match="pyarrow required"):
        mem.export_parquet(out)


def test_probe_pyarrow_returns_bool():
    """_probe_pyarrow() always returns a bool value."""
    result = _probe_pyarrow()
    assert isinstance(result, bool)
    # Since pyarrow is installed (test file reached this point via importorskip)
    assert result is True

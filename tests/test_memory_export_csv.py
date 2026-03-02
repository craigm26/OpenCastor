"""Tests for EpisodeMemory.export_csv() (#377)."""

import csv
import os

import pytest

from castor.memory import EpisodeMemory


@pytest.fixture
def mem(tmp_path):
    db = str(tmp_path / "mem.db")
    m = EpisodeMemory(db_path=db)
    return m


@pytest.fixture
def mem_with_data(mem):
    for i in range(5):
        mem.log_episode(
            instruction=f"go {i}",
            raw_thought=f"thought {i}",
            action={"type": "move", "linear": 0.5},
            latency_ms=100.0 + i,
            outcome="success",
            source="test",
            tags=["a", "b"],
        )
    return mem


# ── basic return shape ────────────────────────────────────────────────────────

def test_export_csv_returns_dict(mem_with_data, tmp_path):
    result = mem_with_data.export_csv(str(tmp_path / "out.csv"))
    assert isinstance(result, dict)


def test_export_csv_success_keys(mem_with_data, tmp_path):
    result = mem_with_data.export_csv(str(tmp_path / "out.csv"))
    assert "path" in result
    assert "rows_written" in result
    assert "columns" in result


def test_export_csv_creates_file(mem_with_data, tmp_path):
    path = str(tmp_path / "out.csv")
    mem_with_data.export_csv(path)
    assert os.path.exists(path)


def test_export_csv_rows_written_count(mem_with_data, tmp_path):
    path = str(tmp_path / "out.csv")
    result = mem_with_data.export_csv(path)
    assert result["rows_written"] == 5


def test_export_csv_columns_list(mem_with_data, tmp_path):
    result = mem_with_data.export_csv(str(tmp_path / "out.csv"))
    expected = ["id", "ts", "instruction", "raw_thought", "action_type",
                "latency_ms", "outcome", "source", "tags"]
    assert result["columns"] == expected


# ── file contents ─────────────────────────────────────────────────────────────

def test_export_csv_has_header(mem_with_data, tmp_path):
    path = str(tmp_path / "out.csv")
    mem_with_data.export_csv(path)
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert set(reader.fieldnames) == {
            "id", "ts", "instruction", "raw_thought", "action_type",
            "latency_ms", "outcome", "source", "tags"
        }


def test_export_csv_row_count_matches(mem_with_data, tmp_path):
    path = str(tmp_path / "out.csv")
    mem_with_data.export_csv(path)
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 5


def test_export_csv_action_type_extracted(mem_with_data, tmp_path):
    path = str(tmp_path / "out.csv")
    mem_with_data.export_csv(path)
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        assert row["action_type"] == "move"


def test_export_csv_instruction_correct(mem_with_data, tmp_path):
    path = str(tmp_path / "out.csv")
    mem_with_data.export_csv(path)
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    instructions = {r["instruction"] for r in rows}
    for i in range(5):
        assert f"go {i}" in instructions


# ── empty memory ─────────────────────────────────────────────────────────────

def test_export_csv_empty_memory(mem, tmp_path):
    path = str(tmp_path / "out.csv")
    result = mem.export_csv(path)
    assert result["rows_written"] == 0
    assert os.path.exists(path)


def test_export_csv_empty_has_header_only(mem, tmp_path):
    path = str(tmp_path / "out.csv")
    mem.export_csv(path)
    with open(path, newline="", encoding="utf-8") as fh:
        content = fh.read()
    assert "instruction" in content


# ── window_s and limit ────────────────────────────────────────────────────────

def test_export_csv_limit_respected(mem_with_data, tmp_path):
    path = str(tmp_path / "out.csv")
    result = mem_with_data.export_csv(path, limit=2)
    assert result["rows_written"] == 2


def test_export_csv_window_s_zero_returns_empty(mem_with_data, tmp_path):
    path = str(tmp_path / "out.csv")
    result = mem_with_data.export_csv(path, window_s=0.0)
    assert result["rows_written"] == 0


# ── error handling ────────────────────────────────────────────────────────────

def test_export_csv_bad_path_returns_error(mem_with_data):
    result = mem_with_data.export_csv("/nonexistent_dir_xyz/out.csv")
    assert "error" in result


def test_export_csv_never_raises(mem_with_data):
    try:
        mem_with_data.export_csv("/nonexistent_dir_xyz/out.csv")
    except Exception as exc:
        pytest.fail(f"export_csv raised: {exc}")

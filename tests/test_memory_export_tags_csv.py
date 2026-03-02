"""Tests for EpisodeMemory.export_tags_csv() — Issue #410."""

from __future__ import annotations

import os

import pytest

from castor.memory import EpisodeMemory


@pytest.fixture
def mem(tmp_path):
    return EpisodeMemory(db_path=str(tmp_path / "test.db"))


def test_returns_dict(mem, tmp_path):
    result = mem.export_tags_csv(str(tmp_path / "tags.csv"))
    assert isinstance(result, dict)


def test_has_required_keys(mem, tmp_path):
    result = mem.export_tags_csv(str(tmp_path / "tags.csv"))
    assert "path" in result
    assert "rows_written" in result
    assert "window_s" in result


def test_path_matches_param(mem, tmp_path):
    out = str(tmp_path / "tags_out.csv")
    result = mem.export_tags_csv(out)
    assert result["path"] == out


def test_rows_written_non_negative(mem, tmp_path):
    result = mem.export_tags_csv(str(tmp_path / "tags.csv"))
    assert result["rows_written"] >= 0


def test_creates_file(mem, tmp_path):
    out = str(tmp_path / "tags.csv")
    mem.export_tags_csv(out)
    assert os.path.exists(out)


def test_file_has_header_row(mem, tmp_path):
    out = str(tmp_path / "tags.csv")
    mem.export_tags_csv(out)
    with open(out, encoding="utf-8") as fh:
        first_line = fh.readline().strip()
    assert first_line == "tag,count,rank"


def test_empty_db_rows_written_zero(mem, tmp_path):
    result = mem.export_tags_csv(str(tmp_path / "tags.csv"))
    assert result["rows_written"] == 0


def test_after_log_episode_rows_written_positive(mem, tmp_path):
    mem.log_episode(
        instruction="go forward",
        action={"type": "move", "linear": 0.5},
    )
    result = mem.export_tags_csv(str(tmp_path / "tags.csv"), window_s=3600.0)
    assert result["rows_written"] > 0


def test_invalid_path_returns_zero_never_raises(mem):
    # Use a path under a non-writable root directory (not a temp path)
    result = mem.export_tags_csv("/proc/nonexistent_dir/tags.csv")
    assert isinstance(result, dict)
    assert result["rows_written"] == 0


def test_window_s_reflected_in_result(mem, tmp_path):
    result = mem.export_tags_csv(str(tmp_path / "tags.csv"), window_s=7200.0)
    assert result["window_s"] == 7200.0


def test_rows_written_le_top_k(mem, tmp_path):
    for i in range(15):
        mem.log_episode(
            instruction=f"action {i}",
            action={"type": f"type_{i}"},
        )
    result = mem.export_tags_csv(str(tmp_path / "tags.csv"), top_k=5)
    assert result["rows_written"] <= 5


def test_rows_written_respects_default_top_k(mem, tmp_path):
    for i in range(25):
        mem.log_episode(
            instruction=f"action {i}",
            action={"type": f"type_{i}"},
        )
    result = mem.export_tags_csv(str(tmp_path / "tags.csv"))
    # default top_k=20
    assert result["rows_written"] <= 20


def test_csv_has_data_rows_after_episodes(mem, tmp_path):
    mem.log_episode(instruction="test", action={"type": "move"})
    out = str(tmp_path / "tags.csv")
    result = mem.export_tags_csv(out, window_s=3600.0)
    assert result["rows_written"] >= 1
    with open(out, encoding="utf-8") as fh:
        lines = fh.readlines()
    # header + at least one data row
    assert len(lines) >= 2

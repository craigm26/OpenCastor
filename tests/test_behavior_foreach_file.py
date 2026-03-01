"""Tests for BehaviorRunner foreach_file step (Issue #341)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from castor.behaviors import BehaviorRunner


def make_runner():
    return BehaviorRunner(driver=None, brain=None, speaker=None, config={})


def write_jsonl(path: Path, records: list) -> None:
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ── Basic dispatch tests ──────────────────────────────────────────────────────


def test_foreach_file_registered_in_dispatch_table():
    runner = make_runner()
    assert "foreach_file" in runner._step_handlers


def test_foreach_file_skips_when_no_file_key():
    runner = make_runner()
    runner._running = True
    step = {"type": "foreach_file", "steps": []}
    runner._step_foreach_file(step)  # Should not raise


def test_foreach_file_skips_when_file_missing():
    runner = make_runner()
    runner._running = True
    step = {
        "type": "foreach_file",
        "file": "/nonexistent/path/does_not_exist.jsonl",
        "steps": [],
    }
    runner._step_foreach_file(step)  # Should not raise (file not found logged as warning)


def test_foreach_file_empty_file():
    runner = make_runner()
    runner._running = True
    with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
        f.write("")
    try:
        step = {"type": "foreach_file", "file": f.name, "steps": []}
        runner._step_foreach_file(step)  # Should not raise
    finally:
        os.unlink(f.name)


def test_foreach_file_iterates_all_rows():
    runner = make_runner()
    runner._running = True

    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "data.jsonl"
        records = [{"id": i, "value": i * 10} for i in range(5)]
        write_jsonl(p, records)

        visited = []

        def fake_run_step_list(steps, ctx):
            for s in steps:
                visited.append(s)

        with patch.object(runner, "_run_step_list", side_effect=fake_run_step_list):
            step = {
                "type": "foreach_file",
                "file": str(p),
                "steps": [{"type": "wait", "seconds": "$item.value"}],
            }
            runner._step_foreach_file(step)

    # 5 rows → 5 wait steps
    assert len(visited) == 5


def test_foreach_file_substitutes_item_field():
    runner = make_runner()
    runner._running = True

    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "waypoints.jsonl"
        records = [{"distance_m": 1.5, "heading_deg": 90.0}]
        write_jsonl(p, records)

        received = []

        def fake_run_step_list(steps, ctx):
            received.extend(steps)

        with patch.object(runner, "_run_step_list", side_effect=fake_run_step_list):
            step = {
                "type": "foreach_file",
                "file": str(p),
                "steps": [
                    {
                        "type": "waypoint",
                        "distance_m": "$item.distance_m",
                        "heading_deg": "$item.heading_deg",
                    }
                ],
            }
            runner._step_foreach_file(step)

    assert len(received) == 1
    assert received[0]["distance_m"] == 1.5
    assert received[0]["heading_deg"] == 90.0


def test_foreach_file_respects_limit():
    runner = make_runner()
    runner._running = True

    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "data.jsonl"
        records = [{"n": i} for i in range(10)]
        write_jsonl(p, records)

        visited = []

        def fake_run_step_list(steps, ctx):
            visited.extend(steps)

        with patch.object(runner, "_run_step_list", side_effect=fake_run_step_list):
            step = {
                "type": "foreach_file",
                "file": str(p),
                "limit": 3,
                "steps": [{"type": "wait", "seconds": 0}],
            }
            runner._step_foreach_file(step)

    assert len(visited) == 3


def test_foreach_file_skips_blank_lines():
    runner = make_runner()
    runner._running = True

    with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
        f.write('{"a": 1}\n')
        f.write("\n")
        f.write('{"a": 2}\n')
        f.write("   \n")
        f.write('{"a": 3}\n')
        fname = f.name

    try:
        visited = []

        def fake_run_step_list(steps, ctx):
            visited.extend(steps)

        with patch.object(runner, "_run_step_list", side_effect=fake_run_step_list):
            step = {
                "type": "foreach_file",
                "file": fname,
                "steps": [{"type": "wait", "seconds": 0}],
            }
            runner._step_foreach_file(step)
    finally:
        os.unlink(fname)

    assert len(visited) == 3


def test_foreach_file_skips_comment_lines():
    runner = make_runner()
    runner._running = True

    with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
        f.write("# This is a comment\n")
        f.write('{"a": 1}\n')
        fname = f.name

    try:
        visited = []

        def fake_run_step_list(steps, ctx):
            visited.extend(steps)

        with patch.object(runner, "_run_step_list", side_effect=fake_run_step_list):
            step = {
                "type": "foreach_file",
                "file": fname,
                "steps": [{"type": "wait", "seconds": 0}],
            }
            runner._step_foreach_file(step)
    finally:
        os.unlink(fname)

    assert len(visited) == 1


def test_foreach_file_skips_invalid_json_rows():
    runner = make_runner()
    runner._running = True

    with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
        f.write('{"ok": 1}\n')
        f.write("not valid json\n")
        f.write('{"ok": 2}\n')
        fname = f.name

    try:
        visited = []

        def fake_run_step_list(steps, ctx):
            visited.extend(steps)

        with patch.object(runner, "_run_step_list", side_effect=fake_run_step_list):
            step = {
                "type": "foreach_file",
                "file": fname,
                "steps": [{"type": "wait", "seconds": 0}],
            }
            runner._step_foreach_file(step)
    finally:
        os.unlink(fname)

    assert len(visited) == 2


def test_foreach_file_stops_when_runner_stopped():
    runner = make_runner()
    runner._running = True

    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "data.jsonl"
        records = [{"n": i} for i in range(10)]
        write_jsonl(p, records)

        visited = []

        def fake_run_step_list(steps, ctx):
            visited.extend(steps)
            runner._running = False  # Stop after first row

        with patch.object(runner, "_run_step_list", side_effect=fake_run_step_list):
            step = {
                "type": "foreach_file",
                "file": str(p),
                "steps": [{"type": "wait", "seconds": 0}],
            }
            runner._step_foreach_file(step)

    # Should stop after processing first row
    assert len(visited) == 1


def test_foreach_file_item_whole_dict_substitution():
    runner = make_runner()
    runner._running = True

    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "data.jsonl"
        records = [{"key": "val"}]
        write_jsonl(p, records)

        received = []

        def fake_run_step_list(steps, ctx):
            received.extend(steps)

        with patch.object(runner, "_run_step_list", side_effect=fake_run_step_list):
            step = {
                "type": "foreach_file",
                "file": str(p),
                "steps": [{"type": "think", "context": "$item"}],
            }
            runner._step_foreach_file(step)

    assert len(received) == 1
    assert received[0]["context"] == {"key": "val"}

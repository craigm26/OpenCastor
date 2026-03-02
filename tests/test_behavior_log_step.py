"""Tests for BehaviorRunner log_step step (#383)."""

import json
import os
from unittest.mock import MagicMock

import pytest

from castor.behaviors import BehaviorRunner


def _make_runner():
    driver = MagicMock()
    runner = BehaviorRunner(driver=driver, config={})
    runner._running = True
    return runner


# ── dispatch table ─────────────────────────────────────────────────────────────

def test_log_step_in_dispatch_table():
    runner = _make_runner()
    assert "log_step" in runner._step_handlers


def test_log_step_handler_callable():
    runner = _make_runner()
    assert callable(runner._step_handlers["log_step"])


# ── missing path ──────────────────────────────────────────────────────────────

def test_log_step_no_path_skips(caplog):
    import logging
    runner = _make_runner()
    with caplog.at_level(logging.WARNING):
        runner._step_log_step({})
    assert any("path" in r.message.lower() for r in caplog.records)


# ── basic file writing ────────────────────────────────────────────────────────

def test_log_step_creates_file(tmp_path):
    runner = _make_runner()
    path = str(tmp_path / "behavior.log")
    runner._step_log_step({"path": path, "data": {"event": "start"}})
    assert os.path.exists(path)


def test_log_step_writes_valid_json(tmp_path):
    runner = _make_runner()
    path = str(tmp_path / "behavior.log")
    runner._step_log_step({"path": path, "data": {"event": "start"}})
    with open(path) as fh:
        record = json.loads(fh.readline())
    assert isinstance(record, dict)


def test_log_step_record_has_ts(tmp_path):
    runner = _make_runner()
    path = str(tmp_path / "behavior.log")
    runner._step_log_step({"path": path})
    with open(path) as fh:
        record = json.loads(fh.readline())
    assert "ts" in record
    assert isinstance(record["ts"], float)


def test_log_step_record_has_step_key(tmp_path):
    runner = _make_runner()
    path = str(tmp_path / "behavior.log")
    runner._step_log_step({"path": path, "label": "my_label"})
    with open(path) as fh:
        record = json.loads(fh.readline())
    assert record["step"] == "my_label"


def test_log_step_default_step_label(tmp_path):
    runner = _make_runner()
    path = str(tmp_path / "behavior.log")
    runner._step_log_step({"path": path})
    with open(path) as fh:
        record = json.loads(fh.readline())
    assert "step" in record


def test_log_step_data_in_record(tmp_path):
    runner = _make_runner()
    path = str(tmp_path / "behavior.log")
    runner._step_log_step({"path": path, "data": {"robot": "alex", "status": "ok"}})
    with open(path) as fh:
        record = json.loads(fh.readline())
    assert record["data"]["robot"] == "alex"
    assert record["data"]["status"] == "ok"


# ── $var substitution ────────────────────────────────────────────────────────

def test_log_step_var_substitution(tmp_path):
    runner = _make_runner()
    runner._vars["speed"] = "fast"
    path = str(tmp_path / "behavior.log")
    runner._step_log_step({"path": path, "data": {"val": "$var.speed"}})
    with open(path) as fh:
        record = json.loads(fh.readline())
    assert record["data"]["val"] == "fast"


def test_log_step_var_substitution_missing_var_uses_placeholder(tmp_path):
    runner = _make_runner()
    path = str(tmp_path / "behavior.log")
    runner._step_log_step({"path": path, "data": {"val": "$var.missing_xyz"}})
    with open(path) as fh:
        record = json.loads(fh.readline())
    # keeps the original string when var not found
    assert record["data"]["val"] == "$var.missing_xyz"


def test_log_step_non_var_values_unchanged(tmp_path):
    runner = _make_runner()
    path = str(tmp_path / "behavior.log")
    runner._step_log_step({"path": path, "data": {"count": 42, "flag": True}})
    with open(path) as fh:
        record = json.loads(fh.readline())
    assert record["data"]["count"] == 42
    assert record["data"]["flag"] is True


# ── appending ─────────────────────────────────────────────────────────────────

def test_log_step_appends_multiple_records(tmp_path):
    runner = _make_runner()
    path = str(tmp_path / "behavior.log")
    runner._step_log_step({"path": path, "data": {"n": 1}})
    runner._step_log_step({"path": path, "data": {"n": 2}})
    runner._step_log_step({"path": path, "data": {"n": 3}})
    with open(path) as fh:
        lines = [line for line in fh if line.strip()]
    assert len(lines) == 3


# ── bad path ──────────────────────────────────────────────────────────────────

def test_log_step_bad_path_logs_warning(caplog):
    import logging
    runner = _make_runner()
    with caplog.at_level(logging.WARNING):
        runner._step_log_step({"path": "/nonexistent_xyz/behavior.log"})
    assert any("log_step" in r.message.lower() for r in caplog.records)


def test_log_step_bad_path_does_not_raise():
    runner = _make_runner()
    try:
        runner._step_log_step({"path": "/nonexistent_xyz/behavior.log"})
    except Exception as exc:
        pytest.fail(f"log_step raised: {exc}")

"""
tests/test_behaviors.py — Unit tests for castor/behaviors.py (issue #121).

Covers:
  - YAML loading / validation
  - Each step type via mocks
  - Unknown step type skipped gracefully
  - stop() terminates the run loop
  - API endpoint contract (POST /api/behavior/run and /api/behavior/stop)
"""

from __future__ import annotations

import io
import textwrap
import threading
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_driver():
    d = MagicMock()
    d.stop = MagicMock()
    d.move = MagicMock()
    return d


@pytest.fixture()
def mock_brain():
    b = MagicMock()
    thought = MagicMock()
    thought.raw_text = "I see a wall ahead."
    thought.action = {"type": "stop"}
    b.think.return_value = thought
    return b


@pytest.fixture()
def mock_speaker():
    s = MagicMock()
    s.enabled = True
    s.say = MagicMock()
    return s


@pytest.fixture()
def runner(mock_driver, mock_brain, mock_speaker):
    from castor.behaviors import BehaviorRunner
    return BehaviorRunner(
        driver=mock_driver,
        brain=mock_brain,
        speaker=mock_speaker,
        config={},
    )


def _write_yaml(tmp_path, name, steps):
    """Helper: write a behavior YAML file and return its path."""
    data = {"name": name, "steps": steps}
    p = tmp_path / f"{name}.behavior.yaml"
    p.write_text(yaml.dump(data))
    return str(p)


# ---------------------------------------------------------------------------
# Loading tests
# ---------------------------------------------------------------------------


def test_load_valid_behavior(runner, tmp_path):
    """Valid YAML with name + steps returns a complete behavior dict."""
    path = _write_yaml(tmp_path, "patrol", [{"type": "wait", "seconds": 1}])
    behavior = runner.load(path)
    assert behavior["name"] == "patrol"
    assert len(behavior["steps"]) == 1


def test_load_missing_steps(runner, tmp_path):
    """A YAML file without 'steps' raises ValueError."""
    p = tmp_path / "bad.behavior.yaml"
    p.write_text(yaml.dump({"name": "bad"}))
    with pytest.raises(ValueError, match="steps"):
        runner.load(str(p))


def test_load_missing_name(runner, tmp_path):
    """A YAML file without 'name' raises ValueError."""
    p = tmp_path / "noname.behavior.yaml"
    p.write_text(yaml.dump({"steps": []}))
    with pytest.raises(ValueError, match="name"):
        runner.load(str(p))


def test_load_invalid_yaml(runner, tmp_path):
    """A file with invalid YAML raises yaml.YAMLError (or subclass)."""
    p = tmp_path / "broken.behavior.yaml"
    p.write_text(": this: is: not: valid\n  yaml: [}")
    with pytest.raises(Exception):   # yaml.YAMLError or ValueError
        runner.load(str(p))


def test_load_missing_file(runner):
    """Loading a non-existent path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        runner.load("/tmp/does_not_exist_at_all.behavior.yaml")


# ---------------------------------------------------------------------------
# Step execution tests
# ---------------------------------------------------------------------------


def test_run_wait_step(runner, tmp_path):
    """wait step calls time.sleep with the correct duration."""
    path = _write_yaml(tmp_path, "waiting", [{"type": "wait", "seconds": 3.5}])
    behavior = runner.load(path)
    with patch("castor.behaviors.time.sleep") as mock_sleep:
        runner.run(behavior)
    mock_sleep.assert_any_call(3.5)


def test_run_speak_step(runner, mock_speaker, tmp_path):
    """speak step calls speaker.say() with the correct text."""
    path = _write_yaml(
        tmp_path, "talker", [{"type": "speak", "text": "Hello robot world"}]
    )
    behavior = runner.load(path)
    with patch("castor.behaviors.time.sleep"):  # in case stop step also sleeps
        runner.run(behavior)
    mock_speaker.say.assert_called_once_with("Hello robot world")


def test_run_think_step(runner, mock_brain, tmp_path):
    """think step calls brain.think() with the provided instruction."""
    path = _write_yaml(
        tmp_path,
        "thinker",
        [{"type": "think", "instruction": "Describe what you see"}],
    )
    behavior = runner.load(path)
    runner.run(behavior)
    mock_brain.think.assert_called_once_with(b"", "Describe what you see")


def test_run_command_alias_calls_think(runner, mock_brain, tmp_path):
    """command is an alias for think and calls brain.think()."""
    path = _write_yaml(
        tmp_path,
        "cmd_alias",
        [{"type": "command", "instruction": "Turn left"}],
    )
    behavior = runner.load(path)
    runner.run(behavior)
    mock_brain.think.assert_called_once_with(b"", "Turn left")


def test_run_stop_step(runner, mock_driver, tmp_path):
    """stop step calls driver.stop()."""
    path = _write_yaml(tmp_path, "stopper", [{"type": "stop"}])
    behavior = runner.load(path)
    runner.run(behavior)
    # driver.stop() is called at least once (by step handler + finally block)
    assert mock_driver.stop.call_count >= 1


def test_run_unknown_step_skipped(runner, tmp_path, caplog):
    """Unknown step type logs a warning and does not raise."""
    path = _write_yaml(
        tmp_path,
        "mystery",
        [{"type": "teleport", "destination": "moon"}],
    )
    behavior = runner.load(path)
    import logging
    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner.run(behavior)   # must not raise
    assert any("teleport" in r.message for r in caplog.records)


def test_run_sets_running_false_after_completion(runner, tmp_path):
    """is_running is False after run() completes normally."""
    path = _write_yaml(tmp_path, "simple", [{"type": "wait", "seconds": 0.01}])
    behavior = runner.load(path)
    with patch("castor.behaviors.time.sleep"):
        runner.run(behavior)
    assert runner.is_running is False


# ---------------------------------------------------------------------------
# stop() test
# ---------------------------------------------------------------------------


def test_stop_sets_running_false(mock_driver, mock_brain, mock_speaker):
    """Calling stop() from another thread terminates the run loop mid-way."""
    from castor.behaviors import BehaviorRunner

    runner = BehaviorRunner(driver=mock_driver, brain=mock_brain, speaker=mock_speaker)
    # Build a behavior with many wait steps
    behavior = {
        "name": "long_patrol",
        "steps": [{"type": "wait", "seconds": 0.05}] * 20,
    }

    completed_steps: list[int] = []
    original_sleep = time.sleep

    def counting_sleep(s):
        completed_steps.append(1)
        original_sleep(0.01)  # actually sleep a tiny bit

    # Start run in a thread, stop() it after a short delay
    t = threading.Thread(target=runner.run, args=(behavior,), daemon=True)
    with patch("castor.behaviors.time.sleep", side_effect=counting_sleep):
        t.start()
        # Let one or two steps run
        time.sleep(0.05)
        runner.stop()
        t.join(timeout=2.0)

    assert runner.is_running is False
    assert len(completed_steps) < 20, "stop() should have cut the loop short"


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


def test_api_behavior_run_returns_job_id(tmp_path):
    """POST /api/behavior/run returns a job_id and name."""
    import importlib
    import sys

    # Build a real (minimal) FastAPI test client
    from fastapi.testclient import TestClient

    from castor.api import app, state
    from castor.behaviors import BehaviorRunner

    # Provide a mock runner on state so it doesn't need a real driver
    mock_runner = MagicMock(spec=BehaviorRunner)
    mock_runner.is_running = False
    mock_runner.load.return_value = {"name": "test_patrol", "steps": []}
    mock_runner.run = MagicMock()
    state.behavior_runner = mock_runner

    client = TestClient(app, raise_server_exceptions=False)

    # Write a dummy behavior file
    beh_file = tmp_path / "test.behavior.yaml"
    beh_file.write_text(yaml.dump({"name": "test_patrol", "steps": []}))

    with patch.object(mock_runner, "load", return_value={"name": "test_patrol", "steps": []}):
        resp = client.post(
            "/api/behavior/run",
            json={"path": str(beh_file)},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "job_id" in data
    assert data["name"] == "test_patrol"

    # Cleanup
    state.behavior_runner = None
    state.behavior_job = None


def test_api_behavior_stop():
    """POST /api/behavior/stop returns {'stopped': true}."""
    from fastapi.testclient import TestClient

    from castor.api import app, state
    from castor.behaviors import BehaviorRunner

    mock_runner = MagicMock(spec=BehaviorRunner)
    mock_runner.stop = MagicMock()
    state.behavior_runner = mock_runner
    state.behavior_job = {"job_id": "abc", "name": "x", "running": True}

    client = TestClient(app)
    resp = client.post("/api/behavior/stop")

    assert resp.status_code == 200
    assert resp.json().get("stopped") is True

    # Cleanup
    state.behavior_runner = None
    state.behavior_job = None

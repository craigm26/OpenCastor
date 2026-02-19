"""Tests for castor.demo — full-stack pipeline demo."""

from __future__ import annotations

import asyncio

import pytest


# ---------------------------------------------------------------------------
# Helper: import demo with NO_COLOR so rich is bypassed in CI
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _force_no_color(monkeypatch):
    """Disable rich for all demo tests so output is plain and predictable."""
    monkeypatch.setenv("NO_COLOR", "1")
    # Also patch the module-level flag so already-imported module is affected
    import castor.demo as demo_mod
    monkeypatch.setattr(demo_mod, "_RICH", False, raising=False)
    monkeypatch.setattr(demo_mod, "_console", None, raising=False)
    monkeypatch.setattr(demo_mod, "_NO_COLOR", True, raising=False)


# ---------------------------------------------------------------------------
# test_run_demo_completes
# ---------------------------------------------------------------------------

def test_run_demo_completes():
    """run_demo(steps=2, delay=0) must complete without raising."""
    from castor.demo import run_demo

    # Should not raise
    run_demo(steps=2, delay=0, layout="full", no_color=True)


# ---------------------------------------------------------------------------
# test_run_demo_minimal
# ---------------------------------------------------------------------------

def test_run_demo_minimal():
    """run_demo with layout='minimal' must complete (skips Acts 3 & 4)."""
    from castor.demo import run_demo

    run_demo(steps=2, delay=0, layout="minimal", no_color=True)


# ---------------------------------------------------------------------------
# test_run_demo_returns_summary
# ---------------------------------------------------------------------------

def test_run_demo_returns_summary():
    """run_demo must return a dict with the expected summary keys."""
    from castor.demo import run_demo

    result = run_demo(steps=3, delay=0, layout="full", no_color=True)

    assert isinstance(result, dict), "run_demo must return a dict"
    expected_keys = {
        "tick_count",
        "move_count",
        "stop_count",
        "obstacles_avoided",
        "tasks_completed",
        "patches_applied",
        "elapsed_s",
    }
    missing = expected_keys - result.keys()
    assert not missing, f"Summary dict missing keys: {missing}"

    assert result["tick_count"] == 3
    assert result["move_count"] + result["stop_count"] == 3
    assert isinstance(result["elapsed_s"], float)
    assert result["elapsed_s"] >= 0


# ---------------------------------------------------------------------------
# test_mock_sensor_data_generation
# ---------------------------------------------------------------------------

def test_mock_sensor_data_generation():
    """_generate_mock_sensor_data must return a dict with required keys."""
    from castor.demo import _generate_mock_sensor_data

    data = _generate_mock_sensor_data(tick=1)

    assert isinstance(data, dict)
    required_keys = {"hailo_detections", "frame_shape", "frame_size_kb", "timestamp", "tick"}
    missing = required_keys - data.keys()
    assert not missing, f"Sensor data dict missing keys: {missing}"

    assert isinstance(data["hailo_detections"], list)
    assert len(data["hailo_detections"]) >= 1

    for det in data["hailo_detections"]:
        assert "label" in det
        assert "confidence" in det
        assert "bbox" in det
        assert len(det["bbox"]) == 4
        assert 0.0 <= det["confidence"] <= 1.0

    assert data["frame_shape"] == (480, 640)
    assert isinstance(data["frame_size_kb"], int)
    assert data["tick"] == 1


# ---------------------------------------------------------------------------
# test_observer_processes_demo_data
# ---------------------------------------------------------------------------

def test_observer_processes_demo_data():
    """ObserverAgent.observe() must process demo sensor data without errors."""
    from castor.agents.observer import ObserverAgent, SceneGraph
    from castor.agents.shared_state import SharedState
    from castor.demo import _generate_mock_sensor_data

    state = SharedState()
    observer = ObserverAgent(shared_state=state)

    sensor_pkg = _generate_mock_sensor_data(tick=1)

    scene = asyncio.run(observer.observe(sensor_pkg))

    assert isinstance(scene, SceneGraph)
    assert scene.timestamp > 0
    assert isinstance(scene.detections, list)
    assert 0.0 <= scene.free_space_pct <= 1.0
    # Scene should be published to shared state
    published = state.get("scene_graph")
    assert published is not None
    assert published is scene

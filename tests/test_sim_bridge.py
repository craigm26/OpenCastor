"""
tests/test_sim_bridge.py — Unit tests for castor/sim_bridge.py

Covers:
  - SimBridge: export() for json, mjcf, sdf, gym formats
  - export() returns {path, format, episode_count, size_bytes}
  - generate_sim_config() returns XML/SDF strings
  - import_trajectory() round-trip for JSON format
  - supported_formats() excludes hdf5 when HAS_H5PY=False
  - Unsupported format raises ValueError
  - Singleton factory (get_bridge)
  - HDF5 export falls back to JSON when HAS_H5PY=False
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Module-level fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_sim_singleton():
    """Reset singleton between tests to avoid cross-test contamination."""
    import castor.sim_bridge as sb_mod
    sb_mod._singleton = None
    yield
    sb_mod._singleton = None


@pytest.fixture()
def sim_dir(tmp_path):
    """Provide a tmp sim dir and patch module-level _SIM_DIR for the test duration."""
    d = tmp_path / "sim"
    d.mkdir()
    with patch("castor.sim_bridge._SIM_DIR", str(d)):
        yield str(d)


@pytest.fixture()
def bridge(sim_dir):
    """A fresh SimBridge writing into the patched tmp sim dir."""
    from castor.sim_bridge import SimBridge

    return SimBridge()


@pytest.fixture()
def episodes():
    return [
        {
            "instruction": "go forward",
            "observation": {"speed": 0.3, "heading": 0.0},
            "action": {"type": "move", "linear": 0.3, "angular": 0.0},
            "reward": 1.0,
            "done": False,
            "metadata": {"step": 1},
        },
        {
            "instruction": "turn left",
            "observation": {"speed": 0.0, "heading": 90.0},
            "action": {"type": "move", "linear": 0.0, "angular": 0.5},
            "reward": 0.5,
            "done": True,
            "metadata": {"step": 2},
        },
    ]


# ---------------------------------------------------------------------------
# export() — JSON format
# ---------------------------------------------------------------------------


def test_export_json_returns_dict(bridge, episodes, sim_dir):
    result = bridge.export(episodes, fmt="json")
    assert isinstance(result, dict)
    assert "path" in result
    assert "format" in result
    assert "episode_count" in result
    assert "size_bytes" in result


def test_export_json_episode_count(bridge, episodes, sim_dir):
    result = bridge.export(episodes, fmt="json")
    assert result["episode_count"] == len(episodes)


def test_export_json_file_exists(bridge, episodes, sim_dir):
    result = bridge.export(episodes, fmt="json")
    assert os.path.exists(result["path"])


def test_export_json_size_bytes_positive(bridge, episodes, sim_dir):
    result = bridge.export(episodes, fmt="json")
    assert result["size_bytes"] > 0


def test_export_json_file_content(bridge, episodes, sim_dir):
    """Exported JSON must be parseable and contain episode data."""
    result = bridge.export(episodes, fmt="json")
    with open(result["path"]) as f:
        data = json.load(f)
    assert data["episode_count"] == len(episodes)
    assert data["format"] == "opencastor_episodes_v1"


# ---------------------------------------------------------------------------
# export() — MJCF format
# ---------------------------------------------------------------------------


def test_export_mjcf_creates_xml(bridge, episodes, sim_dir):
    result = bridge.export(episodes, fmt="mjcf", robot_name="test_bot")
    assert result["path"].endswith(".xml")
    assert os.path.exists(result["path"])


def test_export_mjcf_xml_contains_robot_name(bridge, episodes, sim_dir):
    result = bridge.export(episodes, fmt="mjcf", robot_name="myrobot")
    with open(result["path"]) as f:
        xml = f.read()
    assert "myrobot" in xml


def test_export_mjcf_trajectory_sidecar(bridge, episodes, sim_dir):
    """MJCF export must also write a _trajectory.json sidecar."""
    result = bridge.export(episodes, fmt="mjcf", robot_name="sidecar_bot")
    traj_path = result["path"].replace(".xml", "_trajectory.json")
    assert os.path.exists(traj_path)


# ---------------------------------------------------------------------------
# export() — SDF format
# ---------------------------------------------------------------------------


def test_export_sdf_creates_file(bridge, episodes, sim_dir):
    result = bridge.export(episodes, fmt="sdf", robot_name="gazebo_bot")
    assert os.path.exists(result["path"])


def test_export_sdf_contains_robot_name(bridge, episodes, sim_dir):
    result = bridge.export(episodes, fmt="sdf", robot_name="gazebo_bot")
    with open(result["path"]) as f:
        sdf = f.read()
    assert "gazebo_bot" in sdf


# ---------------------------------------------------------------------------
# export() — Gym format
# ---------------------------------------------------------------------------


def test_export_gym_creates_file(bridge, episodes, sim_dir):
    result = bridge.export(episodes, fmt="gym")
    assert os.path.exists(result["path"])


def test_export_gym_file_is_list(bridge, episodes, sim_dir):
    result = bridge.export(episodes, fmt="gym")
    with open(result["path"]) as f:
        data = json.load(f)
    assert isinstance(data, list)
    assert len(data) == len(episodes)


# ---------------------------------------------------------------------------
# export() — HDF5 fallback to JSON when HAS_H5PY=False
# ---------------------------------------------------------------------------


def test_export_hdf5_fallback_no_h5py(episodes, sim_dir):
    """When HAS_H5PY=False, hdf5 export must fall back to a .json file."""
    with patch("castor.sim_bridge.HAS_H5PY", False):
        from castor.sim_bridge import SimBridge

        bridge = SimBridge()
        result = bridge.export(episodes, fmt="hdf5")
    json_path = result["path"].replace(".hdf5", ".json")
    assert os.path.exists(json_path)


# ---------------------------------------------------------------------------
# Unsupported format raises ValueError
# ---------------------------------------------------------------------------


def test_export_unsupported_format_raises(bridge, episodes, sim_dir):
    with pytest.raises(ValueError, match="Unsupported format"):
        bridge.export(episodes, fmt="parquet")


# ---------------------------------------------------------------------------
# generate_sim_config()
# ---------------------------------------------------------------------------


def test_generate_sim_config_mujoco(bridge, sim_dir):
    rcan = {"metadata": {"robot_name": "mujoco_bot"}}
    xml = bridge.generate_sim_config(rcan, sim="mujoco")
    assert "<mujoco" in xml
    assert "mujoco_bot" in xml


def test_generate_sim_config_gazebo(bridge, sim_dir):
    rcan = {"metadata": {"robot_name": "gazebo_bot"}}
    sdf = bridge.generate_sim_config(rcan, sim="gazebo")
    assert "<sdf" in sdf
    assert "gazebo_bot" in sdf


def test_generate_sim_config_ros2_alias(bridge, sim_dir):
    rcan = {"metadata": {"robot_name": "ros2_bot"}}
    sdf = bridge.generate_sim_config(rcan, sim="ros2")
    assert "<sdf" in sdf


def test_generate_sim_config_unknown_sim_raises(bridge, sim_dir):
    with pytest.raises(ValueError, match="Unknown sim"):
        bridge.generate_sim_config({}, sim="webots_v2")


def test_generate_sim_config_default_robot_name(bridge, sim_dir):
    """Missing metadata.robot_name falls back to 'opencastor_robot'."""
    xml = bridge.generate_sim_config({}, sim="mujoco")
    assert "opencastor_robot" in xml


# ---------------------------------------------------------------------------
# import_trajectory() — JSON round-trip
# ---------------------------------------------------------------------------


def test_import_trajectory_json_roundtrip(bridge, episodes, sim_dir):
    payload = json.dumps(episodes).encode()
    result = bridge.import_trajectory(payload, fmt="json")
    assert isinstance(result, list)
    assert len(result) == len(episodes)
    assert result[0]["instruction"] == episodes[0]["instruction"]


def test_import_trajectory_unsupported_format_raises(sim_dir):
    """import_trajectory() with unsupported format and no h5py raises ValueError."""
    with patch("castor.sim_bridge.HAS_H5PY", False):
        from castor.sim_bridge import SimBridge

        bridge = SimBridge()
        with pytest.raises(ValueError, match="Cannot import format"):
            bridge.import_trajectory(b"{}", fmt="hdf5")


# ---------------------------------------------------------------------------
# supported_formats()
# ---------------------------------------------------------------------------


def test_supported_formats_excludes_hdf5_without_h5py(sim_dir):
    with patch("castor.sim_bridge.HAS_H5PY", False):
        from castor.sim_bridge import SimBridge

        bridge = SimBridge()
        fmts = bridge.supported_formats()
    assert "hdf5" not in fmts


def test_supported_formats_includes_json(bridge, sim_dir):
    fmts = bridge.supported_formats()
    assert "json" in fmts


def test_supported_formats_includes_mjcf_sdf_gym(bridge, sim_dir):
    fmts = bridge.supported_formats()
    for fmt in ("mjcf", "sdf", "gym"):
        assert fmt in fmts


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------


def test_get_bridge_singleton(sim_dir):
    """get_bridge() must return the same instance on repeated calls."""
    import castor.sim_bridge as sb_mod

    b1 = sb_mod.get_bridge()
    b2 = sb_mod.get_bridge()
    assert b1 is b2


def test_get_bridge_creates_sim_dir(tmp_path):
    """get_bridge() should create the sim directory if it does not exist."""
    import castor.sim_bridge as sb_mod

    sim_dir = tmp_path / "new_sim_dir"
    assert not sim_dir.exists()
    with patch("castor.sim_bridge._SIM_DIR", str(sim_dir)):
        sb_mod.get_bridge()
    assert sim_dir.exists()

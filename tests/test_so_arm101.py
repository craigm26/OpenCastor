"""Tests for SO-ARM101 auto-setup module."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from castor.hardware.so_arm101.constants import (
    FOLLOWER_ASSEMBLY_STEPS,
    FOLLOWER_MOTORS,
    LEADER_MOTORS,
)
from castor.hardware.so_arm101.config_generator import generate_config
from castor.hardware.so_arm101.assembly_guide import assembly_steps_json


# ── constants ─────────────────────────────────────────────────────────────────

def test_follower_motors_count():
    assert len(FOLLOWER_MOTORS) == 6


def test_leader_motors_count():
    assert len(LEADER_MOTORS) == 6


def test_motor_ids_sequential():
    for i, m in enumerate(FOLLOWER_MOTORS, start=1):
        assert m["id"] == i, f"Expected ID {i}, got {m['id']}"


def test_leader_gear_ratios():
    """Leader arm has mixed gear ratios for backdrivability."""
    gears = [m["gear"] for m in LEADER_MOTORS]
    # Not all identical (unlike follower)
    assert len(set(gears)) > 1


def test_follower_uniform_gear():
    """Follower uses 1/345 throughout."""
    gears = [m["gear"] for m in FOLLOWER_MOTORS]
    assert all(g == "1/345" for g in gears)


def test_assembly_steps_ordered():
    for i, step in enumerate(FOLLOWER_ASSEMBLY_STEPS):
        assert step.step == i


def test_assembly_steps_have_descriptions():
    for step in FOLLOWER_ASSEMBLY_STEPS:
        assert step.description, f"Step {step.step} has no description"


# ── config generator ──────────────────────────────────────────────────────────

def test_generate_config_single_arm():
    yaml = generate_config(follower_port="/dev/ttyACM0")
    assert "follower_arm" in yaml
    assert "feetech" in yaml
    assert "/dev/ttyACM0" in yaml
    assert "rcan_version" in yaml
    assert "1.4" in yaml


def test_generate_config_bimanual():
    yaml = generate_config(follower_port="/dev/ttyACM0", leader_port="/dev/ttyACM1")
    assert "follower_arm" in yaml
    assert "leader_arm" in yaml
    assert "/dev/ttyACM1" in yaml


def test_generate_config_safety_limits():
    yaml = generate_config()
    assert "joint_limits" in yaml
    assert "shoulder_pan" in yaml
    assert "gripper" in yaml


def test_generate_config_custom_name():
    yaml = generate_config(robot_name="my_arm_001")
    assert "my_arm_001" in yaml


def test_generate_config_rrn():
    yaml = generate_config(rrn="RRN-000000000010")
    assert "RRN-000000000010" in yaml


# ── assembly guide ────────────────────────────────────────────────────────────

def test_assembly_steps_json():
    steps = assembly_steps_json()
    assert len(steps) == len(FOLLOWER_ASSEMBLY_STEPS)
    for s in steps:
        assert "step" in s
        assert "title" in s
        assert "description" in s


def test_assembly_guide_runs(capsys):
    """Verify the CLI guide runs without error in dry mode."""
    from castor.hardware.so_arm101.assembly_guide import run_assembly_guide

    inputs = iter([""] * 20 + ["q"])
    run_assembly_guide(print_fn=lambda *a: None, input_fn=lambda *a: next(inputs))


# ── port finder (no hardware) ─────────────────────────────────────────────────

def test_detect_feetech_ports_no_crash():
    """Should return a list (possibly empty) even without hardware."""
    from castor.hardware.so_arm101.port_finder import detect_feetech_ports
    result = detect_feetech_ports()
    assert isinstance(result, list)


def test_list_serial_ports_no_crash():
    from castor.hardware.so_arm101.port_finder import list_serial_ports
    result = list_serial_ports()
    assert isinstance(result, list)


# ── motor setup (dry run) ─────────────────────────────────────────────────────

def test_motor_setup_dry_run():
    from castor.hardware.so_arm101.motor_setup import setup_motors

    results = setup_motors(
        port="/dev/null",
        arm="follower",
        dry_run=True,
        print_fn=lambda *a: None,
        input_fn=lambda *a: "",
    )
    assert len(results) == 6
    assert all(results.values())


def test_motor_setup_leader_dry_run():
    from castor.hardware.so_arm101.motor_setup import setup_motors

    results = setup_motors(
        port="/dev/null",
        arm="leader",
        dry_run=True,
        print_fn=lambda *a: None,
        input_fn=lambda *a: "",
    )
    assert len(results) == 6


# ── CLI ───────────────────────────────────────────────────────────────────────

def test_arm_cli_help():
    from castor.hardware.so_arm101.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0


def test_arm_cli_detect_no_crash(capsys):
    from castor.hardware.so_arm101.cli import cmd_detect
    import argparse

    cmd_detect(argparse.Namespace())


def test_arm_cli_config_dry(tmp_path):
    from castor.hardware.so_arm101.cli import cmd_config
    import argparse

    out = str(tmp_path / "test.rcan.yaml")
    cmd_config(argparse.Namespace(
        name="test_arm",
        out=out,
        follower_port="/dev/ttyACM0",
        leader_port=None,
    ))
    content = Path(out).read_text()
    assert "test_arm" in content
    assert "feetech" in content


# ── lerobot bridge ────────────────────────────────────────────────────────────

def test_lerobot_bridge_status():
    from castor.hardware.so_arm101.lerobot_bridge import status

    st = status()
    assert "available" in st
    assert "venv" in st
    assert "tools" in st
    # Tools dict has expected keys
    for key in ["lerobot-find-port", "lerobot-setup-motors", "lerobot-calibrate"]:
        assert key in st["tools"]


def test_lerobot_bridge_find_bin_no_crash():
    from castor.hardware.so_arm101.lerobot_bridge import find_lerobot_bin

    result = find_lerobot_bin("lerobot-find-port")
    # May be None (no LeRobot installed in CI) — just must not raise
    assert result is None or result.exists()


def test_motor_setup_dry_run_uses_native_when_no_lerobot():
    """Dry-run should not require LeRobot."""
    from castor.hardware.so_arm101.motor_setup import setup_motors

    results = setup_motors(
        port="/dev/null",
        arm="follower",
        dry_run=True,
        prefer_lerobot=False,
        print_fn=lambda *a: None,
        input_fn=lambda *a: "",
    )
    assert len(results) == 6


def test_arm_cli_calibrate_no_lerobot(capsys):
    """calibrate should print install instructions when LeRobot not available."""
    import argparse
    from unittest.mock import patch
    from castor.hardware.so_arm101.cli import cmd_calibrate

    with patch("castor.hardware.so_arm101.lerobot_bridge.lerobot_available", return_value=False):
        cmd_calibrate(argparse.Namespace(arm="follower", port=None))

    captured = capsys.readouterr()
    assert "lerobot-calibrate not found" in captured.out or "LeRobot" in captured.out


def test_arm_cli_status_no_crash(capsys):
    import argparse
    from castor.hardware.so_arm101.cli import cmd_status

    cmd_status(argparse.Namespace())

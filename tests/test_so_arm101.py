"""Tests for SO-ARM101 auto-setup module."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from castor.hardware.so_arm101.assembly_guide import assembly_steps_json
from castor.hardware.so_arm101.config_generator import generate_config
from castor.hardware.so_arm101.constants import (
    FOLLOWER_ASSEMBLY_STEPS,
    FOLLOWER_MOTORS,
    LEADER_MOTORS,
)

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
    import argparse

    from castor.hardware.so_arm101.cli import cmd_detect

    cmd_detect(argparse.Namespace())


def test_arm_cli_config_dry(tmp_path):
    import argparse

    from castor.hardware.so_arm101.cli import cmd_config

    out = str(tmp_path / "test.rcan.yaml")
    cmd_config(
        argparse.Namespace(
            name="test_arm",
            out=out,
            follower_port="/dev/ttyACM0",
            leader_port=None,
        )
    )
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

    from castor.hardware.so_arm101.cli import cmd_calibrate

    with patch("castor.hardware.so_arm101.lerobot_bridge.lerobot_available", return_value=False):
        cmd_calibrate(argparse.Namespace(arm="follower", port=None))

    captured = capsys.readouterr()
    assert "lerobot-calibrate not found" in captured.out or "LeRobot" in captured.out


def test_arm_cli_status_no_crash(capsys):
    import argparse

    from castor.hardware.so_arm101.cli import cmd_status

    cmd_status(argparse.Namespace())


# ── record / grasp (new in #658) ──────────────────────────────────────────────


def test_record_no_lerobot(capsys):
    """cmd_record should return 1 and print a helpful message when LeRobot is absent."""
    import argparse
    from unittest.mock import MagicMock

    from castor.hardware.so_arm101.cli import cmd_record

    mock_bridge = MagicMock()
    mock_bridge.available = False

    with patch(
        "castor.hardware.so_arm101.lerobot_bridge.LeRobotBridge",
        return_value=mock_bridge,
    ):
        rc = cmd_record(
            argparse.Namespace(
                port=None,
                leader_port=None,
                dataset=None,
                episodes=10,
                push=False,
            )
        )

    assert rc == 1
    captured = capsys.readouterr()
    assert "LeRobot not available" in captured.out


def test_record_with_lerobot(capsys):
    """cmd_record should invoke lerobot-record via subprocess when LeRobot is present."""
    import argparse
    from unittest.mock import MagicMock

    from castor.hardware.so_arm101.cli import cmd_record

    mock_bridge = MagicMock()
    mock_bridge.available = True
    mock_bridge._prefix_cmd.side_effect = lambda cmd: cmd  # pass-through

    mock_result = MagicMock()
    mock_result.returncode = 0

    with (
        patch(
            "castor.hardware.so_arm101.lerobot_bridge.LeRobotBridge",
            return_value=mock_bridge,
        ),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        rc = cmd_record(
            argparse.Namespace(
                port="/dev/ttyACM0",
                leader_port="/dev/ttyACM1",
                dataset="local/my_demo",
                episodes=5,
                push=False,
            )
        )

    assert rc == 0
    call_args = mock_run.call_args[0][0]  # first positional arg (the cmd list)
    assert call_args[0] == "lerobot-record"
    assert "--robot.type=so101_follower" in call_args
    assert "--robot.port=/dev/ttyACM0" in call_args
    assert "--dataset.num_episodes=5" in call_args


def test_grasp_hook_no_hailo(capsys):
    """cmd_grasp should print a helpful message when hailo_vision is not available."""
    import argparse

    from castor.hardware.so_arm101.cli import cmd_grasp

    # Patch both the importlib spec lookup and the local file existence check
    with (
        patch("importlib.util.find_spec", return_value=None),
        patch("os.path.exists", return_value=False),
    ):
        rc = cmd_grasp(argparse.Namespace())

    assert rc == 1
    captured = capsys.readouterr()
    assert "Hailo not available" in captured.out


# ── safety_bridge ─────────────────────────────────────────────────────────────


class TestSafetyBridge:
    """Tests for castor.hardware.so_arm101.safety_bridge.write_arm_command."""

    def _make_safety_layer(self):
        """Create a minimal SafetyLayer suitable for testing."""
        from castor.fs.namespace import Namespace
        from castor.fs.permissions import PermissionTable
        from castor.fs.safety import SafetyLayer

        ns = Namespace()
        perms = PermissionTable()
        sl = SafetyLayer(ns, perms, limits={"motor_rate_hz": 100.0})
        # Ensure /dev/arm exists so writes don't fail on missing parent
        try:
            ns.mkdir("/dev/arm")
        except Exception:
            pass
        return sl

    def test_safety_bridge_calls_safety_layer(self):
        """write_arm_command delegates to safety_layer.write() with /dev/arm/<joint>."""
        from unittest.mock import MagicMock

        from castor.hardware.so_arm101.safety_bridge import write_arm_command

        mock_sl = MagicMock()
        mock_sl.is_estopped = False
        mock_sl.write.return_value = True

        result = write_arm_command(mock_sl, "shoulder_pan", position=0.5, velocity=0.1)

        assert result is True
        mock_sl.write.assert_called_once()
        call_args = mock_sl.write.call_args
        path = call_args[0][0] if call_args[0] else call_args[1].get("path")
        assert path == "/dev/arm/shoulder_pan"

    def test_safety_bridge_blocked_by_estop(self):
        """write_arm_command returns False when SafetyLayer is estopped."""
        from castor.hardware.so_arm101.safety_bridge import write_arm_command

        sl = self._make_safety_layer()
        sl.estop(principal="root")
        assert sl.is_estopped

        result = write_arm_command(sl, "shoulder_pan", position=0.5)
        assert result is False

    def test_safety_bridge_no_safety_layer(self):
        """write_arm_command with safety_layer=None must not crash (legacy path)."""
        from castor.hardware.so_arm101.safety_bridge import write_arm_command

        # Should return True and not raise
        result = write_arm_command(None, "elbow_flex", position=1.0)
        assert result is True


# ── vision.py (camera ROI) ────────────────────────────────────────────────────


def test_get_camera_frame_roi_no_camera():
    """Returns None gracefully when cv2 is unavailable."""
    from unittest.mock import patch

    # Hide cv2 so the import fails
    with patch.dict(sys.modules, {"cv2": None}):
        from castor.hardware.so_arm101.vision import get_camera_frame_roi

        result = get_camera_frame_roi("/dev/video_nonexistent")
    assert result is None


def test_get_camera_frame_roi_returns_none_on_bad_device():
    """Returns None when cv2 is present but the device cannot be opened."""
    try:
        import cv2  # noqa: F401
    except ImportError:
        import pytest

        pytest.skip("cv2 not installed")

    from unittest.mock import MagicMock

    from castor.hardware.so_arm101.vision import get_camera_frame_roi

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = False

    with patch("cv2.VideoCapture", return_value=mock_cap):
        result = get_camera_frame_roi("/dev/video99")

    assert result is None


# ── rcan_bridge.py ────────────────────────────────────────────────────────────


def test_send_arm_pose_rcan_no_config():
    """Returns True (graceful) even without rcan.yaml and without yaml installed."""
    from castor.hardware.so_arm101.rcan_bridge import send_arm_pose_rcan

    # Ensure yaml is importable (it usually is); rcan config file won't exist
    result = send_arm_pose_rcan(
        {"shoulder_pan": 0.0},
        rcan_config_path="/nonexistent/path/bob.rcan.yaml",
    )
    assert result is True


def test_send_arm_pose_rcan_builds_message():
    """Verify the built message has message_type=1 and action='arm_pose'."""
    import json

    from castor.hardware.so_arm101.rcan_bridge import send_arm_pose_rcan

    captured_msgs = []

    # Patch logger.info on the already-imported module to capture log output
    with patch("castor.hardware.so_arm101.rcan_bridge.logger") as mock_logger:
        mock_logger.info.side_effect = lambda fmt, msg: captured_msgs.append(msg)

        result = send_arm_pose_rcan(
            {"shoulder_pan": 0.5, "elbow_flex": 1.0},
            rcan_config_path="/nonexistent/bob.rcan.yaml",  # no file → default ruri
        )

    assert result is True
    assert len(captured_msgs) >= 1
    raw = captured_msgs[0]
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    assert parsed.get("message_type") == 1
    assert parsed.get("action") == "arm_pose"

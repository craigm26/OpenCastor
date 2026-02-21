"""Tests for castor.daemon — systemd service management."""

import os
import sys
import textwrap
from unittest.mock import MagicMock, patch

import pytest

from castor.daemon import (
    SERVICE_NAME,
    daemon_status,
    generate_service_file,
)


class TestGenerateServiceFile:
    def test_basic_output(self):
        content = generate_service_file(
            config_path="/home/pi/opencastor/bob.rcan.yaml",
            user="pi",
            venv_path="/home/pi/opencastor/venv",
            working_dir="/home/pi/opencastor",
        )
        assert "[Unit]" in content
        assert "[Service]" in content
        assert "[Install]" in content
        assert "bob.rcan.yaml" in content
        assert "User=pi" in content
        assert "WantedBy=multi-user.target" in content

    def test_restart_policy(self):
        content = generate_service_file(
            "/tmp/robot.rcan.yaml", user="robot"
        )
        assert "Restart=on-failure" in content
        assert "RestartSec=5s" in content

    def test_network_dependency(self):
        content = generate_service_file("/tmp/robot.rcan.yaml")
        assert "After=network-online.target" in content
        assert "Wants=network-online.target" in content

    def test_journald_output(self):
        content = generate_service_file("/tmp/robot.rcan.yaml")
        assert "StandardOutput=journal" in content
        assert "StandardError=journal" in content

    def test_unbuffered_env(self):
        content = generate_service_file("/tmp/robot.rcan.yaml")
        assert "PYTHONUNBUFFERED=1" in content

    def test_defaults_to_current_user(self):
        content = generate_service_file("/tmp/robot.rcan.yaml")
        current_user = os.environ.get("USER", "pi")
        assert f"User={current_user}" in content

    def test_castor_bin_in_venv(self):
        content = generate_service_file(
            "/tmp/robot.rcan.yaml",
            venv_path="/custom/venv",
        )
        assert "/custom/venv/bin/castor" in content

    def test_memory_limit(self):
        content = generate_service_file("/tmp/robot.rcan.yaml")
        assert "MemoryMax=" in content


class TestDaemonStatus:
    def test_no_systemctl(self):
        with patch("shutil.which", return_value=None):
            status = daemon_status()
        assert status["available"] is False

    def test_parses_running_state(self):
        fake_output = (
            "ActiveState=active\n"
            "SubState=running\n"
            "MainPID=1234\n"
            "ExecMainStartTimestamp=Fri 2026-02-20 10:00:00 PST\n"
        )
        fake_enabled = MagicMock(stdout=b"enabled\n", returncode=0)
        fake_show = MagicMock(stdout=fake_output.encode(), returncode=0)

        with (
            patch("shutil.which", return_value="/usr/bin/systemctl"),
            patch("castor.daemon.SERVICE_PATH") as mock_path,
            patch("castor.daemon._run", side_effect=[fake_show, fake_enabled]),
        ):
            mock_path.exists.return_value = True
            status = daemon_status()

        assert status["available"] is True
        assert status["running"] is True
        assert status["pid"] == "1234"
        assert status["enabled"] is True

    def test_stopped_state(self):
        fake_output = "ActiveState=inactive\nSubState=dead\nMainPID=0\nExecMainStartTimestamp=\n"
        fake_enabled = MagicMock(stdout=b"disabled\n", returncode=0)
        fake_show = MagicMock(stdout=fake_output.encode(), returncode=0)

        with (
            patch("shutil.which", return_value="/usr/bin/systemctl"),
            patch("castor.daemon.SERVICE_PATH") as mock_path,
            patch("castor.daemon._run", side_effect=[fake_show, fake_enabled]),
        ):
            mock_path.exists.return_value = False
            status = daemon_status()

        assert status["running"] is False
        assert status["enabled"] is False
        assert status["installed"] is False

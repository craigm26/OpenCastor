"""Tests for castor.daemon — systemd service management."""

import os
import textwrap
from unittest.mock import MagicMock, patch

from castor.daemon import (
    daemon_security_status,
    daemon_status,
    generate_driver_worker_units,
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
        content = generate_service_file("/tmp/robot.rcan.yaml", user="robot")
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

    def test_hardened_profile_enabled_by_default(self, tmp_path):
        config_path = tmp_path / "robot.rcan.yaml"
        config_path.write_text("robot: {}\n", encoding="utf-8")

        content = generate_service_file(str(config_path))

        assert "NoNewPrivileges=true" in content
        assert "ProtectSystem=strict" in content
        assert "DevicePolicy=closed" in content
        assert "AppArmorProfile=opencastor-gateway" in content
        assert "SystemCallFilter=" in content

    def test_permissive_profile_from_config(self, tmp_path):
        config_path = tmp_path / "robot.rcan.yaml"
        config_path.write_text("service:\n  security_profile: permissive\n", encoding="utf-8")

        content = generate_service_file(str(config_path))

        assert "NoNewPrivileges=true" not in content
        assert "DevicePolicy=closed" not in content
        assert "AppArmorProfile=opencastor-gateway" not in content


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


def test_generate_driver_worker_units(tmp_path):
    cfg = tmp_path / "robot.rcan.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            drivers:
              - id: base
                protocol: pca9685_rc
                port: /dev/i2c-1
              - id: scan
                protocol: lidar
                port: /dev/ttyUSB2
            """
        ),
        encoding="utf-8",
    )

    units = generate_driver_worker_units(str(cfg))
    assert "castor-driver@base.service" in units
    base = units["castor-driver@base.service"]
    assert "User=castor-drv-base" in base
    assert "DevicePolicy=closed" in base
    assert "DeviceAllow=/dev/i2c-1 rw" in base

    scan = units["castor-driver@scan.service"]
    assert "DeviceAllow=/dev/ttyUSB2 rw" in scan
    assert "PrivateNetwork=true" in scan


class TestDaemonSecurityStatus:
    def test_reports_unit_configuration_without_pid(self, tmp_path):
        service_file = tmp_path / "castor-gateway.service"
        service_file.write_text(
            "AppArmorProfile=opencastor-gateway\nSystemCallFilter=@system-service\n",
            encoding="utf-8",
        )

        with (
            patch("castor.daemon.SECURITY_INSTALL_PATH", tmp_path),
            patch("castor.daemon.SERVICE_PATH", service_file),
            patch("castor.daemon.daemon_status", return_value={"pid": None}),
        ):
            status = daemon_security_status()

        assert status["profiles_installed"] is True
        assert status["enabled_in_unit"] is True
        assert status["seccomp_mode"] is None
        assert status["apparmor_profile"] is None

"""Tests for castor register --dry-run (Issue #496).

Ensures dry-run mode validates config and prints summary without making API calls.
"""

from __future__ import annotations

import io
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch


def _make_register_args(**kwargs):
    """Build a SimpleNamespace args object for cmd_register."""
    defaults = {
        "config": "robot.rcan.yaml",
        "dry_run": True,
        "api_key": None,
        "manufacturer": None,
        "model": None,
        "version": None,
        "device_id": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _mock_yaml_config(
    name="TestBot",
    manufacturer="Acme",
    model="Explorer",
    version="v2",
    rcan_version="1.2",
):
    return {
        "metadata": {
            "robot_name": name,
            "manufacturer": manufacturer,
            "model": model,
            "version": version,
        },
        "rcan_version": rcan_version,
    }


def _import_cmd_register():
    from castor.cli import cmd_register

    return cmd_register


class TestRegisterDryRun:
    def _run_dry_run(self, config=None, extra_args=None):
        """Run cmd_register with --dry-run and return captured stdout."""
        cmd_register = _import_cmd_register()
        args = _make_register_args(**(extra_args or {}))
        cfg = config or _mock_yaml_config()

        import yaml

        yaml_text = yaml.dump(cfg)

        with (
            patch("builtins.open", mock_open(read_data=yaml_text)),
            patch("castor.rcan.sdk_compat.validate_before_register", return_value=(True, [])),
            patch("sys.stdout", new_callable=io.StringIO) as mock_out,
        ):
            try:
                cmd_register(args)
            except SystemExit:
                pass
            return mock_out.getvalue()

    def test_dry_run_does_not_call_registry_client(self):
        """API calls must NOT happen during dry run."""
        cmd_register = _import_cmd_register()
        args = _make_register_args(dry_run=True)
        cfg = _mock_yaml_config()

        import yaml

        yaml_text = yaml.dump(cfg)

        registry_mock = MagicMock()
        with (
            patch("builtins.open", mock_open(read_data=yaml_text)),
            patch("castor.rcan.sdk_compat.validate_before_register", return_value=(True, [])),
            patch.dict(sys.modules, {"rcan": MagicMock(), "rcan.registry": registry_mock}),
        ):
            try:
                cmd_register(args)
            except SystemExit:
                pass

        # RegistryClient should never have been instantiated
        registry_mock.RegistryClient.assert_not_called()

    def test_dry_run_prints_robot_name(self, capsys):
        cmd_register = _import_cmd_register()
        args = _make_register_args(dry_run=True)
        cfg = _mock_yaml_config(name="Robby", manufacturer="AcmeCorp", model="HX-500")

        import yaml

        yaml_text = yaml.dump(cfg)

        with (
            patch("builtins.open", mock_open(read_data=yaml_text)),
            patch("castor.rcan.sdk_compat.validate_before_register", return_value=(True, [])),
        ):
            try:
                cmd_register(args)
            except SystemExit:
                pass

        captured = capsys.readouterr()
        assert "Dry run" in captured.out
        assert "AcmeCorp" in captured.out or "HX-500" in captured.out

    def test_dry_run_prints_registry_url(self, capsys):
        cmd_register = _import_cmd_register()
        args = _make_register_args(dry_run=True)
        cfg = _mock_yaml_config()

        import yaml

        yaml_text = yaml.dump(cfg)

        with (
            patch("builtins.open", mock_open(read_data=yaml_text)),
            patch("castor.rcan.sdk_compat.validate_before_register", return_value=(True, [])),
        ):
            try:
                cmd_register(args)
            except SystemExit:
                pass

        captured = capsys.readouterr()
        assert "robot-registry-foundation.pages.dev/v2/registry" in captured.out

    def test_dry_run_prints_complete_message(self, capsys):
        cmd_register = _import_cmd_register()
        args = _make_register_args(dry_run=True)
        cfg = _mock_yaml_config(rcan_version="1.2")

        import yaml

        yaml_text = yaml.dump(cfg)

        with (
            patch("builtins.open", mock_open(read_data=yaml_text)),
            patch("castor.rcan.sdk_compat.validate_before_register", return_value=(True, [])),
        ):
            try:
                cmd_register(args)
            except SystemExit:
                pass

        captured = capsys.readouterr()
        assert "Dry run complete" in captured.out
        assert "no API calls" in captured.out

    def test_dry_run_shows_validation_warnings(self, capsys):
        """Validation warnings should appear in stderr during dry run."""
        cmd_register = _import_cmd_register()
        args = _make_register_args(dry_run=True)
        cfg = _mock_yaml_config()

        import yaml

        yaml_text = yaml.dump(cfg)

        with (
            patch("builtins.open", mock_open(read_data=yaml_text)),
            patch(
                "castor.rcan.sdk_compat.validate_before_register",
                return_value=(True, ["Missing field: firmware_version"]),
            ),
        ):
            try:
                cmd_register(args)
            except SystemExit:
                pass

        captured = capsys.readouterr()
        assert "Missing field" in captured.err

    def test_dry_run_config_not_found(self, capsys):
        """Missing config file should exit with error, not crash."""
        cmd_register = _import_cmd_register()
        args = _make_register_args(dry_run=True, config="nonexistent.rcan.yaml")

        with patch("builtins.open", side_effect=FileNotFoundError):
            try:
                cmd_register(args)
                exited = False
            except SystemExit:
                exited = True

        # Should have handled it gracefully (printed error or exited)
        assert exited

    def test_dry_run_rcan_version_shown(self, capsys):
        """RCAN version from config should appear in dry-run output."""
        cmd_register = _import_cmd_register()
        args = _make_register_args(dry_run=True)
        cfg = _mock_yaml_config(rcan_version="2.0")

        import yaml

        yaml_text = yaml.dump(cfg)

        with (
            patch("builtins.open", mock_open(read_data=yaml_text)),
            patch("castor.rcan.sdk_compat.validate_before_register", return_value=(True, [])),
        ):
            try:
                cmd_register(args)
            except SystemExit:
                pass

        captured = capsys.readouterr()
        assert "2.0" in captured.out


class TestRegisterNormalPath:
    """Ensure dry_run=False still hits the normal registration path."""

    def test_normal_register_proceeds_past_dryrun_gate(self, capsys):
        """When dry_run is False, we should NOT see the dry-run output."""
        cmd_register = _import_cmd_register()
        args = _make_register_args(dry_run=False, api_key=None)
        cfg = _mock_yaml_config()

        import yaml

        yaml_text = yaml.dump(cfg)

        # We don't want to actually call the registry — just check that
        # the dry-run block is NOT triggered.  Interrupt after validation.
        with (
            patch("builtins.open", mock_open(read_data=yaml_text)),
            patch(
                "castor.rcan.sdk_compat.validate_before_register",
                return_value=(True, []),
            ),
            patch("builtins.input", return_value=""),  # no api key → open browser path
            patch("webbrowser.open"),
        ):
            try:
                cmd_register(args)
            except (SystemExit, Exception):
                pass

        captured = capsys.readouterr()
        assert "Dry run complete" not in captured.out

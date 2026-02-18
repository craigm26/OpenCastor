"""Tests for castor.cli -- unified CLI entry point and command handlers."""

import argparse
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from castor.cli import (
    _friendly_error_handler,
    cmd_approvals,
    cmd_audit,
    cmd_backup,
    cmd_benchmark,
    cmd_calibrate,
    cmd_configure,
    cmd_dashboard,
    cmd_demo,
    cmd_diff,
    cmd_discover,
    cmd_doctor,
    cmd_export,
    cmd_fix,
    cmd_fleet,
    cmd_gateway,
    cmd_install_service,
    cmd_learn,
    cmd_lint,
    cmd_logs,
    cmd_migrate,
    cmd_network,
    cmd_plugins,
    cmd_privacy,
    cmd_profile,
    cmd_quickstart,
    cmd_repl,
    cmd_replay,
    cmd_restore,
    cmd_run,
    cmd_schedule,
    cmd_search,
    cmd_shell,
    cmd_status,
    cmd_test,
    cmd_test_hardware,
    cmd_token,
    cmd_update_check,
    cmd_upgrade,
    cmd_watch,
    cmd_wizard,
    main,
)

# =====================================================================
# Helpers
# =====================================================================

ALL_COMMANDS = [
    "run",
    "gateway",
    "wizard",
    "dashboard",
    "token",
    "discover",
    "doctor",
    "demo",
    "test-hardware",
    "calibrate",
    "logs",
    "backup",
    "restore",
    "migrate",
    "upgrade",
    "install-service",
    "status",
    "shell",
    "watch",
    "fix",
    "repl",
    "record",
    "replay",
    "benchmark",
    "lint",
    "learn",
    "fleet",
    "export",
    "approvals",
    "schedule",
    "configure",
    "search",
    "network",
    "privacy",
    "update-check",
    "profile",
    "test",
    "diff",
    "quickstart",
    "plugins",
    "audit",
]


def _make_args(**kwargs):
    """Build an argparse.Namespace with the given keyword arguments."""
    return argparse.Namespace(**kwargs)


def _run_main_with_plugins_mocked(*argv):
    """Call main() with sys.argv patched and the plugin import mocked.

    The plugin loading inside main() uses a local ``from castor.plugins
    import load_plugins`` which is not a module-level attribute.  We mock
    it via ``sys.modules`` so the import resolves to a controlled object.
    """
    mock_registry = MagicMock()
    mock_registry.commands = {}
    mock_plugins_mod = MagicMock()
    mock_plugins_mod.load_plugins.return_value = mock_registry
    with patch("sys.argv", list(argv)):
        with patch.dict("sys.modules", {"castor.plugins": mock_plugins_mod}):
            main()


# =====================================================================
# Parser: command recognition -- all 41 commands dispatched
# =====================================================================
class TestParserCommandRecognition:
    """Verify that all 41 commands are dispatched by main()."""

    # Note: "schedule" is excluded because Python 3.13 argparse has a known
    # issue where subparsers with a positional ``nargs="?"`` argument fail to
    # set the ``dest="command"`` attribute.  The cmd_schedule handler is still
    # tested directly in TestCmdSchedule.
    _DISPATCHABLE = [c for c in ALL_COMMANDS if c != "schedule"]

    @pytest.mark.parametrize("cmd", _DISPATCHABLE)
    def test_main_dispatches_command(self, cmd):
        """main() should dispatch to the correct handler for each command."""
        argv_map = {
            "restore": ["castor", cmd, "archive.tar.gz"],
            "search": ["castor", cmd, "some query"],
            "replay": ["castor", cmd, "session.jsonl"],
            "diff": ["castor", cmd, "--baseline", "base.yaml"],
        }
        test_argv = argv_map.get(cmd, ["castor", cmd])

        handler_name = "cmd_" + cmd.replace("-", "_")
        target = f"castor.cli.{handler_name}"

        mock_registry = MagicMock()
        mock_registry.commands = {}
        mock_plugins_mod = MagicMock()
        mock_plugins_mod.load_plugins.return_value = mock_registry

        with patch("sys.argv", test_argv):
            with patch.dict("sys.modules", {"castor.plugins": mock_plugins_mod}):
                with patch(target) as mock_handler:
                    main()
                    mock_handler.assert_called_once()


# =====================================================================
# Parser: no-command shows help
# =====================================================================
class TestParserNoCommand:
    def test_no_command_prints_help(self, capsys):
        """When no command is given, main() prints help text."""
        _run_main_with_plugins_mocked("castor")
        out = capsys.readouterr().out
        assert "OpenCastor" in out

    def test_unknown_command_prints_help(self, capsys):
        """When an unrecognized command is given, argparse errors out."""
        with pytest.raises(SystemExit):
            _run_main_with_plugins_mocked("castor", "nonexistent_command")


# =====================================================================
# Parser: help epilog contains command groups
# =====================================================================
class TestHelpEpilog:
    def test_help_contains_command_groups(self, capsys):
        """The --help output should include the command group labels."""
        with pytest.raises(SystemExit) as exc_info:
            _run_main_with_plugins_mocked("castor", "--help")
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        for group in [
            "Setup:",
            "Run:",
            "Diagnostics:",
            "Hardware:",
            "Config:",
            "Safety:",
            "Network:",
            "Advanced:",
        ]:
            assert group in out


# =====================================================================
# Parser: argument parsing edge cases
# =====================================================================
class TestParserEdgeCases:
    def _dispatch(self, *argv):
        """Helper: call main() with argv, return the handler's args."""
        _run_main_with_plugins_mocked(*argv)

    def _dispatch_and_capture(self, handler_path, *argv):
        """Call main() and capture the args passed to the handler."""
        mock_registry = MagicMock()
        mock_registry.commands = {}
        mock_plugins_mod = MagicMock()
        mock_plugins_mod.load_plugins.return_value = mock_registry

        with patch("sys.argv", list(argv)):
            with patch.dict("sys.modules", {"castor.plugins": mock_plugins_mod}):
                with patch(handler_path) as mock_handler:
                    main()
        return mock_handler.call_args[0][0]

    def test_run_default_config(self):
        """'castor run' should default --config to robot.rcan.yaml."""
        args = self._dispatch_and_capture("castor.cli.cmd_run", "castor", "run")
        assert args.config == "robot.rcan.yaml"
        assert args.simulate is False

    def test_run_with_simulate(self):
        """'castor run --simulate' sets the flag."""
        args = self._dispatch_and_capture("castor.cli.cmd_run", "castor", "run", "--simulate")
        assert args.simulate is True

    def test_gateway_custom_host_port(self):
        """'castor gateway' should accept --host and --port."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_gateway",
            "castor",
            "gateway",
            "--host",
            "0.0.0.0",
            "--port",
            "9090",
        )
        assert args.host == "0.0.0.0"
        assert args.port == 9090

    def test_gateway_defaults(self):
        """'castor gateway' defaults host=127.0.0.1 port=8000."""
        args = self._dispatch_and_capture("castor.cli.cmd_gateway", "castor", "gateway")
        assert args.host == "127.0.0.1"
        assert args.port == 8000

    def test_demo_args(self):
        """'castor demo --steps 5 --delay 2.0' parses correctly."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_demo",
            "castor",
            "demo",
            "--steps",
            "5",
            "--delay",
            "2.0",
        )
        assert args.steps == 5
        assert args.delay == 2.0

    def test_demo_defaults(self):
        """'castor demo' defaults to steps=10 delay=1.5."""
        args = self._dispatch_and_capture("castor.cli.cmd_demo", "castor", "demo")
        assert args.steps == 10
        assert args.delay == 1.5

    def test_restore_requires_archive(self):
        """'castor restore' without the archive positional arg should error."""
        with pytest.raises(SystemExit):
            _run_main_with_plugins_mocked("castor", "restore")

    def test_search_requires_query(self):
        """'castor search' without the query positional arg should error."""
        with pytest.raises(SystemExit):
            _run_main_with_plugins_mocked("castor", "search")

    def test_replay_requires_recording(self):
        """'castor replay' without the recording positional arg should error."""
        with pytest.raises(SystemExit):
            _run_main_with_plugins_mocked("castor", "replay")

    def test_diff_requires_baseline(self):
        """'castor diff' without --baseline should error."""
        with pytest.raises(SystemExit):
            _run_main_with_plugins_mocked("castor", "diff")

    def test_logs_args(self):
        """Logs supports -f, --level, --module, -n, --no-color."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_logs",
            "castor",
            "logs",
            "-f",
            "--level",
            "ERROR",
            "--module",
            "Gateway",
            "-n",
            "100",
            "--no-color",
        )
        assert args.follow is True
        assert args.level == "ERROR"
        assert args.module == "Gateway"
        assert args.lines == 100
        assert args.no_color is True

    def test_token_args(self):
        """Token supports --role, --scope, --ttl, --subject."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_token",
            "castor",
            "token",
            "--role",
            "admin",
            "--scope",
            "status,control",
            "--ttl",
            "48",
            "--subject",
            "ci-bot",
        )
        assert args.role == "admin"
        assert args.scope == "status,control"
        assert args.ttl == "48"
        assert args.subject == "ci-bot"

    def test_wizard_flags(self):
        """Wizard supports --simple, --accept-risk, --web, --web-port."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_wizard",
            "castor",
            "wizard",
            "--simple",
            "--accept-risk",
            "--web",
            "--web-port",
            "9090",
        )
        assert args.simple is True
        assert args.accept_risk is True
        assert args.web is True
        assert args.web_port == 9090

    def test_benchmark_args(self):
        """Benchmark supports --iterations and --simulate."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_benchmark",
            "castor",
            "benchmark",
            "--iterations",
            "10",
            "--simulate",
        )
        assert args.iterations == 10
        assert args.simulate is True

    def test_audit_args(self):
        """Audit supports --since, --event, --limit."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_audit",
            "castor",
            "audit",
            "--since",
            "24h",
            "--event",
            "motor_command",
            "--limit",
            "100",
        )
        assert args.since == "24h"
        assert args.event == "motor_command"
        assert args.limit == 100

    def test_export_format_choices(self):
        """Export --format only accepts zip or json."""
        with pytest.raises(SystemExit):
            _run_main_with_plugins_mocked("castor", "export", "--format", "csv")

    def test_network_action_choices(self):
        """Network action only accepts status or expose."""
        with pytest.raises(SystemExit):
            _run_main_with_plugins_mocked("castor", "network", "delete")

    def test_schedule_action_choices(self):
        """Schedule action only accepts list, add, remove, install."""
        with pytest.raises(SystemExit):
            _run_main_with_plugins_mocked("castor", "schedule", "purge")

    def test_profile_action_choices(self):
        """Profile action only accepts list, save, use, remove."""
        with pytest.raises(SystemExit):
            _run_main_with_plugins_mocked("castor", "profile", "destroy")

    def test_test_hardware_yes_flag(self):
        """test-hardware accepts -y to skip confirmation."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_test_hardware",
            "castor",
            "test-hardware",
            "-y",
        )
        assert args.yes is True

    def test_discover_timeout(self):
        """Discover accepts --timeout."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_discover",
            "castor",
            "discover",
            "--timeout",
            "10",
        )
        assert args.timeout == "10"

    def test_watch_args(self):
        """Watch accepts --gateway and --refresh."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_watch",
            "castor",
            "watch",
            "--gateway",
            "http://192.168.1.100:8000",
            "--refresh",
            "5.0",
        )
        assert args.gateway == "http://192.168.1.100:8000"
        assert args.refresh == 5.0

    def test_search_all_args(self):
        """Search accepts query, --since, --log-file, --max-results."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_search",
            "castor",
            "search",
            "battery low",
            "--since",
            "7d",
            "--log-file",
            "/tmp/log",
            "--max-results",
            "50",
        )
        assert args.query == "battery low"
        assert args.since == "7d"
        assert args.log_file == "/tmp/log"
        assert args.max_results == 50

    def test_install_service_args(self):
        """install-service accepts --config, --host, --port."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_install_service",
            "castor",
            "install-service",
            "--config",
            "my.rcan.yaml",
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
        )
        assert args.config == "my.rcan.yaml"
        assert args.host == "0.0.0.0"
        assert args.port == 9000

    def test_learn_lesson(self):
        """Learn accepts --lesson."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_learn",
            "castor",
            "learn",
            "--lesson",
            "3",
        )
        assert args.lesson == 3

    def test_backup_output(self):
        """Backup accepts -o."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_backup",
            "castor",
            "backup",
            "-o",
            "/tmp/bk.tar.gz",
        )
        assert args.output == "/tmp/bk.tar.gz"

    def test_restore_dry_run(self):
        """Restore accepts --dry-run."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_restore",
            "castor",
            "restore",
            "backup.tar.gz",
            "--dry-run",
        )
        assert args.dry_run is True
        assert args.archive == "backup.tar.gz"

    def test_migrate_dry_run(self):
        """Migrate accepts --dry-run."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_migrate",
            "castor",
            "migrate",
            "--dry-run",
        )
        assert args.dry_run is True

    def test_upgrade_verbose(self):
        """Upgrade accepts -v."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_upgrade",
            "castor",
            "upgrade",
            "-v",
        )
        assert args.verbose is True

    def test_doctor_with_config(self):
        """Doctor accepts --config."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_doctor",
            "castor",
            "doctor",
            "--config",
            "test.rcan.yaml",
        )
        assert args.config == "test.rcan.yaml"

    def test_fleet_timeout(self):
        """Fleet accepts --timeout."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_fleet",
            "castor",
            "fleet",
            "--timeout",
            "15",
        )
        assert args.timeout == "15"

    def test_approvals_approve_flag(self):
        """Approvals accepts --approve."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_approvals",
            "castor",
            "approvals",
            "--approve",
            "5",
        )
        assert args.approve == "5"

    def test_privacy_config(self):
        """Privacy accepts --config."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_privacy",
            "castor",
            "privacy",
            "--config",
            "test.rcan.yaml",
        )
        assert args.config == "test.rcan.yaml"

    def test_replay_execute_and_config(self):
        """Replay accepts --execute and --config."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_replay",
            "castor",
            "replay",
            "session.jsonl",
            "--execute",
            "--config",
            "robot.rcan.yaml",
        )
        assert args.execute is True
        assert args.config == "robot.rcan.yaml"

    def test_record_output_and_simulate(self):
        """Record accepts -o and --simulate."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_record",
            "castor",
            "record",
            "-o",
            "my_session.jsonl",
            "--simulate",
        )
        assert args.output == "my_session.jsonl"
        assert args.simulate is True

    def test_test_verbose_and_keyword(self):
        """Test accepts -v and -k."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_test",
            "castor",
            "test",
            "-v",
            "-k",
            "test_auth",
        )
        assert args.verbose is True
        assert args.keyword == "test_auth"

    def test_diff_baseline(self):
        """Diff accepts --baseline (required) and --config."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_diff",
            "castor",
            "diff",
            "--config",
            "new.yaml",
            "--baseline",
            "old.yaml",
        )
        assert args.config == "new.yaml"
        assert args.baseline == "old.yaml"

    def test_network_expose_mode(self):
        """Network expose accepts --mode and --port."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_network",
            "castor",
            "network",
            "expose",
            "--mode",
            "funnel",
            "--port",
            "9000",
        )
        assert args.action == "expose"
        assert args.mode == "funnel"
        assert args.port == 9000

    def test_schedule_add_args(self):
        """Schedule add accepts --name, --command, --cron."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_schedule",
            "castor",
            "schedule",
            "add",
            "--name",
            "patrol",
            "--command",
            "castor run",
            "--cron",
            "*/30 * * * *",
        )
        assert args.action == "add"
        assert args.name == "patrol"
        assert args.task_command == "castor run"

    def test_profile_save_name(self):
        """Profile save accepts a positional name."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_profile",
            "castor",
            "profile",
            "save",
            "indoor",
        )
        assert args.action == "save"
        assert args.name == "indoor"

    def test_export_json_format(self):
        """Export accepts --format json."""
        args = self._dispatch_and_capture(
            "castor.cli.cmd_export",
            "castor",
            "export",
            "--format",
            "json",
        )
        assert args.format == "json"


# =====================================================================
# _friendly_error_handler
# =====================================================================
class TestFriendlyErrorHandler:
    def test_keyboard_interrupt(self, capsys):
        """KeyboardInterrupt should print 'Interrupted' and exit 130."""
        with patch("castor.cli.main", side_effect=KeyboardInterrupt):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 130
        out = capsys.readouterr().out
        assert "Interrupted" in out

    def test_system_exit_passthrough(self):
        """SystemExit should be re-raised unchanged."""
        with patch("castor.cli.main", side_effect=SystemExit(42)):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 42

    def test_file_not_found_rcan(self, capsys):
        """FileNotFoundError for .rcan.yaml should hint at wizard."""
        exc = FileNotFoundError("robot.rcan.yaml")
        exc.filename = "robot.rcan.yaml"
        with patch("castor.cli.main", side_effect=exc):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "File not found" in out
        assert "castor wizard" in out

    def test_file_not_found_env(self, capsys):
        """FileNotFoundError for .env should hint at cp .env.example."""
        exc = FileNotFoundError(".env")
        exc.filename = ".env"
        with patch("castor.cli.main", side_effect=exc):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "File not found" in out
        assert ".env.example" in out

    def test_file_not_found_generic(self, capsys):
        """FileNotFoundError for other files should give generic hint."""
        exc = FileNotFoundError("somefile.txt")
        exc.filename = "somefile.txt"
        with patch("castor.cli.main", side_effect=exc):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "File not found" in out
        assert "Check the path" in out

    def test_import_error_known_dep(self, capsys):
        """ImportError for dynamixel_sdk should suggest pip install."""
        exc = ImportError("No module named 'dynamixel_sdk'")
        exc.name = "dynamixel_sdk"
        with patch("castor.cli.main", side_effect=exc):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "Missing dependency" in out
        assert "dynamixel-sdk" in out

    def test_import_error_cv2(self, capsys):
        """ImportError for cv2 should suggest opencv-python-headless."""
        exc = ImportError("No module named 'cv2'")
        exc.name = "cv2"
        with patch("castor.cli.main", side_effect=exc):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "opencv-python-headless" in out

    def test_import_error_rich(self, capsys):
        """ImportError for rich should suggest pip install rich."""
        exc = ImportError("No module named 'rich'")
        exc.name = "rich"
        with patch("castor.cli.main", side_effect=exc):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "pip install rich" in out

    def test_import_error_yaml(self, capsys):
        """ImportError for yaml should suggest pip install pyyaml."""
        exc = ImportError("No module named 'yaml'")
        exc.name = "yaml"
        with patch("castor.cli.main", side_effect=exc):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "pyyaml" in out

    def test_import_error_fastapi(self, capsys):
        """ImportError for fastapi should suggest pip install fastapi uvicorn."""
        exc = ImportError("No module named 'fastapi'")
        exc.name = "fastapi"
        with patch("castor.cli.main", side_effect=exc):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "fastapi" in out

    def test_import_error_streamlit(self, capsys):
        """ImportError for streamlit should suggest pip install streamlit."""
        exc = ImportError("No module named 'streamlit'")
        exc.name = "streamlit"
        with patch("castor.cli.main", side_effect=exc):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "streamlit" in out

    def test_import_error_neonize(self, capsys):
        """ImportError for neonize should suggest opencastor[whatsapp]."""
        exc = ImportError("No module named 'neonize'")
        exc.name = "neonize"
        with patch("castor.cli.main", side_effect=exc):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "opencastor[whatsapp]" in out

    def test_import_error_telegram(self, capsys):
        """ImportError for telegram should suggest opencastor[telegram]."""
        exc = ImportError("No module named 'telegram'")
        exc.name = "telegram"
        with patch("castor.cli.main", side_effect=exc):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "opencastor[telegram]" in out

    def test_import_error_discord(self, capsys):
        """ImportError for discord should suggest opencastor[discord]."""
        exc = ImportError("No module named 'discord'")
        exc.name = "discord"
        with patch("castor.cli.main", side_effect=exc):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "opencastor[discord]" in out

    def test_import_error_slack_bolt(self, capsys):
        """ImportError for slack_bolt should suggest opencastor[slack]."""
        exc = ImportError("No module named 'slack_bolt'")
        exc.name = "slack_bolt"
        with patch("castor.cli.main", side_effect=exc):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "opencastor[slack]" in out

    def test_import_error_unknown_dep(self, capsys):
        """ImportError for unknown dep should suggest pip install -e '.[dev]'."""
        exc = ImportError("No module named 'foobar'")
        exc.name = "foobar"
        with patch("castor.cli.main", side_effect=exc):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "Missing dependency" in out
        assert "castor fix" in out

    def test_connection_error(self, capsys):
        """ConnectionError should hint about network."""
        with patch("castor.cli.main", side_effect=ConnectionError("refused")):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "Connection error" in out
        assert "castor network status" in out

    def test_generic_exception(self, capsys):
        """Unrecognized exceptions should suggest doctor and fix."""
        with patch("castor.cli.main", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                _friendly_error_handler()
            assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "Unexpected error" in out
        assert "castor doctor" in out
        assert "castor fix" in out

    def test_debug_mode_prints_traceback(self, capsys):
        """With LOG_LEVEL=DEBUG, generic exception should print traceback."""
        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}):
            with patch("castor.cli.main", side_effect=RuntimeError("boom")):
                with pytest.raises(SystemExit):
                    _friendly_error_handler()
        err = capsys.readouterr().err
        assert "Traceback" in err or "RuntimeError" in err

    def test_normal_execution(self):
        """If main() succeeds, no exception should bubble up."""
        with patch("castor.cli.main"):
            _friendly_error_handler()  # Should not raise


# =====================================================================
# cmd_run
# =====================================================================
class TestCmdRun:
    def test_config_exists_calls_main(self, tmp_path):
        """When config exists, cmd_run imports and calls castor.main.main."""
        config = tmp_path / "robot.rcan.yaml"
        config.write_text("rcan_version: 1.0\n")
        args = _make_args(config=str(config), simulate=False)
        mock_main_fn = MagicMock()
        with patch.dict("sys.modules", {"castor.main": MagicMock(main=mock_main_fn)}):
            cmd_run(args)
            mock_main_fn.assert_called_once()

    def test_config_exists_simulate(self, tmp_path):
        """When --simulate is set, sys.argv should include --simulate."""
        config = tmp_path / "robot.rcan.yaml"
        config.write_text("rcan_version: 1.0\n")
        args = _make_args(config=str(config), simulate=True)
        mock_main_fn = MagicMock()
        with patch.dict("sys.modules", {"castor.main": MagicMock(main=mock_main_fn)}):
            cmd_run(args)
        assert "--simulate" in sys.argv

    def test_config_missing_no_rcan_files_wizard_decline(self, tmp_path, capsys):
        """When config is missing and user declines wizard, print exit message."""
        args = _make_args(config=str(tmp_path / "nonexistent.rcan.yaml"), simulate=False)
        with patch("glob.glob", return_value=[]):
            with patch("builtins.input", return_value="n"):
                cmd_run(args)
        out = capsys.readouterr().out
        assert "castor wizard" in out

    def test_config_missing_suggests_existing_rcan(self, tmp_path, capsys):
        """When config is missing but other .rcan.yaml files exist, suggest them.

        Note: the current code prints the suggestion and then falls through to
        ``from castor.main import main`` which is expected to run the actual
        loop, so we mock the castor.main module to avoid side effects.
        """
        args = _make_args(config=str(tmp_path / "missing.rcan.yaml"), simulate=False)
        mock_main_fn = MagicMock()
        with patch("glob.glob", return_value=["other.rcan.yaml"]):
            with patch.dict("sys.modules", {"castor.main": MagicMock(main=mock_main_fn)}):
                cmd_run(args)
        out = capsys.readouterr().out
        assert "other.rcan.yaml" in out

    def test_config_missing_wizard_accept(self, tmp_path):
        """When config is missing and user accepts wizard, it runs the wizard."""
        args = _make_args(config=str(tmp_path / "nonexistent.rcan.yaml"), simulate=False)
        mock_wizard = MagicMock()
        with patch("glob.glob", return_value=[]):
            with patch("builtins.input", return_value="y"):
                with patch.dict("sys.modules", {"castor.wizard": MagicMock(main=mock_wizard)}):
                    cmd_run(args)
        mock_wizard.assert_called_once()

    def test_config_missing_eof_in_input(self, tmp_path, capsys):
        """When EOFError is raised in input, treat as decline."""
        args = _make_args(config=str(tmp_path / "nonexistent.rcan.yaml"), simulate=False)
        with patch("glob.glob", return_value=[]):
            with patch("builtins.input", side_effect=EOFError):
                cmd_run(args)
        out = capsys.readouterr().out
        assert "castor wizard" in out

    def test_config_missing_keyboard_interrupt_in_input(self, tmp_path, capsys):
        """When KeyboardInterrupt is raised in input, treat as decline."""
        args = _make_args(config=str(tmp_path / "nonexistent.rcan.yaml"), simulate=False)
        with patch("glob.glob", return_value=[]):
            with patch("builtins.input", side_effect=KeyboardInterrupt):
                cmd_run(args)
        out = capsys.readouterr().out
        assert "castor wizard" in out


# =====================================================================
# cmd_gateway
# =====================================================================
class TestCmdGateway:
    def test_calls_api_main(self):
        """cmd_gateway should import and call castor.api.main."""
        args = _make_args(config="robot.rcan.yaml", host="0.0.0.0", port=9090)
        mock_gateway = MagicMock()
        with patch.dict("sys.modules", {"castor.api": MagicMock(main=mock_gateway)}):
            cmd_gateway(args)
        mock_gateway.assert_called_once()
        assert "--host" in sys.argv
        assert "0.0.0.0" in sys.argv
        assert "--port" in sys.argv
        assert "9090" in sys.argv


# =====================================================================
# cmd_wizard
# =====================================================================
class TestCmdWizard:
    def test_basic_wizard_call(self):
        """cmd_wizard with no flags calls castor.wizard.main."""
        args = _make_args(simple=False, accept_risk=False, web=False, web_port=8080)
        mock_wizard = MagicMock()
        with patch.dict("sys.modules", {"castor.wizard": MagicMock(main=mock_wizard)}):
            cmd_wizard(args)
        mock_wizard.assert_called_once()

    def test_simple_flag(self):
        """--simple should be forwarded to wizard args."""
        args = _make_args(simple=True, accept_risk=False, web=False, web_port=8080)
        mock_wizard = MagicMock()
        with patch.dict("sys.modules", {"castor.wizard": MagicMock(main=mock_wizard)}):
            cmd_wizard(args)
        assert "--simple" in sys.argv

    def test_accept_risk_flag(self):
        """--accept-risk should be forwarded to wizard args."""
        args = _make_args(simple=False, accept_risk=True, web=False, web_port=8080)
        mock_wizard = MagicMock()
        with patch.dict("sys.modules", {"castor.wizard": MagicMock(main=mock_wizard)}):
            cmd_wizard(args)
        assert "--accept-risk" in sys.argv

    def test_web_wizard(self):
        """--web should call launch_web_wizard instead."""
        args = _make_args(simple=False, accept_risk=False, web=True, web_port=9090)
        mock_launch = MagicMock()
        mock_module = MagicMock(launch_web_wizard=mock_launch)
        with patch.dict("sys.modules", {"castor.web_wizard": mock_module}):
            cmd_wizard(args)
        mock_launch.assert_called_once_with(port=9090)

    def test_simple_and_accept_risk(self):
        """Both --simple and --accept-risk can be set together."""
        args = _make_args(simple=True, accept_risk=True, web=False, web_port=8080)
        mock_wizard = MagicMock()
        with patch.dict("sys.modules", {"castor.wizard": MagicMock(main=mock_wizard)}):
            cmd_wizard(args)
        assert "--simple" in sys.argv
        assert "--accept-risk" in sys.argv


# =====================================================================
# cmd_dashboard
# =====================================================================
class TestCmdDashboard:
    def test_calls_subprocess(self):
        """cmd_dashboard should call subprocess.run with streamlit."""
        args = _make_args()
        with patch("subprocess.run") as mock_run:
            cmd_dashboard(args)
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "streamlit" in call_args
        assert "castor/dashboard.py" in call_args


# =====================================================================
# cmd_doctor
# =====================================================================
class TestCmdDoctor:
    def test_calls_run_all_checks(self, capsys):
        """cmd_doctor should call run_all_checks and print_report."""
        args = _make_args(config=None)
        mock_run = MagicMock(return_value=[(True, "Test", "ok")])
        mock_print = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.doctor": MagicMock(
                    run_all_checks=mock_run,
                    print_report=mock_print,
                )
            },
        ):
            cmd_doctor(args)
        mock_run.assert_called_once_with(config_path=None)
        mock_print.assert_called_once()

    def test_prints_header(self, capsys):
        """cmd_doctor should print a header before results."""
        args = _make_args(config=None)
        mock_run = MagicMock(return_value=[])
        mock_print = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.doctor": MagicMock(
                    run_all_checks=mock_run,
                    print_report=mock_print,
                )
            },
        ):
            cmd_doctor(args)
        out = capsys.readouterr().out
        assert "Doctor" in out

    def test_with_config_path(self, capsys):
        """cmd_doctor passes config path to run_all_checks."""
        args = _make_args(config="robot.rcan.yaml")
        mock_run = MagicMock(return_value=[])
        mock_print = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.doctor": MagicMock(
                    run_all_checks=mock_run,
                    print_report=mock_print,
                )
            },
        ):
            cmd_doctor(args)
        mock_run.assert_called_once_with(config_path="robot.rcan.yaml")


# =====================================================================
# cmd_demo
# =====================================================================
class TestCmdDemo:
    def test_calls_run_demo(self):
        """cmd_demo should call run_demo with steps and delay."""
        args = _make_args(steps=5, delay=2.0)
        mock_demo = MagicMock()
        with patch.dict("sys.modules", {"castor.demo": MagicMock(run_demo=mock_demo)}):
            cmd_demo(args)
        mock_demo.assert_called_once_with(steps=5, delay=2.0)

    def test_default_args(self):
        """cmd_demo with default args."""
        args = _make_args(steps=10, delay=1.5)
        mock_demo = MagicMock()
        with patch.dict("sys.modules", {"castor.demo": MagicMock(run_demo=mock_demo)}):
            cmd_demo(args)
        mock_demo.assert_called_once_with(steps=10, delay=1.5)


# =====================================================================
# cmd_status
# =====================================================================
class TestCmdStatus:
    def test_prints_providers_and_channels(self, capsys):
        """cmd_status should print provider and channel status."""
        args = _make_args()
        mock_providers = MagicMock(return_value={"google": True, "openai": False})
        mock_channels = MagicMock(return_value={"telegram": True, "discord": False})
        mock_load = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.auth": MagicMock(
                    list_available_providers=mock_providers,
                    list_available_channels=mock_channels,
                    load_dotenv_if_available=mock_load,
                )
            },
        ):
            cmd_status(args)
        out = capsys.readouterr().out
        assert "AI Providers" in out
        assert "Messaging Channels" in out
        assert "google" in out
        assert "telegram" in out

    def test_ready_marker(self, capsys):
        """Ready providers should show [+], not-ready should show [-]."""
        args = _make_args()
        mock_providers = MagicMock(return_value={"google": True, "openai": False})
        mock_channels = MagicMock(return_value={})
        mock_load = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.auth": MagicMock(
                    list_available_providers=mock_providers,
                    list_available_channels=mock_channels,
                    load_dotenv_if_available=mock_load,
                )
            },
        ):
            cmd_status(args)
        out = capsys.readouterr().out
        assert "[+] google" in out
        assert "[-] openai" in out

    def test_calls_load_dotenv(self):
        """cmd_status should load .env before checking."""
        args = _make_args()
        mock_load = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.auth": MagicMock(
                    list_available_providers=MagicMock(return_value={}),
                    list_available_channels=MagicMock(return_value={}),
                    load_dotenv_if_available=mock_load,
                )
            },
        ):
            cmd_status(args)
        mock_load.assert_called_once()


# =====================================================================
# cmd_test_hardware
# =====================================================================
class TestCmdTestHardware:
    def test_config_missing(self, tmp_path, capsys):
        """When config is missing, print a message and return."""
        args = _make_args(config=str(tmp_path / "nope.rcan.yaml"), yes=False)
        cmd_test_hardware(args)
        out = capsys.readouterr().out
        assert "Config not found" in out

    def test_config_exists(self, tmp_path):
        """When config exists, call run_test."""
        config = tmp_path / "robot.rcan.yaml"
        config.write_text("rcan_version: 1.0\n")
        args = _make_args(config=str(config), yes=True)
        mock_run = MagicMock()
        with patch.dict("sys.modules", {"castor.test_hardware": MagicMock(run_test=mock_run)}):
            cmd_test_hardware(args)
        mock_run.assert_called_once_with(config_path=str(config), skip_confirm=True)

    def test_config_exists_no_skip(self, tmp_path):
        """When -y is not set, skip_confirm=False."""
        config = tmp_path / "robot.rcan.yaml"
        config.write_text("rcan_version: 1.0\n")
        args = _make_args(config=str(config), yes=False)
        mock_run = MagicMock()
        with patch.dict("sys.modules", {"castor.test_hardware": MagicMock(run_test=mock_run)}):
            cmd_test_hardware(args)
        mock_run.assert_called_once_with(config_path=str(config), skip_confirm=False)


# =====================================================================
# cmd_calibrate
# =====================================================================
class TestCmdCalibrate:
    def test_config_missing(self, tmp_path, capsys):
        args = _make_args(config=str(tmp_path / "nope.rcan.yaml"))
        cmd_calibrate(args)
        out = capsys.readouterr().out
        assert "Config not found" in out

    def test_config_exists(self, tmp_path):
        config = tmp_path / "robot.rcan.yaml"
        config.write_text("rcan_version: 1.0\n")
        args = _make_args(config=str(config))
        mock_cal = MagicMock()
        with patch.dict("sys.modules", {"castor.calibrate": MagicMock(run_calibration=mock_cal)}):
            cmd_calibrate(args)
        mock_cal.assert_called_once_with(config_path=str(config))


# =====================================================================
# cmd_logs
# =====================================================================
class TestCmdLogs:
    def test_calls_view_logs(self):
        """cmd_logs should call view_logs with the right arguments."""
        args = _make_args(follow=True, level="ERROR", module="Gateway", lines=100, no_color=True)
        mock_view = MagicMock()
        with patch.dict("sys.modules", {"castor.logs": MagicMock(view_logs=mock_view)}):
            cmd_logs(args)
        mock_view.assert_called_once_with(
            follow=True,
            level="ERROR",
            module="Gateway",
            lines=100,
            no_color=True,
        )

    def test_default_args(self):
        """cmd_logs with defaults."""
        args = _make_args(follow=False, level=None, module=None, lines=50, no_color=False)
        mock_view = MagicMock()
        with patch.dict("sys.modules", {"castor.logs": MagicMock(view_logs=mock_view)}):
            cmd_logs(args)
        mock_view.assert_called_once_with(
            follow=False,
            level=None,
            module=None,
            lines=50,
            no_color=False,
        )


# =====================================================================
# cmd_backup
# =====================================================================
class TestCmdBackup:
    def test_creates_backup_and_prints_summary(self, tmp_path):
        """cmd_backup should call create_backup and print_backup_summary."""
        args = _make_args(output=str(tmp_path / "backup.tar.gz"))
        mock_create = MagicMock(return_value=str(tmp_path / "backup.tar.gz"))
        mock_summary = MagicMock()

        mock_member = MagicMock()
        mock_member.name = "robot.rcan.yaml"
        mock_tar = MagicMock()
        mock_tar.__enter__ = MagicMock(return_value=mock_tar)
        mock_tar.__exit__ = MagicMock(return_value=False)
        mock_tar.getmembers.return_value = [mock_member]

        with patch.dict(
            "sys.modules",
            {
                "castor.backup": MagicMock(
                    create_backup=mock_create,
                    print_backup_summary=mock_summary,
                )
            },
        ):
            with patch("tarfile.open", return_value=mock_tar):
                cmd_backup(args)
        mock_create.assert_called_once()
        mock_summary.assert_called_once()

    def test_no_archive_produced(self):
        """When create_backup returns None, no summary is printed."""
        args = _make_args(output=None)
        mock_create = MagicMock(return_value=None)
        mock_summary = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.backup": MagicMock(
                    create_backup=mock_create,
                    print_backup_summary=mock_summary,
                )
            },
        ):
            cmd_backup(args)
        mock_summary.assert_not_called()


# =====================================================================
# cmd_restore
# =====================================================================
class TestCmdRestore:
    def test_dry_run(self):
        """--dry-run should call restore_backup(dry_run=True)."""
        args = _make_args(archive="backup.tar.gz", dry_run=True)
        mock_restore = MagicMock()
        mock_summary = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.backup": MagicMock(
                    restore_backup=mock_restore,
                    print_restore_summary=mock_summary,
                )
            },
        ):
            cmd_restore(args)
        mock_restore.assert_called_once_with("backup.tar.gz", dry_run=True)
        mock_summary.assert_not_called()

    def test_actual_restore(self):
        """Without --dry-run, restore and print summary."""
        args = _make_args(archive="backup.tar.gz", dry_run=False)
        mock_restore = MagicMock(return_value=["file1.yaml"])
        mock_summary = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.backup": MagicMock(
                    restore_backup=mock_restore,
                    print_restore_summary=mock_summary,
                )
            },
        ):
            cmd_restore(args)
        mock_restore.assert_called_once_with("backup.tar.gz")
        mock_summary.assert_called_once_with(["file1.yaml"])

    def test_actual_restore_no_files(self):
        """When restore returns empty/None, no summary is printed."""
        args = _make_args(archive="backup.tar.gz", dry_run=False)
        mock_restore = MagicMock(return_value=None)
        mock_summary = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.backup": MagicMock(
                    restore_backup=mock_restore,
                    print_restore_summary=mock_summary,
                )
            },
        ):
            cmd_restore(args)
        mock_summary.assert_not_called()


# =====================================================================
# cmd_migrate
# =====================================================================
class TestCmdMigrate:
    def test_calls_migrate_file(self):
        args = _make_args(config="robot.rcan.yaml", dry_run=False)
        mock_migrate = MagicMock()
        with patch.dict("sys.modules", {"castor.migrate": MagicMock(migrate_file=mock_migrate)}):
            cmd_migrate(args)
        mock_migrate.assert_called_once_with("robot.rcan.yaml", dry_run=False)

    def test_dry_run(self):
        args = _make_args(config="robot.rcan.yaml", dry_run=True)
        mock_migrate = MagicMock()
        with patch.dict("sys.modules", {"castor.migrate": MagicMock(migrate_file=mock_migrate)}):
            cmd_migrate(args)
        mock_migrate.assert_called_once_with("robot.rcan.yaml", dry_run=True)


# =====================================================================
# cmd_upgrade
# =====================================================================
class TestCmdUpgrade:
    def test_upgrade_success(self, capsys):
        """Successful pip upgrade should run doctor."""
        args = _make_args(verbose=False)
        mock_result = MagicMock(returncode=0)
        mock_run_checks = MagicMock(return_value=[])
        mock_print_report = MagicMock()
        with patch("subprocess.run", return_value=mock_result):
            with patch.dict(
                "sys.modules",
                {
                    "castor.doctor": MagicMock(
                        run_all_checks=mock_run_checks,
                        print_report=mock_print_report,
                    )
                },
            ):
                cmd_upgrade(args)
        out = capsys.readouterr().out
        assert "Upgrade complete" in out

    def test_upgrade_failure(self, capsys):
        """Failed pip upgrade should report failure."""
        args = _make_args(verbose=False)
        mock_result = MagicMock(returncode=1)
        with patch("subprocess.run", return_value=mock_result):
            cmd_upgrade(args)
        out = capsys.readouterr().out
        assert "Upgrade failed" in out
        assert "--verbose" in out

    def test_upgrade_failure_verbose(self, capsys):
        """Failed pip upgrade with --verbose should not suggest --verbose again."""
        args = _make_args(verbose=True)
        mock_result = MagicMock(returncode=1)
        with patch("subprocess.run", return_value=mock_result):
            cmd_upgrade(args)
        out = capsys.readouterr().out
        assert "Upgrade failed" in out


# =====================================================================
# cmd_install_service
# =====================================================================
class TestCmdInstallService:
    def test_generates_service_file(self, capsys, tmp_path):
        """cmd_install_service should write a systemd unit to /tmp/."""
        args = _make_args(config="robot.rcan.yaml", host="0.0.0.0", port=8080)
        with patch("getpass.getuser", return_value="testuser"):
            with patch("os.getcwd", return_value="/home/test"):
                with patch("os.path.abspath", return_value="/home/test/robot.rcan.yaml"):
                    with patch("os.path.exists", return_value=True):
                        cmd_install_service(args)
        out = capsys.readouterr().out
        assert "Service file written" in out
        assert "testuser" in out
        assert "systemctl" in out

    def test_contains_port(self, capsys):
        """The service file should reference the correct port."""
        args = _make_args(config="robot.rcan.yaml", host="127.0.0.1", port=9090)
        with patch("getpass.getuser", return_value="user"):
            with patch("os.getcwd", return_value="/tmp"):
                with patch("os.path.abspath", return_value="/tmp/robot.rcan.yaml"):
                    with patch("os.path.exists", return_value=True):
                        cmd_install_service(args)
        out = capsys.readouterr().out
        assert "9090" in out


# =====================================================================
# cmd_shell
# =====================================================================
class TestCmdShell:
    def test_config_missing(self, tmp_path, capsys):
        args = _make_args(config=str(tmp_path / "nope.rcan.yaml"))
        cmd_shell(args)
        out = capsys.readouterr().out
        assert "Config not found" in out

    def test_config_exists(self, tmp_path):
        config = tmp_path / "robot.rcan.yaml"
        config.write_text("rcan_version: 1.0\n")
        args = _make_args(config=str(config))
        mock_launch = MagicMock()
        with patch.dict("sys.modules", {"castor.shell": MagicMock(launch_shell=mock_launch)}):
            cmd_shell(args)
        mock_launch.assert_called_once_with(config_path=str(config))


# =====================================================================
# cmd_watch
# =====================================================================
class TestCmdWatch:
    def test_calls_launch_watch(self):
        args = _make_args(gateway="http://127.0.0.1:8000", refresh=2.0)
        mock_launch = MagicMock()
        with patch.dict("sys.modules", {"castor.watch": MagicMock(launch_watch=mock_launch)}):
            cmd_watch(args)
        mock_launch.assert_called_once_with(gateway_url="http://127.0.0.1:8000", refresh=2.0)


# =====================================================================
# cmd_fix
# =====================================================================
class TestCmdFix:
    def test_calls_run_fix(self):
        args = _make_args(config=None)
        mock_fix = MagicMock()
        with patch.dict("sys.modules", {"castor.fix": MagicMock(run_fix=mock_fix)}):
            cmd_fix(args)
        mock_fix.assert_called_once_with(config_path=None)

    def test_calls_run_fix_with_config(self):
        args = _make_args(config="robot.rcan.yaml")
        mock_fix = MagicMock()
        with patch.dict("sys.modules", {"castor.fix": MagicMock(run_fix=mock_fix)}):
            cmd_fix(args)
        mock_fix.assert_called_once_with(config_path="robot.rcan.yaml")


# =====================================================================
# cmd_repl
# =====================================================================
class TestCmdRepl:
    def test_config_missing(self, tmp_path, capsys):
        args = _make_args(config=str(tmp_path / "nope.rcan.yaml"))
        cmd_repl(args)
        out = capsys.readouterr().out
        assert "Config not found" in out

    def test_config_exists(self, tmp_path):
        config = tmp_path / "robot.rcan.yaml"
        config.write_text("rcan_version: 1.0\n")
        args = _make_args(config=str(config))
        mock_launch = MagicMock()
        with patch.dict("sys.modules", {"castor.repl": MagicMock(launch_repl=mock_launch)}):
            cmd_repl(args)
        mock_launch.assert_called_once_with(config_path=str(config))


# =====================================================================
# cmd_replay
# =====================================================================
class TestCmdReplay:
    def test_calls_replay_session(self):
        args = _make_args(recording="session.jsonl", execute=False, config=None)
        mock_replay = MagicMock()
        with patch.dict("sys.modules", {"castor.record": MagicMock(replay_session=mock_replay)}):
            cmd_replay(args)
        mock_replay.assert_called_once_with(
            recording_path="session.jsonl", execute=False, config_path=None
        )

    def test_with_execute_and_config(self):
        args = _make_args(recording="session.jsonl", execute=True, config="robot.rcan.yaml")
        mock_replay = MagicMock()
        with patch.dict("sys.modules", {"castor.record": MagicMock(replay_session=mock_replay)}):
            cmd_replay(args)
        mock_replay.assert_called_once_with(
            recording_path="session.jsonl",
            execute=True,
            config_path="robot.rcan.yaml",
        )


# =====================================================================
# cmd_benchmark
# =====================================================================
class TestCmdBenchmark:
    def test_config_missing(self, tmp_path, capsys):
        args = _make_args(config=str(tmp_path / "nope.rcan.yaml"), iterations=3, simulate=False)
        cmd_benchmark(args)
        out = capsys.readouterr().out
        assert "Config not found" in out

    def test_config_exists(self, tmp_path):
        config = tmp_path / "robot.rcan.yaml"
        config.write_text("rcan_version: 1.0\n")
        args = _make_args(config=str(config), iterations=5, simulate=True)
        mock_bench = MagicMock()
        with patch.dict("sys.modules", {"castor.benchmark": MagicMock(run_benchmark=mock_bench)}):
            cmd_benchmark(args)
        mock_bench.assert_called_once_with(config_path=str(config), iterations=5, simulate=True)


# =====================================================================
# cmd_lint
# =====================================================================
class TestCmdLint:
    def test_config_missing(self, tmp_path, capsys):
        args = _make_args(config=str(tmp_path / "nope.rcan.yaml"))
        cmd_lint(args)
        out = capsys.readouterr().out
        assert "Config not found" in out

    def test_config_exists(self, tmp_path):
        config = tmp_path / "robot.rcan.yaml"
        config.write_text("rcan_version: 1.0\n")
        args = _make_args(config=str(config))
        mock_lint = MagicMock(return_value=[])
        mock_report = MagicMock()
        with patch.dict(
            "sys.modules",
            {"castor.lint": MagicMock(run_lint=mock_lint, print_lint_report=mock_report)},
        ):
            cmd_lint(args)
        mock_lint.assert_called_once_with(str(config))
        mock_report.assert_called_once()


# =====================================================================
# cmd_learn
# =====================================================================
class TestCmdLearn:
    def test_calls_run_learn(self):
        args = _make_args(lesson=3)
        mock_learn = MagicMock()
        with patch.dict("sys.modules", {"castor.learn": MagicMock(run_learn=mock_learn)}):
            cmd_learn(args)
        mock_learn.assert_called_once_with(lesson=3)

    def test_no_lesson(self):
        args = _make_args(lesson=None)
        mock_learn = MagicMock()
        with patch.dict("sys.modules", {"castor.learn": MagicMock(run_learn=mock_learn)}):
            cmd_learn(args)
        mock_learn.assert_called_once_with(lesson=None)


# =====================================================================
# cmd_fleet
# =====================================================================
class TestCmdFleet:
    def test_calls_fleet_status(self):
        args = _make_args(timeout="10")
        mock_fleet = MagicMock()
        with patch.dict("sys.modules", {"castor.fleet": MagicMock(fleet_status=mock_fleet)}):
            cmd_fleet(args)
        mock_fleet.assert_called_once_with(timeout=10.0)


# =====================================================================
# cmd_export
# =====================================================================
class TestCmdExport:
    def test_config_missing(self, tmp_path, capsys):
        args = _make_args(config=str(tmp_path / "nope.rcan.yaml"), output=None, format="zip")
        cmd_export(args)
        out = capsys.readouterr().out
        assert "Config not found" in out

    def test_config_exists(self, tmp_path):
        config = tmp_path / "robot.rcan.yaml"
        config.write_text("rcan_version: 1.0\n")
        args = _make_args(config=str(config), output=None, format="json")
        mock_export = MagicMock(return_value="/tmp/export.json")
        mock_summary = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.export": MagicMock(
                    export_bundle=mock_export,
                    print_export_summary=mock_summary,
                )
            },
        ):
            cmd_export(args)
        mock_export.assert_called_once_with(config_path=str(config), output_path=None, fmt="json")
        mock_summary.assert_called_once_with("/tmp/export.json", "json")


# =====================================================================
# cmd_token
# =====================================================================
class TestCmdToken:
    def test_no_jwt_secret(self, capsys):
        """When OPENCASTOR_JWT_SECRET is not set, should error."""
        args = _make_args(role="user", scope=None, ttl="24", subject=None)
        mock_load = MagicMock()
        with patch.dict(
            "sys.modules", {"castor.auth": MagicMock(load_dotenv_if_available=mock_load)}
        ):
            with patch.dict(os.environ, {}, clear=True):
                with pytest.raises(SystemExit):
                    cmd_token(args)
        out = capsys.readouterr().out
        assert "OPENCASTOR_JWT_SECRET" in out

    def test_issues_token(self, capsys):
        """With a valid secret, should print the token."""
        args = _make_args(role="admin", scope="status,control", ttl="48", subject="ci-bot")
        mock_load = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.issue.return_value = "jwt-token-string"
        mock_role = MagicMock()
        mock_role.name = "OWNER"

        with patch.dict(
            "sys.modules",
            {
                "castor.auth": MagicMock(load_dotenv_if_available=mock_load),
                "castor.rcan.jwt_auth": MagicMock(
                    RCANTokenManager=MagicMock(return_value=mock_mgr)
                ),
                "castor.rcan.rbac": MagicMock(RCANRole={"OWNER": mock_role}),
            },
        ):
            with patch.dict(os.environ, {"OPENCASTOR_JWT_SECRET": "secret123"}):
                cmd_token(args)
        out = capsys.readouterr().out
        assert "jwt-token-string" in out

    def test_invalid_role(self, capsys):
        """An invalid role should raise SystemExit."""
        args = _make_args(role="superuser", scope=None, ttl="24", subject=None)
        mock_load = MagicMock()

        mock_rbac = MagicMock()
        mock_rbac.RCANRole.__getitem__ = MagicMock(side_effect=KeyError("SUPERUSER"))

        with patch.dict(
            "sys.modules",
            {
                "castor.auth": MagicMock(load_dotenv_if_available=mock_load),
                "castor.rcan.jwt_auth": MagicMock(),
                "castor.rcan.rbac": mock_rbac,
            },
        ):
            with patch.dict(os.environ, {"OPENCASTOR_JWT_SECRET": "secret123"}):
                with pytest.raises(SystemExit):
                    cmd_token(args)

    def test_missing_pyjwt(self, capsys):
        """When PyJWT is not installed, should print hint and exit."""
        args = _make_args(role="user", scope=None, ttl="24", subject=None)
        mock_load = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "castor.auth": MagicMock(load_dotenv_if_available=mock_load),
                "castor.rcan.jwt_auth": None,  # Force ImportError
                "castor.rcan": MagicMock(),
            },
        ):
            with patch.dict(os.environ, {"OPENCASTOR_JWT_SECRET": "secret123"}):
                with pytest.raises((SystemExit, ImportError, TypeError)):
                    cmd_token(args)


# =====================================================================
# cmd_discover
# =====================================================================
class TestCmdDiscover:
    def test_no_peers_found(self, capsys):
        """When no peers are found, print a message."""
        args = _make_args(timeout="0.01")
        mock_browser = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "castor.rcan.mdns": MagicMock(
                    RCANServiceBrowser=MagicMock(return_value=mock_browser)
                ),
            },
        ):
            with patch("time.sleep"):
                cmd_discover(args)
        out = capsys.readouterr().out
        assert "Scanning" in out

    def test_import_error(self, capsys):
        """When zeroconf is not installed, print a message and exit."""
        args = _make_args(timeout="5")
        with patch.dict("sys.modules", {"castor.rcan.mdns": None}):
            with pytest.raises((SystemExit, ImportError, TypeError)):
                cmd_discover(args)


# =====================================================================
# cmd_approvals
# =====================================================================
class TestCmdApprovals:
    def test_list_pending(self):
        """Default action should list pending approvals."""
        args = _make_args(config=None, approve=None, deny=None, clear=False)
        mock_gate = MagicMock()
        mock_gate.list_pending.return_value = []
        mock_print = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.approvals": MagicMock(
                    ApprovalGate=MagicMock(return_value=mock_gate),
                    print_approvals=mock_print,
                )
            },
        ):
            cmd_approvals(args)
        mock_gate.list_pending.assert_called_once()
        mock_print.assert_called_once()

    def test_approve_action(self, capsys):
        """--approve should call gate.approve."""
        args = _make_args(config=None, approve="1", deny=None, clear=False)
        mock_gate = MagicMock()
        mock_gate.approve.return_value = "move forward"
        mock_print = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.approvals": MagicMock(
                    ApprovalGate=MagicMock(return_value=mock_gate),
                    print_approvals=mock_print,
                )
            },
        ):
            cmd_approvals(args)
        mock_gate.approve.assert_called_once_with(1)
        out = capsys.readouterr().out
        assert "Approved" in out

    def test_approve_not_found(self, capsys):
        """--approve with unknown ID should print not found."""
        args = _make_args(config=None, approve="99", deny=None, clear=False)
        mock_gate = MagicMock()
        mock_gate.approve.return_value = None
        with patch.dict(
            "sys.modules",
            {
                "castor.approvals": MagicMock(
                    ApprovalGate=MagicMock(return_value=mock_gate),
                    print_approvals=MagicMock(),
                )
            },
        ):
            cmd_approvals(args)
        out = capsys.readouterr().out
        assert "not found" in out

    def test_deny_action(self, capsys):
        """--deny should call gate.deny."""
        args = _make_args(config=None, approve=None, deny="2", clear=False)
        mock_gate = MagicMock()
        mock_gate.deny.return_value = True
        with patch.dict(
            "sys.modules",
            {
                "castor.approvals": MagicMock(
                    ApprovalGate=MagicMock(return_value=mock_gate),
                    print_approvals=MagicMock(),
                )
            },
        ):
            cmd_approvals(args)
        mock_gate.deny.assert_called_once_with(2)

    def test_deny_not_found(self, capsys):
        """--deny with unknown ID should print not found."""
        args = _make_args(config=None, approve=None, deny="99", clear=False)
        mock_gate = MagicMock()
        mock_gate.deny.return_value = False
        with patch.dict(
            "sys.modules",
            {
                "castor.approvals": MagicMock(
                    ApprovalGate=MagicMock(return_value=mock_gate),
                    print_approvals=MagicMock(),
                )
            },
        ):
            cmd_approvals(args)
        out = capsys.readouterr().out
        assert "not found" in out

    def test_clear_action(self, capsys):
        """--clear should call gate.clear."""
        args = _make_args(config=None, approve=None, deny=None, clear=True)
        mock_gate = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.approvals": MagicMock(
                    ApprovalGate=MagicMock(return_value=mock_gate),
                    print_approvals=MagicMock(),
                )
            },
        ):
            cmd_approvals(args)
        mock_gate.clear.assert_called_once()

    def test_with_config_file(self, tmp_path):
        """When config file exists, load it with yaml."""
        config = tmp_path / "robot.rcan.yaml"
        config.write_text("rcan_version: 1.0\n")
        args = _make_args(config=str(config), approve=None, deny=None, clear=False)
        mock_gate = MagicMock()
        mock_gate.list_pending.return_value = []
        with patch.dict(
            "sys.modules",
            {
                "castor.approvals": MagicMock(
                    ApprovalGate=MagicMock(return_value=mock_gate),
                    print_approvals=MagicMock(),
                ),
            },
        ):
            cmd_approvals(args)
        mock_gate.list_pending.assert_called_once()


# =====================================================================
# cmd_schedule
# =====================================================================
class TestCmdSchedule:
    def _make_schedule_modules(self, **overrides):
        defaults = dict(
            list_tasks=MagicMock(return_value=[]),
            print_schedule=MagicMock(),
            add_task=MagicMock(),
            remove_task=MagicMock(),
            install_crontab=MagicMock(),
        )
        defaults.update(overrides)
        return {"castor.schedule": MagicMock(**defaults)}

    def test_list_tasks(self):
        args = _make_args(action="list", config=None, name=None, task_command=None, cron=None)
        mock_list = MagicMock(return_value=[])
        mock_print = MagicMock()
        with patch.dict(
            "sys.modules",
            self._make_schedule_modules(list_tasks=mock_list, print_schedule=mock_print),
        ):
            cmd_schedule(args)
        mock_list.assert_called_once()

    def test_add_task(self, capsys):
        args = _make_args(
            action="add", config=None, name="patrol", task_command="castor run", cron="*/30 * * * *"
        )
        mock_add = MagicMock(return_value={"name": "patrol", "cron": "*/30 * * * *"})
        with patch.dict("sys.modules", self._make_schedule_modules(add_task=mock_add)):
            cmd_schedule(args)
        mock_add.assert_called_once_with("patrol", "castor run", "*/30 * * * *")
        out = capsys.readouterr().out
        assert "Added" in out

    def test_add_missing_fields(self, capsys):
        """add without --name, --command, --cron should print usage."""
        args = _make_args(action="add", config=None, name=None, task_command=None, cron=None)
        with patch.dict("sys.modules", self._make_schedule_modules()):
            cmd_schedule(args)
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_remove_task(self, capsys):
        args = _make_args(action="remove", config=None, name="patrol", task_command=None, cron=None)
        mock_remove = MagicMock(return_value=True)
        with patch.dict("sys.modules", self._make_schedule_modules(remove_task=mock_remove)):
            cmd_schedule(args)
        mock_remove.assert_called_once_with("patrol")

    def test_remove_not_found(self, capsys):
        args = _make_args(action="remove", config=None, name="ghost", task_command=None, cron=None)
        mock_remove = MagicMock(return_value=False)
        with patch.dict("sys.modules", self._make_schedule_modules(remove_task=mock_remove)):
            cmd_schedule(args)
        out = capsys.readouterr().out
        assert "not found" in out

    def test_remove_no_name(self, capsys):
        """remove without --name should print usage."""
        args = _make_args(action="remove", config=None, name=None, task_command=None, cron=None)
        with patch.dict("sys.modules", self._make_schedule_modules()):
            cmd_schedule(args)
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_install(self):
        args = _make_args(action="install", config=None, name=None, task_command=None, cron=None)
        mock_install = MagicMock()
        with patch.dict("sys.modules", self._make_schedule_modules(install_crontab=mock_install)):
            cmd_schedule(args)
        mock_install.assert_called_once()

    def test_unknown_action(self, capsys):
        """Unknown action should print usage."""
        args = _make_args(action="unknown", config=None, name=None, task_command=None, cron=None)
        with patch.dict("sys.modules", self._make_schedule_modules()):
            cmd_schedule(args)
        out = capsys.readouterr().out
        assert "Usage" in out


# =====================================================================
# cmd_configure
# =====================================================================
class TestCmdConfigure:
    def test_calls_run_configure(self):
        args = _make_args(config="robot.rcan.yaml")
        mock_conf = MagicMock()
        with patch.dict("sys.modules", {"castor.configure": MagicMock(run_configure=mock_conf)}):
            cmd_configure(args)
        mock_conf.assert_called_once_with(config_path="robot.rcan.yaml")


# =====================================================================
# cmd_search
# =====================================================================
class TestCmdSearch:
    def test_calls_search_logs(self):
        args = _make_args(query="battery low", log_file=None, since="7d", max_results=20)
        mock_search = MagicMock(return_value=[])
        mock_print = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.memory_search": MagicMock(
                    search_logs=mock_search,
                    print_search_results=mock_print,
                )
            },
        ):
            cmd_search(args)
        mock_search.assert_called_once_with(
            query="battery low", log_file=None, since="7d", max_results=20
        )
        mock_print.assert_called_once()


# =====================================================================
# cmd_network
# =====================================================================
class TestCmdNetwork:
    def test_status_action(self):
        args = _make_args(action="status", config=None, mode=None, port=8000)
        mock_status = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.network": MagicMock(
                    network_status=mock_status,
                    expose=MagicMock(),
                )
            },
        ):
            cmd_network(args)
        mock_status.assert_called_once_with(config_path=None)

    def test_expose_action(self):
        args = _make_args(action="expose", config=None, mode="funnel", port=9000)
        mock_expose = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.network": MagicMock(
                    network_status=MagicMock(),
                    expose=mock_expose,
                )
            },
        ):
            cmd_network(args)
        mock_expose.assert_called_once_with(mode="funnel", port=9000)

    def test_expose_default_mode(self):
        """When --mode is not set, default to 'serve'."""
        args = _make_args(action="expose", config=None, mode=None, port=8000)
        mock_expose = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.network": MagicMock(
                    network_status=MagicMock(),
                    expose=mock_expose,
                )
            },
        ):
            cmd_network(args)
        mock_expose.assert_called_once_with(mode="serve", port=8000)

    def test_default_action(self):
        """When action is None or unrecognized, default to status."""
        args = _make_args(action=None, config="some.yaml", mode=None, port=8000)
        mock_status = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.network": MagicMock(
                    network_status=mock_status,
                    expose=MagicMock(),
                )
            },
        ):
            cmd_network(args)
        mock_status.assert_called_once()


# =====================================================================
# cmd_privacy
# =====================================================================
class TestCmdPrivacy:
    def test_without_config(self):
        args = _make_args(config=None)
        mock_print = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.privacy": MagicMock(print_privacy_policy=mock_print),
            },
        ):
            cmd_privacy(args)
        mock_print.assert_called_once_with({})

    def test_with_config_file(self, tmp_path):
        config = tmp_path / "robot.rcan.yaml"
        config.write_text("rcan_version: '1.0'\n")
        args = _make_args(config=str(config))
        mock_print = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.privacy": MagicMock(print_privacy_policy=mock_print),
            },
        ):
            cmd_privacy(args)
        mock_print.assert_called_once()
        # The config dict should have been loaded
        call_config = mock_print.call_args[0][0]
        assert call_config.get("rcan_version") == "1.0"


# =====================================================================
# cmd_update_check
# =====================================================================
class TestCmdUpdateCheck:
    def test_calls_print_update_status(self):
        args = _make_args()
        mock_check = MagicMock()
        with patch.dict(
            "sys.modules", {"castor.update_check": MagicMock(print_update_status=mock_check)}
        ):
            cmd_update_check(args)
        mock_check.assert_called_once()


# =====================================================================
# cmd_profile
# =====================================================================
class TestCmdProfile:
    def _make_profile_modules(self, **overrides):
        defaults = dict(
            list_profiles=MagicMock(return_value=[]),
            print_profiles=MagicMock(),
            save_profile=MagicMock(),
            use_profile=MagicMock(),
            remove_profile=MagicMock(),
        )
        defaults.update(overrides)
        return {"castor.profiles": MagicMock(**defaults)}

    def test_list_profiles(self):
        args = _make_args(action="list", name=None, config="robot.rcan.yaml")
        mock_list = MagicMock(return_value=[])
        mock_print = MagicMock()
        with patch.dict(
            "sys.modules",
            self._make_profile_modules(list_profiles=mock_list, print_profiles=mock_print),
        ):
            cmd_profile(args)
        mock_list.assert_called_once()
        mock_print.assert_called_once()

    def test_save_profile(self, capsys):
        args = _make_args(action="save", name="indoor", config="robot.rcan.yaml")
        mock_save = MagicMock()
        with patch.dict("sys.modules", self._make_profile_modules(save_profile=mock_save)):
            cmd_profile(args)
        mock_save.assert_called_once_with("indoor", "robot.rcan.yaml")
        out = capsys.readouterr().out
        assert "indoor" in out

    def test_save_no_name(self, capsys):
        """save without a name should print usage."""
        args = _make_args(action="save", name=None, config="robot.rcan.yaml")
        with patch.dict("sys.modules", self._make_profile_modules()):
            cmd_profile(args)
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_use_profile(self, capsys):
        args = _make_args(action="use", name="outdoor", config="robot.rcan.yaml")
        mock_use = MagicMock()
        with patch.dict("sys.modules", self._make_profile_modules(use_profile=mock_use)):
            cmd_profile(args)
        mock_use.assert_called_once_with("outdoor")
        out = capsys.readouterr().out
        assert "activated" in out

    def test_use_profile_not_found(self, capsys):
        args = _make_args(action="use", name="ghost", config="robot.rcan.yaml")
        mock_use = MagicMock(side_effect=FileNotFoundError)
        with patch.dict("sys.modules", self._make_profile_modules(use_profile=mock_use)):
            cmd_profile(args)
        out = capsys.readouterr().out
        assert "not found" in out

    def test_use_no_name(self, capsys):
        args = _make_args(action="use", name=None, config="robot.rcan.yaml")
        with patch.dict("sys.modules", self._make_profile_modules()):
            cmd_profile(args)
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_remove_profile(self, capsys):
        args = _make_args(action="remove", name="old", config="robot.rcan.yaml")
        mock_remove = MagicMock(return_value=True)
        with patch.dict("sys.modules", self._make_profile_modules(remove_profile=mock_remove)):
            cmd_profile(args)
        out = capsys.readouterr().out
        assert "removed" in out

    def test_remove_not_found(self, capsys):
        args = _make_args(action="remove", name="ghost", config="robot.rcan.yaml")
        mock_remove = MagicMock(return_value=False)
        with patch.dict("sys.modules", self._make_profile_modules(remove_profile=mock_remove)):
            cmd_profile(args)
        out = capsys.readouterr().out
        assert "not found" in out

    def test_remove_no_name(self, capsys):
        args = _make_args(action="remove", name=None, config="robot.rcan.yaml")
        with patch.dict("sys.modules", self._make_profile_modules()):
            cmd_profile(args)
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_unknown_action(self, capsys):
        args = _make_args(action="unknown", name=None, config="robot.rcan.yaml")
        with patch.dict("sys.modules", self._make_profile_modules()):
            cmd_profile(args)
        out = capsys.readouterr().out
        assert "Usage" in out


# =====================================================================
# cmd_test
# =====================================================================
class TestCmdTest:
    def test_basic_run(self):
        """cmd_test should run pytest via subprocess."""
        args = _make_args(verbose=False, keyword=None)
        mock_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(SystemExit) as exc_info:
                cmd_test(args)
            assert exc_info.value.code == 0

    def test_verbose_flag(self):
        """--verbose should pass -v to pytest."""
        args = _make_args(verbose=True, keyword=None)
        mock_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with pytest.raises(SystemExit):
                cmd_test(args)
        cmd_args = mock_run.call_args[0][0]
        assert "-v" in cmd_args

    def test_keyword_filter(self):
        """--keyword should pass -k to pytest."""
        args = _make_args(verbose=False, keyword="test_doctor")
        mock_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with pytest.raises(SystemExit):
                cmd_test(args)
        cmd_args = mock_run.call_args[0][0]
        assert "-k" in cmd_args
        assert "test_doctor" in cmd_args

    def test_failure_exit_code(self):
        """Non-zero return from pytest should propagate."""
        args = _make_args(verbose=False, keyword=None)
        mock_result = MagicMock(returncode=1)
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(SystemExit) as exc_info:
                cmd_test(args)
            assert exc_info.value.code == 1


# =====================================================================
# cmd_diff
# =====================================================================
class TestCmdDiff:
    def test_config_missing(self, tmp_path, capsys):
        args = _make_args(
            config=str(tmp_path / "missing.rcan.yaml"),
            baseline=str(tmp_path / "base.rcan.yaml"),
        )
        cmd_diff(args)
        out = capsys.readouterr().out
        assert "not found" in out

    def test_baseline_missing(self, tmp_path, capsys):
        config = tmp_path / "robot.rcan.yaml"
        config.write_text("rcan_version: 1.0\n")
        args = _make_args(
            config=str(config),
            baseline=str(tmp_path / "missing_base.rcan.yaml"),
        )
        cmd_diff(args)
        out = capsys.readouterr().out
        assert "not found" in out

    def test_both_exist(self, tmp_path):
        config = tmp_path / "robot.rcan.yaml"
        config.write_text("rcan_version: 1.0\n")
        baseline = tmp_path / "base.rcan.yaml"
        baseline.write_text("rcan_version: 0.9\n")
        args = _make_args(config=str(config), baseline=str(baseline))
        mock_diff = MagicMock(return_value=[])
        mock_print = MagicMock()
        with patch.dict(
            "sys.modules", {"castor.diff": MagicMock(diff_configs=mock_diff, print_diff=mock_print)}
        ):
            cmd_diff(args)
        mock_diff.assert_called_once_with(str(config), str(baseline))
        mock_print.assert_called_once()


# =====================================================================
# cmd_quickstart
# =====================================================================
class TestCmdQuickstart:
    def test_wizard_success_then_demo(self, capsys):
        """Successful wizard should proceed to demo."""
        args = _make_args()
        mock_results = [
            MagicMock(returncode=0),  # wizard
            MagicMock(returncode=0),  # demo
        ]
        with patch("subprocess.run", side_effect=mock_results):
            cmd_quickstart(args)
        out = capsys.readouterr().out
        assert "QuickStart" in out
        assert "Step 1" in out
        assert "Step 2" in out
        assert "QuickStart complete" in out

    def test_wizard_failure(self, capsys):
        """Failed wizard should abort quickstart."""
        args = _make_args()
        mock_result = MagicMock(returncode=1)
        with patch("subprocess.run", return_value=mock_result):
            cmd_quickstart(args)
        out = capsys.readouterr().out
        assert "Wizard failed" in out


# =====================================================================
# cmd_plugins
# =====================================================================
class TestCmdPlugins:
    def test_calls_list_and_print(self):
        args = _make_args()
        mock_load = MagicMock()
        mock_list = MagicMock(return_value=[])
        mock_print = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.plugins": MagicMock(
                    load_plugins=mock_load,
                    list_plugins=mock_list,
                    print_plugins=mock_print,
                )
            },
        ):
            cmd_plugins(args)
        mock_load.assert_called_once()
        mock_list.assert_called_once()
        mock_print.assert_called_once()


# =====================================================================
# cmd_audit
# =====================================================================
class TestCmdAudit:
    def test_calls_get_audit_and_read(self):
        args = _make_args(since="24h", event="motor_command", limit=50)
        mock_audit_obj = MagicMock()
        mock_audit_obj.read.return_value = []
        mock_get = MagicMock(return_value=mock_audit_obj)
        mock_print = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.audit": MagicMock(
                    get_audit=mock_get,
                    print_audit=mock_print,
                )
            },
        ):
            cmd_audit(args)
        mock_get.assert_called_once()
        mock_audit_obj.read.assert_called_once_with(since="24h", event="motor_command", limit=50)
        mock_print.assert_called_once_with([])

    def test_default_args(self):
        args = _make_args(since=None, event=None, limit=50)
        mock_audit_obj = MagicMock()
        mock_audit_obj.read.return_value = []
        mock_get = MagicMock(return_value=mock_audit_obj)
        mock_print = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "castor.audit": MagicMock(
                    get_audit=mock_get,
                    print_audit=mock_print,
                )
            },
        ):
            cmd_audit(args)
        mock_audit_obj.read.assert_called_once_with(since=None, event=None, limit=50)


# =====================================================================
# Command dispatch map completeness
# =====================================================================
class TestCommandMapCompleteness:
    """Verify that the commands dict in main() contains all 41 entries."""

    def test_all_41_commands_in_list(self):
        """Our ALL_COMMANDS list should have exactly 41 entries."""
        assert len(ALL_COMMANDS) == 41

    def test_all_commands_have_handlers(self):
        """Each command in ALL_COMMANDS should have a corresponding cmd_* function."""
        import castor.cli as cli_module

        for cmd in ALL_COMMANDS:
            handler_name = "cmd_" + cmd.replace("-", "_")
            assert hasattr(cli_module, handler_name), (
                f"Missing handler: {handler_name} for command '{cmd}'"
            )

    def test_all_handlers_are_callable(self):
        """All cmd_* handler functions should be callable."""
        import castor.cli as cli_module

        for cmd in ALL_COMMANDS:
            handler_name = "cmd_" + cmd.replace("-", "_")
            handler = getattr(cli_module, handler_name)
            assert callable(handler), f"{handler_name} is not callable"

    def test_no_duplicate_commands(self):
        """ALL_COMMANDS should have no duplicates."""
        assert len(ALL_COMMANDS) == len(set(ALL_COMMANDS))


# =====================================================================
# Plugin loading in main()
# =====================================================================
class TestPluginLoading:
    def test_plugin_load_failure_does_not_crash(self, capsys):
        """If plugin loading fails, main should still work."""
        # Create a mock module where load_plugins raises
        mock_plugins_mod = MagicMock()
        mock_plugins_mod.load_plugins.side_effect = RuntimeError("bad plugin")
        with patch("sys.argv", ["castor"]):
            with patch.dict("sys.modules", {"castor.plugins": mock_plugins_mod}):
                main()
        out = capsys.readouterr().out
        assert "OpenCastor" in out

    def test_plugin_commands_merged(self):
        """Plugin commands should be merged into the commands dict without crash."""
        mock_handler = MagicMock()
        mock_registry = MagicMock()
        mock_registry.commands = {"custom-cmd": (mock_handler, "Custom help")}
        mock_plugins_mod = MagicMock()
        mock_plugins_mod.load_plugins.return_value = mock_registry

        with patch("sys.argv", ["castor"]):
            with patch.dict("sys.modules", {"castor.plugins": mock_plugins_mod}):
                main()  # Just verify no crash

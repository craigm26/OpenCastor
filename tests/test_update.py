"""
tests/test_update.py — Unit tests for castor/commands/update.py (issue #122).

Covers:
  - Dry-run mode: prints commands, no subprocess called
  - Git editable install: git pull + pip install called
  - Pip install: pip install --upgrade called
  - --version flag: git checkout tag / pip version specifier
  - Swarm update: SSH command built per node
  - Swarm update: sshpass not found → prints manual instructions
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**kwargs) -> argparse.Namespace:
    """Build a Namespace with sensible defaults plus overrides."""
    defaults = {
        "dry_run": False,
        "version": None,
        "swarm_config": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# cmd_update tests
# ---------------------------------------------------------------------------


class TestCmdUpdateDryRun:
    """Dry-run should print the commands without calling subprocess.run."""

    def test_dry_run_no_subprocess_called(self, capsys, tmp_path):
        """--dry-run prints commands but does NOT invoke subprocess.run."""
        # Simulate a git-based editable install
        fake_repo = tmp_path / "OpenCastor"
        fake_repo.mkdir()
        (fake_repo / ".git").mkdir()

        with (
            patch("castor.commands.update._repo_dir", return_value=fake_repo),
            patch("castor.commands.update._is_editable_install", return_value=True),
            patch("castor.commands.update.subprocess.run") as mock_sub,
        ):
            from castor.commands.update import cmd_update
            cmd_update(_make_args(dry_run=True))

        mock_sub.assert_not_called()
        out = capsys.readouterr().out
        assert "git" in out.lower() or "pull" in out.lower()


class TestCmdUpdateGit:
    """Non-dry-run git install should call git pull and pip install."""

    def test_git_calls_pull(self, capsys, tmp_path):
        """Git editable install: subprocess.run is called with git pull."""
        fake_repo = tmp_path / "OpenCastor"
        fake_repo.mkdir()
        (fake_repo / ".git").mkdir()

        captured_cmds: list[list[str]] = []

        def _mock_run(cmd, **_kw):
            captured_cmds.append(cmd)
            return MagicMock(returncode=0)

        with (
            patch("castor.commands.update._repo_dir", return_value=fake_repo),
            patch("castor.commands.update._is_editable_install", return_value=True),
            patch("castor.commands.update.subprocess.run", side_effect=_mock_run),
        ):
            from castor.commands.update import cmd_update
            cmd_update(_make_args(dry_run=False))

        # At least one command should contain "git" and "pull"
        git_pull_calls = [c for c in captured_cmds if "git" in c and "pull" in c]
        assert git_pull_calls, f"No git pull call found in: {captured_cmds}"

    def test_git_calls_pip_install(self, capsys, tmp_path):
        """Git editable install: subprocess.run is called with pip install -e."""
        fake_repo = tmp_path / "OpenCastor"
        fake_repo.mkdir()
        (fake_repo / ".git").mkdir()

        captured_cmds: list[list[str]] = []

        def _mock_run(cmd, **_kw):
            captured_cmds.append(cmd)
            return MagicMock(returncode=0)

        with (
            patch("castor.commands.update._repo_dir", return_value=fake_repo),
            patch("castor.commands.update._is_editable_install", return_value=True),
            patch("castor.commands.update.subprocess.run", side_effect=_mock_run),
        ):
            from castor.commands.update import cmd_update
            cmd_update(_make_args(dry_run=False))

        pip_calls = [c for c in captured_cmds if "pip" in " ".join(c) and "-e" in c]
        assert pip_calls, f"No pip install -e call found in: {captured_cmds}"


class TestCmdUpdatePip:
    """Regular pip (non-editable) install should call pip install --upgrade."""

    def test_pip_upgrade_called(self, capsys):
        """pip install: subprocess.run called with 'pip install --upgrade opencastor'."""
        captured_cmds: list[list[str]] = []

        def _mock_run(cmd, **_kw):
            captured_cmds.append(cmd)
            return MagicMock(returncode=0)

        with (
            patch("castor.commands.update._is_editable_install", return_value=False),
            patch("castor.commands.update.subprocess.run", side_effect=_mock_run),
        ):
            from castor.commands.update import cmd_update
            cmd_update(_make_args(dry_run=False))

        upgrade_calls = [c for c in captured_cmds if "--upgrade" in c and "opencastor" in " ".join(c)]
        assert upgrade_calls, f"No pip upgrade call found in: {captured_cmds}"


class TestCmdUpdateVersionFlag:
    """--version X.Y.Z should pass the tag/version to git checkout or pip specifier."""

    def test_version_flag_git_checkout(self, capsys, tmp_path):
        """--version with git install: git checkout v<version> is called."""
        fake_repo = tmp_path / "OpenCastor"
        fake_repo.mkdir()
        (fake_repo / ".git").mkdir()

        captured_cmds: list[list[str]] = []

        def _mock_run(cmd, **_kw):
            captured_cmds.append(cmd)
            return MagicMock(returncode=0)

        with (
            patch("castor.commands.update._repo_dir", return_value=fake_repo),
            patch("castor.commands.update._is_editable_install", return_value=True),
            patch("castor.commands.update.subprocess.run", side_effect=_mock_run),
        ):
            from castor.commands.update import cmd_update
            cmd_update(_make_args(dry_run=False, version="2026.2.0"))

        checkout_calls = [
            c for c in captured_cmds
            if "git" in c and "checkout" in c and "v2026.2.0" in c
        ]
        assert checkout_calls, f"No git checkout v2026.2.0 found in: {captured_cmds}"

    def test_version_flag_pip_specifier(self, capsys):
        """--version with pip install: pip install opencastor==X.Y.Z is called."""
        captured_cmds: list[list[str]] = []

        def _mock_run(cmd, **_kw):
            captured_cmds.append(cmd)
            return MagicMock(returncode=0)

        with (
            patch("castor.commands.update._is_editable_install", return_value=False),
            patch("castor.commands.update.subprocess.run", side_effect=_mock_run),
        ):
            from castor.commands.update import cmd_update
            cmd_update(_make_args(dry_run=False, version="2026.1.0"))

        version_calls = [
            c for c in captured_cmds
            if "opencastor==2026.1.0" in " ".join(c)
        ]
        assert version_calls, f"No versioned pip call found in: {captured_cmds}"


# ---------------------------------------------------------------------------
# cmd_swarm_update tests
# ---------------------------------------------------------------------------


class TestSwarmUpdate:
    """Tests for the multi-node SSH update."""

    def _make_swarm_yaml(self, tmp_path, nodes: list[dict]) -> str:
        """Write a minimal swarm.yaml and return its path."""
        p = tmp_path / "swarm.yaml"
        p.write_text(yaml.dump({"nodes": nodes}))
        return str(p)

    def test_swarm_reads_swarm_yaml_and_builds_ssh_cmd(self, tmp_path, capsys):
        """Swarm update reads swarm.yaml and builds an SSH command per node."""
        swarm_path = self._make_swarm_yaml(
            tmp_path,
            [{"name": "bot1", "ip": "192.168.1.10", "user": "pi"}],
        )

        captured_cmds: list[list[str]] = []

        def _mock_run(cmd, **_kw):
            captured_cmds.append(cmd)
            return MagicMock(returncode=0)

        with (
            patch("castor.commands.update.subprocess.run", side_effect=_mock_run),
            patch("castor.commands.update.shutil.which", return_value=None),  # no sshpass
        ):
            from castor.commands.update import cmd_swarm_update
            cmd_swarm_update(_make_args(dry_run=False, swarm_config=swarm_path))

        # An SSH command targeting 192.168.1.10 should have been issued
        ssh_calls = [c for c in captured_cmds if "ssh" in c and "192.168.1.10" in " ".join(c)]
        assert ssh_calls, f"No SSH command found for bot1. Captured: {captured_cmds}"

    def test_swarm_update_dry_run(self, tmp_path, capsys):
        """Dry-run prints command for each node without calling subprocess."""
        swarm_path = self._make_swarm_yaml(
            tmp_path,
            [{"name": "bot2", "ip": "10.0.0.2", "user": "pi"}],
        )
        with patch("castor.commands.update.subprocess.run") as mock_sub:
            from castor.commands.update import cmd_swarm_update
            cmd_swarm_update(_make_args(dry_run=True, swarm_config=swarm_path))

        mock_sub.assert_not_called()
        out = capsys.readouterr().out
        assert "DRY-RUN" in out or "bot2" in out

    def test_swarm_no_sshpass_prints_manual_instructions(self, tmp_path, capsys):
        """When node has a password but sshpass is missing, print manual SSH instructions."""
        swarm_path = self._make_swarm_yaml(
            tmp_path,
            [{"name": "secure_bot", "ip": "10.0.0.5", "user": "pi", "password": "secret123"}],
        )
        with patch("castor.commands.update.shutil.which", return_value=None):  # no sshpass
            from castor.commands.update import cmd_swarm_update
            cmd_swarm_update(_make_args(dry_run=False, swarm_config=swarm_path))

        out = capsys.readouterr().out
        # Should print either the manual SSH instruction or a sshpass-related message
        assert "ssh" in out.lower() or "sshpass" in out.lower() or "manual" in out.lower()

    def test_swarm_update_no_nodes(self, tmp_path, capsys):
        """Empty swarm.yaml prints a helpful message and exits cleanly."""
        swarm_path = self._make_swarm_yaml(tmp_path, [])
        from castor.commands.update import cmd_swarm_update
        cmd_swarm_update(_make_args(dry_run=False, swarm_config=swarm_path))
        out = capsys.readouterr().out
        assert "nothing" in out.lower() or "no nodes" in out.lower()

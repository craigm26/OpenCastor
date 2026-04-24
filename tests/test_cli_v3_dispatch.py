"""R4 v3.0 CLI dispatch tests — legacy guard + compliance-submit wiring."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest


def test_cmd_run_rejects_legacy_rcan_yaml_with_guidance(tmp_path, capsys):
    """castor run on a .rcan.yaml input must exit 1 with migrate guidance."""
    from castor.cli import cmd_run

    legacy = tmp_path / "foo.rcan.yaml"
    legacy.write_text("rcan_version: '2.2'\n")
    ns = argparse.Namespace(config=str(legacy), manifest=None)
    with pytest.raises(SystemExit) as exc:
        cmd_run(ns)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "legacy .rcan.yaml" in err
    assert "castor migrate" in err


def test_cmd_compliance_submit_dispatches_to_helper():
    """castor compliance submit <artifact> must invoke _cmd_compliance_submit."""
    from castor import cli

    ns = argparse.Namespace(
        compliance_action="submit",
        artifact="fria",
        manifest="ROBOT.md",
        data=None,
        config="robot.rcan.yaml",
    )
    with patch.object(cli, "_cmd_compliance_submit", return_value=0) as mock_helper:
        cli.cmd_compliance(ns)
    mock_helper.assert_called_once_with(ns)


def test_compliance_submit_helper_errors_without_rrn(tmp_path, capsys):
    """_cmd_compliance_submit must refuse if manifest has no rrn."""
    from castor import cli

    manifest = tmp_path / "ROBOT.md"
    manifest.write_text("---\nrcan_version: '3.2'\nmetadata: {robot_name: x}\n---\n\n# x\n")
    ns = argparse.Namespace(
        compliance_action="submit",
        artifact="fria",
        manifest=str(manifest),
        data=None,
    )
    mock_reader = MagicMock()
    mock_manifest = MagicMock()
    mock_manifest.rrn = None
    mock_manifest.endpoint = "https://rcan.dev"
    mock_reader.read_robot_md.return_value = mock_manifest
    with patch.dict(
        "sys.modules",
        {"castor.rcan3.reader": mock_reader},
    ):
        rc = cli._cmd_compliance_submit(ns)
    assert rc == 1
    err = capsys.readouterr().err
    assert "no rrn" in err
    assert "register" in err

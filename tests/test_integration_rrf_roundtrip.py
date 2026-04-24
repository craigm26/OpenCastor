"""End-to-end round-trip with mocked RRF: init → register → compliance submit.

Validates the v3.0 CLI surface against a respx-mocked RRF v2 endpoint. No
hardware or live network calls. Exercises the same helpers the real
`castor register` + `castor compliance submit fria` subcommands call.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import respx
from httpx import Response


def test_full_init_register_compliance_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CASTOR_KEYDIR", str(tmp_path / "keys"))
    monkeypatch.chdir(tmp_path)

    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        register_route = router.post("https://rcan.dev/v2/robots/register").mock(
            return_value=Response(
                201,
                json={
                    "rrn": "RRN-000000000001",
                    "rcan_uri": "rcan://rcan.dev/craigm26/so-arm101/1-0-0/bob-001",
                },
            )
        )
        fria_route = router.post("https://rcan.dev/v2/compliance/fria").mock(
            return_value=Response(202, json={"accepted": True, "artifact_id": "fria-001"})
        )
        _run_roundtrip(tmp_path)
        assert register_route.called, "expected /v2/robots/register to be called"
        assert fria_route.called, "expected /v2/compliance/fria to be called"


def _run_roundtrip(tmp_path: Path) -> None:

    # 1. init → writes ROBOT.md with agent.runtimes[] and no rrn yet
    import os

    os.chdir(tmp_path)
    from castor.init_wizard import cmd_init

    ns = argparse.Namespace(
        non_interactive=True,
        path="ROBOT.md",
        robot_name="bob",
        manufacturer="craigm26",
        model="so-arm101",
        version="1.0.0",
        device_id="bob-001",
        provider="anthropic",
        llm_model="claude-sonnet-4-6",
        force=True,
    )
    assert cmd_init(ns) == 0

    # Patch the manifest with the rrn we'd receive from register (init_wizard
    # doesn't fill it in because registration happens post-init).
    md = Path("ROBOT.md").read_text()
    md = md.replace(
        "  device_id: bob-001",
        "  device_id: bob-001\n  rrn: RRN-000000000001",
    )
    Path("ROBOT.md").write_text(md)

    # 2. register — exercise rrf_cmd via cmd_rrf dispatch
    from castor.rrf_cmd import cmd_rrf

    rc = cmd_rrf(argparse.Namespace(subcommand="register", manifest="ROBOT.md"))
    assert rc == 0

    # 3. compliance submit fria — exercise via the cli-level helper
    from castor.cli import _cmd_compliance_submit

    data_file = tmp_path / "fria.json"
    data_file.write_text(json.dumps({"deployment": {"region": "eu"}}))
    rc = _cmd_compliance_submit(
        argparse.Namespace(
            artifact="fria",
            manifest="ROBOT.md",
            data=str(data_file),
            compliance_action="submit",
        )
    )
    assert rc == 0

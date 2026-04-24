"""Tests for castor.rrf_cmd after RRF v1 → v2 migration."""

from __future__ import annotations

import argparse

import respx
from httpx import Response


@respx.mock
def test_cmd_rrf_register_calls_v2(tmp_path, monkeypatch):
    """`castor rrf register` POSTs to /v2/robots/register."""
    from castor.rrf_cmd import cmd_rrf

    # Create a minimal ROBOT.md
    p = tmp_path / "ROBOT.md"
    p.write_text(
        "---\nrcan_version: '3.2'\n"
        "metadata:\n  robot_name: bob\n  manufacturer: c\n  model: m\n  version: '1.0'\n  device_id: d\n"
        "network: {rrf_endpoint: https://rcan.dev}\n"
        "agent: {runtimes: [{id: opencastor, harness: castor-default, default: true}]}\n"
        "---\n"
    )

    # Use a throwaway keydir
    monkeypatch.setenv("CASTOR_KEYDIR", str(tmp_path / "keys"))

    route = respx.post("https://rcan.dev/v2/robots/register").mock(
        return_value=Response(201, json={"rrn": "RRN-000000000001", "rcan_uri": "rcan://x"})
    )

    ns = argparse.Namespace(
        subcommand="register",
        manifest=str(p),
    )
    rc = cmd_rrf(ns)
    assert rc == 0
    assert route.called


@respx.mock
def test_cmd_rrf_get_calls_v2(monkeypatch, tmp_path):
    from castor.rrf_cmd import cmd_rrf

    monkeypatch.setenv("CASTOR_KEYDIR", str(tmp_path / "keys"))
    respx.get("https://rcan.dev/v2/robots/RRN-000000000001").mock(
        return_value=Response(200, json={"rrn": "RRN-000000000001", "robot_name": "bob"})
    )
    ns = argparse.Namespace(
        subcommand="get",
        rrn="RRN-000000000001",
        endpoint="https://rcan.dev",
    )
    rc = cmd_rrf(ns)
    assert rc == 0


def test_cmd_rrf_components_returns_deprecation_error():
    """components subcommand prints deprecation error and returns exit code 1."""
    from castor.rrf_cmd import cmd_rrf

    ns = argparse.Namespace(subcommand="components")
    rc = cmd_rrf(ns)
    assert rc == 1


def test_cmd_rrf_deprecated_subcommands_return_1():
    """All five deprecated subcommands return exit code 1."""
    from castor.rrf_cmd import cmd_rrf

    for sub in ("models", "harness", "status", "wipe"):
        ns = argparse.Namespace(subcommand=sub)
        rc = cmd_rrf(ns)
        assert rc == 1, f"Expected rc=1 for deprecated subcommand {sub!r}, got {rc}"

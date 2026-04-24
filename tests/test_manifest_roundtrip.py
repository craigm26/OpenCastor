"""Cross-SDK manifest round-trip test.

Asserts that what `castor init` writes can be parsed verbatim by rcan-py
3.3.0 and the agent.runtimes[] shape survives unchanged.
"""

from __future__ import annotations

import argparse

import yaml
from rcan import from_manifest


def test_castor_init_output_parses_identically_via_rcan_py(tmp_path):
    from castor.init_wizard import cmd_init

    path = tmp_path / "ROBOT.md"
    ns = argparse.Namespace(
        non_interactive=True,
        path=str(path),
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

    # Parse with rcan-py
    info = from_manifest(path)

    # Directly parse the frontmatter with PyYAML for comparison
    text = path.read_text()
    fm = yaml.safe_load(text.split("---")[1])

    # Shape checks — rcan-py must see the same runtimes[] shape as PyYAML
    assert info.rcan_version == "3.2"
    assert info.rcan_version == fm["rcan_version"]
    assert info.agent_runtimes == fm["agent"]["runtimes"]
    assert len(info.agent_runtimes) == 1

    entry = info.agent_runtimes[0]
    assert entry["id"] == "opencastor"
    assert entry["harness"] == "castor-default"
    assert entry["default"] is True
    models = entry["models"]
    assert models[0]["provider"] == "anthropic"
    assert models[0]["model"] == "claude-sonnet-4-6"
    assert models[0]["role"] == "primary"

"""Tests for castor.rcan3.reader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


def _write_robot_md(tmp_path: Path, frontmatter: str, body: str = "") -> Path:
    """Write a ROBOT.md with the given YAML frontmatter."""
    p = tmp_path / "ROBOT.md"
    p.write_text(f"---\n{frontmatter}\n---\n{body}")
    return p


BOB_FRONTMATTER = textwrap.dedent("""\
    rcan_version: "3.2"
    metadata:
      robot_name: bob
      manufacturer: craigm26
      model: so-arm101
      version: 1.0.0
      device_id: bob-001
      rrn: RRN-000000000001
      rcan_uri: rcan://rcan.dev/craigm26/so-arm101/1-0-0/bob-001
    network:
      rrf_endpoint: https://rcan.dev
      signing_alg: pqc-hybrid-v1
    agent:
      runtimes:
        - id: opencastor
          harness: castor-default
          default: true
          models:
            - provider: anthropic
              model: claude-sonnet-4-6
              role: primary
    safety:
      estop:
        software: true
        response_ms: 100
""")


def test_read_robot_md_returns_typed_manifest(tmp_path):
    from castor.rcan3.reader import RcanManifest, read_robot_md

    p = _write_robot_md(tmp_path, BOB_FRONTMATTER)
    m = read_robot_md(p)
    assert isinstance(m, RcanManifest)
    assert m.rrn == "RRN-000000000001"
    assert m.rcan_version == "3.2"
    assert m.endpoint == "https://rcan.dev"
    assert m.robot_name == "bob"
    assert m.agent_runtimes is not None
    assert m.agent_runtimes[0]["id"] == "opencastor"


def test_read_robot_md_missing_file_raises(tmp_path):
    from castor.rcan3.reader import read_robot_md

    with pytest.raises(FileNotFoundError):
        read_robot_md(tmp_path / "nope.md")


def test_read_robot_md_empty_file_raises_valueerror(tmp_path):
    from castor.rcan3.reader import read_robot_md

    p = tmp_path / "ROBOT.md"
    p.write_text("")
    with pytest.raises(ValueError, match="not a ROBOT.md manifest"):
        read_robot_md(p)


def test_select_runtime_returns_matching_entry(tmp_path):
    from castor.rcan3.reader import read_robot_md

    p = _write_robot_md(tmp_path, BOB_FRONTMATTER)
    m = read_robot_md(p)
    entry = m.select_runtime("opencastor")
    assert entry["id"] == "opencastor"
    assert entry["harness"] == "castor-default"


def test_select_runtime_missing_id_raises(tmp_path):
    from castor.rcan3.reader import read_robot_md

    p = _write_robot_md(tmp_path, BOB_FRONTMATTER)
    m = read_robot_md(p)
    with pytest.raises(KeyError, match="runtime id 'robot-md' not declared"):
        m.select_runtime("robot-md")


def test_select_runtime_none_returns_default_entry(tmp_path):
    """select_runtime(None) picks the entry with default: true."""
    from castor.rcan3.reader import read_robot_md

    p = _write_robot_md(tmp_path, BOB_FRONTMATTER)
    m = read_robot_md(p)
    entry = m.select_runtime(None)
    assert entry["id"] == "opencastor"
    assert entry.get("default") is True


def test_safety_block_is_preserved(tmp_path):
    from castor.rcan3.reader import read_robot_md

    p = _write_robot_md(tmp_path, BOB_FRONTMATTER)
    m = read_robot_md(p)
    assert m.safety["estop"]["response_ms"] == 100

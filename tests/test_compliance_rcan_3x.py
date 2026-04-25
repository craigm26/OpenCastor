"""Tests for opencastor ROBOT.md frontmatter loading in castor.compliance.

Background: castor.compliance._load_config previously did plain
yaml.safe_load which fails on ROBOT.md's markdown-frontmatter shape
("expected a single document"). These tests pin the loader's filename-
dispatched behaviour: *.md files extract frontmatter; *.rcan.yaml stay
on the plain-YAML path.

3.x version-acceptance is covered by the main suite (is_accepted_version
hard-cuts to 3.x major); not duplicated here.
"""

from __future__ import annotations

from pathlib import Path

from castor.compliance import _load_config


def test_load_config_plain_rcan_yaml(tmp_path: Path):
    """Legacy *.rcan.yaml path stays on yaml.safe_load passthrough."""
    p = tmp_path / "robot.rcan.yaml"
    p.write_text("rcan_version: '3.0'\nrobot_name: legacy\n")
    cfg = _load_config(str(p))
    assert cfg["rcan_version"] == "3.0"
    assert cfg["robot_name"] == "legacy"


def test_load_config_robot_md_frontmatter(tmp_path: Path):
    """ROBOT.md (markdown + YAML frontmatter) loads — this is the bug fix.

    Plain yaml.safe_load raises ComposerError on multi-document streams.
    The loader extracts the frontmatter between the two `---` lines.
    """
    p = tmp_path / "ROBOT.md"
    p.write_text(
        '---\n'
        'rcan_version: "3.2"\n'
        'metadata:\n'
        '  rrn: RRN-000000000099\n'
        '  robot_name: bob\n'
        '---\n'
        '# bob\n'
        '\n'
        'Prose body that must NOT trip the YAML parser.\n'
    )
    cfg = _load_config(str(p))
    assert cfg["rcan_version"] == "3.2"
    assert cfg["metadata"]["rrn"] == "RRN-000000000099"
    assert cfg["metadata"]["robot_name"] == "bob"


def test_load_config_robot_md_with_no_body(tmp_path: Path):
    """Edge case: ROBOT.md with frontmatter but empty body still loads."""
    p = tmp_path / "ROBOT.md"
    p.write_text('---\nrcan_version: "3.0"\n---\n')
    cfg = _load_config(str(p))
    assert cfg["rcan_version"] == "3.0"


def test_load_config_dispatches_by_filename(tmp_path: Path):
    """Dispatch is by filename suffix (.md → frontmatter, else yaml.safe_load).
    Pin this so future loaders don't drift."""
    md_path = tmp_path / "Anything.md"
    md_path.write_text('---\nrcan_version: "3.2"\n---\n# header\n')
    yaml_path = tmp_path / "Anything.rcan.yaml"
    yaml_path.write_text('rcan_version: "3.0"\n')
    assert _load_config(str(md_path))["rcan_version"] == "3.2"
    assert _load_config(str(yaml_path))["rcan_version"] == "3.0"

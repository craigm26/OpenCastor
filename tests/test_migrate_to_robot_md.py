"""Golden-file tests for castor migrate (.rcan.yaml → ROBOT.md)."""

from __future__ import annotations

from pathlib import Path


def test_migrate_to_robot_md_matches_golden(tmp_path):
    from castor.migrate import migrate_to_robot_md

    fixture_dir = Path(__file__).parent / "fixtures" / "legacy_rcan_yaml"
    src = fixture_dir / "minimal.legacy.yaml"
    golden = fixture_dir / "minimal.ROBOT.md.golden"

    out = tmp_path / "ROBOT.md"
    rc = migrate_to_robot_md(src, out)

    assert rc == 0
    assert out.read_text() == golden.read_text()


def test_migrate_to_robot_md_warns_deprecated(tmp_path, capsys):
    from castor.migrate import migrate_to_robot_md

    fixture_dir = Path(__file__).parent / "fixtures" / "legacy_rcan_yaml"
    src = fixture_dir / "minimal.legacy.yaml"
    out = tmp_path / "ROBOT.md"
    migrate_to_robot_md(src, out)
    captured = capsys.readouterr()
    assert "deprecated" in (captured.out + captured.err).lower()

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


# ── The drivers block, which is the whole robot ──────────────────────────────


def _duck_legacy(tmp_path) -> Path:
    """A generated Microduck config, as `castor duck` used to write one."""
    import yaml

    from castor import microduck as md

    cfg = md.build_config(host="192.168.1.42", user="radxa", robot_name="quacky")
    src = tmp_path / "quacky.rcan.yaml"
    src.write_text(yaml.dump(cfg, sort_keys=False))
    return src


def test_migrate_carries_the_drivers_block(tmp_path):
    """Without `drivers`, ComponentRegistry.get_driver returns None.

    `castor/registry.py:201-202` — so a migration that drops the block converts a
    configured duck into a brain with no body, and does it silently. This is the
    test that has to fail before that can ship again.
    """
    from castor.main import _read_manifest_frontmatter
    from castor.migrate import migrate_to_robot_md

    src = _duck_legacy(tmp_path)
    out = tmp_path / "ROBOT.md"
    assert migrate_to_robot_md(src, out) == 0

    fm = _read_manifest_frontmatter(out.read_text())
    drivers = fm.get("drivers")
    assert drivers, "the drivers block did not survive the migration"
    duck = next(d for d in drivers if d.get("protocol") == "microduck")
    assert duck["transport"] == "ssh"
    assert duck["ssh_host"] == "192.168.1.42"
    assert duck["ssh_user"] == "radxa"
    # And the velocity envelope, which is the difference between this duck and
    # a duck configured for something else.
    assert duck.get("max_vx")


def test_migrate_refuses_to_drop_a_block_it_cannot_carry(tmp_path, monkeypatch, capsys):
    """When a block would be lost, exit non-zero and write nothing."""
    from castor import migrate as migrate_mod

    src = _duck_legacy(tmp_path)
    out = tmp_path / "ROBOT.md"

    real = migrate_mod._convert_to_v32
    monkeypatch.setattr(
        migrate_mod,
        "_convert_to_v32",
        lambda old: {k: v for k, v in real(old).items() if k != "drivers"},
    )

    assert migrate_mod.migrate_to_robot_md(src, out) == 1
    assert not out.exists(), "nothing may be written when a block would be lost"
    assert "drivers" in capsys.readouterr().err


def test_cmd_migrate_exits_non_zero_when_a_block_would_be_lost(tmp_path, monkeypatch):
    """The CLI must propagate it — it used to return None regardless."""
    import argparse

    import pytest

    from castor import cli, migrate as migrate_mod

    src = _duck_legacy(tmp_path)
    out = tmp_path / "ROBOT.md"
    real = migrate_mod._convert_to_v32
    monkeypatch.setattr(
        migrate_mod,
        "_convert_to_v32",
        lambda old: {k: v for k, v in real(old).items() if k != "drivers"},
    )
    args = argparse.Namespace(src=str(src), out=str(out), config=None)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_migrate(args)
    assert exc.value.code == 1

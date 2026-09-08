"""Tests for castor.init_wizard — ROBOT.md emission."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def test_cmd_init_writes_robot_md(tmp_path, monkeypatch):
    """cmd_init writes a ROBOT.md with v3.2 frontmatter to the given path."""
    from castor.init_wizard import cmd_init

    ns = argparse.Namespace(
        non_interactive=True,
        path=str(tmp_path / "ROBOT.md"),
        robot_name="bob",
        manufacturer="craigm26",
        model="so-arm101",
        version="1.0.0",
        device_id="bob-001",
        provider="anthropic",
        llm_model="claude-sonnet-4-6",
    )
    rc = cmd_init(ns)
    assert rc == 0

    md = (tmp_path / "ROBOT.md").read_text()
    assert md.startswith("---\n")
    # Parse frontmatter
    _, front, _ = md.split("---", 2)
    fm = yaml.safe_load(front)
    assert fm["rcan_version"] == "3.2"
    assert fm["metadata"]["robot_name"] == "bob"
    assert fm["agent"]["runtimes"][0]["id"] == "opencastor"
    assert fm["agent"]["runtimes"][0]["harness"] == "castor-default"
    assert fm["agent"]["runtimes"][0]["default"] is True


def test_cmd_init_refuses_overwrite_without_force(tmp_path):
    from castor.init_wizard import cmd_init

    p = tmp_path / "ROBOT.md"
    p.write_text("---\nrcan_version: '3.2'\n---\n")

    ns = argparse.Namespace(
        non_interactive=True,
        path=str(p),
        robot_name="b",
        manufacturer="a",
        model="c",
        version="1.0",
        device_id="d",
        provider="anthropic",
        llm_model="claude",
    )
    rc = cmd_init(ns)
    assert rc != 0  # non-zero exit code


def test_cmd_init_force_overwrites(tmp_path):
    from castor.init_wizard import cmd_init

    p = tmp_path / "ROBOT.md"
    p.write_text("old")

    ns = argparse.Namespace(
        non_interactive=True,
        path=str(p),
        robot_name="b",
        manufacturer="a",
        model="c",
        version="1.0",
        device_id="d",
        provider="anthropic",
        llm_model="claude",
        force=True,
    )
    rc = cmd_init(ns)
    assert rc == 0
    assert "rcan_version" in p.read_text()


def test_cmd_quickstart_is_available():
    """cmd_quickstart is the second CLI entry point — must still be importable."""
    from castor.init_wizard import cmd_quickstart

    assert callable(cmd_quickstart)


def test_emitted_robot_md_round_trips_through_rcan_py(tmp_path):
    """rcan.from_manifest parses our output and finds the runtime."""
    from rcan import from_manifest

    from castor.init_wizard import cmd_init

    ns = argparse.Namespace(
        non_interactive=True,
        path=str(tmp_path / "ROBOT.md"),
        robot_name="bob",
        manufacturer="craigm26",
        model="so-arm101",
        version="1.0.0",
        device_id="bob-001",
        provider="anthropic",
        llm_model="claude-sonnet-4-6",
    )
    cmd_init(ns)
    info = from_manifest(tmp_path / "ROBOT.md")
    assert info.robot_name == "bob"
    assert info.rcan_version == "3.2"
    assert info.agent_runtimes is not None
    assert info.agent_runtimes[0]["id"] == "opencastor"


def test_init_and_quickstart_in_cli_help():
    """castor --help output must mention init and quickstart."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "castor", "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    combined = result.stdout + result.stderr
    assert "init" in combined, "'init' not found in castor --help"
    assert "quickstart" in combined, "'quickstart' not found in castor --help"


# ---------------------------------------------------------------------------
# Shapes. `castor init` used to describe an arm in every default — name `bob`,
# model `so-arm101`, device id `bob-001` — and offered no question that would
# have told a newcomer with an RC car that they were getting somebody else's
# robot. It was also worse than that: argparse defaults every flag to None, so
# the attribute always EXISTED and `getattr(args, "robot_name", "bob")` never
# once reached its default. `castor init --non-interactive` wrote nulls.
# ---------------------------------------------------------------------------


def _ns(**kw):
    base = dict(
        non_interactive=True,
        robot_name=None,
        manufacturer=None,
        model=None,
        version=None,
        device_id=None,
        provider=None,
        llm_model=None,
        shape=None,
        force=True,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _front(path: Path) -> dict:
    _, front, _ = path.read_text().split("---", 2)
    return yaml.safe_load(front)


def test_non_interactive_init_writes_no_nulls(tmp_path):
    """The bug: every flag defaults to None, so the manifest was all-null."""
    from castor.init_wizard import cmd_init

    p = tmp_path / "ROBOT.md"
    assert cmd_init(_ns(path=str(p))) == 0
    md = _front(p)["metadata"]
    for field in ("robot_name", "manufacturer", "model", "version", "device_id"):
        assert md[field], f"{field} is {md[field]!r} — an unusable manifest"


def test_default_shape_is_still_the_arm(tmp_path):
    """No flag, no prompt: byte-for-byte the identity this always emitted."""
    from castor.init_wizard import cmd_init

    p = tmp_path / "ROBOT.md"
    assert cmd_init(_ns(path=str(p))) == 0
    md = _front(p)["metadata"]
    assert md["robot_name"] == "bob"
    assert md["model"] == "so-arm101"
    assert md["device_id"] == "bob-001"


def test_rc_car_shape_is_car_shaped(tmp_path):
    """A car asks for a car and gets one, not an arm."""
    from castor.init_wizard import cmd_init

    p = tmp_path / "ROBOT.md"
    assert cmd_init(_ns(path=str(p), shape="rc-car")) == 0
    md = _front(p)["metadata"]
    assert md["robot_name"] == "car"
    assert md["model"] == "rpi-rc-car"
    assert md["device_id"] == "car-001"
    assert "so-arm101" not in p.read_text()


def test_rc_car_body_routes_to_castor_up_not_a_preset(tmp_path):
    """The catalog's `rpi_rc_car` preset has no YAML. `castor up` owns this."""
    from castor.init_wizard import cmd_init

    p = tmp_path / "ROBOT.md"
    cmd_init(_ns(path=str(p), shape="rc-car"))
    body = p.read_text()
    assert "castor up --home ~/car --name car" in body
    assert "no `rpi_rc_car` preset YAML" in body
    # The two traps a drafting model reads this file to avoid.
    assert "simulated wheels" in body
    assert "duration_s" in body


def test_explicit_flags_still_beat_the_shape(tmp_path):
    from castor.init_wizard import cmd_init

    p = tmp_path / "ROBOT.md"
    cmd_init(_ns(path=str(p), shape="rc-car", robot_name="zip", model="custom"))
    md = _front(p)["metadata"]
    assert md["robot_name"] == "zip"
    assert md["model"] == "custom"
    assert md["device_id"] == "car-001"  # unset fields still follow the shape


def test_unknown_shape_falls_back_rather_than_crashing(tmp_path, capsys):
    from castor.init_wizard import cmd_init

    p = tmp_path / "ROBOT.md"
    assert cmd_init(_ns(path=str(p), shape="submarine")) == 0
    assert _front(p)["metadata"]["model"] == "so-arm101"
    assert "submarine" in capsys.readouterr().err


def test_interactive_asks_for_the_shape_first(tmp_path, monkeypatch):
    """The question is the fix. Without it nobody learns the default is an arm."""
    from castor.init_wizard import cmd_init

    answers = iter(["rc-car", "", "", "", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    p = tmp_path / "ROBOT.md"
    assert cmd_init(_ns(path=str(p), non_interactive=False)) == 0
    assert _front(p)["metadata"]["model"] == "rpi-rc-car"


def test_every_shape_round_trips_through_rcan_py(tmp_path):
    from rcan import from_manifest

    from castor.init_wizard import _SHAPES, cmd_init

    for shape in _SHAPES:
        p = tmp_path / f"{shape}-ROBOT.md"
        assert cmd_init(_ns(path=str(p), shape=shape)) == 0
        info = from_manifest(p)
        assert info.rcan_version == "3.2"
        assert info.agent_runtimes[0]["id"] == "opencastor"


def test_quickstart_honours_the_shape(tmp_path):
    from castor.init_wizard import cmd_quickstart

    p = tmp_path / "ROBOT.md"
    ns = _ns(path=str(p), shape="rc-car")
    assert cmd_quickstart(ns) == 0
    assert _front(p)["metadata"]["model"] == "rpi-rc-car"


def test_suggest_preset_routes_the_car_to_castor_up():
    """`rpi_rc_car` has no backing YAML, so the reason must name the real path."""
    from castor.hardware_detect import suggest_preset

    hw = {
        "i2c_devices": [{"bus": 1, "address": "0x40"}],
        "usb_serial": [],
        "usb_descriptors": [],
        "cameras": [],
        "platform": "rpi",
    }
    preset, _confidence, reason = suggest_preset(hw)
    assert preset == "rpi_rc_car"
    # Prove it is the PCA9685 branch and not the no-hardware fallback, which
    # also names `castor up` and would make this assertion meaningless.
    assert "PCA9685" in reason, reason
    assert "castor up" in reason
    assert "no rpi_rc_car preset file" in reason

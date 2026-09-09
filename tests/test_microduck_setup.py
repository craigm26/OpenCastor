"""Tests for zero-config Microduck setup (`castor duck`).

Covers discovery, verification, config generation and the wiring into
hardware detection, the setup catalog and the CLI.  No network, no hardware:
every subprocess and socket call is monkeypatched.
"""

from __future__ import annotations

import pytest
import sys

import yaml

import castor.microduck as md
from castor.main import _read_manifest_frontmatter
from castor.microduck import PRESET_ID, DuckCandidate


# ── Profile / preset ──────────────────────────────────────────────────────────


def test_packaged_profile_matches_repo_preset():
    """The pip-installed profile must not drift from the repo preset."""
    from pathlib import Path

    root = Path(md.__file__).resolve().parent.parent
    repo = yaml.safe_load((root / "config" / "presets" / f"{PRESET_ID}.rcan.yaml").read_text())
    packaged = yaml.safe_load(
        (root / "castor" / "profiles" / "pollen" / "microduck.yaml").read_text()
    )

    assert packaged["profile"] == "pollen/microduck"
    for key in ("rcan_version", "drivers", "safety", "physics", "connection"):
        assert packaged[key] == repo[key], f"{key} drifted between preset and packaged profile"


def test_profile_is_loadable():
    profile = md.load_profile()
    assert profile["drivers"][0]["protocol"] == "microduck"


def test_profile_path_prefers_repo_preset():
    assert md.profile_path().name == f"{PRESET_ID}.rcan.yaml"


# ── Config generation ─────────────────────────────────────────────────────────


def test_build_config_ssh_transport():
    cfg = md.build_config(host="192.168.1.42", user="radxa", robot_name="quacky")
    driver = cfg["drivers"][0]
    assert driver["transport"] == "ssh"
    assert driver["ssh_host"] == "192.168.1.42"
    assert driver["ssh_user"] == "radxa"
    assert cfg["metadata"]["robot_name"] == "quacky"
    assert cfg["connection"]["host"] == "192.168.1.42"
    assert "profile" not in cfg  # profile marker is stripped from generated configs


def test_build_config_unix_transport_drops_network_keys():
    cfg = md.build_config(transport="unix", robot_name="duck")
    driver = cfg["drivers"][0]
    assert driver["transport"] == "unix"
    assert driver["socket"] == "/run/robotd.sock"
    for key in ("ssh_host", "ssh_user", "local_port", "host", "port"):
        assert key not in driver
    assert cfg["connection"]["type"] == "local"
    assert "host" not in cfg["connection"]


def test_build_config_tcp_transport():
    cfg = md.build_config(host="127.0.0.1", transport="tcp")
    driver = cfg["drivers"][0]
    assert driver["host"] == "127.0.0.1"
    assert "ssh_host" not in driver


def test_build_config_applies_agent_override():
    cfg = md.build_config(host="d.local", agent={"provider": "ollama", "model": "gemma3:4b"})
    assert cfg["agent"]["provider"] == "ollama"
    assert cfg["agent"]["model"] == "gemma3:4b"


def test_build_config_generates_unique_identity():
    a = md.build_config(host="d.local", robot_name="duck")
    b = md.build_config(host="d.local", robot_name="duck")
    assert a["metadata"]["robot_uuid"] != b["metadata"]["robot_uuid"]
    assert a["metadata"]["rrn_uri"].endswith("/duck")


def test_write_config_round_trips(tmp_path):
    cfg = md.build_config(host="d.local", user="radxa", robot_name="duck")
    path = md.write_config(cfg, robot_name="duck", path=tmp_path / "duck.rcan.yaml")
    assert path.exists()
    reloaded = yaml.safe_load(path.read_text())
    assert reloaded["drivers"][0]["ssh_host"] == "d.local"


def test_written_config_drives_the_real_driver_factory(tmp_path):
    """A generated config must produce a MicroduckDriver via the normal factory."""
    from castor.drivers import get_driver
    from castor.drivers.microduck_driver import MicroduckDriver

    cfg = md.build_config(host="127.0.0.1", user="nobody", transport="tcp")
    cfg["drivers"][0]["port"] = 1  # unreachable — driver degrades to mock
    driver = get_driver(cfg)
    assert isinstance(driver, MicroduckDriver)
    driver.close()


# ── Discovery ─────────────────────────────────────────────────────────────────


def test_discover_prefers_local_socket(monkeypatch):
    monkeypatch.setattr(md, "local_socket_present", lambda *a, **k: True)
    monkeypatch.setattr(md, "probe_hostnames", lambda **k: [])
    monkeypatch.setattr(md, "duckctl_ip", lambda **k: None)
    monkeypatch.setattr(md, "mdns_hosts", lambda **k: [])

    found = md.discover()
    assert found[0].transport == "unix"
    assert found[0].source == "local"


def test_discover_orders_and_dedupes(monkeypatch):
    monkeypatch.setattr(md, "local_socket_present", lambda *a, **k: False)
    monkeypatch.setattr(md, "probe_hostnames", lambda **k: ["duck.local"])
    monkeypatch.setattr(md, "duckctl_ip", lambda **k: "192.168.1.42")
    monkeypatch.setattr(md, "mdns_hosts", lambda **k: ["duck.local"])  # duplicate

    found = md.discover(extra_hosts=("10.0.0.5",))
    assert [c.host for c in found] == ["10.0.0.5", "duck.local", "192.168.1.42"]
    assert [c.source for c in found] == ["manual", "hostname", "duckctl"]


def test_discover_returns_empty_when_nothing_responds(monkeypatch):
    monkeypatch.setattr(md, "local_socket_present", lambda *a, **k: False)
    monkeypatch.setattr(md, "probe_hostnames", lambda **k: [])
    monkeypatch.setattr(md, "duckctl_ip", lambda **k: None)
    monkeypatch.setattr(md, "mdns_hosts", lambda **k: [])
    assert md.discover() == []


def test_duckctl_ip_parses_output(monkeypatch):
    monkeypatch.setattr(md.shutil, "which", lambda name: "/usr/bin/duckctl")
    monkeypatch.setattr(md, "_run", lambda cmd, timeout=10.0: (0, "ip 192.168.1.42", ""))
    assert md.duckctl_ip() == "192.168.1.42"


def test_duckctl_ip_absent_is_not_an_error(monkeypatch):
    monkeypatch.setattr(md.shutil, "which", lambda name: None)
    assert md.duckctl_ip() is None


def test_arp_neighbours_parses_ip_neigh(monkeypatch):
    out = "192.168.1.42 dev wlan0 lladdr aa:bb REACHABLE\n192.168.1.1 dev wlan0 lladdr cc:dd STALE"
    monkeypatch.setattr(md, "_run", lambda cmd, timeout=5.0: (0, out, ""))
    assert md.arp_neighbours() == ["192.168.1.42", "192.168.1.1"]


def test_mdns_without_zeroconf_returns_empty(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "zeroconf":
            raise ImportError("no zeroconf")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert md.mdns_hosts() == []


# ── Verification ──────────────────────────────────────────────────────────────


def _ssh_stub(responses: dict):
    """Build an ssh() stub from a {substring: (rc, stdout)} mapping."""

    def _ssh(host, user, command, timeout=6.0):
        for needle, (rc, out) in responses.items():
            if needle in command:
                return rc, out, ""
        return 0, "", ""

    return _ssh


def test_verify_confirms_a_real_duck(monkeypatch):
    monkeypatch.setattr(md, "_port_open", lambda *a, **k: True)
    monkeypatch.setattr(
        md,
        "ssh",
        _ssh_stub(
            {
                "test -S": (0, "duck"),
                "id -nG": (0, "radxa robot sudo"),
                "robotctl system info": (0, "name: duck-01\npin: 123456"),
                "true": (0, ""),
            }
        ),
    )
    cand = md.verify(DuckCandidate(host="duck.local", user="radxa"))
    assert cand.is_duck is True
    assert cand.in_robot_group is True
    assert cand.robot_name == "duck-01"
    assert cand.ready is True
    assert cand.blocker is None


def test_verify_flags_missing_robot_group(monkeypatch):
    monkeypatch.setattr(md, "_port_open", lambda *a, **k: True)
    monkeypatch.setattr(
        md,
        "ssh",
        _ssh_stub({"test -S": (0, "duck"), "id -nG": (0, "radxa sudo"), "true": (0, "")}),
    )
    cand = md.verify(DuckCandidate(host="duck.local", user="radxa"))
    assert cand.in_robot_group is False
    assert cand.ready is False
    assert cand.blocker == "user not in 'robot' group"
    assert "usermod -aG robot" in md.robot_group_command("duck.local", "radxa")


def test_verify_rejects_a_host_without_robotd(monkeypatch):
    monkeypatch.setattr(md, "_port_open", lambda *a, **k: True)
    monkeypatch.setattr(md, "ssh", _ssh_stub({"test -S": (1, ""), "true": (0, "")}))
    cand = md.verify(DuckCandidate(host="nas.local", user="pi"))
    assert cand.ssh_auth is True
    assert cand.is_duck is False
    assert cand.blocker == "robotd socket not found"


def test_verify_reports_missing_ssh_key(monkeypatch):
    monkeypatch.setattr(md, "_port_open", lambda *a, **k: True)
    monkeypatch.setattr(md, "ssh", lambda *a, **k: (255, "", "Permission denied"))
    cand = md.verify(DuckCandidate(host="duck.local", user="radxa"))
    assert cand.ssh_open is True
    assert cand.ssh_auth is False
    assert md.ssh_copy_id_command("duck.local", "radxa") == "ssh-copy-id radxa@duck.local"


def test_verify_unreachable_host_stops_early(monkeypatch):
    monkeypatch.setattr(md, "_port_open", lambda *a, **k: False)
    cand = md.verify(DuckCandidate(host="10.0.0.9"))
    assert cand.ssh_open is False
    assert cand.is_duck is False


def test_verify_local_transport_uses_socket(monkeypatch):
    monkeypatch.setattr(md, "local_socket_present", lambda *a, **k: True)
    cand = md.verify(DuckCandidate(host="localhost", transport="unix"))
    assert cand.is_duck is True
    assert cand.ready is True


def test_resolve_ssh_user_tries_current_user_first(monkeypatch):
    monkeypatch.setenv("USER", "craig")
    tried: list = []

    def _ssh(host, user, command, timeout=6.0):
        tried.append(user)
        return (0, "", "") if user == "radxa" else (255, "", "denied")

    monkeypatch.setattr(md, "ssh", _ssh)
    assert md.resolve_ssh_user("duck.local") == "radxa"
    assert tried[0] == "craig"


def test_resolve_ssh_user_returns_none_when_all_fail(monkeypatch):
    monkeypatch.setattr(md, "ssh", lambda *a, **k: (255, "", "denied"))
    assert md.resolve_ssh_user("duck.local") is None


# ── Health ────────────────────────────────────────────────────────────────────


def test_health_reports_error_instead_of_raising():
    result = md.health(host="203.0.113.1", user="nobody", timeout=1.0)
    assert result["ok"] is False
    assert result["error"]


# ── Wiring: detection, catalog, CLI ───────────────────────────────────────────


def test_hardware_detect_suggests_the_duck_profile():
    from castor.hardware_detect import suggest_preset

    preset, confidence, reason = suggest_preset({"microduck": ["duck-01.local"]})
    assert preset == "pollen/microduck"
    assert confidence == "high"
    assert "castor duck" in reason


def test_hardware_detect_exposes_a_microduck_detector():
    from castor.hardware_detect import _HARDWARE_EXTRAS, detect_microduck_network

    assert callable(detect_microduck_network)
    assert _HARDWARE_EXTRAS["microduck"] == []  # stdlib-only driver


def test_setup_catalog_offers_the_duck():
    from castor.setup_catalog import get_hardware_preset_map, get_hardware_presets

    assert PRESET_ID in {p.id for p in get_hardware_presets()}
    assert get_hardware_preset_map()["16"] == PRESET_ID


def test_wizard_generates_a_duck_config_from_the_preset():
    from castor.wizard import generate_preset_config

    cfg = generate_preset_config(
        PRESET_ID, "duck", {"provider": "anthropic", "model": "claude-sonnet-4-5"}
    )
    assert cfg["drivers"][0]["protocol"] == "microduck"
    assert cfg["metadata"]["robot_name"] == "duck"


def test_wizard_handles_slash_profiles_without_metadata():
    """Regression: castor/profiles/**.yaml may omit metadata/agent blocks."""
    from castor.wizard import generate_preset_config

    cfg = generate_preset_config(
        "pollen/reachy-mini", "mini", {"provider": "anthropic", "model": "claude-sonnet-4-5"}
    )
    assert cfg["metadata"]["robot_name"] == "mini"
    assert cfg["agent"]["provider"] == "anthropic"


def test_cli_registers_the_duck_command():
    from castor import cli

    assert callable(cli.cmd_duck)


def test_cli_duck_find_reports_nothing_found(monkeypatch, capsys):
    from castor import cli

    monkeypatch.setattr(md, "discover", lambda **k: [])

    class Args:
        duck_cmd = "find"
        deep = False
        json = False

    assert cli.cmd_duck(Args()) == 1
    assert "No duck found" in capsys.readouterr().out


def test_cli_duck_find_lists_candidates(monkeypatch, capsys):
    from castor import cli

    cand = DuckCandidate(host="duck.local", source="hostname", user="radxa")
    cand.is_duck = True
    cand.ssh_auth = True
    cand.in_robot_group = True
    monkeypatch.setattr(md, "discover", lambda **k: [cand])
    monkeypatch.setattr(md, "verify", lambda c, **k: c)

    class Args:
        duck_cmd = "find"
        deep = False
        json = False

    assert cli.cmd_duck(Args()) == 0
    out = capsys.readouterr().out
    assert "duck.local" in out
    assert "ready" in out


def test_cli_duck_setup_writes_config(monkeypatch, tmp_path, capsys):
    from castor import cli

    cand = DuckCandidate(host="duck.local", source="hostname", user="radxa")
    cand.is_duck = True
    cand.ssh_auth = True
    cand.ssh_open = True
    cand.in_robot_group = True
    monkeypatch.setattr(md, "discover", lambda **k: [cand])
    monkeypatch.setattr(md, "verify", lambda c, **k: c)
    monkeypatch.setattr(
        md,
        "health",
        lambda **k: {
            "ok": True,
            "control_loop": {"target_hz": 50.0, "achieved_hz": 49.8, "missed": 0},
            "battery": {"volts": 7.9, "percent": 64.0},
            "policies": ["alpha_walking.onnx"],
            "policy_slots": {"walk": "alpha_walking.onnx", "skills": []},
        },
    )
    monkeypatch.setattr(md, "config_dir", lambda: tmp_path)

    class Args:
        duck_cmd = None
        host = None
        user = None
        name = "quacky"
        deep = False
        yes = True
        start = False
        json = False

    assert cli.cmd_duck(Args()) == 0
    # A ROBOT.md, because that is what `castor run` accepts. The legacy
    # <name>.rcan.yaml it used to write is refused by cmd_run's own guard.
    written = tmp_path / "quacky.ROBOT.md"
    assert written.exists()
    assert not (tmp_path / "quacky.rcan.yaml").exists()
    cfg = _read_manifest_frontmatter(written.read_text())
    assert cfg["drivers"][0]["ssh_host"] == "duck.local"
    out = capsys.readouterr().out
    assert "castor duck health" in out
    assert "49.8 Hz" in out, "the real loop rate, read from control_loop.achieved_hz"
    assert "64%" in out


def test_cli_duck_setup_end_to_end_against_a_fake_robotd(monkeypatch, tmp_path):
    """The whole `castor duck` flow, driven against a real NDJSON robotd stand-in.

    Nothing is mocked below the CLI: discovery, the driver, `robot.subscribe`,
    `robot.health` and config generation all run for real over a Unix socket.
    """
    import castor.drivers.microduck_driver as drv
    from castor import cli
    from test_microduck_driver import FakeRobotd

    sock = tmp_path / "robotd.sock"
    server = FakeRobotd(str(sock))

    original_init = drv.MicroduckDriver.__init__

    def _init_with_fake_socket(self, config):
        config = dict(config)
        config["socket"] = str(sock)
        original_init(self, config)

    monkeypatch.setattr(drv.MicroduckDriver, "__init__", _init_with_fake_socket)
    monkeypatch.setattr(md, "local_socket_present", lambda *a, **k: True)
    monkeypatch.setattr(md, "config_dir", lambda: tmp_path)

    class Args:
        duck_cmd = None
        host = None
        user = None
        name = "fake-duck"
        deep = False
        yes = True
        start = False
        json = False

    try:
        assert cli.cmd_duck(Args()) == 0
    finally:
        server.close()

    cfg = _read_manifest_frontmatter((tmp_path / "fake-duck.ROBOT.md").read_text())
    assert cfg["drivers"][0]["transport"] == "unix"
    assert cfg["connection"]["type"] == "local"
    assert "robot.subscribe" in server.request_methods()
    assert "robot.health" in server.request_methods()


def _ready_duck(monkeypatch, tmp_path):
    """Wire cmd_duck onto a discovered, healthy duck writing into tmp_path."""
    cand = DuckCandidate(host="duck.local", source="hostname", user="radxa")
    cand.is_duck = True
    cand.ssh_auth = True
    cand.ssh_open = True
    cand.in_robot_group = True
    monkeypatch.setattr(md, "discover", lambda **k: [cand])
    monkeypatch.setattr(md, "verify", lambda c, **k: c)
    monkeypatch.setattr(
        md,
        "health",
        lambda **k: {
            "ok": True,
            "control_loop": {"target_hz": 50.0, "achieved_hz": 49.8, "missed": 0},
            "battery": {"volts": 7.9, "percent": 64.0},
            "policy_slots": {"walk": "alpha_walking.onnx", "skills": []},
        },
    )
    monkeypatch.setattr(md, "config_dir", lambda: tmp_path)


class _DuckArgs:
    duck_cmd = None
    host = None
    user = None
    name = "duck"
    brain = None
    deep = False
    yes = True
    start = False
    json = False


def test_cli_duck_brain_flag_sets_provider_and_model(monkeypatch, tmp_path):
    from castor import cli

    _ready_duck(monkeypatch, tmp_path)

    class Args(_DuckArgs):
        brain = "ollama:gemma3:4b"

    assert cli.cmd_duck(Args()) == 0
    cfg = _read_manifest_frontmatter((tmp_path / "duck.ROBOT.md").read_text())
    assert cfg["agent"]["provider"] == "ollama"
    assert cfg["agent"]["model"] == "gemma3:4b"


def test_cli_duck_points_at_login_when_the_brain_has_no_credentials(monkeypatch, tmp_path, capsys):
    from castor import cli

    _ready_duck(monkeypatch, tmp_path)
    monkeypatch.setattr("castor.auth.check_provider_ready", lambda *a, **k: False)

    assert cli.cmd_duck(_DuckArgs()) == 0
    out = capsys.readouterr().out
    assert "castor login" in out
    assert "castor duck test" in out  # walking never needs a brain


def test_cli_duck_declares_ready_when_the_brain_is_configured(monkeypatch, tmp_path, capsys):
    from castor import cli

    _ready_duck(monkeypatch, tmp_path)
    monkeypatch.setattr("castor.auth.check_provider_ready", lambda *a, **k: True)

    assert cli.cmd_duck(_DuckArgs()) == 0
    out = capsys.readouterr().out
    assert "Ready." in out
    assert "castor login" not in out


def test_cli_duck_json_reports_brain_readiness(monkeypatch, tmp_path, capsys):
    import json

    from castor import cli

    _ready_duck(monkeypatch, tmp_path)
    monkeypatch.setattr("castor.auth.check_provider_ready", lambda *a, **k: True)

    class Args(_DuckArgs):
        json = True

    assert cli.cmd_duck(Args()) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["brain"]["ready"] is True
    assert payload["verified"] is True
    assert payload["manifest"].endswith("duck.ROBOT.md")
    assert payload["config"] == payload["manifest"]


# ── The printed command has to work ───────────────────────────────────────────


def _duck_manifest(monkeypatch, tmp_path, sock: str) -> str:
    """Run the whole `castor duck` flow against a wire-shaped robotd.

    Returns the exact string the setup command told the operator to run.
    """
    import castor.drivers.microduck_driver as drv
    from castor import cli

    original_init = drv.MicroduckDriver.__init__

    def _init_with_fake_socket(self, config):
        config = dict(config)
        config["socket"] = sock
        original_init(self, config)

    monkeypatch.setattr(drv.MicroduckDriver, "__init__", _init_with_fake_socket)
    monkeypatch.setattr(md, "local_socket_present", lambda *a, **k: True)
    monkeypatch.setattr(md, "config_dir", lambda: tmp_path)

    class Args:
        duck_cmd = None
        host = None
        user = None
        name = "quacky"
        brain = None
        deep = False
        yes = True
        start = False
        json = False

    assert cli.cmd_duck(Args()) == 0
    return str(tmp_path / "quacky.ROBOT.md")


def test_the_command_castor_duck_prints_is_one_castor_run_accepts(monkeypatch, tmp_path):
    """`castor duck` used to end by printing a command that exits 1.

    It wrote `<name>.rcan.yaml` (castor/microduck.py:624) and printed
    `castor run --config <that>`, which cmd_run rejects on the suffix alone
    (castor/cli.py:57-67, :207). The two halves of the product disagreed about the
    format and the half that generates was losing. This proves they now agree:
    the printed path clears the guard, loads, and yields a real driver.
    """
    import argparse

    from castor import cli
    from castor.drivers import get_driver
    from castor.main import load_config
    from microduck_wire_fixtures import WireRobotd

    sock = str(tmp_path / "robotd.sock")
    server = WireRobotd(sock)
    try:
        path = _duck_manifest(monkeypatch, tmp_path, sock)

        # 1. cmd_run's legacy guard lets it through.
        assert cli._legacy_rcan_yaml_guard(path) is False

        # 2. cmd_run itself reaches the runtime rather than exiting 1.
        ran: list[str] = []
        monkeypatch.setattr("castor.main.main", lambda: ran.append(sys.argv[2]))
        args = argparse.Namespace(
            config=path, manifest=None, simulate=False, behavior=None, dashboard=False
        )
        assert cli.cmd_run(args) is None
        assert ran == [path]

        # 3. The manifest carries a body: a driver comes back, connected.
        config = load_config(path)
        assert config["drivers"], "no drivers block => registry.get_driver returns None"
        config["drivers"][0]["socket"] = sock
        driver = get_driver(config)
        try:
            assert driver is not None, "the manifest yielded no driver"
            assert driver._mode == "hardware"
        finally:
            driver.close()
    finally:
        server.close()


def test_the_manifest_records_that_health_answered(monkeypatch, tmp_path):
    from microduck_wire_fixtures import WireRobotd

    sock = str(tmp_path / "robotd.sock")
    server = WireRobotd(sock)
    try:
        path = _duck_manifest(monkeypatch, tmp_path, sock)
    finally:
        server.close()
    text = open(path).read()
    assert "`robot.health` answered during setup" in text
    assert "did not answer" not in text


# ── Never claim a duck is ready on the strength of an SSH login ───────────────


def _unreachable_duck(monkeypatch, tmp_path):
    cand = DuckCandidate(host="duck.local", source="hostname", user="radxa")
    cand.is_duck = True
    cand.ssh_auth = True
    cand.ssh_open = True
    cand.in_robot_group = True
    monkeypatch.setattr(md, "discover", lambda **k: [cand])
    monkeypatch.setattr(md, "verify", lambda c, **k: c)
    monkeypatch.setattr(
        md,
        "health",
        lambda **k: {"ok": False, "mode": "mock", "error": "could not reach robotd"},
    )
    monkeypatch.setattr(md, "config_dir", lambda: tmp_path)


def test_a_duck_that_did_not_answer_is_never_called_ready(monkeypatch, tmp_path, capsys):
    from castor import cli

    _unreachable_duck(monkeypatch, tmp_path)
    monkeypatch.setattr("castor.auth.check_provider_ready", lambda *a, **k: True)

    assert cli.cmd_duck(_DuckArgs()) == 0
    out = capsys.readouterr().out
    assert "Duck ready" not in out
    assert "Ready." not in out
    assert "did not answer" in out
    assert "castor duck health" in out
    # The manifest is still written, and it says so about itself.
    assert "**`robot.health` did not answer" in (tmp_path / "duck.ROBOT.md").read_text()


def test_an_unverified_duck_reports_verified_false(monkeypatch, tmp_path, capsys):
    import json

    from castor import cli

    _unreachable_duck(monkeypatch, tmp_path)

    class Args(_DuckArgs):
        json = True

    assert cli.cmd_duck(Args()) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verified"] is False


def test_start_is_refused_for_a_duck_that_did_not_answer(monkeypatch, tmp_path, capsys):
    from castor import cli

    _unreachable_duck(monkeypatch, tmp_path)
    called: list = []
    monkeypatch.setattr(cli, "cmd_run", lambda a: called.append(a))

    class Args(_DuckArgs):
        start = True

    assert cli.cmd_duck(Args()) == 0
    assert called == [], "must not start a runtime for a robot that never answered"
    assert "--start skipped" in capsys.readouterr().out


# ── Discovery, for a duck that actually exists ────────────────────────────────


def test_the_stock_hostname_is_tried_first():
    """`radxa-zero3` is the name on every board flashed from one image.

    Pollen's docs/design/webrtc-console.md:27. The old ladder was
    duck.local, duck-01.local, microduck.local, duckling.local — none of which a
    stock duck answers to.
    """
    assert md.CANDIDATE_HOSTNAMES[0] == "radxa-zero3.local"
    assert "duck-01.local" in md.CANDIDATE_HOSTNAMES


def test_duck_hostname_prefix_matching():
    """configd names a duck `duck-<4 hex>` off the SoC serial (identity.rs:84).

    65536 possibilities: it cannot be probed, so it is recognised instead.
    """
    assert md.looks_like_duck_hostname("duck-c51b")
    assert md.looks_like_duck_hostname("duck-c51b.local")
    assert md.looks_like_duck_hostname("radxa-zero3")
    assert md.looks_like_duck_hostname("microduck.local")
    assert not md.looks_like_duck_hostname("printer")
    assert not md.looks_like_duck_hostname("ducky-mcduckface"), "prefix, not substring"


def test_mdns_is_not_advertised_as_a_way_to_find_a_stock_duck():
    """A stock duck publishes no mDNS: no Avahi file, no zeroconf, anywhere.

    Pollen's own scripts route around name resolution (scripts/dev-push.sh:80,
    scripts/provision-board.sh:8). Browsing still finds a duck someone has added a
    record to, so it lives in the --deep path where its 2 s wait is deliberate.
    """
    assert "mdns" not in md.ALL_METHODS
    assert "mdns" not in md.FAST_METHODS
    assert "mdns" in md.DEEP_METHODS


def test_deep_still_browses_mdns(monkeypatch):
    """--deep is where the mDNS wait is paid for on purpose."""
    browsed: list[bool] = []

    monkeypatch.setattr(md, "local_socket_present", lambda *a, **k: False)
    monkeypatch.setattr(md, "probe_hostnames", lambda **k: [])
    monkeypatch.setattr(md, "duckctl_ip", lambda **k: None)
    monkeypatch.setattr(md, "arp_neighbours", lambda: [])
    monkeypatch.setattr(
        md, "mdns_hosts", lambda **k: (browsed.append(True), ["duck-c51b.local"])[1]
    )

    assert md.discover() == [], "the default ladder must not browse"
    assert browsed == []

    found = md.discover(deep=True)
    assert browsed == [True]
    assert [c.host for c in found] == ["duck-c51b.local"]
    assert found[0].source == "mdns"


def test_not_found_message_leads_with_host(monkeypatch, capsys):
    from castor import cli

    monkeypatch.setattr(md, "discover", lambda **k: [])

    assert cli.cmd_duck(_DuckArgs()) == 1
    out = capsys.readouterr().out
    head = out.split("nothing found.")[1]
    # --host is the first thing offered, before duckctl or --deep.
    assert head.index("--host") < head.index("--deep")
    assert head.index("--host") < head.index("duckctl")
    assert "publishes no" in head and "mDNS" in head


# ── castor duck do uses the duck's own brain ─────────────────────────────────


def test_duck_do_uses_the_manifest_brain_not_gemini(monkeypatch, tmp_path):
    """get_provider({}) means provider="google" (castor/registry.py:176).

    So `castor duck --brain ollama`, the profile's own agent.provider and whoever
    `castor login` signed in were all ignored, and the duck was planned by Gemini
    whatever the operator chose.
    """
    from castor import cli

    monkeypatch.setattr(md, "config_dir", lambda: tmp_path)
    cfg = md.build_config(host="d.local", agent={"provider": "anthropic", "model": "claude-x"})
    md.write_manifest(cfg, robot_name="duck", path=tmp_path / "duck.ROBOT.md")

    resolved = cli._duck_agent_config(robot_name="duck")
    assert resolved["provider"] == "anthropic"
    assert resolved["model"] == "claude-x"


def test_duck_do_brain_flag_wins(monkeypatch, tmp_path):
    from castor import cli

    monkeypatch.setattr(md, "config_dir", lambda: tmp_path)
    assert cli._duck_agent_config(brain="ollama:gemma3:4b") == {
        "provider": "ollama",
        "model": "gemma3:4b",
    }


def test_duck_do_falls_back_to_the_packaged_profile(monkeypatch, tmp_path):
    """With no manifest written yet, the profile's brain is still not google."""
    from castor import cli

    monkeypatch.setattr(md, "config_dir", lambda: tmp_path)
    resolved = cli._duck_agent_config()
    profile_agent = (md.load_profile() or {}).get("agent") or {}
    assert resolved.get("provider") == profile_agent.get("provider")


def test_duck_plan_hands_the_agent_config_to_get_provider(monkeypatch):
    from castor import cli
    from castor.microduck_choreography import DuckChoreographer

    seen: list[dict] = []

    class _Thought:
        text = '[{"move": "nod"}]'

    class _Provider:
        def think(self, prompt):
            return _Thought()

    def _get_provider(config):
        seen.append(dict(config))
        return _Provider()

    monkeypatch.setattr("castor.providers.get_provider", _get_provider)

    duck = DuckChoreographer(object())
    plan = cli._duck_plan_from_request(
        duck, "wander about", lambda *a, **k: None, agent={"provider": "ollama"}
    )
    assert plan == [{"move": "nod"}]
    assert seen == [{"provider": "ollama"}]


# ── castor duck test says what speed it is asking for ────────────────────────


class _TestArgs:
    duck_cmd = "test"
    host = "duck.local"
    user = "radxa"
    yes = True
    json = False
    speed = None


def _wired_test_command(monkeypatch, tmp_path, sock: str):
    """Point `castor duck test` at a wire-shaped robotd, with no sleeping."""
    import castor.drivers.microduck_driver as drv

    cand = DuckCandidate(host="duck.local", source="manual", user="radxa")
    cand.is_duck = True
    cand.ssh_auth = True
    cand.in_robot_group = True
    monkeypatch.setattr(md, "verify", lambda c, **k: cand)

    original_init = drv.MicroduckDriver.__init__

    def _init_with_fake_socket(self, config):
        config = dict(config)
        config["transport"] = "unix"
        config["socket"] = sock
        original_init(self, config)

    monkeypatch.setattr(drv.MicroduckDriver, "__init__", _init_with_fake_socket)
    monkeypatch.setattr("time.sleep", lambda s: None)


def test_duck_test_prints_the_envelope_and_pollens_own_limit(monkeypatch, tmp_path, capsys):
    """"it walks" printed, and the duck barely moved, and nothing said why.

    driver.move(0.3) times max_vx 0.2 is 0.06 m/s — a fifth of what padd allows
    (padd/src/main.rs:139-163). The number is a deliberate envelope; the silence
    about it is what lost the demo.
    """
    from castor import cli
    from microduck_wire_fixtures import WireRobotd

    sock = str(tmp_path / "robotd.sock")
    server = WireRobotd(sock)
    try:
        _wired_test_command(monkeypatch, tmp_path, sock)
        assert cli.cmd_duck(_TestArgs()) == 0
    finally:
        server.close()

    out = capsys.readouterr().out
    assert "0.06 m/s" in out
    assert "0.2 m/s envelope" in out
    assert "gamepad's own limit is 0.3" in out


def test_duck_test_speed_flag_changes_what_goes_on_the_wire(monkeypatch, tmp_path, capsys):
    from castor import cli
    from microduck_wire_fixtures import WireRobotd

    sock = str(tmp_path / "robotd.sock")
    server = WireRobotd(sock)
    try:
        _wired_test_command(monkeypatch, tmp_path, sock)

        class Args(_TestArgs):
            speed = 0.2

        assert cli.cmd_duck(Args()) == 0
        vxs = [m["params"]["vx"] for m in server.notifications_for("robot.move")]
        assert vxs, "nothing was sent"
        # The last one is stop()'s zeroing move; the walk itself is the max.
        assert max(vxs) == pytest.approx(0.2)
        assert vxs[-1] == pytest.approx(0.0)
    finally:
        server.close()
    assert "0.2 m/s" in capsys.readouterr().out


def test_duck_test_clamps_speed_to_the_envelope_and_says_so(monkeypatch, tmp_path, capsys):
    from castor import cli
    from microduck_wire_fixtures import WireRobotd

    sock = str(tmp_path / "robotd.sock")
    server = WireRobotd(sock)
    try:
        _wired_test_command(monkeypatch, tmp_path, sock)

        class Args(_TestArgs):
            speed = 5.0

        assert cli.cmd_duck(Args()) == 0
        vxs = [m["params"]["vx"] for m in server.notifications_for("robot.move")]
        assert max(vxs) == pytest.approx(0.2), "clamped to the envelope, not 5 m/s"
    finally:
        server.close()
    assert "outside this duck's envelope" in capsys.readouterr().out

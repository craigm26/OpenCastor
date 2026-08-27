"""Tests for zero-config Microduck setup (`castor duck`).

Covers discovery, verification, config generation and the wiring into
hardware detection, the setup catalog and the CLI.  No network, no hardware:
every subprocess and socket call is monkeypatched.
"""

from __future__ import annotations

import yaml

import castor.microduck as md
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
        lambda **k: {"ok": True, "loop": {"hz": 49.8}, "battery": {"percent": 64}, "policies": []},
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
    written = tmp_path / "quacky.rcan.yaml"
    assert written.exists()
    cfg = yaml.safe_load(written.read_text())
    assert cfg["drivers"][0]["ssh_host"] == "duck.local"
    assert "castor duck health" in capsys.readouterr().out


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

    cfg = yaml.safe_load((tmp_path / "fake-duck.rcan.yaml").read_text())
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
    monkeypatch.setattr(md, "health", lambda **k: {"ok": True, "loop": {}, "battery": {}})
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
    cfg = yaml.safe_load((tmp_path / "duck.rcan.yaml").read_text())
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
    assert payload["config"].endswith("duck.rcan.yaml")

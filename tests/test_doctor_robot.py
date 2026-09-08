"""castor doctor, the robot half — the checks that decide whether a car can move.

Everything here runs against FAKES: fake unit files in a tmp dir, a fake
`gateway-policy.env`, a fake /dev listing, a fake port probe, a fake mDNS
browse, a fake /sys/class/video4linux tree. No hardware is touched and no
service is read, which is the point: a health check that can only be tested on
the one Pi that has the fault is a health check nobody maintains.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from castor.doctor import (
    DoctorReport,
    _check_console_port,
    _check_drive_mode,
    _check_gaps,
    _check_gateway_policy,
    _check_gateway_port,
    _check_i2c_bus,
    _check_mdns_advertiser,
    _check_robot_home,
    _check_runtime_port,
    _check_usb_power_budget,
    CheckResult,
    RobotUnits,
    discover_robots,
    read_env_file,
    resolve_robot,
    run_robot_checks,
    usb_video_devices,
)

# ── fixtures: a robot home and the units `castor up` writes ──────────────────

POLICY_SIMULATED = """\
# Operator policy. Quoted because the tier bindings contain '|'.
ROBOT_MD_TOOL_ALLOWLIST="drive.set,drive.stop,status.report"
ROBOT_MD_TOOL_MIN_TIER="drive.set:actuate|commission"
#OPENCASTOR_DRIVE=pca9685
#OPENCASTOR_DRIVE_I2C_ADDRESS=0x40
"""

POLICY_REAL = """\
ROBOT_MD_TOOL_ALLOWLIST="drive.set,drive.stop,status.report"
OPENCASTOR_DRIVE=pca9685
OPENCASTOR_DRIVE_I2C_BUS=1
OPENCASTOR_DRIVE_I2C_ADDRESS=0x40
OPENCASTOR_DRIVE_THROTTLE_CHANNEL=1
OPENCASTOR_DRIVE_STEERING_CHANNEL=0
"""


def _write_units(unit_dir: Path, home: Path, name="car", gw=8080, runtime=8081, console=8082):
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / f"{name}-gateway.service").write_text(
        "[Unit]\nDescription=robot-md-gateway\n\n[Service]\n"
        f"EnvironmentFile={home}/gateway-attestation.env\n"
        f"EnvironmentFile={home}/gateway-policy.env\n"
        f"Environment=ROBOT_MANIFEST={home}/ROBOT.md\n"
        f"ExecStart=/venv/bin/robot-md-gateway serve --host 0.0.0.0 --port {gw} "
        f"--bearers {home}/bearers.yaml --robot-md {home}/ROBOT.md\n"
    )
    (unit_dir / f"{name}-castor.service").write_text(
        "[Unit]\n\n[Service]\n"
        f"Environment=ROBOT_HOME={home}\nEnvironment=ROBOT_RUNTIME_PORT={runtime}\n"
    )
    (unit_dir / f"{name}-console.service").write_text(
        "[Unit]\n\n[Service]\n"
        f"Environment=ROBOT_HOME={home}\nEnvironment=CONSOLE_PORT={console}\n"
    )


@pytest.fixture
def robot_home(tmp_path):
    home = tmp_path / "car"
    home.mkdir()
    (home / "ROBOT.md").write_text("# ROBOT.md\n")
    (home / "bearers.yaml").write_text("bearers: []\n")
    (home / "gateway-policy.env").write_text(POLICY_SIMULATED)
    return home


@pytest.fixture
def unit_dir(tmp_path, robot_home):
    d = tmp_path / "systemd"
    _write_units(d, robot_home)
    return d


# ── read_env_file ────────────────────────────────────────────────────────────


def test_read_env_file_strips_quotes_comments_and_export(tmp_path):
    f = tmp_path / "policy.env"
    f.write_text(
        '# a comment\nROBOT_MD_TOOL_MIN_TIER="drive.set:actuate|commission"\n'
        "\nexport OPENCASTOR_DRIVE=pca9685\nnot an assignment\n"
    )
    env = read_env_file(f)
    assert env["ROBOT_MD_TOOL_MIN_TIER"] == "drive.set:actuate|commission"
    assert env["OPENCASTOR_DRIVE"] == "pca9685"
    assert "# a comment" not in env


def test_read_env_file_missing_file_is_empty(tmp_path):
    assert read_env_file(tmp_path / "nope.env") == {}


# ── discovery from unit files ────────────────────────────────────────────────


def test_discover_robots_reads_ports_home_and_policy(unit_dir, robot_home):
    (robot,) = discover_robots(unit_dir)
    assert robot.name == "car"
    assert robot.home == robot_home
    assert robot.gateway_port == 8080
    assert robot.runtime_port == 8081
    assert robot.console_port == 8082
    assert robot.policy_env == robot_home / "gateway-policy.env"


def test_discover_robots_accepts_a_hand_written_runtime_port_name(tmp_path, robot_home):
    """The rover names it ROVER_RUNTIME_PORT. Matching on the suffix reads it."""
    d = tmp_path / "systemd"
    _write_units(d, robot_home, name="rover")
    (d / "rover-castor.service").write_text(
        f"[Service]\nEnvironment=ROBOT_HOME={robot_home}\nEnvironment=ROVER_RUNTIME_PORT=8003\n"
    )
    (robot,) = discover_robots(d)
    assert robot.runtime_port == 8003


def test_discover_robots_reads_console_port_from_an_environment_file(tmp_path, robot_home):
    d = tmp_path / "systemd"
    _write_units(d, robot_home)
    (robot_home / "console.env").write_text("CONSOLE_PORT=8099\n")
    (d / "car-console.service").write_text(
        f"[Service]\nEnvironment=ROBOT_HOME={robot_home}\n"
        f"EnvironmentFile={robot_home}/console.env\n"
    )
    (robot,) = discover_robots(d)
    assert robot.console_port == 8099


def test_discover_robots_empty_when_no_unit_dir(tmp_path):
    assert discover_robots(tmp_path / "absent") == []


def test_resolve_robot_prefers_explicit_home(tmp_path, unit_dir, robot_home):
    other = tmp_path / "elsewhere"
    robot = resolve_robot(home=str(other), unit_dir=unit_dir)
    assert robot.home == other


def test_resolve_robot_uses_robot_home_env(unit_dir, robot_home):
    robot = resolve_robot(unit_dir=unit_dir, env={"ROBOT_HOME": str(robot_home)})
    assert robot.home == robot_home
    assert robot.gateway_port == 8080  # matched the unit, so the port came with it


def test_resolve_robot_single_unit_needs_no_argument(unit_dir, robot_home):
    assert resolve_robot(unit_dir=unit_dir, env={}).home == robot_home


# ── the port the gateway actually binds ──────────────────────────────────────


def test_gateway_port_probes_the_unit_port_not_18789(unit_dir):
    probed = []

    def probe(port, *a, **k):
        probed.append(port)
        return True

    robot = resolve_robot(unit_dir=unit_dir, env={})
    result = _check_gateway_port(robot, probe=probe)
    assert probed == [8080]
    assert 18789 not in probed
    assert result.status == "ok"


def test_gateway_port_refused_is_blocking_with_a_restart_command(unit_dir):
    robot = resolve_robot(unit_dir=unit_dir, env={})
    result = _check_gateway_port(robot, probe=lambda *a, **k: False)
    assert result.status == "fail"
    assert result.blocking
    assert "systemctl --user restart car-gateway" in result.fix
    assert "castor run --config" not in result.fix  # the old, wrong fix line


def test_runtime_and_console_ports_warn_but_do_not_block(unit_dir):
    robot = resolve_robot(unit_dir=unit_dir, env={})
    for check in (_check_runtime_port, _check_console_port):
        result = check(robot, probe=lambda *a, **k: False)
        assert result.status == "warn"
        assert not result.blocking
        assert result.fix


def test_ports_skip_cleanly_when_there_is_no_unit():
    bare = RobotUnits(name="x")
    assert _check_gateway_port(bare, probe=lambda *a, **k: True).status == "skip"


# ── robot home + policy ──────────────────────────────────────────────────────


def test_robot_home_ok(unit_dir, robot_home):
    result = _check_robot_home(resolve_robot(unit_dir=unit_dir, env={}))
    assert result.status == "ok"
    assert str(robot_home) in result.detail


def test_robot_home_missing_is_blocking():
    result = _check_robot_home(None)
    assert result.status == "fail" and result.blocking
    assert result.fix == "castor up"


def test_robot_home_without_a_signed_manifest_is_blocking(tmp_path):
    home = tmp_path / "half"
    home.mkdir()
    result = _check_robot_home(RobotUnits(name="half", home=home))
    assert result.status == "fail" and result.blocking
    assert "ROBOT.md" in result.detail


def test_robot_home_mentions_other_robots_on_the_host(unit_dir, robot_home):
    result = _check_robot_home(RobotUnits(name="car", home=robot_home), others=2)
    assert "--home" in result.detail


def test_gateway_policy_missing_is_blocking(tmp_path):
    robot = RobotUnits(name="car", home=tmp_path / "gone")
    result = _check_gateway_policy(robot)
    assert result.status == "fail" and result.blocking


def test_gateway_policy_without_drive_set_is_blocking(tmp_path):
    home = tmp_path / "car"
    home.mkdir()
    (home / "gateway-policy.env").write_text('ROBOT_MD_TOOL_ALLOWLIST="status.report"\n')
    result = _check_gateway_policy(RobotUnits(name="car", home=home))
    assert result.status == "fail" and result.blocking
    assert "drive.set" in result.detail


def test_gateway_policy_ok(robot_home):
    result = _check_gateway_policy(RobotUnits(name="car", home=robot_home))
    assert result.status == "ok"


# ── /dev/i2c-1 ───────────────────────────────────────────────────────────────


def test_i2c_bus_present():
    result = _check_i2c_bus({}, exists=lambda p: True)
    assert result.status == "ok"


def test_i2c_bus_absent_prints_the_raspi_config_command():
    result = _check_i2c_bus({}, exists=lambda p: False)
    assert result.status == "warn"  # simulated wheels: not yet blocking
    assert result.fix == "sudo raspi-config nonint do_i2c 0 && sudo reboot"


def test_i2c_bus_absent_is_blocking_once_real_wheels_are_configured():
    result = _check_i2c_bus({"OPENCASTOR_DRIVE": "pca9685"}, exists=lambda p: False)
    assert result.status == "fail" and result.blocking
    assert "raspi-config" in result.fix


# ── "your wheels are simulated" ──────────────────────────────────────────────


def _robot(home):
    return RobotUnits(name="car", home=home, policy_env=home / "gateway-policy.env")


def test_chip_present_but_drive_unset_says_the_wheels_are_simulated(robot_home):
    policy = read_env_file(robot_home / "gateway-policy.env")
    assert "OPENCASTOR_DRIVE" not in policy  # it ships commented out
    result = _check_drive_mode(_robot(robot_home), policy, addresses={0x40})
    assert result.status == "fail" and result.blocking
    assert "YOUR WHEELS ARE SIMULATED" in result.detail
    assert "OPENCASTOR_DRIVE=pca9685" in result.fix
    assert "WHEELS OFF THE GROUND" in result.fix


def test_drive_simulated_and_no_chip_is_only_a_warning(robot_home):
    policy = {"OPENCASTOR_DRIVE": "simulated"}
    result = _check_drive_mode(_robot(robot_home), policy, addresses=set())
    assert result.status == "warn"
    assert not result.blocking


def test_real_drive_with_no_chip_is_blocking(robot_home):
    policy = {"OPENCASTOR_DRIVE": "pca9685"}
    result = _check_drive_mode(_robot(robot_home), policy, addresses=set())
    assert result.status == "fail" and result.blocking
    assert "will not start" in result.detail


def test_real_drive_with_the_chip_is_ok(robot_home):
    policy = read_env_file_text(POLICY_REAL)
    result = _check_drive_mode(_robot(robot_home), policy, addresses={0x40})
    assert result.status == "ok"
    assert "real wheels" in result.detail


def test_template_channel_defaults_are_flagged_as_reversed(robot_home):
    policy = {
        "OPENCASTOR_DRIVE": "pca9685",
        "OPENCASTOR_DRIVE_THROTTLE_CHANNEL": "0",
        "OPENCASTOR_DRIVE_STEERING_CHANNEL": "1",
    }
    result = _check_drive_mode(_robot(robot_home), policy, addresses={0x40})
    assert result.status == "warn"
    assert "reversed" in result.detail


def test_drive_mode_honours_a_shifted_address(robot_home):
    policy = {"OPENCASTOR_DRIVE": "pca9685", "OPENCASTOR_DRIVE_I2C_ADDRESS": "0x41"}
    assert _check_drive_mode(_robot(robot_home), policy, addresses={0x41}).status == "ok"
    assert _check_drive_mode(_robot(robot_home), policy, addresses={0x40}).status == "fail"


def read_env_file_text(text, tmp=[]):
    """Round-trip a policy body through the real parser."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
        f.write(text)
        name = f.name  # read AFTER the close, or the buffer is still in memory
    tmp.append(name)
    return read_env_file(name)


# ── mDNS ─────────────────────────────────────────────────────────────────────


def test_mdns_browses_the_type_the_app_looks_for():
    seen = {}

    def browse(service_type, timeout):
        seen["type"] = service_type
        return ["car._opencastor._tcp.local."]

    result = _check_mdns_advertiser(browse=browse)
    assert seen["type"] == "_opencastor._tcp.local."
    assert result.status == "ok"


def test_mdns_silence_is_a_warning_that_names_the_qr():
    def browse(service_type, timeout):
        return []

    result = _check_mdns_advertiser(browse=browse)
    assert result.status == "warn"
    assert "castor pair" in result.fix


def test_mdns_skips_cleanly_without_zeroconf():
    def browse(service_type, timeout):
        raise ImportError("no zeroconf")

    result = _check_mdns_advertiser(browse=browse)
    assert result.status == "skip"
    assert result.fix == "pip install zeroconf"


def test_mdns_skips_cleanly_when_the_host_has_no_multicast():
    def browse(service_type, timeout):
        raise OSError("no route")

    assert _check_mdns_advertiser(browse=browse).status == "skip"


# ── USB current budget (the carbot lesson) ───────────────────────────────────


def test_usb_camera_without_the_current_flag_is_a_failure():
    result = _check_usb_power_budget(config_text="dtparam=audio=on\n", cameras=["video0"])
    assert result.status == "fail"
    assert "usb_max_current_enable=1" in result.fix


def test_usb_current_flag_set_is_ok():
    result = _check_usb_power_budget(
        config_text="usb_max_current_enable=1\n", cameras=["video0"]
    )
    assert result.status == "ok"


def test_no_usb_camera_yet_is_only_a_warning():
    result = _check_usb_power_budget(config_text="", cameras=[])
    assert result.status == "warn"


def test_usb_video_devices_ignores_a_csi_camera(tmp_path):
    root = tmp_path / "video4linux"
    (root / "video19").mkdir(parents=True)
    codec = tmp_path / "1000800000.codec"
    codec.mkdir()
    (root / "video19" / "device").symlink_to(codec)
    assert usb_video_devices(root) == []


def test_usb_video_devices_finds_a_webcam(tmp_path):
    root = tmp_path / "video4linux"
    (root / "video0").mkdir(parents=True)
    usb = tmp_path / "sys" / "devices" / "platform" / "usb1" / "1-1" / "1-1:1.0"
    usb.mkdir(parents=True)
    (root / "video0" / "device").symlink_to(usb)
    assert usb_video_devices(root) == ["video0"]


def test_usb_video_devices_missing_tree_is_empty(tmp_path):
    assert usb_video_devices(tmp_path / "absent") == []


# ── the gaps rail ────────────────────────────────────────────────────────────


class _Gap:
    def __init__(self, gid, kind, evidence, suggestion):
        self.id, self.kind = gid, kind
        self.evidence, self.suggestion = evidence, suggestion


def test_doctor_calls_gaps_and_a_missing_actuator_is_blocking(robot_home):
    def collect(*, home):
        assert home == robot_home
        return [
            _Gap(
                "actuator.rc-car.missing",
                "missing-package",
                "gateway actuator resolved to 'noop'",
                "pip install rc-car-actuator && castor up",
            )
        ]

    (result,) = _check_gaps(_robot(robot_home), collect=collect)
    assert result.status == "fail" and result.blocking
    assert result.fix == "pip install rc-car-actuator && castor up"


def test_an_unclaimed_peripheral_is_a_warning_not_a_block(robot_home):
    def collect(*, home):
        return [_Gap("peripheral.imu.x", "unclaimed-peripheral", "an IMU", "declare it")]

    (result,) = _check_gaps(_robot(robot_home), collect=collect)
    assert result.status == "warn" and not result.blocking


def test_no_gaps_is_one_ok_row(robot_home):
    (result,) = _check_gaps(_robot(robot_home), collect=lambda *, home: [])
    assert result.status == "ok"


def test_gap_scan_failure_never_breaks_doctor(robot_home):
    def collect(*, home):
        raise RuntimeError("no i2c bus")

    (result,) = _check_gaps(_robot(robot_home), collect=collect)
    assert result.status == "skip"


# ── the exit code ────────────────────────────────────────────────────────────


def test_report_exit_code_is_zero_without_blocking_failures():
    report = DoctorReport(
        checks=[
            CheckResult("a", "ok"),
            CheckResult("b", "warn"),
            CheckResult("c", "fail"),  # not blocking: a host gripe, not a still car
        ]
    )
    assert report.can_move
    assert report.exit_code == 0


def test_report_exit_code_is_one_when_the_car_cannot_move():
    report = DoctorReport(
        checks=[CheckResult("a", "ok"), CheckResult("wheels", "fail", blocking=True)]
    )
    assert not report.can_move
    assert report.exit_code == 1
    assert [c.name for c in report.blocking_failures] == ["wheels"]


def test_a_blocking_check_that_passes_does_not_set_the_exit_code():
    report = DoctorReport(checks=[CheckResult("wheels", "ok", blocking=True)])
    assert report.exit_code == 0


# ── end to end, all fakes ────────────────────────────────────────────────────


def test_run_robot_checks_on_a_simulated_car_reports_it_cannot_move(
    monkeypatch, unit_dir, robot_home
):
    import castor.doctor as doctor

    monkeypatch.setattr(doctor, "_probe_port", lambda *a, **k: True)
    monkeypatch.setattr(doctor, "i2c_addresses", lambda *a, **k: {0x40})
    monkeypatch.setattr(doctor, "_browse_mdns", lambda *a, **k: [])
    monkeypatch.setattr(doctor, "_read_boot_config", lambda *a, **k: (None, ""))
    monkeypatch.setattr(doctor, "usb_video_devices", lambda *a, **k: [])
    monkeypatch.setattr(doctor, "_check_gaps", lambda robot: [CheckResult("gaps", "ok")])

    report = doctor.run_robot_checks(home=str(robot_home), unit_dir=unit_dir)
    names = {c.name: c for c in report.checks}
    assert names["Gateway port"].status == "ok"
    assert names["Drive mode"].status == "fail"
    assert not report.can_move
    assert report.exit_code == 1
    assert "YOUR WHEELS ARE SIMULATED" in names["Drive mode"].detail


def test_run_robot_checks_on_a_real_car_can_move(monkeypatch, unit_dir, robot_home):
    import castor.doctor as doctor

    (robot_home / "gateway-policy.env").write_text(POLICY_REAL)
    monkeypatch.setattr(doctor, "_probe_port", lambda *a, **k: True)
    monkeypatch.setattr(doctor, "i2c_addresses", lambda *a, **k: {0x40})
    monkeypatch.setattr(doctor, "_browse_mdns", lambda *a, **k: ["car._opencastor._tcp.local."])
    monkeypatch.setattr(doctor, "_read_boot_config", lambda *a, **k: (None, "usb_max_current_enable=1"))
    monkeypatch.setattr(doctor, "usb_video_devices", lambda *a, **k: [])
    monkeypatch.setattr(doctor, "_check_gaps", lambda robot: [CheckResult("gaps", "ok")])
    monkeypatch.setattr(Path, "exists", lambda self: True)

    report = doctor.run_robot_checks(home=str(robot_home), unit_dir=unit_dir)
    assert report.can_move, [c for c in report.checks if c.status == "fail"]
    assert report.exit_code == 0


def test_run_robot_checks_never_raises_on_a_bare_host(tmp_path, monkeypatch):
    import castor.doctor as doctor

    monkeypatch.setattr(doctor, "_browse_mdns", lambda *a, **k: [])
    monkeypatch.setattr(doctor, "_read_boot_config", lambda *a, **k: (None, ""))
    monkeypatch.setattr(doctor, "usb_video_devices", lambda *a, **k: [])
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    report = doctor.run_robot_checks(unit_dir=tmp_path / "none")
    assert report.exit_code == 1  # no robot at all is exactly "it cannot move"
    assert any(c.name == "Robot home" and c.blocking for c in report.checks)


# ── the legacy entry point no longer probes a port nothing serves ────────────


def test_legacy_check_gateway_no_longer_defaults_to_18789(monkeypatch, unit_dir, robot_home):
    """`_check_gateway()` with no argument asks the robot, not a constant."""
    import castor.doctor as doctor

    probed = []
    monkeypatch.setattr(doctor, "_probe_port", lambda port, *a, **k: probed.append(port) or True)
    monkeypatch.setattr(
        doctor,
        "resolve_robot",
        lambda *a, **k: RobotUnits(name="car", home=robot_home, gateway_port=8080),
    )
    result = doctor._check_gateway()
    assert probed == [8080]
    assert result.status == "ok"


def test_legacy_check_gateway_with_an_explicit_port_still_works(monkeypatch):
    import castor.doctor as doctor

    monkeypatch.setattr(doctor, "_probe_port", lambda *a, **k: False)
    result = doctor._check_gateway(port=9999)
    assert result.status == "warn"
    assert "castor run --config" not in result.fix  # the old, wrong fix line

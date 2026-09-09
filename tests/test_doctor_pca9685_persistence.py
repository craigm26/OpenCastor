"""`castor doctor` — "PCA9685 configuration persists".

The 2026-09-08 bench found a chip that answered `i2cdetect` at 0x40, accepted
every write, logged "ESC arming: held neutral", reported
`hardware_reachable: true`, passed 27/27 smoke checks, and sat at power-on
defaults the whole time because it reset itself within a second of being
configured. Nothing in the stack read it back.

Everything here runs against a FAKE BUS — a dict of registers — because the
only host that ever had the fault is one Pi with one broken board, and a check
that can only be tested there is a check nobody maintains. doctor never writes
to the chip, so the fake bus refuses writes.
"""

from __future__ import annotations

import pytest

from castor.doctor import (
    PCA9685_POWER_ON_MODE1,
    PCA9685_POWER_ON_PRESCALE,
    RobotUnits,
    _check_pca9685_persistence,
    pca9685_prescale,
)

POLICY_REAL = {
    "OPENCASTOR_DRIVE": "pca9685",
    "OPENCASTOR_DRIVE_I2C_BUS": "1",
    "OPENCASTOR_DRIVE_I2C_ADDRESS": "0x40",
    "OPENCASTOR_DRIVE_THROTTLE_CHANNEL": "1",
    "OPENCASTOR_DRIVE_STEERING_CHANNEL": "0",
}


class FakeBus:
    """A PCA9685's two interesting registers, and a bus that never accepts a write."""

    def __init__(self, mode1: int, prescale: int, raises: Exception | None = None):
        self.registers = {0x00: mode1, 0xFE: prescale}
        self.raises = raises
        self.reads: list[tuple[int, int]] = []

    def read(self, address: int, bus: int = 1) -> tuple[int, int]:
        if self.raises is not None:
            raise self.raises
        self.reads.append((address, bus))
        return self.registers[0x00], self.registers[0xFE]

    def write(self, *a, **k):  # pragma: no cover — the point is that it is never called
        raise AssertionError("doctor must never write to the PCA9685")


CONFIGURED = dict(mode1=0x20, prescale=121)  # awake, auto-increment, 50 Hz
POWER_ON = dict(mode1=PCA9685_POWER_ON_MODE1, prescale=PCA9685_POWER_ON_PRESCALE)


@pytest.fixture
def robot(tmp_path):
    home = tmp_path / "car"
    home.mkdir()
    (home / "gateway-policy.env").write_text("OPENCASTOR_DRIVE=pca9685\n")
    return RobotUnits(name="car", home=home, gateway_port=8080)


def _run(robot, bus, policy=None, addresses=(0x40,), gateway_up=True):
    return _check_pca9685_persistence(
        robot,
        dict(policy or POLICY_REAL),
        addresses=set(addresses),
        read_registers=bus.read,
        probe=lambda port: gateway_up,
    )


# ── the arithmetic ───────────────────────────────────────────────────────────


def test_prescale_is_the_datasheet_number_the_driver_would_write():
    assert pca9685_prescale(50, 25_000_000) == 121
    assert pca9685_prescale(200, 25_000_000) == 30  # the power-on default, ~197 Hz
    assert pca9685_prescale(60, 25_000_000) == 101


def test_prescale_stays_inside_the_legal_range():
    assert pca9685_prescale(4000, 25_000_000) == 3
    assert pca9685_prescale(1, 25_000_000) == 255


# ── the five states ──────────────────────────────────────────────────────────


def test_a_chip_that_holds_its_configuration_is_ok(robot):
    result = _run(robot, FakeBus(**CONFIGURED))
    assert result.status == "ok"
    assert not result.blocking
    assert "prescale=121" in result.detail
    assert "awake" in result.detail


def test_a_sleeping_chip_is_a_blocking_failure(robot):
    """The bench fault exactly: MODE1 0x11, prescale 30, everything else green."""
    result = _run(robot, FakeBus(**POWER_ON))
    assert result.status == "fail"
    assert result.blocking
    assert "PCA9685 configuration does not persist (MODE1=0x11, prescale=30)" in result.detail
    assert "measure VCC and V+ with the ESC arming" in result.detail
    assert "docs/hardware/pca9685-bringup.md step 3b" in result.detail


def test_an_awake_chip_at_the_wrong_frame_rate_is_a_blocking_failure(robot):
    """Awake but at 197 Hz, which a servo reads as permanent full travel."""
    result = _run(robot, FakeBus(mode1=0x20, prescale=30))
    assert result.status == "fail"
    assert result.blocking
    assert "prescale=30" in result.detail
    assert "expected prescale 121" in result.detail


def test_unconfigured_with_no_gateway_running_is_only_a_warning(robot):
    result = _run(robot, FakeBus(**POWER_ON), gateway_up=False)
    assert result.status == "warn"
    assert not result.blocking
    assert "unconfigured (no driver has written it yet)" in result.detail


def test_no_chip_on_the_bus_is_a_skip(robot):
    """Drive mode already reports this, and reports it as blocking."""
    result = _run(robot, FakeBus(**CONFIGURED), addresses=())
    assert result.status == "skip"
    assert not result.blocking
    assert "0x40" in result.detail


# ── the edges ────────────────────────────────────────────────────────────────


def test_a_bus_error_is_the_symptom_and_blocks(robot):
    """`[Errno 121] Remote I/O error` three seconds after bring-up, on the bench."""
    result = _run(robot, FakeBus(0, 0, raises=OSError("[Errno 121] Remote I/O error")))
    assert result.status == "fail"
    assert result.blocking
    assert "Errno 121" in result.detail
    assert "docs/hardware/pca9685-bringup.md step 3b" in result.detail


def test_missing_smbus2_skips_rather_than_failing(robot):
    result = _run(robot, FakeBus(0, 0, raises=ImportError("No module named 'smbus2'")))
    assert result.status == "skip"
    assert "smbus2" in result.detail


def test_a_simulated_car_is_skipped(robot):
    result = _run(robot, FakeBus(**POWER_ON), policy={"OPENCASTOR_DRIVE": "simulated"})
    assert result.status == "skip"
    assert "does not name the PCA9685" in result.detail


def test_a_maestro_car_is_skipped(robot):
    result = _run(robot, FakeBus(**POWER_ON), policy={"OPENCASTOR_DRIVE": "maestro"})
    assert result.status == "skip"


def test_no_robot_home_is_a_skip():
    result = _check_pca9685_persistence(None, {}, addresses={0x40})
    assert result.status == "skip"


def test_a_calibrated_oscillator_moves_the_expected_prescale(robot):
    """Step 4's OSCILLATOR_HZ calibration must not turn into a false failure."""
    policy = dict(POLICY_REAL, OPENCASTOR_DRIVE_OSCILLATOR_HZ="26000000")
    expected = pca9685_prescale(50, 26_000_000)
    assert expected != 121
    assert _run(robot, FakeBus(mode1=0x20, prescale=expected), policy=policy).status == "ok"
    assert _run(robot, FakeBus(mode1=0x20, prescale=121), policy=policy).status == "fail"


def test_doctor_never_writes_to_the_chip(robot):
    bus = FakeBus(**CONFIGURED)
    _run(robot, bus)
    assert bus.registers == {0x00: 0x20, 0xFE: 121}
    assert bus.reads == [(0x40, 1)]


def test_a_non_default_address_and_bus_are_honoured(robot):
    policy = dict(
        POLICY_REAL, OPENCASTOR_DRIVE_I2C_ADDRESS="0x41", OPENCASTOR_DRIVE_I2C_BUS="3"
    )
    bus = FakeBus(**CONFIGURED)
    result = _run(robot, bus, policy=policy, addresses=(0x41,))
    assert result.status == "ok"
    assert bus.reads == [(0x41, 3)]


def test_the_check_is_registered_in_the_car_section(monkeypatch, tmp_path):
    """One row, from `run_robot_checks`, on a host with no bus at all."""
    from castor import doctor

    monkeypatch.setattr(doctor, "discover_robots", lambda unit_dir=None: [])
    report = doctor.run_robot_checks(unit_dir=tmp_path)
    names = [c.name for c in report.checks]
    assert names.count("PCA9685 configuration persists") == 1

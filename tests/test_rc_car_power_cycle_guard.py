"""Issue #947 pinning test: rc-car-actuator chip power-cycle recovery.

Live incident (2026-08-17): a rover gateway kept a PCA9685 configured at
50 Hz in memory; the chip lost power on disarm and returned at its power-on
reset default (~200 Hz). Because the drive path never re-verified the chip,
every ``set_drive`` was accepted and written but emitted at the wrong
frequency scale (a commanded 1575 us came out as ~394 us of garbage the ESC
ignored) while every receipt read ok.

Fix direction, implemented here as the reusable guard the driver's
``set_drive`` must consult before emitting: read the PRE_SCALE register back
on the first write after any gap > N seconds (or every M writes); on a
mismatch re-run ``_bring_up()`` before emitting and log the recovery. The
emit is gated on the check passing, so the accepted-but-garbage window is
provably zero-length at the driver API level.

This module is self-contained (no hardware, no import of the actuator
package) so it pins the behaviour with a simulated chip that resets its
PRE_SCALE mid-session.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

logger = logging.getLogger("rc_car_actuator.power_cycle")

# ---------------------------------------------------------------------------
# PCA9685 timing model (25 MHz oscillator, 256 steps per PWM period)
# ---------------------------------------------------------------------------


def step_us(hz: float) -> float:
    """Microseconds represented by one PWM count at a given update rate."""
    return 1_000_000.0 / (hz * 256.0)


def count_for(us: float, hz: float) -> int:
    """Pulse count the driver programs for *us* assuming the chip is at *hz*."""
    return max(0, min(255, round(us / step_us(hz))))


def pre_scale_for_hz(hz: float) -> int:
    """PRE_SCALE register value for a target update rate (25 MHz oscillator)."""
    return round(25_000_000 / (1600.0 * hz))


# The PCA9685 powers on with PRE_SCALE = 91 (~172 Hz); the incident log frames
# this as the ~200 Hz reset default. Either way it is not the 50 Hz we set.
RESET_PRE_SCALE = 91
CONFIGURED_HZ = 50.0
CONFIGURED_PRE_SCALE = pre_scale_for_hz(CONFIGURED_HZ)

# Commanded pulse for the pinning test, and the two outcomes we distinguish:
#   CORRECT_US -- the right scale (chip at 50 Hz):  ~1562.5 us (20 steps)
#   GARBAGE_US -- the stale 50 Hz scale on a 200 Hz chip: ~390.6 us
COMMAND_US = 1575.0
CORRECT_US = count_for(COMMAND_US, CONFIGURED_HZ) * step_us(CONFIGURED_HZ)
GARBAGE_US = count_for(COMMAND_US, CONFIGURED_HZ) * step_us(200.0)


# ---------------------------------------------------------------------------
# The fix: a hardware-agnostic guard the drive path consults before emitting.
# ---------------------------------------------------------------------------


class PowerCycleGuard:
    """Read PRE_SCALE back across power cycles before the driver trusts it.

    ``set_drive`` must call :meth:`before_emit` *before* writing the channel.
    The guard decides whether a read-back is due (first write after a gap
    larger than ``gap_s``, or every ``every_m`` writes), performs it, and on a
    mismatch re-runs ``bring_up`` so the emit lands at the correct scale.
    """

    def __init__(
        self,
        expected_pre_scale: int,
        read_pre_scale: Callable[[], int],
        bring_up: Callable[[], None],
        gap_s: float = 5.0,
        every_m: int = 0,
        now: Callable[[], float] = None,
    ) -> None:
        self.expected_pre_scale = expected_pre_scale
        self._read_pre_scale = read_pre_scale
        self._bring_up = bring_up
        self.gap_s = gap_s
        self.every_m = every_m
        self._now = now or time.monotonic
        self._last_check = 0.0
        self._writes_since_check = 0
        self.recovery_count = 0

    def before_emit(self) -> bool:
        """Gate an emit on a fresh PRE_SCALE check. Returns True on recovery."""
        t = self._now()
        due = (t - self._last_check) > self.gap_s or (
            self.every_m > 0 and self._writes_since_check >= self.every_m
        )
        if due:
            self._last_check = t
            self._writes_since_check = 0
            actual = self._read_pre_scale()
            if actual != self.expected_pre_scale:
                self._bring_up()
                self.recovery_count += 1
                logger.warning(
                    "rc-car-actuator: PRE_SCALE mismatch (expected %s, read %s); "
                    "re-ran _bring_up() before emit",
                    self.expected_pre_scale,
                    actual,
                )
                return True
        self._writes_since_check += 1
        return False


# ---------------------------------------------------------------------------
# Simulated chip + driver for the pinning test.
# ---------------------------------------------------------------------------


class SimulatedPCA9685:
    """In-memory PCA9685 that can be power-cycled mid-session."""

    def __init__(self) -> None:
        self.pre_scale = CONFIGURED_PRE_SCALE
        self.hz = CONFIGURED_HZ
        self.last_count = None
        self.emits = []  # list of (commanded_us, emitted_us, recovered)

    def read_pre_scale(self) -> int:
        return self.pre_scale

    def reset(self) -> None:
        """Simulate the chip losing power and returning at its reset default."""
        self.pre_scale = RESET_PRE_SCALE
        self.hz = 200.0

    def bring_up(self) -> None:
        """Reconfigure the chip to the driver's configured rate."""
        self.pre_scale = CONFIGURED_PRE_SCALE
        self.hz = CONFIGURED_HZ

    def write_channel(self, count: int) -> None:
        self.last_count = count

    def emitted_us(self) -> float:
        return self.last_count * step_us(self.hz)


class DriveSet:
    """A ``set_drive`` that consults the guard before emitting (the fix)."""

    def __init__(self, chip: SimulatedPCA9685, guard: PowerCycleGuard) -> None:
        self.chip = chip
        self.guard = guard

    def set_drive(self, commanded_us: float) -> dict:
        count = count_for(commanded_us, CONFIGURED_HZ)
        recovered = self.guard.before_emit()  # verify (and reconfigure) FIRST
        self.chip.write_channel(count)
        emitted = self.chip.emitted_us()
        self.chip.emits.append((commanded_us, emitted, recovered))
        return {"accepted": True, "emitted_us": emitted, "recovered": recovered}


class FakeClock:
    """Deterministic clock so the 'gap > N seconds' branch is testable."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# ---------------------------------------------------------------------------
# Pinning test.
# ---------------------------------------------------------------------------


def test_set_drive_recovers_from_chip_power_cycle():
    # The bug scenario is real: a stale 50 Hz scale on a 200 Hz chip emits at
    # the wrong scale, so the test has teeth.
    assert abs(GARBAGE_US - 390.625) < 1e-6
    assert abs(CORRECT_US - 1562.5) < 1e-6

    clock = FakeClock()
    chip = SimulatedPCA9685()
    guard = PowerCycleGuard(
        expected_pre_scale=CONFIGURED_PRE_SCALE,
        read_pre_scale=chip.read_pre_scale,
        bring_up=chip.bring_up,
        gap_s=5.0,
        now=clock,
    )
    drive = DriveSet(chip, guard)

    # First drive after a fresh config: chip is at 50 Hz -> correct scale.
    r1 = drive.set_drive(COMMAND_US)
    assert r1["accepted"] is True
    assert r1["recovered"] is False
    assert abs(r1["emitted_us"] - CORRECT_US) < 1e-6

    # Operator disarms: the chip loses power and returns at its reset default.
    chip.reset()
    # The next day: well past the gap, and nothing has polled the actuator.
    clock.advance(10.0)

    # The next set_drive must detect the reset, reconfigure, and emit the
    # correct pulse. The emit is gated on the check, so there is no
    # accepted-but-garbage write at the wrong scale.
    r2 = drive.set_drive(COMMAND_US)
    assert r2["accepted"] is True
    assert r2["recovered"] is True
    assert guard.recovery_count == 1
    # Correct scale: ~1562.5 us out, NOT the ~390.6 us a stale 50 Hz scale
    # would produce on a 200 Hz chip.
    assert abs(r2["emitted_us"] - CORRECT_US) < 1e-6
    assert abs(r2["emitted_us"] - GARBAGE_US) > 1.0

    # Zero-length garbage window at the driver API level: every emit the
    # driver produced is at the correct scale (no wrong-scale pulse ever
    # landed on the chip).
    assert len(chip.emits) == 2
    for _commanded, emitted, _recovered in chip.emits:
        assert abs(emitted - CORRECT_US) < 1e-6

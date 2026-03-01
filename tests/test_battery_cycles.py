"""Tests for BatteryDriver.get_charge_cycles() (issue #300)."""

from __future__ import annotations

import time

import pytest

import castor.drivers.battery_driver as _mod
from castor.drivers.battery_driver import BatteryDriver


def _reset_singleton():
    _mod._singleton = None


@pytest.fixture()
def driver(tmp_path, monkeypatch):
    """BatteryDriver in mock mode with an isolated history DB."""
    _reset_singleton()
    db = str(tmp_path / "battery_hist.db")
    monkeypatch.setenv("BATTERY_HISTORY_DB", db)
    monkeypatch.setenv("BATTERY_MOCK", "true")
    d = BatteryDriver({})
    yield d
    d.close()
    _reset_singleton()


@pytest.fixture()
def driver_no_history(tmp_path, monkeypatch):
    """BatteryDriver with history disabled (BATTERY_HISTORY_DB=none)."""
    _reset_singleton()
    monkeypatch.setenv("BATTERY_HISTORY_DB", "none")
    monkeypatch.setenv("BATTERY_MOCK", "true")
    d = BatteryDriver({})
    yield d
    d.close()
    _reset_singleton()


def _insert_rows(driver: BatteryDriver, rows: list[tuple]) -> None:
    """Insert (ts, voltage_v, current_ma, power_mw, percent, mode) rows directly."""
    driver._ensure_history_db()
    for row in rows:
        driver._history_con.execute(
            "INSERT INTO readings (ts, voltage_v, current_ma, power_mw, percent, mode) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            row,
        )
    driver._history_con.commit()


# ── Empty / minimal history ───────────────────────────────────────────────────


def test_empty_history_returns_empty_list(driver):
    """No rows → empty list."""
    cycles = driver.get_charge_cycles()
    assert cycles == []


def test_single_reading_returns_empty_list(driver):
    """One row is not enough to detect a cycle boundary."""
    now = time.time()
    _insert_rows(driver, [(now, 12.0, 500.0, 6000.0, 70.0, "mock")])
    cycles = driver.get_charge_cycles()
    assert cycles == []


# ── Single-type runs ──────────────────────────────────────────────────────────


def test_all_positive_current_one_charge_cycle(driver):
    """Consecutive charging readings → exactly one 'charge' cycle."""
    now = time.time()
    rows = [(now + i, 12.0, 600.0, 7200.0, 60.0 + i, "mock") for i in range(5)]
    _insert_rows(driver, rows)
    cycles = driver.get_charge_cycles()
    assert len(cycles) == 1
    assert cycles[0]["type"] == "charge"


def test_all_negative_current_one_discharge_cycle(driver):
    """Consecutive discharging readings → exactly one 'discharge' cycle."""
    now = time.time()
    rows = [(now + i, 12.0, -600.0, 7200.0, 80.0 - i * 2, "mock") for i in range(4)]
    _insert_rows(driver, rows)
    cycles = driver.get_charge_cycles()
    assert len(cycles) == 1
    assert cycles[0]["type"] == "discharge"


def test_all_idle_current_one_idle_cycle(driver):
    """Near-zero current (within ±50 mA) → exactly one 'idle' cycle."""
    now = time.time()
    rows = [(now + i, 12.0, 10.0, 120.0, 75.0, "mock") for i in range(4)]
    _insert_rows(driver, rows)
    cycles = driver.get_charge_cycles()
    assert len(cycles) == 1
    assert cycles[0]["type"] == "idle"


# ── Sign-change transitions ────────────────────────────────────────────────────


def test_charge_then_discharge_two_cycles(driver):
    """Positive then negative current → two cycles."""
    now = time.time()
    rows = [
        (now + 0, 12.0, 600.0, 7200.0, 50.0, "mock"),
        (now + 1, 12.0, 600.0, 7200.0, 55.0, "mock"),
        (now + 2, 12.0, -600.0, 7200.0, 55.0, "mock"),
        (now + 3, 12.0, -600.0, 7200.0, 50.0, "mock"),
    ]
    _insert_rows(driver, rows)
    cycles = driver.get_charge_cycles()
    assert len(cycles) == 2
    assert cycles[0]["type"] == "charge"
    assert cycles[1]["type"] == "discharge"


def test_multiple_transitions(driver):
    """charge → discharge → idle → charge should yield 4 cycles."""
    now = time.time()
    rows = [
        (now + 0, 12.0, 600.0, 7200.0, 40.0, "mock"),  # charge
        (now + 1, 12.0, 600.0, 7200.0, 45.0, "mock"),  # charge
        (now + 2, 12.0, -600.0, 7200.0, 44.0, "mock"),  # discharge
        (now + 3, 12.0, -600.0, 7200.0, 40.0, "mock"),  # discharge
        (now + 4, 12.0, 5.0, 60.0, 40.0, "mock"),  # idle
        (now + 5, 12.0, 5.0, 60.0, 40.0, "mock"),  # idle
        (now + 6, 12.0, 700.0, 8400.0, 42.0, "mock"),  # charge
        (now + 7, 12.0, 700.0, 8400.0, 46.0, "mock"),  # charge
    ]
    _insert_rows(driver, rows)
    cycles = driver.get_charge_cycles()
    assert len(cycles) == 4
    types = [c["type"] for c in cycles]
    assert types == ["charge", "discharge", "idle", "charge"]


# ── delta_percent and duration_s ──────────────────────────────────────────────


def test_delta_percent_is_computed_correctly(driver):
    """delta_percent = percent at end_ts minus percent at start_ts."""
    now = time.time()
    rows = [
        (now + 0, 12.0, 600.0, 7200.0, 50.0, "mock"),
        (now + 1, 12.0, 600.0, 7200.0, 60.0, "mock"),
        (now + 2, 12.0, 600.0, 7200.0, 70.0, "mock"),
    ]
    _insert_rows(driver, rows)
    cycles = driver.get_charge_cycles()
    assert len(cycles) == 1
    assert cycles[0]["delta_percent"] == pytest.approx(20.0, abs=0.01)


def test_duration_s_equals_end_minus_start(driver):
    """duration_s must equal end_ts - start_ts."""
    now = time.time()
    gap = 5.0
    rows = [
        (now + 0, 12.0, 600.0, 7200.0, 50.0, "mock"),
        (now + gap, 12.0, 600.0, 7200.0, 55.0, "mock"),
    ]
    _insert_rows(driver, rows)
    cycles = driver.get_charge_cycles()
    assert len(cycles) == 1
    assert cycles[0]["duration_s"] == pytest.approx(gap, abs=0.001)
    assert cycles[0]["end_ts"] - cycles[0]["start_ts"] == pytest.approx(gap, abs=0.001)


def test_cycle_type_is_valid_string(driver):
    """Every returned cycle must have type in {'charge','discharge','idle'}."""
    now = time.time()
    rows = [
        (now + 0, 12.0, 600.0, 7200.0, 50.0, "mock"),
        (now + 1, 12.0, -600.0, 7200.0, 48.0, "mock"),
        (now + 2, 12.0, 10.0, 120.0, 48.0, "mock"),
    ]
    _insert_rows(driver, rows)
    cycles = driver.get_charge_cycles()
    valid = {"charge", "discharge", "idle"}
    for c in cycles:
        assert c["type"] in valid


# ── Boundary current values ────────────────────────────────────────────────────


def test_boundary_current_exactly_50ma_is_idle(driver):
    """current_ma == 50.0 is within the ±50 mA idle band."""
    now = time.time()
    rows = [
        (now + 0, 12.0, 50.0, 600.0, 60.0, "mock"),
        (now + 1, 12.0, 50.0, 600.0, 60.0, "mock"),
    ]
    _insert_rows(driver, rows)
    cycles = driver.get_charge_cycles()
    assert len(cycles) == 1
    assert cycles[0]["type"] == "idle"


def test_boundary_current_51ma_is_charge(driver):
    """current_ma == 51.0 exceeds the idle band → charge."""
    now = time.time()
    rows = [
        (now + 0, 12.0, 51.0, 612.0, 60.0, "mock"),
        (now + 1, 12.0, 51.0, 612.0, 61.0, "mock"),
    ]
    _insert_rows(driver, rows)
    cycles = driver.get_charge_cycles()
    assert len(cycles) == 1
    assert cycles[0]["type"] == "charge"


# ── History disabled ──────────────────────────────────────────────────────────


def test_disabled_history_returns_empty_list(driver_no_history):
    """When BATTERY_HISTORY_DB=none, get_charge_cycles() must return []."""
    cycles = driver_no_history.get_charge_cycles()
    assert cycles == []


# ── Error robustness ──────────────────────────────────────────────────────────


def test_db_error_does_not_raise(tmp_path, monkeypatch):
    """A corrupt / broken DB must not propagate an exception from get_charge_cycles()."""
    _reset_singleton()
    db = str(tmp_path / "battery_hist.db")
    monkeypatch.setenv("BATTERY_HISTORY_DB", db)
    monkeypatch.setenv("BATTERY_MOCK", "true")
    d = BatteryDriver({})
    try:
        # Force the connection to close so the next query raises
        d._ensure_history_db()
        d._history_con.close()
        # Deliberately corrupt the connection reference to trigger an error
        d._history_con = None
        d._history_db_path = "/dev/null/nonexistent.db"
        result = d.get_charge_cycles()
        assert result == []
    finally:
        d.close()
        _reset_singleton()

"""Tests for BatteryDriver charge/discharge history (issue #288)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

import castor.drivers.battery_driver as _mod
from castor.drivers.battery_driver import BatteryDriver


def _reset_singleton():
    _mod._singleton = None


@pytest.fixture()
def driver(tmp_path, monkeypatch):
    """BatteryDriver in mock mode with isolated history DB."""
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
    """BatteryDriver with history disabled."""
    _reset_singleton()
    monkeypatch.setenv("BATTERY_HISTORY_DB", "none")
    monkeypatch.setenv("BATTERY_MOCK", "true")
    d = BatteryDriver({})
    yield d
    d.close()
    _reset_singleton()


# ── History logging ───────────────────────────────────────────────────────────


def test_read_logs_to_history(driver):
    driver.read()
    hist = driver.get_history()
    assert len(hist) == 1


def test_multiple_reads_logged(driver):
    for _ in range(5):
        driver.read()
    hist = driver.get_history()
    assert len(hist) == 5


def test_history_row_has_required_keys(driver):
    driver.read()
    hist = driver.get_history()
    row = hist[0]
    assert "ts" in row
    assert "voltage_v" in row
    assert "current_ma" in row
    assert "power_mw" in row
    assert "percent" in row
    assert "mode" in row


def test_history_newest_first(driver):
    driver.read()
    time.sleep(0.01)
    driver.read()
    hist = driver.get_history()
    assert len(hist) == 2
    assert hist[0]["ts"] >= hist[1]["ts"]


def test_history_limit_respected(driver):
    for _ in range(20):
        driver.read()
    hist = driver.get_history(limit=5)
    assert len(hist) <= 5


def test_history_window_filters_old_rows(driver):
    """Readings older than window_s should not appear."""
    driver.read()
    # Request only readings from last 0.001 seconds — all readings should be excluded
    # because they were logged at least a little time ago
    time.sleep(0.05)
    hist = driver.get_history(window_s=0.001)
    assert len(hist) == 0


# ── History disabled ──────────────────────────────────────────────────────────


def test_history_disabled_get_returns_empty(driver_no_history):
    driver_no_history.read()
    hist = driver_no_history.get_history()
    assert hist == []


def test_read_succeeds_when_history_disabled(driver_no_history):
    """Even with history disabled, read() should still return data."""
    data = driver_no_history.read()
    assert "voltage_v" in data


# ── get_history params ────────────────────────────────────────────────────────


def test_get_history_default_returns_last_24h(driver):
    for _ in range(3):
        driver.read()
    hist = driver.get_history()  # default window_s=86400
    assert len(hist) == 3


def test_get_history_default_limit(driver):
    for _ in range(5):
        driver.read()
    hist = driver.get_history()
    assert len(hist) <= 1000  # default limit is 1000


# ── Robustness ────────────────────────────────────────────────────────────────


def test_history_does_not_crash_read_on_db_error(monkeypatch, tmp_path):
    """A DB error during history logging must not propagate from read()."""
    _reset_singleton()
    # Use an invalid path (a directory) so sqlite3.connect raises
    bad_dir = str(tmp_path / "not_a_file")
    import os

    os.makedirs(bad_dir, exist_ok=True)
    monkeypatch.setenv("BATTERY_HISTORY_DB", bad_dir)
    monkeypatch.setenv("BATTERY_MOCK", "true")
    d = BatteryDriver({})
    # read() must not raise even when history DB path is invalid
    data = d.read()
    assert "voltage_v" in data
    d.close()
    _reset_singleton()


# ── API endpoint ──────────────────────────────────────────────────────────────


@pytest.fixture()
def client():
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    import castor.api as _api

    mock_battery = MagicMock()
    mock_battery.get_history.return_value = [
        {
            "ts": 1000.0,
            "voltage_v": 12.1,
            "current_ma": 500.0,
            "power_mw": 6050.0,
            "percent": 70.0,
            "mode": "mock",
        }
    ]

    with patch("castor.drivers.battery_driver.get_battery", return_value=mock_battery):
        yield TestClient(_api.app)


def test_api_battery_history(client):
    resp = client.get("/api/battery/history")
    assert resp.status_code == 200
    data = resp.json()
    assert "readings" in data
    assert "count" in data
    assert "window_s" in data


def test_api_battery_history_with_params(client):
    resp = client.get("/api/battery/history", params={"window_s": 3600, "limit": 100})
    assert resp.status_code == 200
    data = resp.json()
    assert data["window_s"] == 3600.0

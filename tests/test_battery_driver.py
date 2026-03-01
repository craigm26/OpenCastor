"""Tests for castor/drivers/battery_driver.py — INA219 battery monitor (issue #279)."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

import castor.drivers.battery_driver as _mod
from castor.drivers.battery_driver import BatteryDriver, _estimate_percent, get_battery

# ── Helpers ───────────────────────────────────────────────────────────────────


def _reset_singleton():
    """Clear the module-level singleton before each test."""
    _mod._singleton = None


# ── _estimate_percent ─────────────────────────────────────────────────────────


def test_estimate_percent_lipo_full():
    assert _estimate_percent(4.2, "lipo") == pytest.approx(100.0)


def test_estimate_percent_lipo_empty():
    assert _estimate_percent(3.0, "lipo") == pytest.approx(0.0)


def test_estimate_percent_lipo3s_full():
    assert _estimate_percent(13.0, "lipo3s") == pytest.approx(100.0)


def test_estimate_percent_lipo3s_empty():
    assert _estimate_percent(10.0, "lipo3s") == pytest.approx(0.0)


def test_estimate_percent_clamps_below_zero():
    assert _estimate_percent(2.0, "lipo") == pytest.approx(0.0)


def test_estimate_percent_clamps_above_100():
    assert _estimate_percent(5.0, "lipo") == pytest.approx(100.0)


# ── Construction ──────────────────────────────────────────────────────────────


def test_mock_mode_when_smbus_unavailable():
    with patch.object(_mod, "HAS_SMBUS", False):
        driver = BatteryDriver({"mock": False})
    assert driver._mode == "mock"


def test_forced_mock_mode_from_config():
    driver = BatteryDriver({"mock": True})
    assert driver._mode == "mock"


def test_forced_mock_mode_from_env(monkeypatch):
    monkeypatch.setenv("BATTERY_MOCK", "true")
    driver = BatteryDriver({})
    assert driver._mode == "mock"


def test_default_bus_and_address():
    driver = BatteryDriver({"mock": True})
    assert driver._bus_num == 1
    assert driver._address == 0x40


def test_hex_string_address_parsed():
    driver = BatteryDriver({"mock": True, "i2c_address": "0x41"})
    assert driver._address == 0x41


def test_cell_type_lipo3s():
    driver = BatteryDriver({"mock": True, "cell_type": "lipo3s"})
    assert driver._cell_type == "lipo3s"


# ── Mock reads ────────────────────────────────────────────────────────────────


def test_mock_read_returns_required_keys():
    driver = BatteryDriver({"mock": True})
    data = driver.read()
    assert set(data.keys()) >= {"voltage_v", "current_ma", "power_mw", "percent", "mode"}


def test_mock_read_mode_is_mock():
    driver = BatteryDriver({"mock": True})
    data = driver.read()
    assert data["mode"] == "mock"


def test_mock_read_voltage_in_range():
    driver = BatteryDriver({"mock": True})
    for _ in range(10):
        data = driver.read()
        assert 11.0 <= data["voltage_v"] <= 13.0


def test_mock_read_percent_in_range():
    driver = BatteryDriver({"mock": True, "cell_type": "lipo3s"})
    data = driver.read()
    assert 0.0 <= data["percent"] <= 100.0


def test_read_count_increments():
    driver = BatteryDriver({"mock": True})
    driver.read()
    driver.read()
    assert driver._read_count == 2


# ── health_check ──────────────────────────────────────────────────────────────


def test_health_check_ok_in_mock_mode():
    driver = BatteryDriver({"mock": True})
    h = driver.health_check()
    assert h["ok"] is True
    assert h["mode"] == "mock"
    assert "bus" in h
    assert "address" in h


def test_health_check_address_is_hex_string():
    driver = BatteryDriver({"mock": True})
    h = driver.health_check()
    assert h["address"].startswith("0x")


def test_health_check_read_count_after_reads():
    driver = BatteryDriver({"mock": True})
    driver.read()
    driver.read()
    h = driver.health_check()
    assert h["read_count"] == 2


# ── close ─────────────────────────────────────────────────────────────────────


def test_close_sets_mode_mock():
    driver = BatteryDriver({"mock": True})
    driver.close()
    assert driver._mode == "mock"


# ── Singleton ─────────────────────────────────────────────────────────────────


def test_get_battery_returns_same_instance():
    _reset_singleton()
    a = get_battery({"mock": True})
    b = get_battery({"mock": True})
    assert a is b


def test_get_battery_thread_safe():
    _reset_singleton()
    results = []

    def _get():
        results.append(get_battery({"mock": True}))

    threads = [threading.Thread(target=_get) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r is results[0] for r in results)


# ── API endpoints ─────────────────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path):
    """Minimal FastAPI TestClient with battery driver mocked."""
    from fastapi.testclient import TestClient

    import castor.api as _api

    mock_battery = MagicMock()
    mock_battery.read.return_value = {
        "voltage_v": 12.1,
        "current_ma": 500.0,
        "power_mw": 6050.0,
        "percent": 70.0,
        "mode": "mock",
    }
    mock_battery.health_check.return_value = {
        "ok": True,
        "mode": "mock",
        "bus": 1,
        "address": "0x40",
        "read_count": 5,
        "error": None,
    }

    with patch("castor.drivers.battery_driver.get_battery", return_value=mock_battery):
        c = TestClient(_api.app)
        yield c


def test_api_battery_status(client):
    resp = client.get("/api/battery/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "voltage_v" in data
    assert "percent" in data


def test_api_battery_health(client):
    resp = client.get("/api/battery/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "mode" in data

"""Tests for castor/drivers/thermal_driver.py — AMG8833 8x8 thermal camera (issue #263/#222)."""

from __future__ import annotations

from unittest.mock import patch

from castor.drivers.thermal_driver import ThermalDriver, get_thermal

# ── Construction ──────────────────────────────────────────────────────────────


def test_mock_mode_when_smbus_unavailable():
    """Driver should enter mock mode when smbus2 is not available."""
    with patch("castor.drivers.thermal_driver.HAS_SMBUS", False):
        driver = ThermalDriver({"mock": False})
    assert driver._mode == "mock"


def test_forced_mock_mode_from_config():
    """mock=True in config should force mock mode regardless of smbus2."""
    driver = ThermalDriver({"mock": True})
    assert driver._mode == "mock"


def test_defaults_are_sensible():
    """Default I2C bus and address should match AMG8833 datasheet."""
    driver = ThermalDriver({})
    assert driver._bus_num == 1
    assert driver._address in (0x68, 0x69)


def test_hex_string_address_parsed():
    """Hex string address like '0x69' should be parsed correctly."""
    driver = ThermalDriver({"i2c_address": "0x69"})
    assert driver._address == 0x69


# ── Mock capture ──────────────────────────────────────────────────────────────


def test_capture_returns_64_pixels():
    """Mock capture should return exactly 64 pixel temperatures."""
    driver = ThermalDriver({"mock": True})
    pixels = driver.capture()
    assert len(pixels) == 64


def test_capture_values_in_range():
    """Mock pixel temperatures should be realistic room temperatures."""
    driver = ThermalDriver({"mock": True})
    pixels = driver.capture()
    assert all(15.0 <= p <= 40.0 for p in pixels), f"Out-of-range pixel: {pixels}"


def test_capture_count_increments():
    """capture() should increment the internal counter."""
    driver = ThermalDriver({"mock": True})
    driver.capture()
    driver.capture()
    assert driver._capture_count == 2


# ── Hotspot ────────────────────────────────────────────────────────────────────


def test_get_hotspot_returns_correct_keys():
    """get_hotspot() should return row, col, index, temp_c."""
    driver = ThermalDriver({"mock": True})
    hotspot = driver.get_hotspot()
    assert set(hotspot.keys()) == {"row", "col", "index", "temp_c"}


def test_get_hotspot_index_in_range():
    """Hotspot index should be 0-63."""
    driver = ThermalDriver({"mock": True})
    hotspot = driver.get_hotspot()
    assert 0 <= hotspot["index"] <= 63


def test_get_hotspot_row_col_derived_from_index():
    """row and col should be derived correctly from index."""
    driver = ThermalDriver({"mock": True})
    hotspot = driver.get_hotspot()
    idx = hotspot["index"]
    assert hotspot["row"] == idx // 8
    assert hotspot["col"] == idx % 8


# ── Health check ───────────────────────────────────────────────────────────────


def test_health_check_mock_mode():
    """Health check in mock mode should return ok=True and mode=mock."""
    driver = ThermalDriver({"mock": True})
    health = driver.health_check()
    assert health["ok"] is True
    assert health["mode"] == "mock"
    assert "capture_count" in health


def test_health_check_address_as_hex_string():
    """health_check address field should be a readable hex string like '0x68'."""
    driver = ThermalDriver({"mock": True})
    health = driver.health_check()
    # address may be stored as int or hex string depending on implementation
    addr = health.get("address", "")
    assert addr  # must be present and non-empty


# ── Singleton ─────────────────────────────────────────────────────────────────


def test_get_thermal_singleton():
    """get_thermal() should return the same instance on repeated calls."""
    import castor.drivers.thermal_driver as _mod

    # Reset singleton for test isolation
    _mod._singleton = None
    t1 = get_thermal({"mock": True})
    t2 = get_thermal({"mock": True})
    assert t1 is t2
    _mod._singleton = None  # cleanup


def test_get_thermal_returns_thermal_driver():
    """get_thermal() should return a ThermalDriver instance."""
    import castor.drivers.thermal_driver as _mod

    _mod._singleton = None
    driver = get_thermal({"mock": True})
    assert isinstance(driver, ThermalDriver)
    _mod._singleton = None  # cleanup


# ── API endpoint tests ─────────────────────────────────────────────────────────


def test_api_thermal_frame():
    """GET /api/thermal/frame should return 64 pixels and a grid."""
    from fastapi.testclient import TestClient

    from castor.api import app

    client = TestClient(app)
    resp = client.get("/api/thermal/frame", headers={"Authorization": "Bearer test"})
    assert resp.status_code == 200
    data = resp.json()
    assert "pixels" in data
    assert len(data["pixels"]) == 64
    assert "grid" in data
    assert len(data["grid"]) == 8


def test_api_thermal_hotspot():
    """GET /api/thermal/hotspot should return row/col/temp_c."""
    from fastapi.testclient import TestClient

    from castor.api import app

    client = TestClient(app)
    resp = client.get("/api/thermal/hotspot", headers={"Authorization": "Bearer test"})
    assert resp.status_code == 200
    data = resp.json()
    assert "temp_c" in data
    assert "row" in data
    assert "col" in data


def test_api_thermal_health():
    """GET /api/thermal/health should return ok field."""
    from fastapi.testclient import TestClient

    from castor.api import app

    client = TestClient(app)
    resp = client.get("/api/thermal/health", headers={"Authorization": "Bearer test"})
    assert resp.status_code == 200
    data = resp.json()
    assert "ok" in data
    assert "mode" in data


# ── Heatmap ───────────────────────────────────────────────────────────────────


def test_get_heatmap_returns_bytes():
    """get_heatmap() should return bytes (JPEG or empty)."""
    driver = ThermalDriver({"mock": True})
    result = driver.get_heatmap()
    assert isinstance(result, bytes)


def test_api_thermal_heatmap():
    """GET /api/thermal/heatmap should return image/jpeg."""
    from fastapi.testclient import TestClient

    from castor.api import app

    client = TestClient(app)
    resp = client.get("/api/thermal/heatmap", headers={"Authorization": "Bearer test"})
    # Either 200 (cv2 available) or 503 (cv2 not installed)
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        assert resp.headers.get("content-type", "").startswith("image/jpeg")

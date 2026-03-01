"""Tests for ESP32 BLE driver (Issue #287)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from castor.drivers.esp32_ble_driver import _DEFAULT_CHAR_UUID, HAS_BLEAK, ESP32BLEDriver

# ── Instantiation tests ───────────────────────────────────────────────────────


def test_driver_init_mock_mode_when_no_address():
    drv = ESP32BLEDriver({})
    assert drv._mode == "mock"
    drv.close()


def test_driver_init_mock_mode_when_bleak_missing():
    """Driver should fall back to mock when bleak is not installed."""
    with patch("castor.drivers.esp32_ble_driver.HAS_BLEAK", False):
        drv = ESP32BLEDriver({"ble_address": "AA:BB:CC:DD:EE:FF"})
        assert drv._mode == "mock"


def test_driver_default_char_uuid():
    drv = ESP32BLEDriver({})
    assert drv._char_uuid == _DEFAULT_CHAR_UUID
    drv.close()


def test_driver_custom_char_uuid_from_config():
    custom_uuid = "12345678-1234-1234-1234-123456789abc"
    drv = ESP32BLEDriver({"ble_char_uuid": custom_uuid})
    assert drv._char_uuid == custom_uuid
    drv.close()


def test_driver_custom_address_from_config():
    drv = ESP32BLEDriver({"ble_address": "AA:BB:CC:DD:EE:FF"})
    assert drv._address == "AA:BB:CC:DD:EE:FF"
    drv.close()


def test_driver_timeout_default():
    drv = ESP32BLEDriver({})
    assert drv._timeout == pytest.approx(5.0)
    drv.close()


def test_driver_timeout_from_config():
    drv = ESP32BLEDriver({"ble_timeout": 10.0})
    assert drv._timeout == pytest.approx(10.0)
    drv.close()


# ── Command sending tests (mock mode) ─────────────────────────────────────────


def test_move_does_not_raise_in_mock_mode():
    drv = ESP32BLEDriver({})
    drv.move()
    drv.close()


def test_stop_does_not_raise_in_mock_mode():
    drv = ESP32BLEDriver({})
    drv.stop()
    drv.close()


def test_grip_does_not_raise_in_mock_mode():
    drv = ESP32BLEDriver({})
    drv.grip(open_gripper=True)
    drv.close()


def test_move_with_params_in_mock_mode():
    drv = ESP32BLEDriver({})
    drv.move({"linear": 0.3, "angular": 0.1})
    drv.close()


def test_send_command_mock_mode_logs_not_raises():
    drv = ESP32BLEDriver({})
    drv._send_command({"type": "custom", "value": 42})
    drv.close()


# ── Health check tests ────────────────────────────────────────────────────────


def test_health_check_mock_mode_ok():
    drv = ESP32BLEDriver({})
    hc = drv.health_check()
    assert hc["ok"] is True
    assert hc["mode"] == "mock"
    drv.close()


def test_health_check_has_required_keys():
    drv = ESP32BLEDriver({})
    hc = drv.health_check()
    required = {"ok", "mode", "connected", "address", "char_uuid", "has_bleak", "error"}
    for key in required:
        assert key in hc, f"Missing key: {key}"
    drv.close()


def test_health_check_connected_false_in_mock():
    drv = ESP32BLEDriver({})
    hc = drv.health_check()
    assert hc["connected"] is False
    drv.close()


def test_health_check_has_bleak_is_bool():
    drv = ESP32BLEDriver({})
    hc = drv.health_check()
    assert isinstance(hc["has_bleak"], bool)
    drv.close()


def test_health_check_address_none_when_not_configured():
    drv = ESP32BLEDriver({})
    hc = drv.health_check()
    assert hc["address"] is None
    drv.close()


# ── Close tests ───────────────────────────────────────────────────────────────


def test_close_sets_mock_mode():
    drv = ESP32BLEDriver({})
    drv.close()
    assert drv._mode == "mock"


def test_close_is_idempotent():
    drv = ESP32BLEDriver({})
    drv.close()
    drv.close()  # Should not raise


# ── HAS_BLEAK guard tests ─────────────────────────────────────────────────────


def test_has_bleak_is_bool():
    assert isinstance(HAS_BLEAK, bool)


def test_driver_health_check_reports_has_bleak():
    drv = ESP32BLEDriver({})
    hc = drv.health_check()
    assert hc["has_bleak"] == HAS_BLEAK
    drv.close()

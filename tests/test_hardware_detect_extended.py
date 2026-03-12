"""Extended tests for hardware_detect — issues #537–#541."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_hw_cache():
    from castor.hardware_detect import invalidate_hardware_cache, invalidate_usb_descriptors_cache

    invalidate_usb_descriptors_cache()
    invalidate_hardware_cache()
    yield
    invalidate_usb_descriptors_cache()
    invalidate_hardware_cache()


# ---------------------------------------------------------------------------
# #537 — Dynamixel U2D2 explicit VID/PID
# ---------------------------------------------------------------------------


def _make_port(vid: int, pid: int, device: str = "/dev/ttyUSB0", product: str = "") -> MagicMock:
    p = MagicMock()
    p.vid = vid
    p.pid = pid
    p.device = device
    p.description = ""
    p.product = product
    p.manufacturer = ""
    return p


def test_dynamixel_detects_u2d2_ftdi_ft232r():
    """VID 0x0403 / PID 0x6014 → U2D2 detected."""
    port = _make_port(0x0403, 0x6014, "/dev/ttyUSB0")
    with patch(
        "castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]
    ):
        from castor.hardware_detect import detect_dynamixel_usb
        result = detect_dynamixel_usb()
    assert len(result) == 1
    assert result[0]["vid_pid"] == "0403:6014"


def test_dynamixel_detects_u2d2h_ftdi_ft232h():
    """VID 0x0403 / PID 0x6015 → U2D2-H detected."""
    port = _make_port(0x0403, 0x6015, "/dev/ttyUSB1")
    with patch(
        "castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]
    ):
        from castor.hardware_detect import detect_dynamixel_usb
        result = detect_dynamixel_usb()
    assert len(result) == 1
    assert result[0]["vid_pid"] == "0403:6015"


def test_dynamixel_no_match_returns_empty():
    """Unknown VID/PID → no detection."""
    port = _make_port(0xDEAD, 0xBEEF)
    with patch(
        "castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]
    ):
        from castor.hardware_detect import detect_dynamixel_usb
        result = detect_dynamixel_usb()
    assert result == []


def test_suggest_preset_dynamixel_arm_for_u2d2():
    """suggest_preset returns 'dynamixel_arm' when U2D2 detected."""
    from castor.hardware_detect import suggest_preset
    hw = {
        "dynamixel": [{"port": "/dev/ttyUSB0", "vid_pid": "0403:6014", "model": "Dynamixel U2D2 (FT232R)"}],
        "i2c_devices": [], "usb_serial": [], "cameras": [], "platform": "generic",
        "usb_descriptors": [], "realsense": [], "oakd": [], "odrive": [], "vesc": [],
        "feetech": [], "arduino": [], "circuitpython": [], "lidar": [], "hailo": [],
        "coral": [], "imx500": [], "reachy": [],
    }
    preset, conf, reason = suggest_preset(hw)
    assert preset == "dynamixel_arm"
    assert conf == "high"

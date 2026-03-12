"""Extended tests for hardware_detect — issues #537–#541."""

from __future__ import annotations

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


def test_suggest_preset_non_u2d2_dynamixel_gives_koch():
    """suggest_preset returns 'lerobot/koch-arm' for non-U2D2 Dynamixel (e.g. OpenCR)."""
    from castor.hardware_detect import suggest_preset
    hw = {
        "dynamixel": [{"port": "/dev/ttyUSB0", "vid_pid": "0483:5740", "model": "Robotis OpenCR 1.0"}],
        "i2c_devices": [], "usb_serial": [], "cameras": [], "platform": "generic",
        "usb_descriptors": [], "realsense": [], "oakd": [], "odrive": [], "vesc": [],
        "feetech": [], "arduino": [], "circuitpython": [], "lidar": [], "hailo": [],
        "coral": [], "imx500": [], "reachy": [],
    }
    preset, conf, reason = suggest_preset(hw)
    assert preset == "lerobot/koch-arm"
    assert conf == "high"


# ---------------------------------------------------------------------------
# #539 — RPLidar / YDLIDAR VID/PID detection
# ---------------------------------------------------------------------------


def test_detect_rplidar_usb_rplidar_by_product():
    """CP2102 device with RPLIDAR product string → model=rplidar."""
    port = _make_port(0x10C4, 0xEA60, "/dev/ttyUSB0", product="RPLIDAR")
    with patch(
        "castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]
    ):
        from castor.hardware_detect import detect_rplidar_usb
        result = detect_rplidar_usb()
    assert result["detected"] is True
    assert result["model"] == "rplidar"


def test_detect_rplidar_usb_ydlidar_by_product():
    """CP2102 device with YDLIDAR product string → model=ydlidar."""
    port = _make_port(0x10C4, 0xEA60, "/dev/ttyUSB0", product="YDLIDAR")
    with patch(
        "castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]
    ):
        from castor.hardware_detect import detect_rplidar_usb
        result = detect_rplidar_usb()
    assert result["detected"] is True
    assert result["model"] == "ydlidar"


def test_detect_rplidar_usb_unknown_lidar():
    """CP2102 device with no discriminating product string → model=unknown_lidar."""
    port = _make_port(0x10C4, 0xEA60, "/dev/ttyUSB0", product="USB Serial")
    with patch(
        "castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]
    ):
        from castor.hardware_detect import detect_rplidar_usb
        result = detect_rplidar_usb()
    assert result["detected"] is True
    assert result["model"] == "unknown_lidar"


def test_detect_rplidar_usb_no_device():
    """No matching device → detected=False."""
    with patch(
        "castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[]
    ):
        from castor.hardware_detect import detect_rplidar_usb
        result = detect_rplidar_usb()
    assert result["detected"] is False


def test_suggest_preset_lidar_navigation_rplidar():
    """suggest_preset returns 'lidar_navigation' when rplidar detected."""
    from castor.hardware_detect import suggest_preset
    hw = {
        "rplidar": {"detected": True, "model": "rplidar"},
        "i2c_devices": [], "usb_serial": [], "cameras": [], "platform": "generic",
        "usb_descriptors": [], "realsense": [], "oakd": [], "odrive": [], "vesc": [],
        "feetech": [], "arduino": [], "circuitpython": [], "lidar": [],
        "hailo": [], "coral": [], "imx500": [], "reachy": [],
    }
    preset, conf, reason = suggest_preset(hw)
    assert preset == "lidar_navigation"


def test_suggest_extras_rplidar():
    """suggest_extras returns ['rplidar'] when rplidar model detected."""
    from castor.hardware_detect import suggest_extras
    hw = {"rplidar": {"detected": True, "model": "rplidar"}}
    with patch("builtins.__import__", side_effect=ImportError):
        extras = suggest_extras(hw)
    assert "rplidar" in extras


def test_suggest_extras_ydlidar():
    """suggest_extras returns ['ydlidar'] when ydlidar model detected."""
    from castor.hardware_detect import suggest_extras
    hw = {"rplidar": {"detected": True, "model": "ydlidar"}}
    with patch("builtins.__import__", side_effect=ImportError):
        extras = suggest_extras(hw)
    assert "ydlidar" in extras


def test_detect_rplidar_usb_lsusb_fallback():
    """When serial ports return no match, lsusb fallback detects CP2102 → unknown_lidar."""
    with (
        patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[]),
        patch(
            "castor.hardware_detect.scan_usb_descriptors",
            return_value=["bus 001 device 003: id 10c4:ea60 silicon laboratories"],
        ),
    ):
        from castor.hardware_detect import detect_rplidar_usb
        result = detect_rplidar_usb()
    assert result["detected"] is True
    assert result["model"] == "unknown_lidar"


def test_detect_rplidar_usb_stm32_vid_pid():
    """STM32 VCP device (0483:5740) with YDLIDAR product string → model=ydlidar."""
    port = _make_port(0x0483, 0x5740, "/dev/ttyACM0", product="YDLIDAR T15")
    with patch(
        "castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]
    ):
        from castor.hardware_detect import detect_rplidar_usb
        result = detect_rplidar_usb()
    assert result["detected"] is True
    assert result["model"] == "ydlidar"


def test_suggest_extras_unknown_lidar_skips():
    """suggest_extras skips package recommendation when model=unknown_lidar."""
    from castor.hardware_detect import suggest_extras
    hw = {"rplidar": {"detected": True, "model": "unknown_lidar"}}
    with patch("builtins.__import__", side_effect=ImportError):
        extras = suggest_extras(hw)
    assert "rplidar" not in extras
    assert "ydlidar" not in extras

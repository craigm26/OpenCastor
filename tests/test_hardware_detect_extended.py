"""Extended tests for hardware_detect — issues #537–#541."""

from __future__ import annotations

import subprocess
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
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        from castor.hardware_detect import detect_dynamixel_usb

        result = detect_dynamixel_usb()
    assert len(result) == 1
    assert result[0]["vid_pid"] == "0403:6014"


def test_dynamixel_detects_u2d2h_ftdi_ft232h():
    """VID 0x0403 / PID 0x6015 → U2D2-H detected."""
    port = _make_port(0x0403, 0x6015, "/dev/ttyUSB1")
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        from castor.hardware_detect import detect_dynamixel_usb

        result = detect_dynamixel_usb()
    assert len(result) == 1
    assert result[0]["vid_pid"] == "0403:6015"


def test_dynamixel_no_match_returns_empty():
    """Unknown VID/PID → no detection."""
    port = _make_port(0xDEAD, 0xBEEF)
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        from castor.hardware_detect import detect_dynamixel_usb

        result = detect_dynamixel_usb()
    assert result == []


def test_suggest_preset_dynamixel_arm_for_u2d2():
    """suggest_preset returns 'dynamixel_arm' when U2D2 detected."""
    from castor.hardware_detect import suggest_preset

    hw = {
        "dynamixel": [
            {"port": "/dev/ttyUSB0", "vid_pid": "0403:6014", "model": "Dynamixel U2D2 (FT232R)"}
        ],
        "i2c_devices": [],
        "usb_serial": [],
        "cameras": [],
        "platform": "generic",
        "usb_descriptors": [],
        "realsense": [],
        "oakd": [],
        "odrive": [],
        "vesc": [],
        "feetech": [],
        "arduino": [],
        "circuitpython": [],
        "lidar": [],
        "hailo": [],
        "coral": [],
        "imx500": [],
        "reachy": [],
    }
    preset, conf, reason = suggest_preset(hw)
    assert preset == "dynamixel_arm"
    assert conf == "high"


def test_suggest_preset_non_u2d2_dynamixel_gives_koch():
    """suggest_preset returns 'lerobot/koch-arm' for non-U2D2 Dynamixel (e.g. OpenCR)."""
    from castor.hardware_detect import suggest_preset

    hw = {
        "dynamixel": [
            {"port": "/dev/ttyUSB0", "vid_pid": "0483:5740", "model": "Robotis OpenCR 1.0"}
        ],
        "i2c_devices": [],
        "usb_serial": [],
        "cameras": [],
        "platform": "generic",
        "usb_descriptors": [],
        "realsense": [],
        "oakd": [],
        "odrive": [],
        "vesc": [],
        "feetech": [],
        "arduino": [],
        "circuitpython": [],
        "lidar": [],
        "hailo": [],
        "coral": [],
        "imx500": [],
        "reachy": [],
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
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        from castor.hardware_detect import detect_rplidar_usb

        result = detect_rplidar_usb()
    assert result["detected"] is True
    assert result["model"] == "rplidar"


def test_detect_rplidar_usb_ydlidar_by_product():
    """CP2102 device with YDLIDAR product string → model=ydlidar."""
    port = _make_port(0x10C4, 0xEA60, "/dev/ttyUSB0", product="YDLIDAR")
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        from castor.hardware_detect import detect_rplidar_usb

        result = detect_rplidar_usb()
    assert result["detected"] is True
    assert result["model"] == "ydlidar"


def test_detect_rplidar_usb_unknown_lidar():
    """CP2102 device with no discriminating product string → model=unknown_lidar."""
    port = _make_port(0x10C4, 0xEA60, "/dev/ttyUSB0", product="USB Serial")
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        from castor.hardware_detect import detect_rplidar_usb

        result = detect_rplidar_usb()
    assert result["detected"] is True
    assert result["model"] == "unknown_lidar"


def test_detect_rplidar_usb_no_device():
    """No matching device → detected=False."""
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[]):
        from castor.hardware_detect import detect_rplidar_usb

        result = detect_rplidar_usb()
    assert result["detected"] is False


def test_suggest_preset_lidar_navigation_rplidar():
    """suggest_preset returns 'lidar_navigation' when rplidar detected."""
    from castor.hardware_detect import suggest_preset

    hw = {
        "rplidar": {"detected": True, "model": "rplidar"},
        "i2c_devices": [],
        "usb_serial": [],
        "cameras": [],
        "platform": "generic",
        "usb_descriptors": [],
        "realsense": [],
        "oakd": [],
        "odrive": [],
        "vesc": [],
        "feetech": [],
        "arduino": [],
        "circuitpython": [],
        "lidar": [],
        "hailo": [],
        "coral": [],
        "imx500": [],
        "reachy": [],
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
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
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


# ---------------------------------------------------------------------------
# #538 — I2C device lookup table
# ---------------------------------------------------------------------------


def test_detect_i2c_devices_returns_empty_on_non_linux(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "platform", "darwin")
    from castor.hardware_detect import detect_i2c_devices

    result = detect_i2c_devices()
    assert result == []


def test_detect_i2c_devices_sysfs_fallback():
    """Without smbus2, parse /sys/bus/i2c/devices/ for known addresses."""
    import sys as _sys

    if _sys.platform != "linux":
        pytest.skip("Linux only")
    with (
        patch("castor.hardware_detect.HAS_SMBUS", False),
        patch("os.path.isdir", return_value=True),
        patch(
            "os.listdir",
            side_effect=lambda p: ["1-0040"] if "devices" in p else ["i2c-1"],
        ),
    ):
        from castor.hardware_detect import detect_i2c_devices

        result = detect_i2c_devices()
    assert len(result) == 1
    assert result[0]["device_name"] != ""
    assert result[0]["bus"] == 1
    assert result[0]["address"] == "0x40"


def test_detect_i2c_devices_known_address_bme280():
    """Sysfs entry '1-0076' → bus=1, address='0x76', device_name contains BME280."""
    import sys as _sys

    if _sys.platform != "linux":
        pytest.skip("Linux only")
    with (
        patch("castor.hardware_detect.HAS_SMBUS", False),
        patch("os.path.isdir", return_value=True),
        patch("os.listdir", side_effect=lambda p: ["1-0076"] if "devices" in p else ["i2c-1"]),
    ):
        from castor.hardware_detect import detect_i2c_devices

        result = detect_i2c_devices()
    assert len(result) == 1
    assert result[0]["bus"] == 1
    assert result[0]["address"] == "0x76"
    assert "BME280" in result[0]["device_name"] or "BMP280" in result[0]["device_name"]


def test_detect_i2c_devices_unknown_address():
    """Sysfs entry with unknown address → device_name = 'unknown'."""
    import sys as _sys

    if _sys.platform != "linux":
        pytest.skip("Linux only")
    with (
        patch("castor.hardware_detect.HAS_SMBUS", False),
        patch("os.path.isdir", return_value=True),
        patch("os.listdir", side_effect=lambda p: ["1-00ff"] if "devices" in p else ["i2c-1"]),
    ):
        from castor.hardware_detect import detect_i2c_devices

        result = detect_i2c_devices()
    assert len(result) == 1
    assert result[0]["device_name"] == "unknown"


def test_detect_hardware_includes_i2c_key():
    """detect_hardware() result dict has 'i2c' key."""
    with patch("castor.hardware_detect._run_all_detectors", return_value={"i2c": []}):
        from castor.hardware_detect import detect_hardware

        result = detect_hardware(refresh=True)
    assert "i2c" in result


def test_suggest_extras_i2c():
    """suggest_extras returns ['smbus2'] when i2c devices found."""
    from castor.hardware_detect import suggest_extras

    hw = {"i2c": [{"bus": 1, "address": "0x40", "device_name": "PCA9685 PWM Driver"}]}
    with patch("castor.hardware_detect.HAS_SMBUS", False):
        extras = suggest_extras(hw)
    assert "smbus2" in extras


# ---------------------------------------------------------------------------
# #540 — RPi AI Camera (IMX500) detection
# ---------------------------------------------------------------------------


def test_detect_rpi_ai_camera_via_libcamera():
    """libcamera-hello output containing 'imx500' → detected=True, model='imx500'."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "Available cameras\n--------------\n0 : imx500 [4056x3040]\n"
    with (
        patch("subprocess.run", return_value=mock_proc),
        patch("os.path.isdir", return_value=False),
    ):
        from castor.hardware_detect import detect_rpi_ai_camera

        result = detect_rpi_ai_camera()
    assert result["detected"] is True
    assert result["model"] == "imx500"


def test_detect_rpi_ai_camera_npu_detected():
    """NPU firmware dir present → npu=True."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "imx500 [4056x3040]"

    def _isdir(p):
        return "/lib/firmware/imx500" in p

    with (
        patch("subprocess.run", return_value=mock_proc),
        patch("os.path.isdir", side_effect=_isdir),
    ):
        from castor.hardware_detect import detect_rpi_ai_camera

        result = detect_rpi_ai_camera()
    assert result["npu"] is True


def test_detect_rpi_ai_camera_not_found():
    """libcamera output without imx500 → detected=False."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "No cameras available\n"
    with (
        patch("subprocess.run", return_value=mock_proc),
        patch("os.path.isdir", return_value=False),
    ):
        from castor.hardware_detect import detect_rpi_ai_camera

        result = detect_rpi_ai_camera()
    assert result["detected"] is False


def test_detect_rpi_ai_camera_libcamera_missing():
    """subprocess.FileNotFoundError (libcamera not installed) → detected=False."""
    with (
        patch("subprocess.run", side_effect=FileNotFoundError),
        patch("os.path.isdir", return_value=False),
    ):
        from castor.hardware_detect import detect_rpi_ai_camera

        result = detect_rpi_ai_camera()
    assert result["detected"] is False


def test_detect_rpi_ai_camera_timeout():
    """subprocess.TimeoutExpired → detected=False (graceful)."""
    with (
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired("libcamera-hello", 3)),
        patch("os.path.isdir", return_value=False),
    ):
        from castor.hardware_detect import detect_rpi_ai_camera

        result = detect_rpi_ai_camera()
    assert result["detected"] is False


def test_detect_hardware_includes_rpi_ai_camera_key():
    """detect_hardware() result dict has 'rpi_ai_camera' key."""
    with patch(
        "castor.hardware_detect._run_all_detectors",
        return_value={"rpi_ai_camera": {"detected": False, "model": "imx500", "npu": False}},
    ):
        from castor.hardware_detect import detect_hardware

        result = detect_hardware(refresh=True)
    assert "rpi_ai_camera" in result


def test_suggest_extras_rpi_ai_camera():
    """suggest_extras returns ['picamera2'] when rpi_ai_camera detected."""
    from castor.hardware_detect import suggest_extras

    hw = {"rpi_ai_camera": {"detected": True, "model": "imx500", "npu": False}}
    with patch("builtins.__import__", side_effect=ImportError):
        extras = suggest_extras(hw)
    assert "picamera2" in extras

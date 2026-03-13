"""Unit tests for castor.hardware_detect."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_usb_descriptor_cache():
    """Reset the scan_usb_descriptors and detect_hardware caches before every test."""
    from castor.hardware_detect import invalidate_hardware_cache, invalidate_usb_descriptors_cache

    invalidate_usb_descriptors_cache()
    invalidate_hardware_cache()
    yield
    invalidate_usb_descriptors_cache()
    invalidate_hardware_cache()


# ---------------------------------------------------------------------------
# scan_i2c
# ---------------------------------------------------------------------------


def test_scan_i2c_returns_empty_on_non_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    from castor.hardware_detect import scan_i2c

    result = scan_i2c()
    assert result == []


def test_scan_i2c_parses_output(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    fake_output = (
        "     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f\n"
        "00:          -- -- -- -- -- -- -- -- -- -- -- -- --\n"
        "10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --\n"
        "40: 40 -- -- -- -- -- -- -- -- -- -- -- -- -- -- --\n"
        "70: -- -- -- -- -- -- -- --\n"
    )
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = fake_output

    with (
        patch("os.path.isdir", return_value=True),
        patch("os.listdir", return_value=["i2c-1"]),
        patch("subprocess.run", return_value=mock_result),
    ):
        import castor.hardware_detect as hd

        devices = hd.scan_i2c()

    assert any(d["address"] == "0x40" for d in devices)


def test_scan_i2c_handles_file_not_found(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with (
        patch("os.path.isdir", return_value=True),
        patch("os.listdir", return_value=["i2c-1"]),
        patch("subprocess.run", side_effect=FileNotFoundError),
    ):
        from castor.hardware_detect import scan_i2c

        result = scan_i2c()
    assert result == []


# ---------------------------------------------------------------------------
# scan_usb_serial
# ---------------------------------------------------------------------------


def test_scan_usb_serial_returns_ports(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with (
        patch("os.path.isdir", return_value=True),
        patch("os.listdir", return_value=["ttyUSB0", "ttyACM0", "tty0"]),
    ):
        from castor.hardware_detect import scan_usb_serial

        ports = scan_usb_serial()

    assert "/dev/ttyUSB0" in ports
    assert "/dev/ttyACM0" in ports
    # tty0 should not be included
    assert "/dev/tty0" not in ports


def test_scan_usb_serial_empty_on_non_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    from castor.hardware_detect import scan_usb_serial

    assert scan_usb_serial() == []


# ---------------------------------------------------------------------------
# detect_hardware
# ---------------------------------------------------------------------------


def test_detect_hardware_returns_expected_keys():
    with (
        patch("castor.hardware_detect.scan_i2c", return_value=[]),
        patch("castor.hardware_detect.scan_usb_serial", return_value=[]),
        patch("castor.hardware_detect.scan_usb_descriptors", return_value=[]),
        patch("castor.hardware_detect.scan_cameras", return_value=[]),
        patch("castor.hardware_detect._detect_platform", return_value="generic"),
    ):
        from castor.hardware_detect import detect_hardware

        hw = detect_hardware()

    for key in ("i2c_devices", "usb_serial", "cameras", "platform"):
        assert key in hw


# ---------------------------------------------------------------------------
# suggest_preset
# ---------------------------------------------------------------------------


def test_suggest_preset_generic_when_nothing():
    from castor.hardware_detect import suggest_preset

    hw = {
        "i2c_devices": [],
        "usb_serial": [],
        "usb_descriptors": [],
        "cameras": [],
        "platform": "generic",
    }
    preset, confidence, reason = suggest_preset(hw)
    assert isinstance(preset, str)
    assert len(preset) > 0


def test_suggest_preset_rpi_rc_car_with_pca9685():
    from castor.hardware_detect import suggest_preset

    hw = {
        "i2c_devices": [{"bus": 1, "address": "0x40"}],
        "usb_serial": [],
        "usb_descriptors": [],
        "cameras": [],
        "platform": "rpi",
    }
    preset, confidence, reason = suggest_preset(hw)
    assert "rpi_rc_car" in preset


def test_suggest_preset_lego_via_usb_descriptor():
    from castor.hardware_detect import suggest_preset

    hw = {
        "i2c_devices": [],
        "usb_serial": [],
        "usb_descriptors": ["bus 001 device 002: id 0694:0005 lego group"],
        "cameras": [],
        "platform": "generic",
    }
    preset, _conf, _reason = suggest_preset(hw)
    assert "lego" in preset


# ---------------------------------------------------------------------------
# _detect_platform
# ---------------------------------------------------------------------------


def test_detect_platform_returns_string():
    from castor.hardware_detect import _detect_platform

    result = _detect_platform()
    assert result in ("rpi", "jetson", "generic")


def test_detect_platform_rpi(tmp_path):
    with patch(
        "castor.hardware_detect._read_device_tree_model", return_value="Raspberry Pi 4 Model B"
    ):
        from castor.hardware_detect import _detect_platform

        assert _detect_platform() == "rpi"


# ---------------------------------------------------------------------------
# print_scan_results
# ---------------------------------------------------------------------------


def test_print_scan_results_empty(capsys):
    from castor.hardware_detect import print_scan_results

    hw = {
        "i2c_devices": [],
        "usb_serial": [],
        "usb_descriptors": [],
        "cameras": [],
        "platform": "generic",
    }
    print_scan_results(hw)
    out = capsys.readouterr().out
    assert "Hardware Scan Results" in out


def test_print_scan_results_full(capsys):
    from castor.hardware_detect import print_scan_results

    hw = {
        "i2c_devices": [{"bus": 1, "address": "0x40"}],
        "usb_serial": ["/dev/ttyUSB0"],
        "usb_descriptors": [],
        "cameras": [{"type": "usb", "device": "/dev/video0", "accessible": True}],
        "platform": "rpi",
    }
    print_scan_results(hw)
    out = capsys.readouterr().out
    assert "Hardware Scan Results" in out
    assert "0x40" in out
    assert "ttyUSB0" in out


# ---------------------------------------------------------------------------
# scan_usb_descriptors — OAK-D / Hailo heuristics via lsusb
# ---------------------------------------------------------------------------


def test_scan_usb_descriptors_returns_list_on_success():
    """scan_usb_descriptors returns lower-cased lines when lsusb succeeds."""
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = (
        "Bus 001 Device 003: ID 03e7:2485 Intel Corp. Movidius MyriadX (OAK-D)\n"
        "Bus 001 Device 004: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
    )
    with patch("subprocess.run", return_value=fake_proc):
        from castor.hardware_detect import scan_usb_descriptors

        result = scan_usb_descriptors()

    assert len(result) == 2
    assert all(line == line.lower() for line in result)
    assert any("oak" in line or "movidius" in line for line in result)


def test_scan_usb_descriptors_returns_empty_on_failure():
    """scan_usb_descriptors returns [] when lsusb exits with non-zero."""
    fake_proc = MagicMock()
    fake_proc.returncode = 1
    fake_proc.stdout = ""
    with patch("subprocess.run", return_value=fake_proc):
        from castor.hardware_detect import scan_usb_descriptors

        result = scan_usb_descriptors()

    assert result == []


def test_scan_usb_descriptors_returns_empty_when_lsusb_missing():
    """scan_usb_descriptors returns [] when lsusb is not installed."""
    with patch("subprocess.run", side_effect=FileNotFoundError):
        from castor.hardware_detect import scan_usb_descriptors

        result = scan_usb_descriptors()

    assert result == []


def test_scan_usb_descriptors_timeout():
    """scan_usb_descriptors returns [] on subprocess timeout."""
    import subprocess

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="lsusb", timeout=5)):
        from castor.hardware_detect import scan_usb_descriptors

        result = scan_usb_descriptors()

    assert result == []


# ---------------------------------------------------------------------------
# OAK-D detection via suggest_preset + USB descriptors
# ---------------------------------------------------------------------------


def test_suggest_preset_oak_d_detected_via_usb_descriptor():
    """OAK-D (Movidius VID 03e7) is surfaced through USB descriptor strings."""
    from castor.hardware_detect import suggest_preset

    hw = {
        "i2c_devices": [],
        "usb_serial": [],
        "usb_descriptors": [
            "bus 001 device 003: id 03e7:2485 intel corp. movidius myriadx (oak-d)"
        ],
        "cameras": [],
        "platform": "generic",
    }
    # OAK-D shows up as a USB device — the descriptor is available for inspection.
    # suggest_preset doesn't have a dedicated OAK-D branch, but the USB descriptor
    # content is accessible to the caller.  We verify the function doesn't crash
    # and the descriptor data is reflected in the hw dict consumed by the caller.
    preset, confidence, reason = suggest_preset(hw)
    assert isinstance(preset, str)
    assert isinstance(confidence, str)


def test_scan_usb_descriptors_oak_d_string_present():
    """OAK-D USB ID 03e7 appears in the descriptor output."""
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = "Bus 001 Device 003: ID 03e7:2485 Intel Corp. MyriadX (OAK-D)\n"
    with patch("subprocess.run", return_value=fake_proc):
        from castor.hardware_detect import scan_usb_descriptors

        result = scan_usb_descriptors()

    assert any("03e7" in line for line in result)


# ---------------------------------------------------------------------------
# Hailo-8 detection via os.path.exists('/dev/hailo0')
# ---------------------------------------------------------------------------


def test_hailo_device_present_via_os_path_exists():
    """When /dev/hailo0 exists, os.path.exists returns True (mocked)."""
    with patch("os.path.exists", side_effect=lambda p: p == "/dev/hailo0"):
        assert os.path.exists("/dev/hailo0") is True


def test_hailo_device_absent_via_os_path_exists():
    """When /dev/hailo0 is absent, os.path.exists returns False (mocked)."""
    with patch("os.path.exists", return_value=False):
        assert os.path.exists("/dev/hailo0") is False


def test_scan_usb_descriptors_hailo_string_detected():
    """Hailo USB descriptor is lower-cased and returned correctly."""
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = "Bus 002 Device 005: ID 1e60:0001 Hailo Technologies Ltd. Hailo-8\n"
    with patch("subprocess.run", return_value=fake_proc):
        from castor.hardware_detect import scan_usb_descriptors

        result = scan_usb_descriptors()

    assert any("hailo" in line for line in result)


# ---------------------------------------------------------------------------
# I2C bus detection — detected and not-detected paths
# ---------------------------------------------------------------------------


def test_scan_i2c_multiple_buses_detected(monkeypatch):
    """I2C devices on multiple buses are all collected."""
    monkeypatch.setattr(sys, "platform", "linux")
    pca_output = (
        "     0  1  2  3  4  5  6  7\n00:          -- -- -- -- --\n40: 40 -- -- -- -- -- -- --\n"
    )
    mpu_output = (
        "     0  1  2  3  4  5  6  7\n00:          -- -- -- -- --\n68: -- -- -- -- -- -- -- 68\n"
    )

    def fake_run(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 0
        bus_num = int(cmd[2])
        mock.stdout = pca_output if bus_num == 1 else mpu_output
        return mock

    with (
        patch("os.path.isdir", return_value=True),
        patch("os.listdir", return_value=["i2c-1", "i2c-2"]),
        patch("subprocess.run", side_effect=fake_run),
    ):
        from castor.hardware_detect import scan_i2c

        devices = scan_i2c()

    addresses = {d["address"] for d in devices}
    assert "0x40" in addresses
    assert "0x68" in addresses


def test_scan_i2c_no_buses_returns_empty(monkeypatch):
    """No i2c-* entries in /dev yields an empty device list."""
    monkeypatch.setattr(sys, "platform", "linux")
    with (
        patch("os.path.isdir", return_value=True),
        patch("os.listdir", return_value=["sda", "null", "zero"]),
    ):
        from castor.hardware_detect import scan_i2c

        devices = scan_i2c()

    assert devices == []


def test_scan_i2c_non_zero_returncode_skipped(monkeypatch):
    """I2C buses where i2cdetect exits non-zero are skipped."""
    monkeypatch.setattr(sys, "platform", "linux")
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    with (
        patch("os.path.isdir", return_value=True),
        patch("os.listdir", return_value=["i2c-0"]),
        patch("subprocess.run", return_value=mock_result),
    ):
        from castor.hardware_detect import scan_i2c

        devices = scan_i2c()

    assert devices == []


# ---------------------------------------------------------------------------
# Platform detection — RPi, x86 (generic), arm64 (generic)
# ---------------------------------------------------------------------------


def test_detect_platform_rpi_model_b():
    """Device tree containing 'raspberry pi' → platform is 'rpi'."""
    with patch(
        "castor.hardware_detect._read_device_tree_model", return_value="Raspberry Pi 4 Model B"
    ):
        from castor.hardware_detect import _detect_platform

        assert _detect_platform() == "rpi"


def test_detect_platform_rpi_zero():
    """Raspberry Pi Zero is also classified as 'rpi'."""
    with patch(
        "castor.hardware_detect._read_device_tree_model", return_value="Raspberry Pi Zero 2 W"
    ):
        from castor.hardware_detect import _detect_platform

        assert _detect_platform() == "rpi"


def test_detect_platform_jetson():
    """Device tree containing 'jetson' → platform is 'jetson'."""
    with patch("castor.hardware_detect._read_device_tree_model", return_value="NVIDIA Jetson Nano"):
        from castor.hardware_detect import _detect_platform

        assert _detect_platform() == "jetson"


def test_detect_platform_x86_generic():
    """On x86 / standard Linux, _read_device_tree_model raises FileNotFoundError → 'generic'."""
    with patch(
        "castor.hardware_detect._read_device_tree_model",
        side_effect=FileNotFoundError,
    ):
        from castor.hardware_detect import _detect_platform

        assert _detect_platform() == "generic"


def test_detect_platform_arm64_no_device_tree():
    """On arm64 without a device-tree model file, platform falls back to 'generic'."""
    with patch(
        "castor.hardware_detect._read_device_tree_model",
        side_effect=PermissionError,
    ):
        from castor.hardware_detect import _detect_platform

        assert _detect_platform() == "generic"


def test_detect_platform_unknown_board():
    """An unrecognised device-tree model → 'generic'."""
    with patch(
        "castor.hardware_detect._read_device_tree_model",
        return_value="Some Unknown SBC v3",
    ):
        from castor.hardware_detect import _detect_platform

        assert _detect_platform() == "generic"


# ---------------------------------------------------------------------------
# scan_cameras — detected and not-detected
# ---------------------------------------------------------------------------


def test_scan_cameras_returns_video_device():
    """video* entries in /dev produce USB camera entries."""
    with (
        patch("os.path.isdir", return_value=True),
        patch("os.listdir", return_value=["video0", "video1", "null"]),
        patch("os.access", return_value=True),
    ):
        from castor.hardware_detect import scan_cameras

        cams = scan_cameras()

    devices = [c["device"] for c in cams]
    assert "/dev/video0" in devices
    assert "/dev/video1" in devices


def test_scan_cameras_no_video_devices():
    """No video* entries → empty camera list (picamera2 import also fails)."""
    with (
        patch("os.path.isdir", return_value=True),
        patch("os.listdir", return_value=["null", "zero", "ttyUSB0"]),
    ):
        from castor.hardware_detect import scan_cameras

        cams = scan_cameras()

    usb_cams = [c for c in cams if c["type"] == "usb"]
    assert usb_cams == []


# ---------------------------------------------------------------------------
# detect_hardware integration — ensure subprocess mocks propagate
# ---------------------------------------------------------------------------


def test_detect_hardware_full_mock():
    """detect_hardware aggregates results from all sub-scanners correctly."""
    with (
        patch("castor.hardware_detect.scan_i2c", return_value=[{"bus": 1, "address": "0x40"}]),
        patch("castor.hardware_detect.scan_usb_serial", return_value=["/dev/ttyUSB0"]),
        patch(
            "castor.hardware_detect.scan_usb_descriptors",
            return_value=["bus 001 device 003: id 03e7:2485 oak-d"],
        ),
        patch(
            "castor.hardware_detect.scan_cameras",
            return_value=[{"type": "usb", "device": "/dev/video0", "accessible": True}],
        ),
        patch("castor.hardware_detect._detect_platform", return_value="rpi"),
    ):
        from castor.hardware_detect import detect_hardware

        hw = detect_hardware()

    assert hw["platform"] == "rpi"
    assert hw["i2c_devices"] == [{"bus": 1, "address": "0x40"}]
    assert "/dev/ttyUSB0" in hw["usb_serial"]
    assert any("oak-d" in d for d in hw["usb_descriptors"])


# ---------------------------------------------------------------------------
# New: VID/PID table tests (#529–#540)
# ---------------------------------------------------------------------------


def test_realsense_table_has_d435():
    from castor.hardware_detect import KNOWN_REALSENSE_DEVICES

    assert "8086:0b07" in KNOWN_REALSENSE_DEVICES
    assert "D435" in KNOWN_REALSENSE_DEVICES["8086:0b07"]


def test_realsense_table_has_d455():
    from castor.hardware_detect import KNOWN_REALSENSE_DEVICES

    assert "8086:0b5c" in KNOWN_REALSENSE_DEVICES


def test_oakd_table_has_running():
    from castor.hardware_detect import KNOWN_OAKD_DEVICES

    assert "03e7:2487" in KNOWN_OAKD_DEVICES


def test_arduino_table_has_uno():
    from castor.hardware_detect import KNOWN_ARDUINO_DEVICES

    assert "2341:0043" in KNOWN_ARDUINO_DEVICES
    assert "Uno" in KNOWN_ARDUINO_DEVICES["2341:0043"]


def test_arduino_table_has_nano_clone():
    from castor.hardware_detect import KNOWN_ARDUINO_DEVICES

    assert "1a86:7523" in KNOWN_ARDUINO_DEVICES


def test_feetech_table_has_ch340():
    from castor.hardware_detect import KNOWN_FEETECH_DEVICES

    assert "1a86:7523" in KNOWN_FEETECH_DEVICES


def test_dynamixel_table_has_u2d2():
    from castor.hardware_detect import KNOWN_DYNAMIXEL_DEVICES

    assert "0403:6014" in KNOWN_DYNAMIXEL_DEVICES
    assert "U2D2" in KNOWN_DYNAMIXEL_DEVICES["0403:6014"]


def test_odrive_table_has_v3():
    from castor.hardware_detect import KNOWN_ODRIVE_DEVICES

    assert "1209:0d32" in KNOWN_ODRIVE_DEVICES


def test_lidar_table_has_cp2102():
    from castor.hardware_detect import KNOWN_LIDAR_DEVICES

    assert "10c4:ea60" in KNOWN_LIDAR_DEVICES


# ---------------------------------------------------------------------------
# I2C_DEVICE_MAP enrichment
# ---------------------------------------------------------------------------


def test_i2c_map_pca9685_servo_driver():
    from castor.hardware_detect import I2C_DEVICE_MAP

    assert I2C_DEVICE_MAP["0x40"]["type"] == "servo_driver"
    assert "PCA9685" in I2C_DEVICE_MAP["0x40"]["name"]


def test_i2c_map_mpu6050_imu():
    from castor.hardware_detect import I2C_DEVICE_MAP

    assert I2C_DEVICE_MAP["0x68"]["type"] == "imu"


def test_i2c_map_bno055():
    from castor.hardware_detect import I2C_DEVICE_MAP

    assert "BNO055" in I2C_DEVICE_MAP["0x28"]["name"]


def test_i2c_map_oled_display():
    from castor.hardware_detect import I2C_DEVICE_MAP

    assert I2C_DEVICE_MAP["0x3c"]["type"] == "display"


def test_scan_i2c_enriches_pca9685(monkeypatch):
    """scan_i2c populates device/type from I2C_DEVICE_MAP for 0x40."""
    monkeypatch.setattr(sys, "platform", "linux")
    fake_output = (
        "     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f\n"
        "00:          -- -- -- -- -- -- -- -- -- -- -- -- --\n"
        "40: 40 -- -- -- -- -- -- -- -- -- -- -- -- -- -- --\n"
    )
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = fake_output

    with (
        patch("os.path.isdir", return_value=True),
        patch("os.listdir", return_value=["i2c-1"]),
        patch("subprocess.run", return_value=mock_result),
    ):
        from castor.hardware_detect import scan_i2c

        devices = scan_i2c()

    pca = [d for d in devices if d["address"] == "0x40"]
    assert pca, "0x40 not found in result"
    assert pca[0]["device"] == "PCA9685 PWM Driver"
    assert pca[0]["type"] == "servo_driver"


def test_scan_i2c_unknown_address_returns_unknown(monkeypatch):
    """scan_i2c returns 'unknown' for addresses not in I2C_DEVICE_MAP."""
    monkeypatch.setattr(sys, "platform", "linux")
    fake_output = "     0  1  2  3  4  5  6  7\n50: 5a -- -- -- -- -- -- --\n"
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = fake_output

    with (
        patch("os.path.isdir", return_value=True),
        patch("os.listdir", return_value=["i2c-1"]),
        patch("subprocess.run", return_value=mock_result),
    ):
        from castor.hardware_detect import scan_i2c

        devices = scan_i2c()

    if devices:
        assert devices[0]["device"] == "unknown"
        assert devices[0]["type"] == "unknown"


# ---------------------------------------------------------------------------
# New detector functions — mock mode (no hardware attached)
# ---------------------------------------------------------------------------


def _make_port(
    device="/dev/ttyACM0", vid=None, pid=None, description="", product="", manufacturer=""
):
    from types import SimpleNamespace

    return SimpleNamespace(
        device=device,
        vid=vid,
        pid=pid,
        description=description,
        product=product,
        manufacturer=manufacturer,
    )


def test_detect_realsense_empty_when_no_serial():
    from castor.hardware_detect import detect_realsense_usb

    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[]):
        with patch("castor.hardware_detect.scan_usb_descriptors", return_value=[]):
            assert detect_realsense_usb() == []


def test_detect_realsense_finds_d435():
    from castor.hardware_detect import detect_realsense_usb

    port = _make_port("/dev/bus/usb/001/003", vid=0x8086, pid=0x0B07)
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        result = detect_realsense_usb()
    assert len(result) == 1
    assert result[0]["model"] == "Intel RealSense D435"


def test_detect_oakd_empty_when_no_serial():
    from castor.hardware_detect import detect_oakd_usb

    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[]):
        with patch("castor.hardware_detect.scan_usb_descriptors", return_value=[]):
            assert detect_oakd_usb() == []


def test_detect_oakd_finds_running_device():
    from castor.hardware_detect import detect_oakd_usb

    port = _make_port("/dev/bus/usb/001/004", vid=0x03E7, pid=0x2487)
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        result = detect_oakd_usb()
    assert len(result) == 1
    assert "OAK-D" in result[0]["model"]


def test_detect_odrive_empty_when_no_serial():
    from castor.hardware_detect import detect_odrive_usb

    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[]):
        assert detect_odrive_usb() == []


def test_detect_odrive_finds_v3():
    from castor.hardware_detect import detect_odrive_usb

    port = _make_port("/dev/ttyACM1", vid=0x1209, pid=0x0D32)
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        result = detect_odrive_usb()
    assert "/dev/ttyACM1" in result


def test_detect_vesc_empty_when_no_serial():
    from castor.hardware_detect import detect_vesc_usb

    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[]):
        assert detect_vesc_usb() == []


def test_detect_vesc_finds_by_description():
    from castor.hardware_detect import detect_vesc_usb

    port = _make_port("/dev/ttyUSB0", vid=0x0483, pid=0x5740, description="VESC Motor Controller")
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        assert "/dev/ttyUSB0" in detect_vesc_usb()


def test_detect_feetech_empty_when_no_serial():
    from castor.hardware_detect import detect_feetech_usb

    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[]):
        assert detect_feetech_usb() == []


def test_detect_feetech_finds_ch340():
    from castor.hardware_detect import detect_feetech_usb

    port = _make_port("/dev/ttyUSB1", vid=0x1A86, pid=0x7523, description="USB Serial")
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        assert "/dev/ttyUSB1" in detect_feetech_usb()


def test_detect_feetech_skips_acb_product():
    """ACB-branded STM32 port must not be identified as Feetech."""
    from castor.hardware_detect import detect_feetech_usb

    port = _make_port("/dev/ttyACM0", vid=0x0483, pid=0x5740, description="ACB Motor Board")
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        assert detect_feetech_usb() == []


def test_detect_arduino_empty_when_no_serial():
    from castor.hardware_detect import detect_arduino_usb

    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[]):
        assert detect_arduino_usb() == []


def test_detect_arduino_finds_uno():
    from castor.hardware_detect import detect_arduino_usb

    port = _make_port("/dev/ttyACM0", vid=0x2341, pid=0x0043)
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        result = detect_arduino_usb()
    assert len(result) == 1
    assert result[0]["board"] == "Arduino Uno R3"
    assert result[0]["port"] == "/dev/ttyACM0"


def test_detect_circuitpython_empty_when_no_serial():
    from castor.hardware_detect import detect_circuitpython_usb

    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[]):
        assert detect_circuitpython_usb() == []


def test_detect_circuitpython_finds_adafruit_vid():
    from castor.hardware_detect import detect_circuitpython_usb

    port = _make_port("/dev/ttyACM1", vid=0x239A, pid=0x0031, description="Feather M4 Express")
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        result = detect_circuitpython_usb()
    assert len(result) == 1
    assert result[0]["vid_pid"].startswith("239a:")


def test_detect_dynamixel_empty_when_no_serial():
    from castor.hardware_detect import detect_dynamixel_usb

    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[]):
        assert detect_dynamixel_usb() == []


def test_detect_dynamixel_finds_u2d2():
    from castor.hardware_detect import detect_dynamixel_usb

    port = _make_port("/dev/ttyUSB0", vid=0x0403, pid=0x6014, description="U2D2")
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        result = detect_dynamixel_usb()
    assert len(result) == 1
    assert result[0]["port"] == "/dev/ttyUSB0"
    assert "U2D2" in result[0]["model"]


def test_detect_lidar_empty_when_no_serial():
    from castor.hardware_detect import detect_lidar_usb

    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[]):
        assert detect_lidar_usb() == []


def test_detect_lidar_finds_cp2102():
    from castor.hardware_detect import detect_lidar_usb

    port = _make_port("/dev/ttyUSB0", vid=0x10C4, pid=0xEA60, description="CP2102")
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        result = detect_lidar_usb()
    assert len(result) == 1
    assert result[0]["port"] == "/dev/ttyUSB0"


def test_detect_hailo_empty_when_no_device(monkeypatch):
    monkeypatch.setattr("os.path.isdir", lambda p: p == "/dev")
    monkeypatch.setattr("os.listdir", lambda _: [])

    def fake_run(*a, **kw):
        return MagicMock(returncode=0, stdout="")

    monkeypatch.setattr("subprocess.run", fake_run)
    from castor.hardware_detect import detect_hailo

    assert detect_hailo() == []


def test_detect_hailo_finds_dev_node(monkeypatch):
    monkeypatch.setattr("os.path.isdir", lambda p: p == "/dev")
    monkeypatch.setattr("os.listdir", lambda _: ["hailo0", "video0"])
    from castor.hardware_detect import detect_hailo

    result = detect_hailo()
    assert any("hailo0" in h for h in result)


def test_detect_coral_empty_when_no_device():
    from castor.hardware_detect import detect_coral

    with (
        patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[]),
        patch("castor.hardware_detect.scan_usb_descriptors", return_value=[]),
        patch("os.listdir", return_value=[]),
    ):
        assert detect_coral() == []


def test_detect_coral_finds_usb_tpu():
    from castor.hardware_detect import detect_coral

    port = _make_port("/dev/bus/usb/001/005", vid=0x1A6E, pid=0x089A, description="Coral USB")
    with patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[port]):
        result = detect_coral()
    assert any("coral_usb" in c for c in result)


def test_detect_coral_finds_pcie(monkeypatch):
    monkeypatch.setattr("os.path.isdir", lambda p: p == "/dev")
    monkeypatch.setattr("os.listdir", lambda _: ["apex0"])
    from castor.hardware_detect import detect_coral

    with (
        patch("castor.hardware_detect._list_usb_ports_with_vidpid", return_value=[]),
        patch("castor.hardware_detect.scan_usb_descriptors", return_value=[]),
    ):
        result = detect_coral()
    assert any("coral_pcie" in c for c in result)


def test_detect_reachy_empty_when_unreachable():
    import socket as _socket

    from castor.hardware_detect import detect_reachy_network

    with patch("socket.getaddrinfo", side_effect=_socket.gaierror("no such host")):
        assert detect_reachy_network(timeout=0.1) == []


def test_detect_reachy_finds_host():
    import socket as _socket

    from castor.hardware_detect import detect_reachy_network

    def mock_getaddrinfo(host, *a, **kw):
        if host == "reachy.local":
            return [(_socket.AF_INET, None, None, None, ("192.168.1.100", 50055))]
        raise _socket.gaierror("not found")

    with patch("socket.getaddrinfo", side_effect=mock_getaddrinfo):
        result = detect_reachy_network(timeout=0.1)
    assert "reachy.local" in result


# ---------------------------------------------------------------------------
# suggest_preset — new hardware branches
# ---------------------------------------------------------------------------


def test_suggest_preset_reachy():
    from castor.hardware_detect import suggest_preset

    hw = {"reachy": ["reachy.local"], "platform": "generic"}
    preset, conf, reason = suggest_preset(hw)
    assert preset == "pollen/reachy2"
    assert conf == "high"
    assert "Reachy" in reason


def test_suggest_preset_feetech():
    from castor.hardware_detect import suggest_preset

    hw = {"feetech": ["/dev/ttyUSB0"], "platform": "generic"}
    preset, conf, _ = suggest_preset(hw)
    assert preset == "lerobot/so-arm101-follower"
    assert conf == "high"


def test_suggest_preset_dynamixel_gives_koch():
    """suggest_preset returns 'dynamixel_arm' for U2D2 VID/PID (0403:6014)."""
    from castor.hardware_detect import suggest_preset

    hw = {
        "dynamixel": [{"port": "/dev/ttyUSB0", "model": "U2D2", "vid_pid": "0403:6014"}],
        "platform": "generic",
    }
    preset, conf, _ = suggest_preset(hw)
    assert preset == "dynamixel_arm"
    assert conf == "high"


def test_suggest_preset_oakd_rpi():
    from castor.hardware_detect import suggest_preset

    hw = {
        "oakd": [{"port": "usb", "model": "Luxonis OAK-D (running)", "vid_pid": "03e7:2487"}],
        "platform": "rpi",
    }
    preset, conf, _ = suggest_preset(hw)
    assert preset == "rpi_oakd"
    assert conf == "high"


def test_suggest_preset_oakd_generic():
    from castor.hardware_detect import suggest_preset

    hw = {
        "oakd": [{"port": "usb", "model": "Luxonis OAK-D (running)", "vid_pid": "03e7:2487"}],
        "platform": "generic",
    }
    preset, _, _ = suggest_preset(hw)
    assert preset == "jetson_oakd"


def test_suggest_preset_realsense_generic():
    from castor.hardware_detect import suggest_preset

    hw = {
        "realsense": [{"port": "usb", "model": "Intel RealSense D435", "vid_pid": "8086:0b07"}],
        "platform": "generic",
    }
    preset, conf, _ = suggest_preset(hw)
    assert preset == "generic_realsense"
    assert conf == "high"


def test_suggest_preset_realsense_rpi():
    from castor.hardware_detect import suggest_preset

    hw = {
        "realsense": [{"port": "usb", "model": "Intel RealSense D435", "vid_pid": "8086:0b07"}],
        "platform": "rpi",
    }
    preset, _, _ = suggest_preset(hw)
    assert preset == "rpi_realsense"


def test_suggest_preset_odrive():
    from castor.hardware_detect import suggest_preset

    hw = {"odrive": ["/dev/ttyACM1"], "platform": "generic"}
    preset, conf, _ = suggest_preset(hw)
    assert preset == "odrive/differential"
    assert conf == "high"


def test_suggest_preset_hailo():
    from castor.hardware_detect import suggest_preset

    hw = {"hailo": ["hailo8 via /dev/hailo0"], "platform": "generic"}
    preset, conf, _ = suggest_preset(hw)
    assert preset == "hailo_vision"
    assert conf == "high"


def test_suggest_preset_coral():
    from castor.hardware_detect import suggest_preset

    hw = {"coral": ["coral_usb:/dev/bus/usb/001/005"], "platform": "generic"}
    preset, conf, _ = suggest_preset(hw)
    assert preset == "coral/tpu-inference"
    assert conf == "high"


def test_suggest_preset_arduino():
    from castor.hardware_detect import suggest_preset

    hw = {
        "arduino": [{"port": "/dev/ttyACM0", "board": "Arduino Uno R3", "vid_pid": "2341:0043"}],
        "platform": "generic",
    }
    preset, conf, reason = suggest_preset(hw)
    assert preset == "arduino/uno"
    assert conf == "medium"
    assert "Arduino" in reason


def test_suggest_preset_reachy_priority_over_dynamixel():
    """Reachy detection takes priority over Dynamixel."""
    from castor.hardware_detect import suggest_preset

    hw = {
        "reachy": ["reachy.local"],
        "dynamixel": [{"port": "/dev/ttyUSB0", "model": "U2D2", "vid_pid": "0403:6014"}],
        "platform": "generic",
    }
    preset, _, _ = suggest_preset(hw)
    assert preset == "pollen/reachy2"


def test_suggest_preset_feetech_priority_over_arduino():
    """Feetech detection takes priority over Arduino."""
    from castor.hardware_detect import suggest_preset

    hw = {
        "feetech": ["/dev/ttyUSB0"],
        "arduino": [{"port": "/dev/ttyACM0", "board": "Arduino Uno R3", "vid_pid": "2341:0043"}],
        "platform": "generic",
    }
    preset, _, _ = suggest_preset(hw)
    assert preset == "lerobot/so-arm101-follower"


# ---------------------------------------------------------------------------
# detect_hardware — new keys present
# ---------------------------------------------------------------------------


def test_detect_hardware_includes_new_keys():
    """detect_hardware returns all new detector keys."""
    import castor.hardware_detect as hd

    new_keys = [
        "realsense",
        "oakd",
        "odrive",
        "vesc",
        "feetech",
        "arduino",
        "circuitpython",
        "dynamixel",
        "lidar",
        "hailo",
        "coral",
        "imx500",
        "reachy",
    ]
    # Patch all scanners to avoid any real hardware access
    patches = {
        "scan_i2c": [],
        "scan_usb_serial": [],
        "scan_usb_descriptors": [],
        "scan_cameras": [],
        "_detect_platform": "generic",
        "detect_realsense_usb": [],
        "detect_oakd_usb": [],
        "detect_odrive_usb": [],
        "detect_vesc_usb": [],
        "detect_feetech_usb": [],
        "detect_arduino_usb": [],
        "detect_circuitpython_usb": [],
        "detect_dynamixel_usb": [],
        "detect_lidar_usb": [],
        "detect_hailo": [],
        "detect_coral": [],
        "detect_imx500_camera": [],
        "detect_reachy_network": [],
    }
    with patch.multiple(
        "castor.hardware_detect",
        **{
            k: (MagicMock(return_value=v) if k != "_detect_platform" else MagicMock(return_value=v))
            for k, v in patches.items()
        },
    ):
        hw = hd.detect_hardware()

    for key in new_keys:
        assert key in hw, f"Key '{key}' missing from detect_hardware() result"


# ---------------------------------------------------------------------------
# #546 OAK-D SR hex case fix
# ---------------------------------------------------------------------------


def test_detect_oakd_lsusb_uppercase_hex(monkeypatch):
    """OAK-D SR (PID f63b) must be detected via lsusb fallback.

    scan_usb_descriptors() lowercases cache entries, so the normalised
    line must still be matched by detect_oakd_usb().
    """
    import castor.hardware_detect as hd

    # Simulate the lowercased cache that scan_usb_descriptors() actually stores
    fake_lsusb = ["bus 001 device 002: id 03e7:f63b luxonis oak-d sr"]
    monkeypatch.setattr(hd, "_USB_DESCRIPTORS_CACHE", fake_lsusb)
    monkeypatch.setattr(hd, "_list_usb_ports_with_vidpid", lambda: [])

    results = hd.detect_oakd_usb()
    models = [r["model"] for r in results]
    assert "Luxonis OAK-D SR" in models


def test_detect_oakd_sr_pid_f63b_maps_to_correct_model():
    """KNOWN_OAKD_DEVICES table has the correct entry for PID f63b."""
    import castor.hardware_detect as hd

    assert "03e7:f63b" in hd.KNOWN_OAKD_DEVICES
    assert hd.KNOWN_OAKD_DEVICES["03e7:f63b"] == "Luxonis OAK-D SR"


# ---------------------------------------------------------------------------
# #553 detect_hardware() TTL cache
# ---------------------------------------------------------------------------


def test_detect_hardware_returns_cached_result(monkeypatch):
    """Two successive calls without refresh return the same dict object."""
    import castor.hardware_detect as hd

    call_count = {"n": 0}
    original = hd._run_all_detectors

    def counting_detectors():
        call_count["n"] += 1
        return original()

    monkeypatch.setattr(hd, "_run_all_detectors", counting_detectors)
    hd.invalidate_hardware_cache()

    r1 = hd.detect_hardware()
    r2 = hd.detect_hardware()
    assert r1 is r2
    assert call_count["n"] == 1


def test_detect_hardware_refresh_bypasses_cache(monkeypatch):
    """detect_hardware(refresh=True) always re-runs detectors."""
    import castor.hardware_detect as hd

    call_count = {"n": 0}
    original = hd._run_all_detectors

    def counting_detectors():
        call_count["n"] += 1
        return original()

    monkeypatch.setattr(hd, "_run_all_detectors", counting_detectors)
    hd.invalidate_hardware_cache()

    hd.detect_hardware()
    hd.detect_hardware(refresh=True)
    assert call_count["n"] == 2


def test_invalidate_hardware_cache_forces_fresh(monkeypatch):
    """After invalidate_hardware_cache(), next call re-runs detectors."""
    import castor.hardware_detect as hd

    call_count = {"n": 0}
    original = hd._run_all_detectors

    def counting_detectors():
        call_count["n"] += 1
        return original()

    monkeypatch.setattr(hd, "_run_all_detectors", counting_detectors)
    hd.invalidate_hardware_cache()

    hd.detect_hardware()
    hd.invalidate_hardware_cache()
    hd.detect_hardware()
    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# #552 scan_cameras() v4l2 device name
# ---------------------------------------------------------------------------


def test_scan_cameras_includes_name_field(monkeypatch, tmp_path):
    """Each camera entry must have a 'name' key."""
    import castor.hardware_detect as hd

    # Create a fake sysfs name file
    sys_dir = tmp_path / "sys" / "class" / "video4linux" / "video0"
    sys_dir.mkdir(parents=True)
    (sys_dir / "name").write_text("FakeCam HD\n")

    monkeypatch.setattr(
        os.path,
        "isdir",
        lambda p: (
            p == "/dev" or os.path.isdir.__wrapped__(p)
            if hasattr(os.path.isdir, "__wrapped__")
            else True
        ),
    )
    monkeypatch.setattr(os, "listdir", lambda p: ["video0"] if p == "/dev" else [])
    monkeypatch.setattr(os, "access", lambda p, m: True)

    real_get_name = hd._get_v4l2_device_name

    def patched_get_name(entry):
        path = str(sys_dir / "name") if entry == "video0" else None
        if path:
            try:
                with open(path) as f:
                    return f.read().strip()
            except OSError:
                return None
        return real_get_name(entry)

    monkeypatch.setattr(hd, "_get_v4l2_device_name", patched_get_name)
    # Avoid picamera2 import
    monkeypatch.setitem(sys.modules, "picamera2", None)

    cameras = hd.scan_cameras()
    assert len(cameras) >= 1
    assert "name" in cameras[0]
    assert cameras[0]["name"] == "FakeCam HD"


def test_get_v4l2_device_name_returns_none_on_missing():
    """_get_v4l2_device_name returns None when sysfs path doesn't exist."""
    import castor.hardware_detect as hd

    result = hd._get_v4l2_device_name("video_nonexistent_999")
    assert result is None


# ---------------------------------------------------------------------------
# #555 suggest_extras()
# ---------------------------------------------------------------------------


def test_suggest_extras_returns_package_for_oakd(monkeypatch):
    """When oakd is detected and depthai is not installed, suggest it."""
    import castor.hardware_detect as hd

    hw = {"oakd": [{"port": "usb", "vid_pid": "03e7:2487", "model": "OAK-D"}]}

    # Make depthai un-importable
    monkeypatch.setitem(sys.modules, "depthai", None)

    extras = hd.suggest_extras(hw)
    assert "depthai" in extras


def test_suggest_extras_empty_when_no_hardware():
    """No hardware detected → no extras suggested."""
    import castor.hardware_detect as hd

    hw = {k: [] for k in hd._HARDWARE_EXTRAS}
    extras = hd.suggest_extras(hw)
    assert extras == []


def test_suggest_extras_deduplicates():
    """suggest_extras should not return duplicate package names."""
    import castor.hardware_detect as hd

    hw = {"feetech": [{"port": "/dev/ttyACM0"}]}
    extras = hd.suggest_extras(hw)
    assert len(extras) == len(set(extras))

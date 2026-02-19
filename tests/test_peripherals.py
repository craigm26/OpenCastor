"""
tests/test_peripherals.py — Tests for castor.peripherals plug-and-play auto-detection.

Tests use mocking to simulate hardware without requiring physical devices.
"""

import io
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from castor.peripherals import (
    PeripheralInfo,
    _I2C_DEVICES,
    _USB_DEVICES,
    print_scan_table,
    scan_all,
    scan_i2c,
    scan_npu,
    scan_serial,
    scan_usb,
    scan_v4l2,
    to_rcan_snippet,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

LSUSB_OAKD_LINE = (
    "Bus 001 Device 005: ID 03e7:2485 Intel Corp. Movidius MyriadX\n"
    "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
)

LSUSB_UNKNOWN_LINE = (
    "Bus 002 Device 003: ID dead:beef Unknown Gadget Corp. SuperThing 9000\n"
    "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
)

LSUSB_ARDUINO_LINE = (
    "Bus 001 Device 007: ID 2341:0043 Arduino SA Uno R3\n"
)

I2CDETECT_PCA9685_OUTPUT = """\
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00:          -- -- -- -- -- -- -- -- -- -- -- -- --
10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
20: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
30: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
40: 40 -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
50: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
60: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
70: -- -- -- -- -- -- -- --
"""

I2CDETECT_MULTI_OUTPUT = """\
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00:          -- -- -- -- -- -- -- -- -- -- -- -- --
10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
20: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
30: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
40: 40 -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
50: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
60: -- -- -- -- -- -- -- -- 68 -- -- -- -- -- -- --
70: -- -- -- -- -- -- -- --
"""


def _make_completed_process(stdout: str, returncode: int = 0):
    """Create a mock CompletedProcess."""
    mock = MagicMock()
    mock.stdout = stdout
    mock.stderr = ""
    mock.returncode = returncode
    return mock


# ---------------------------------------------------------------------------
# Test 1: scan_usb parses known VID:PID (OAK-D)
# ---------------------------------------------------------------------------


def test_scan_usb_parses_known_vid_pid():
    """scan_usb should return an identified PeripheralInfo for OAK-D."""
    with patch("subprocess.run", return_value=_make_completed_process(LSUSB_OAKD_LINE)):
        results = scan_usb()

    # Filter out root hub (skipped internally)
    oakd = [p for p in results if p.usb_id == "03e7:2485"]
    assert len(oakd) == 1, f"Expected exactly 1 OAK-D, got {len(oakd)}: {results}"

    p = oakd[0]
    assert p.name == "OAK-D / OAK-D Lite / OAK-D Pro"
    assert p.category == "depth"
    assert p.interface == "usb"
    assert p.driver_hint == "depthai"
    assert p.confidence == "identified"
    assert "oakd" in p.rcan_snippet


# ---------------------------------------------------------------------------
# Test 2: scan_usb returns confidence="unknown" for unknown VID:PID
# ---------------------------------------------------------------------------


def test_scan_usb_parses_unknown_device():
    """Unknown VID:PID should still be returned with confidence='unknown'."""
    with patch("subprocess.run", return_value=_make_completed_process(LSUSB_UNKNOWN_LINE)):
        results = scan_usb()

    unknown = [p for p in results if p.usb_id == "dead:beef"]
    assert len(unknown) == 1, f"Expected 1 unknown device, got: {results}"
    p = unknown[0]
    assert p.confidence == "unknown"
    assert p.category == "unknown"


# ---------------------------------------------------------------------------
# Test 3: scan_v4l2 finds /dev/video0 and returns camera PeripheralInfo
# ---------------------------------------------------------------------------


def test_scan_v4l2_finds_devices():
    """scan_v4l2 should return a PeripheralInfo for each /dev/video* device."""
    # Mock glob to return /dev/video0
    with patch("glob.glob", return_value=["/dev/video0"]):
        # Mock v4l2-ctl to return a card name
        mock_v4l2 = _make_completed_process(
            "Driver name   : uvcvideo\nCard type     : Logitech HD Pro Webcam C920\n"
        )
        with patch("subprocess.run", return_value=mock_v4l2):
            results = scan_v4l2()

    assert len(results) >= 1
    p = results[0]
    assert p.category == "camera"
    assert p.device_path == "/dev/video0"
    assert "video0" in p.rcan_snippet


# ---------------------------------------------------------------------------
# Test 4: scan_i2c parses PCA9685 at 0x40
# ---------------------------------------------------------------------------


def test_scan_i2c_parses_pca9685():
    """scan_i2c should detect PCA9685 at address 0x40."""
    with patch("subprocess.run", return_value=_make_completed_process(I2CDETECT_PCA9685_OUTPUT)):
        results = scan_i2c(bus=1)

    pca = [p for p in results if p.i2c_address == 0x40]
    assert len(pca) == 1, f"Expected PCA9685 at 0x40, got: {results}"
    p = pca[0]
    assert "pca9685" in p.name.lower()
    assert p.category == "motor"
    assert p.interface == "i2c"
    assert p.confidence == "identified"
    assert "pca9685" in p.rcan_snippet


# ---------------------------------------------------------------------------
# Test 5: scan_i2c gracefully handles i2cdetect not found
# ---------------------------------------------------------------------------


def test_scan_i2c_graceful_failure():
    """scan_i2c should return empty list without crashing if i2cdetect is missing."""
    with patch("subprocess.run", side_effect=FileNotFoundError("i2cdetect not found")):
        results = scan_i2c(bus=1)

    assert results == []


# ---------------------------------------------------------------------------
# Test 6: scan_serial finds /dev/ttyUSB0
# ---------------------------------------------------------------------------


def test_scan_serial_finds_ttyusb():
    """scan_serial should return a PeripheralInfo for /dev/ttyUSB0."""
    with patch("glob.glob", side_effect=lambda pattern: ["/dev/ttyUSB0"] if "ttyUSB" in pattern else []):
        with patch("os.path.realpath", return_value="/dev/ttyUSB0"):
            results = scan_serial()

    assert len(results) >= 1
    p = results[0]
    assert p.device_path == "/dev/ttyUSB0"
    assert p.category == "serial"
    assert p.interface == "serial"
    assert "ttyUSB0" in p.rcan_snippet


# ---------------------------------------------------------------------------
# Test 7: scan_npu detects Hailo via /dev/hailo* glob
# ---------------------------------------------------------------------------


def test_scan_npu_hailo_detected():
    """scan_npu should detect Hailo-8 when /dev/hailo0 exists."""
    with patch("glob.glob") as mock_glob:
        def glob_side_effect(pattern):
            if "hailo" in pattern:
                return ["/dev/hailo0"]
            elif "apex" in pattern:
                return []
            return []

        mock_glob.side_effect = glob_side_effect
        # Mock lsusb to avoid Coral detection interference
        with patch("subprocess.run", return_value=_make_completed_process("")):
            results = scan_npu()

    hailo = [p for p in results if p.driver_hint == "hailo"]
    assert len(hailo) >= 1, f"Expected Hailo NPU, got: {results}"
    p = hailo[0]
    assert p.category == "npu"
    assert p.confidence == "identified"
    assert "hailo" in p.rcan_snippet


# ---------------------------------------------------------------------------
# Test 8: scan_all deduplicates OAK-D (USB + v4l2)
# ---------------------------------------------------------------------------


def test_scan_all_deduplicates():
    """OAK-D found by USB scan should not be double-counted by v4l2 scan."""
    # USB scan finds OAK-D
    mock_usb_peripheral = PeripheralInfo(
        name="OAK-D / OAK-D Lite / OAK-D Pro",
        category="depth",
        interface="usb",
        device_path=None,
        usb_id="03e7:2485",
        i2c_address=None,
        driver_hint="depthai",
        rcan_snippet='camera:\n  type: "oakd"\n  depth_enabled: true\n  fps: 30',
        confidence="identified",
    )

    # v4l2 scan finds /dev/video0 (generic, unnamed — would duplicate)
    mock_v4l2_peripheral = PeripheralInfo(
        name="Video device /dev/video0",
        category="camera",
        interface="usb",
        device_path="/dev/video0",
        usb_id=None,
        i2c_address=None,
        driver_hint="v4l2",
        rcan_snippet='camera:\n  type: "usb"\n  device: "/dev/video0"\n  fps: 30',
        confidence="probable",
    )

    with patch("castor.peripherals.scan_usb", return_value=[mock_usb_peripheral]):
        with patch("castor.peripherals.scan_v4l2", return_value=[mock_v4l2_peripheral]):
            with patch("castor.peripherals.scan_i2c", return_value=[]):
                with patch("castor.peripherals.scan_serial", return_value=[]):
                    with patch("castor.peripherals.scan_npu", return_value=[]):
                        with patch("castor.peripherals.scan_csi", return_value=[]):
                            results = scan_all()

    # Should have OAK-D but the generic /dev/video0 entry should be suppressed
    oakd = [p for p in results if p.usb_id == "03e7:2485"]
    assert len(oakd) == 1, "OAK-D should appear exactly once"

    # The nameless v4l2 entry should be deduplicated
    generic_v4l2 = [p for p in results if p.name == "Video device /dev/video0"]
    assert len(generic_v4l2) == 0, "Generic v4l2 entry should be suppressed when USB depth found"


# ---------------------------------------------------------------------------
# Test 9: OAK-D peripheral returns expected RCAN snippet
# ---------------------------------------------------------------------------


def test_rcan_snippet_oakd():
    """to_rcan_snippet should return the correct RCAN yaml for OAK-D."""
    oakd = PeripheralInfo(
        name="OAK-D / OAK-D Lite / OAK-D Pro",
        category="depth",
        interface="usb",
        device_path=None,
        usb_id="03e7:2485",
        i2c_address=None,
        driver_hint="depthai",
        rcan_snippet='camera:\n  type: "oakd"\n  depth_enabled: true\n  fps: 30',
        confidence="identified",
    )
    snippet = to_rcan_snippet(oakd)
    assert 'type: "oakd"' in snippet
    assert "depth_enabled: true" in snippet
    assert "fps: 30" in snippet
    assert snippet == oakd.rcan_snippet


# ---------------------------------------------------------------------------
# Test 10: print_scan_table does not crash on empty list
# ---------------------------------------------------------------------------


def test_print_scan_table_no_crash():
    """print_scan_table should handle an empty list without raising exceptions."""
    captured = io.StringIO()
    with patch("sys.stdout", captured):
        try:
            print_scan_table([], color=False)
        except Exception as exc:
            pytest.fail(f"print_scan_table raised an exception on empty list: {exc}")


# ---------------------------------------------------------------------------
# Test 11: print_scan_table no crash with real peripherals
# ---------------------------------------------------------------------------


def test_print_scan_table_with_peripherals_no_crash():
    """print_scan_table should render a table for real PeripheralInfo objects."""
    peripherals = [
        PeripheralInfo(
            name="Logitech HD Pro Webcam C920",
            category="camera",
            interface="usb",
            device_path="/dev/video0",
            usb_id="046d:082d",
            i2c_address=None,
            driver_hint="v4l2",
            rcan_snippet='camera:\n  type: "usb"\n  device: "/dev/video0"\n  fps: 30',
            confidence="identified",
        ),
        PeripheralInfo(
            name="MPU-6050 IMU",
            category="imu",
            interface="i2c",
            device_path="/dev/i2c-1",
            usb_id=None,
            i2c_address=0x68,
            driver_hint="mpu6050",
            rcan_snippet='imu:\n  type: "mpu6050"\n  i2c_bus: 1',
            confidence="identified",
        ),
    ]
    captured = io.StringIO()
    with patch("sys.stdout", captured):
        try:
            print_scan_table(peripherals, color=False)
        except Exception as exc:
            pytest.fail(f"print_scan_table raised an exception: {exc}")


# ---------------------------------------------------------------------------
# Test 12: scan_usb skips root hubs
# ---------------------------------------------------------------------------


def test_scan_usb_skips_root_hubs():
    """scan_usb should skip USB hub / root hub entries."""
    lsusb_with_hubs = (
        "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
        "Bus 002 Device 001: ID 1d6b:0003 Linux Foundation 3.0 root hub\n"
        "Bus 001 Device 002: ID 8087:0024 Intel Corp. Integrated Rate Matching Hub\n"
    )
    with patch("subprocess.run", return_value=_make_completed_process(lsusb_with_hubs)):
        results = scan_usb()

    # All entries are hubs — none should be returned (or returned as unknown with hub names)
    hub_entries = [p for p in results if "hub" in p.name.lower()]
    assert len(hub_entries) == 0, f"Hub entries should be filtered: {results}"


# ---------------------------------------------------------------------------
# Test 13: scan_i2c handles multiple addresses
# ---------------------------------------------------------------------------


def test_scan_i2c_multiple_addresses():
    """scan_i2c should detect all addresses in i2cdetect output."""
    with patch("subprocess.run", return_value=_make_completed_process(I2CDETECT_MULTI_OUTPUT)):
        results = scan_i2c(bus=1)

    addresses = {p.i2c_address for p in results}
    assert 0x40 in addresses, "PCA9685 at 0x40 should be detected"
    assert 0x68 in addresses, "MPU-6050 at 0x68 should be detected"


# ---------------------------------------------------------------------------
# Test 14: scan_all returns sorted list
# ---------------------------------------------------------------------------


def test_scan_all_returns_sorted():
    """scan_all result should be sorted by category priority."""
    depth_p = PeripheralInfo(
        name="OAK-D", category="depth", interface="usb",
        device_path=None, usb_id="03e7:2485", i2c_address=None,
        driver_hint="depthai", rcan_snippet="", confidence="identified",
    )
    motor_p = PeripheralInfo(
        name="PCA9685", category="motor", interface="i2c",
        device_path="/dev/i2c-1", usb_id=None, i2c_address=0x40,
        driver_hint="pca9685", rcan_snippet="", confidence="identified",
    )
    serial_p = PeripheralInfo(
        name="Arduino", category="serial", interface="serial",
        device_path="/dev/ttyACM0", usb_id=None, i2c_address=None,
        driver_hint="arduino", rcan_snippet="", confidence="probable",
    )

    with patch("castor.peripherals.scan_usb", return_value=[serial_p, motor_p, depth_p]):
        with patch("castor.peripherals.scan_v4l2", return_value=[]):
            with patch("castor.peripherals.scan_i2c", return_value=[]):
                with patch("castor.peripherals.scan_serial", return_value=[]):
                    with patch("castor.peripherals.scan_npu", return_value=[]):
                        with patch("castor.peripherals.scan_csi", return_value=[]):
                            results = scan_all()

    categories = [p.category for p in results]
    # depth should come before motor, motor before serial
    if "depth" in categories and "motor" in categories:
        assert categories.index("depth") < categories.index("motor")
    if "motor" in categories and "serial" in categories:
        assert categories.index("motor") < categories.index("serial")


# ---------------------------------------------------------------------------
# Test 15: USB device database sanity checks
# ---------------------------------------------------------------------------


def test_usb_database_has_required_entries():
    """_USB_DEVICES must contain all required VID:PIDs."""
    required = [
        "03e7:2485",   # OAK-D
        "8086:0b3a",   # RealSense D435
        "2341:0043",   # Arduino Uno
        "046d:082d",   # Logitech C920
        "0403:6015",   # FTDI FT231X (RPLiDAR)
        "1a86:7523",   # CH340 USB-Serial
    ]
    for vid_pid in required:
        assert vid_pid in _USB_DEVICES, f"Missing required VID:PID: {vid_pid}"


def test_i2c_database_has_required_entries():
    """_I2C_DEVICES must contain all required addresses."""
    required = [0x40, 0x68, 0x28, 0x29, 0x3C, 0x77, 0x48]
    for addr in required:
        assert addr in _I2C_DEVICES, f"Missing required I2C address: 0x{addr:02X}"

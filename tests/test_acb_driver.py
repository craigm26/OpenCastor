"""Tests for HLaboratories ACB v2.0 driver (#518–#525).

Covers:
  - Mock mode (no hardware needed)
  - USB serial path with mocked pyserial
  - CAN transport path with mocked python-can
  - CalibrationResult dataclass
  - Calibration flow
  - Hardware auto-detection (mocked serial.tools.list_ports)
  - Hardware profile loading
  - CanTransport frame encoding/decoding
  - API endpoints: /api/hardware/scan, /api/drivers/{id}/telemetry,
                   /api/drivers/{id}/calibrate
"""

from __future__ import annotations

import json
import pathlib
import struct
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_acb_driver(config: dict | None = None, *, force_mock: bool = True):
    """Construct an AcbDriver in mock mode with telemetry disabled."""
    with (
        patch("castor.drivers.acb_driver.HAS_PYSERIAL", False),
        patch("castor.drivers.acb_driver.HAS_CAN_TRANSPORT", False),
    ):
        from castor.drivers.acb_driver import AcbDriver

        cfg = {"id": "test_motor", "mock": True, "telemetry_hz": 0, **(config or {})}
        drv = AcbDriver(cfg)
    return drv


# ---------------------------------------------------------------------------
# TestAcbDriverMock — all methods work in mock mode
# ---------------------------------------------------------------------------


class TestAcbDriverMock:
    def test_init_mock_mode(self):
        drv = _make_acb_driver()
        assert drv._mode == "mock"
        assert drv._driver_id == "test_motor"
        drv.close()

    def test_move_mock_no_raise(self):
        drv = _make_acb_driver()
        drv.move(0.5)
        drv.move(-1.0, 0.1)
        drv.close()

    def test_set_velocity_mock(self):
        drv = _make_acb_driver()
        drv.set_velocity(10.0)
        drv.set_velocity(-5.0)
        drv.close()

    def test_set_position_mock(self):
        drv = _make_acb_driver()
        drv.set_position(3.14)
        drv.close()

    def test_set_torque_mock(self):
        drv = _make_acb_driver()
        drv.set_torque(0.5)
        drv.close()

    def test_get_encoder_mock_returns_zeros(self):
        drv = _make_acb_driver()
        enc = drv.get_encoder()
        assert enc["pos_rad"] == 0.0
        assert enc["vel_rad_s"] == 0.0
        assert enc["current_a"] == 0.0
        assert enc["error_flags"] == 0
        drv.close()

    def test_stop_mock(self):
        drv = _make_acb_driver()
        drv.stop()
        drv.close()

    def test_health_check_mock(self):
        drv = _make_acb_driver()
        hc = drv.health_check()
        assert hc["ok"] is True
        assert hc["mode"] == "mock"
        assert hc["transport"] == "usb"
        assert "control_mode" in hc
        assert "pole_pairs" in hc
        drv.close()

    def test_telemetry_contains_expected_keys(self):
        drv = _make_acb_driver()
        tel = drv.get_telemetry()
        for key in (
            "pos_rad",
            "vel_rad_s",
            "current_a",
            "voltage_v",
            "error_flags",
            "is_calibrated",
            "control_mode",
            "ts",
        ):
            assert key in tel, f"Missing key: {key}"
        drv.close()

    def test_close_idempotent(self):
        drv = _make_acb_driver()
        drv.close()
        drv.close()  # second close must not raise

    def test_move_with_angular_ignored(self):
        """angular param is accepted but ignored for single-axis ACB."""
        drv = _make_acb_driver()
        drv.move(linear=0.3, angular=0.5)
        drv.close()

    def test_control_mode_stored(self):
        drv = _make_acb_driver({"control_mode": "position"})
        assert drv._control_mode == "position"
        drv.close()

    def test_pole_pairs_stored(self):
        drv = _make_acb_driver({"pole_pairs": 14})
        assert drv._pole_pairs == 14
        drv.close()

    def test_telemetry_thread_started_when_hz_nonzero(self):
        drv = _make_acb_driver({"telemetry_hz": 10})
        assert drv._telemetry_thread is not None
        assert drv._telemetry_thread.daemon is True
        drv.close()

    def test_telemetry_thread_not_started_when_hz_zero(self):
        drv = _make_acb_driver({"telemetry_hz": 0})
        assert drv._telemetry_thread is None
        drv.close()


# ---------------------------------------------------------------------------
# TestAcbCalibration — CalibrationResult + calibration flow in mock mode
# ---------------------------------------------------------------------------


class TestAcbCalibration:
    def test_calibrate_mock_returns_success(self):
        drv = _make_acb_driver({"pid": {"vel_p": 0.25}})
        result = drv.calibrate()
        assert result.success is True
        assert result.error is None
        assert result.pole_pairs == 7
        drv.close()

    def test_calibrate_result_to_dict(self):
        from castor.drivers.acb_driver import CalibrationResult

        r = CalibrationResult(
            success=True,
            zero_electrical_angle=0.123,
            pole_pairs=7,
            pid_applied={"vel_p": 0.25},
            error=None,
        )
        d = r.to_dict()
        assert d["success"] is True
        assert d["zero_electrical_angle"] == pytest.approx(0.123)
        assert d["pole_pairs"] == 7
        assert d["pid_applied"]["vel_p"] == pytest.approx(0.25)
        assert d["error"] is None

    def test_calibrate_result_with_error(self):
        from castor.drivers.acb_driver import CalibrationResult

        r = CalibrationResult(
            success=False,
            zero_electrical_angle=0.0,
            pole_pairs=7,
            pid_applied={},
            error="timeout",
        )
        assert r.to_dict()["error"] == "timeout"

    def test_calibrate_marks_telemetry_calibrated_in_mock(self):
        # In mock mode, is_calibrated is set to True after calibration
        drv = _make_acb_driver()
        drv.calibrate()
        tel = drv.get_telemetry()
        # Mock calibrate sets is_calibrated
        assert tel["is_calibrated"] is True
        drv.close()

    def test_calibrate_caches_to_disk(self, tmp_path):
        drv = _make_acb_driver({"id": "cache_test"})
        with patch("pathlib.Path.home", return_value=tmp_path):
            drv.calibrate()
        cache_file = tmp_path / ".opencastor" / "calibration" / "cache_test.json"
        assert cache_file.exists()
        data = json.loads(cache_file.read_text())
        assert data["success"] is True


# ---------------------------------------------------------------------------
# TestAcbDriverUsb — mocked pyserial; verify command serialization
# ---------------------------------------------------------------------------


class TestAcbDriverUsb:
    def _make_usb_driver(self):
        """Build an AcbDriver with a mocked serial.Serial connection.

        The driver is built in mock mode to avoid telemetry loop interference;
        tests manually set ``_mode = "hardware"`` and configure mock responses.
        """
        mock_serial = MagicMock()
        mock_serial.is_open = True
        mock_serial.readline.return_value = b""

        with patch("castor.drivers.acb_driver.HAS_PYSERIAL", False):
            from castor.drivers.acb_driver import AcbDriver

            drv = AcbDriver(
                {"id": "usb_test", "mock": True, "telemetry_hz": 0, "port": "/dev/ttyACM0"}
            )

        # Force hardware mode with the mock serial — telemetry loop runs in mock,
        # so it calls get_encoder() which returns zeros (mode check is first).
        drv._mode = "hardware"
        drv._serial_conn = mock_serial
        return drv, mock_serial

    def test_set_velocity_sends_json(self):
        drv, mock_serial = self._make_usb_driver()
        drv.set_velocity(5.0)
        # Grab the most recent write call (telemetry loop may also write)
        calls = [c[0][0] for c in mock_serial.write.call_args_list]
        velocity_calls = [json.loads(c.decode().strip()) for c in calls if c]
        vel_cmds = [c for c in velocity_calls if c.get("cmd") == "set_velocity"]
        assert any(c["value"] == pytest.approx(5.0) for c in vel_cmds)
        drv.close()

    def test_set_position_sends_json(self):
        drv, mock_serial = self._make_usb_driver()
        drv.set_position(1.57)
        calls = [c[0][0] for c in mock_serial.write.call_args_list]
        cmds = [json.loads(c.decode().strip()) for c in calls if c]
        pos_cmds = [c for c in cmds if c.get("cmd") == "set_position"]
        assert any(c["value"] == pytest.approx(1.57) for c in pos_cmds)
        drv.close()

    def test_set_torque_sends_json(self):
        drv, mock_serial = self._make_usb_driver()
        drv.set_torque(0.3)
        calls = [c[0][0] for c in mock_serial.write.call_args_list]
        cmds = [json.loads(c.decode().strip()) for c in calls if c]
        torque_cmds = [c for c in cmds if c.get("cmd") == "set_torque"]
        assert len(torque_cmds) >= 1
        drv.close()

    def test_get_encoder_parses_response(self):
        resp_bytes = (
            json.dumps(
                {"pos_rad": 1.23, "vel_rad_s": 4.56, "current_a": 0.78, "error_flags": 0}
            ).encode()
            + b"\n"
        )
        drv, mock_serial = self._make_usb_driver()
        # Temporarily set readline to always return the test response
        mock_serial.readline.return_value = resp_bytes
        enc = drv.get_encoder()
        assert enc["pos_rad"] == pytest.approx(1.23)
        assert enc["vel_rad_s"] == pytest.approx(4.56)
        assert enc["current_a"] == pytest.approx(0.78)
        drv.close()

    def test_get_encoder_empty_response_returns_zeros(self):
        drv, mock_serial = self._make_usb_driver()
        mock_serial.readline.return_value = b""
        enc = drv.get_encoder()
        assert enc["pos_rad"] == 0.0
        drv.close()

    def test_health_check_hardware_mode(self):
        drv, mock_serial = self._make_usb_driver()
        fw_resp = json.dumps({"version": "1.2.3"}).encode() + b"\n"
        mock_serial.readline.return_value = fw_resp
        hc = drv.health_check()
        assert hc["mode"] == "hardware"
        assert hc["ok"] is True
        assert hc.get("firmware_version") == "1.2.3"
        assert hc["port"] == "/dev/ttyACM0"
        drv.close()


# ---------------------------------------------------------------------------
# TestCanTransport — mocked python-can virtual interface
# ---------------------------------------------------------------------------


class TestCanTransport:
    def test_mock_mode_when_no_can(self):
        with patch("castor.drivers.can_transport.HAS_PYTHON_CAN", False):
            from castor.drivers.can_transport import CanTransport

            ct = CanTransport("socketcan", "can0")
        assert ct._bus is None

    def test_send_mock_no_raise(self):
        with patch("castor.drivers.can_transport.HAS_PYTHON_CAN", False):
            from castor.drivers.can_transport import CanTransport

            ct = CanTransport("socketcan", "can0")
        ct.send(1, 0x01, struct.pack("<f", 3.14))
        ct.close()

    def test_recv_mock_returns_none(self):
        with patch("castor.drivers.can_transport.HAS_PYTHON_CAN", False):
            from castor.drivers.can_transport import CanTransport

            ct = CanTransport("socketcan", "can0")
        assert ct.recv(timeout=0.01) is None

    def test_arb_id_encoding(self):
        from castor.drivers.can_transport import CanTransport

        # (node_id << 5) | cmd_id
        assert CanTransport._make_arb_id(1, 0x01) == 0b00000100001
        assert CanTransport._make_arb_id(3, 0x04) == (3 << 5) | 4
        assert CanTransport._make_arb_id(0, 0) == 0

    def test_arb_id_decoding_round_trip(self):
        from castor.drivers.can_transport import CanTransport

        node_id, cmd_id = 7, 0x03
        arb = CanTransport._make_arb_id(node_id, cmd_id)
        decoded_node = (arb >> 5) & 0x3F
        decoded_cmd = arb & 0x1F
        assert decoded_node == node_id
        assert decoded_cmd == cmd_id

    def test_send_with_mocked_bus(self):
        mock_bus = MagicMock()
        mock_message = MagicMock()

        with (
            patch("castor.drivers.can_transport.HAS_PYTHON_CAN", True),
            patch("castor.drivers.can_transport._can") as mock_can_mod,
        ):
            mock_can_mod.interface.Bus.return_value = mock_bus
            mock_can_mod.Message.return_value = mock_message
            from castor.drivers.can_transport import CanTransport

            ct = CanTransport("socketcan", "can0")
            ct._bus = mock_bus

            data = struct.pack("<f", 1.5)
            ct.send(1, 0x01, data)

        mock_bus.send.assert_called_once_with(mock_message)


# ---------------------------------------------------------------------------
# TestHardwareDetect — mocked serial.tools.list_ports
# ---------------------------------------------------------------------------


class TestHardwareDetect:
    def _make_port_info(self, vid, pid, device, description=""):
        port = MagicMock()
        port.vid = vid
        port.pid = pid
        port.device = device
        port.description = description
        port.product = ""
        return port

    def test_detect_acb_usb_finds_stm32(self):
        fake_port = self._make_port_info(0x0483, 0x5740, "/dev/ttyACM0", "STM32 Virtual COM")
        with patch("serial.tools.list_ports.comports", return_value=[fake_port]):
            from castor.hardware_detect import detect_acb_usb

            result = detect_acb_usb()
        assert "/dev/ttyACM0" in result

    def test_detect_acb_dfu_not_returned(self):
        """DFU mode devices must NOT appear in usable port list."""
        fake_port = self._make_port_info(0x0483, 0xDF11, "/dev/ttyACM1", "STM32 DFU")
        with patch("serial.tools.list_ports.comports", return_value=[fake_port]):
            from castor.hardware_detect import detect_acb_usb

            result = detect_acb_usb()
        assert "/dev/ttyACM1" not in result

    def test_detect_acb_usb_warns_on_dfu(self):
        from castor.hardware_detect import KNOWN_HLABS_DEVICES

        assert "0483:df11" in KNOWN_HLABS_DEVICES
        assert "DFU" in KNOWN_HLABS_DEVICES["0483:df11"]["name"]

    def test_detect_acb_usb_no_ports(self):
        with patch("serial.tools.list_ports.comports", return_value=[]):
            from castor.hardware_detect import detect_acb_usb

            ports = detect_acb_usb()
        assert ports == []

    def test_detect_all_hlabs_returns_dict(self):
        with patch("castor.hardware_detect.detect_acb_usb", return_value=["/dev/ttyACM0"]):
            from castor.hardware_detect import detect_all_hlabs

            result = detect_all_hlabs()
        assert "acb" in result
        assert "/dev/ttyACM0" in result["acb"]

    def test_detect_all_hlabs_empty(self):
        with patch("castor.hardware_detect.detect_acb_usb", return_value=[]):
            from castor.hardware_detect import detect_all_hlabs

            result = detect_all_hlabs()
        assert result == {"acb": []}

    def test_known_devices_table_exists(self):
        from castor.hardware_detect import KNOWN_HLABS_DEVICES

        assert isinstance(KNOWN_HLABS_DEVICES, dict)
        assert len(KNOWN_HLABS_DEVICES) > 0

    def test_acb_auto_detect_via_driver(self):
        """AcbDriver with port=auto should call detect_acb_usb."""
        with (
            patch("castor.drivers.acb_driver.HAS_PYSERIAL", False),
            patch("castor.hardware_detect.detect_acb_usb", return_value=[]) as mock_detect,
        ):
            from castor.drivers.acb_driver import AcbDriver

            drv = AcbDriver({"id": "auto_test", "port": "auto"})
        mock_detect.assert_called_once()
        assert drv._mode == "mock"
        drv.close()


# ---------------------------------------------------------------------------
# TestProfiles — load_profile('hlabs/acb-single') returns valid dict
# ---------------------------------------------------------------------------


class TestProfiles:
    def test_load_acb_single(self):
        from castor.profiles import load_profile

        cfg = load_profile("hlabs/acb-single")
        assert isinstance(cfg, dict)
        assert "drivers" in cfg
        assert cfg["drivers"][0]["protocol"] == "acb"

    def test_load_acb_arm_3dof(self):
        from castor.profiles import load_profile

        cfg = load_profile("hlabs/acb-arm-3dof")
        assert isinstance(cfg, dict)
        assert len(cfg["drivers"]) == 3
        ids = [d["id"] for d in cfg["drivers"]]
        assert "shoulder" in ids
        assert "elbow" in ids
        assert "wrist" in ids

    def test_load_acb_biped_6dof(self):
        from castor.profiles import load_profile

        cfg = load_profile("hlabs/acb-biped-6dof")
        assert len(cfg["drivers"]) == 6

    def test_load_profile_not_found_raises(self):
        from castor.profiles import load_profile

        with pytest.raises(FileNotFoundError):
            load_profile("hlabs/does-not-exist")

    def test_load_profile_path_traversal_raises(self):
        from castor.profiles import load_profile

        with pytest.raises(ValueError):
            load_profile("../../../etc/passwd")

    def test_load_profile_absolute_path_raises(self):
        from castor.profiles import load_profile

        with pytest.raises(ValueError):
            load_profile("/etc/passwd")

    def test_acb_single_has_pid(self):
        from castor.profiles import load_profile

        cfg = load_profile("hlabs/acb-single")
        drv = cfg["drivers"][0]
        pid = drv.get("pid", {})
        assert "vel_p" in pid
        assert "pos_p" in pid

    def test_acb_single_profile_key(self):
        from castor.profiles import load_profile

        cfg = load_profile("hlabs/acb-single")
        assert cfg.get("profile") == "hlabs/acb-single"


# ---------------------------------------------------------------------------
# TestAcbDriverRegistration — driver factory returns AcbDriver for 'acb'
# ---------------------------------------------------------------------------


class TestAcbDriverRegistration:
    def test_get_driver_acb_protocol(self):
        with (
            patch("castor.drivers.acb_driver.HAS_PYSERIAL", False),
            patch("castor.drivers.acb_driver.HAS_CAN_TRANSPORT", False),
        ):
            from castor.drivers import get_driver, is_supported_protocol

            assert is_supported_protocol("acb") is True
            config = {
                "drivers": [{"id": "test", "protocol": "acb", "mock": True}],
                "metadata": {"robot_name": "test"},
                "agent": {"model": "mock"},
            }
            drv = get_driver(config)
            assert drv is not None
            assert type(drv).__name__ == "AcbDriver"
            drv.close()


# ---------------------------------------------------------------------------
# TestAcbApiEndpoints — /api/hardware/scan, telemetry, calibrate
# ---------------------------------------------------------------------------


class TestAcbApiEndpoints:
    @pytest.fixture()
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient

        import castor.api as _api

        monkeypatch.setattr(_api, "API_TOKEN", None)
        _api.state.driver = None
        return TestClient(_api.app)

    def test_hardware_scan_returns_devices(self, client):
        with patch(
            "castor.hardware_detect.detect_all_hlabs",
            return_value={"acb": ["/dev/ttyACM0"]},
        ):
            r = client.get("/api/hardware/scan")
        assert r.status_code == 200
        data = r.json()
        assert "devices" in data
        assert "timestamp" in data

    def test_driver_telemetry_404_when_no_driver(self, client):
        r = client.get("/api/drivers/motor_0/telemetry")
        assert r.status_code == 404

    def test_driver_telemetry_with_acb_driver(self, client):
        import castor.api as _api
        from castor.drivers.acb_driver import AcbDriver

        drv = _make_acb_driver({"id": "motor_0"})
        _api.state.driver = drv
        try:
            r = client.get("/api/drivers/motor_0/telemetry")
            assert r.status_code == 200
            tel = r.json()
            assert "pos_rad" in tel
            assert "vel_rad_s" in tel
            assert "current_a" in tel
        finally:
            drv.close()
            _api.state.driver = None

    def test_driver_calibrate_404_when_no_driver(self, client):
        r = client.post("/api/drivers/motor_0/calibrate")
        assert r.status_code == 404

    def test_driver_calibrate_with_acb_driver(self, client):
        import castor.api as _api

        drv = _make_acb_driver({"id": "motor_0"})
        _api.state.driver = drv
        try:
            r = client.post("/api/drivers/motor_0/calibrate")
            assert r.status_code == 200
            data = r.json()
            assert "success" in data
            assert data["success"] is True
        finally:
            drv.close()
            _api.state.driver = None

    def test_flash_requires_confirm(self, client):
        import castor.api as _api

        drv = _make_acb_driver({"id": "motor_0"})
        _api.state.driver = drv
        try:
            r = client.post(
                "/api/drivers/motor_0/flash",
                json={"confirm": False, "firmware_url": None},
            )
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "confirm_required"
        finally:
            drv.close()
            _api.state.driver = None


# ---------------------------------------------------------------------------
# TestAcbMetrics — record_acb_telemetry updates gauges
# ---------------------------------------------------------------------------


class TestAcbMetrics:
    def test_record_acb_telemetry(self):
        from castor.metrics import MetricsRegistry

        reg = MetricsRegistry()
        reg.record_acb_telemetry(
            joint="hip_l",
            pos_rad=1.23,
            vel_rad_s=0.5,
            current_a=2.1,
            error_flags=0,
        )
        # Check gauge updated without error
        g = reg._gauges.get("opencastor_acb_position_rad")
        assert g is not None

    def test_record_acb_telemetry_disabled(self):
        from castor.metrics import MetricsRegistry

        reg = MetricsRegistry()
        reg._enabled = False
        reg.record_acb_telemetry("hip_l", 0.0, 0.0, 0.0, 0)
        # Should not raise

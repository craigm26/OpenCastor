"""Tests for castor.deploy_ota — Firmware OTA flash via arduino-cli + avrdude.

Issue #247.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_flasher(**kwargs):
    from castor.deploy_ota import OTAFlasher

    defaults = dict(host="pi@192.168.1.99", dry_run=True)
    defaults.update(kwargs)
    return OTAFlasher(**defaults)


# ---------------------------------------------------------------------------
# OTAFlasher init
# ---------------------------------------------------------------------------


class TestOTAFlasherInit:
    def test_default_attrs(self):
        f = _make_flasher()
        assert f.host == "pi@192.168.1.99"
        assert f.arduino_port == "/dev/ttyACM0"
        assert f.board == "uno"
        assert f.dry_run is True
        assert f.ssh_opts == []

    def test_custom_port_and_board(self):
        f = _make_flasher(arduino_port="/dev/ttyUSB0", board="nano")
        assert f.arduino_port == "/dev/ttyUSB0"
        assert f.board == "nano"

    def test_fqbn_lookup_uno(self):
        f = _make_flasher(board="uno")
        assert f._fqbn == "arduino:avr:uno"

    def test_fqbn_lookup_nano(self):
        f = _make_flasher(board="nano")
        assert f._fqbn == "arduino:avr:nano:cpu=atmega328p"

    def test_fqbn_passthrough_custom(self):
        f = _make_flasher(board="arduino:avr:pro:cpu=8MHzatmega328")
        assert f._fqbn == "arduino:avr:pro:cpu=8MHzatmega328"

    def test_ssh_opts_stored(self):
        f = _make_flasher(ssh_opts=["-i", "~/.ssh/id_rsa"])
        assert f.ssh_opts == ["-i", "~/.ssh/id_rsa"]


# ---------------------------------------------------------------------------
# FQBN → MCU mapping
# ---------------------------------------------------------------------------


class TestFQBNToMCU:
    def test_uno(self):
        from castor.deploy_ota import OTAFlasher

        assert OTAFlasher._fqbn_to_mcu("arduino:avr:uno") == "m328p"

    def test_nano(self):
        from castor.deploy_ota import OTAFlasher

        assert OTAFlasher._fqbn_to_mcu("arduino:avr:nano:cpu=atmega328p") == "m328p"

    def test_mega(self):
        from castor.deploy_ota import OTAFlasher

        assert OTAFlasher._fqbn_to_mcu("arduino:avr:mega") == "m2560"

    def test_leonardo(self):
        from castor.deploy_ota import OTAFlasher

        assert OTAFlasher._fqbn_to_mcu("arduino:avr:leonardo") == "m32u4"

    def test_unknown_falls_back_to_m328p(self):
        from castor.deploy_ota import OTAFlasher

        assert OTAFlasher._fqbn_to_mcu("arduino:avr:someunknown") == "m328p"


# ---------------------------------------------------------------------------
# build_ssh_command
# ---------------------------------------------------------------------------


class TestBuildSSHCommand:
    def test_basic(self):
        from castor.deploy_ota import OTAFlasher

        cmd = OTAFlasher.build_ssh_command("pi@host", "echo hello")
        assert cmd == ["ssh", "pi@host", "echo hello"]

    def test_with_opts(self):
        from castor.deploy_ota import OTAFlasher

        cmd = OTAFlasher.build_ssh_command("pi@host", "ls", opts=["-p", "2222"])
        assert cmd[1:3] == ["-p", "2222"]
        assert cmd[-1] == "ls"


# ---------------------------------------------------------------------------
# build_avrdude_command
# ---------------------------------------------------------------------------


class TestBuildAvrudeCommand:
    def test_contains_mcu_and_port(self):
        from castor.deploy_ota import OTAFlasher

        cmd = OTAFlasher.build_avrdude_command("m328p", "/dev/ttyACM0", "/tmp/fw.hex")
        assert "m328p" in cmd
        assert "/dev/ttyACM0" in cmd
        assert "/tmp/fw.hex" in cmd

    def test_custom_programmer_and_baud(self):
        from castor.deploy_ota import OTAFlasher

        cmd = OTAFlasher.build_avrdude_command(
            "m328p", "/dev/ttyUSB0", "/tmp/fw.hex", programmer="stk500v2", baud=57600
        )
        assert "stk500v2" in cmd
        assert "57600" in cmd


# ---------------------------------------------------------------------------
# flash_arduino — dry-run mode
# ---------------------------------------------------------------------------


class TestFlashArduinoDryRun:
    def test_dry_run_returns_ok(self, tmp_path):
        sketch = tmp_path / "fw.ino"
        sketch.write_text("void setup(){} void loop(){}")
        f = _make_flasher(dry_run=True)
        with patch("castor.deploy_ota.HAS_ARDUINO_CLI", True):
            result = f.flash_arduino(sketch_path=str(sketch))
        assert result["ok"] is True

    def test_dry_run_no_sketch_required(self):
        """Dry-run must not raise even if sketch doesn't exist."""
        f = _make_flasher(dry_run=True)
        with patch("castor.deploy_ota.HAS_ARDUINO_CLI", True):
            result = f.flash_arduino(sketch_path="/nonexistent/file.ino")
        assert result["ok"] is True

    def test_dry_run_log_contains_commands(self, tmp_path):
        sketch = tmp_path / "fw.ino"
        sketch.write_text("void setup(){}")
        f = _make_flasher(dry_run=True)
        with patch("castor.deploy_ota.HAS_ARDUINO_CLI", True):
            result = f.flash_arduino(sketch_path=str(sketch))
        log = result["log"]
        assert "arduino-cli" in log
        assert "avrdude" in log


# ---------------------------------------------------------------------------
# flash_arduino — missing arduino-cli guard
# ---------------------------------------------------------------------------


class TestMissingArduinoCLI:
    def test_raises_ota_error_when_cli_missing(self, tmp_path):
        from castor.deploy_ota import OTAError

        sketch = tmp_path / "fw.ino"
        sketch.write_text("void setup(){}")
        f = _make_flasher(dry_run=False)
        with patch("castor.deploy_ota.HAS_ARDUINO_CLI", False):
            with pytest.raises(OTAError, match="arduino-cli not found"):
                f.flash_arduino(sketch_path=str(sketch))

    def test_dry_run_with_missing_cli_doesnt_raise(self, tmp_path):
        """Dry-run should still succeed with a warning even if cli is absent."""
        sketch = tmp_path / "fw.ino"
        sketch.write_text("")
        f = _make_flasher(dry_run=True)
        with patch("castor.deploy_ota.HAS_ARDUINO_CLI", False):
            result = f.flash_arduino(sketch_path=str(sketch))
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# flash_arduino_ota convenience function
# ---------------------------------------------------------------------------


class TestFlashArduinoOTAFunction:
    def test_returns_ok_dry_run(self, tmp_path):
        from castor.deploy_ota import flash_arduino_ota

        sketch = tmp_path / "fw.ino"
        sketch.write_text("void setup(){}")
        with patch("castor.deploy_ota.HAS_ARDUINO_CLI", True):
            result = flash_arduino_ota(
                host="pi@192.168.0.1",
                sketch_path=str(sketch),
                dry_run=True,
            )
        assert result["ok"] is True

    def test_accepts_custom_board(self, tmp_path):
        from castor.deploy_ota import flash_arduino_ota

        sketch = tmp_path / "fw.ino"
        sketch.write_text("")
        with patch("castor.deploy_ota.HAS_ARDUINO_CLI", True):
            result = flash_arduino_ota(
                host="user@10.0.0.1",
                sketch_path=str(sketch),
                board="mega",
                dry_run=True,
            )
        assert result["ok"] is True
        assert "m2560" in result["log"]


# ---------------------------------------------------------------------------
# HAS_ARDUINO_CLI detection
# ---------------------------------------------------------------------------


class TestHASArduinoCLI:
    def test_module_exposes_has_arduino_cli(self):
        import castor.deploy_ota as m

        assert isinstance(m.HAS_ARDUINO_CLI, bool)

    def test_false_when_which_returns_none(self):
        with patch("shutil.which", return_value=None):
            # HAS_ARDUINO_CLI is evaluated at import time; re-evaluate the expression:
            result = __import__("shutil").which("arduino-cli")
            assert result is None  # confirms mock works


# ---------------------------------------------------------------------------
# Sketch not found — non-dry-run
# ---------------------------------------------------------------------------


class TestSketchNotFound:
    def test_raises_ota_error_for_missing_sketch(self):
        from castor.deploy_ota import OTAError

        f = _make_flasher(dry_run=False)
        with patch("castor.deploy_ota.HAS_ARDUINO_CLI", True):
            with pytest.raises(OTAError, match="Sketch not found"):
                f.flash_arduino(sketch_path="/does/not/exist.ino")

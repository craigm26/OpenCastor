"""
castor/deploy_ota.py — Firmware OTA (Over-The-Air) update via arduino-cli + avrdude.

Issue #247: `castor deploy <host> --flash-arduino [--arduino-port /dev/ttyACM0]
                                                   [--board uno] [--dry-run]`

Workflow:
  1. Locally compile firmware/arduino_l298n_bridge.ino → .hex via arduino-cli
  2. SCP the .hex to the target host
  3. SSH into target and flash via avrdude

Guards:
  - HAS_ARDUINO_CLI checked via shutil.which; graceful error + install hint if absent
  - --dry-run prints all commands without executing them
  - Rich progress bar matches existing console patterns

Usage::

    from castor.deploy_ota import OTAFlasher
    flasher = OTAFlasher(host="pi@192.168.1.10", dry_run=False)
    flasher.flash_arduino(sketch_path="firmware/arduino_l298n_bridge.ino")
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("OpenCastor.DeployOTA")

# Optional SDK guard — arduino-cli is an external tool, not a Python package
HAS_ARDUINO_CLI = shutil.which("arduino-cli") is not None

_ARDUINO_CLI_INSTALL_HINT = (
    "arduino-cli not found. Install it with:\n"
    "  curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh\n"
    "  # or: brew install arduino-cli  (macOS)\n"
    "  # or: snap install arduino-cli  (Linux)\n"
    "Then add it to your PATH and retry."
)

_AVRDUDE_INSTALL_HINT = (
    "avrdude not found on the target host. Install it with:\n"
    "  sudo apt-get install avrdude  (Debian/Ubuntu/RPi)\n"
    "  brew install avrdude  (macOS)"
)

# Supported board FQBN strings
BOARD_FQBN: dict[str, str] = {
    "uno": "arduino:avr:uno",
    "nano": "arduino:avr:nano:cpu=atmega328p",
    "mega": "arduino:avr:mega:cpu=atmega2560",
    "leonardo": "arduino:avr:leonardo",
    "micro": "arduino:avr:micro",
}


class OTAError(RuntimeError):
    """Raised when OTA flash fails."""


class OTAFlasher:
    """Compiles and flashes Arduino firmware over SSH.

    Args:
        host:         SSH target, e.g. ``"pi@192.168.1.10"``.
        arduino_port: Serial port on the *target* host, e.g. ``"/dev/ttyACM0"``.
        board:        Short board name (``"uno"``, ``"nano"``, …) or full FQBN.
        dry_run:      Print commands without executing them.
        ssh_opts:     Extra SSH options list, e.g. ``["-i", "~/.ssh/id_rsa"]``.
    """

    def __init__(
        self,
        host: str,
        arduino_port: str = "/dev/ttyACM0",
        board: str = "uno",
        dry_run: bool = False,
        ssh_opts: Optional[list[str]] = None,
    ) -> None:
        self.host = host
        self.arduino_port = arduino_port
        self.board = board
        self.dry_run = dry_run
        self.ssh_opts = ssh_opts or []
        self._fqbn = BOARD_FQBN.get(board, board)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def flash_arduino(
        self,
        sketch_path: str = "firmware/arduino_l298n_bridge.ino",
        remote_tmp: str = "/tmp/castor_ota",
    ) -> dict:
        """Compile sketch locally, SCP .hex to target, then flash via avrdude.

        Args:
            sketch_path: Path to the .ino sketch file (local).
            remote_tmp:  Temporary directory on the target host.

        Returns:
            Dict with ``{"ok": bool, "hex_path": str, "log": str}``.

        Raises:
            OTAError: If any step fails (and not dry-run).
        """
        sketch = Path(sketch_path).resolve()
        if not self.dry_run and not sketch.exists():
            raise OTAError(f"Sketch not found: {sketch}")

        try:
            from rich.console import Console

            console = Console()
            has_rich = True
        except ImportError:
            console = None
            has_rich = False

        log_lines: list[str] = []

        def _log(msg: str) -> None:
            logger.info(msg)
            log_lines.append(msg)
            if has_rich:
                console.print(f"  [dim]{msg}[/]")
            else:
                print(f"  {msg}")

        def _run(cmd: list[str], step: str) -> subprocess.CompletedProcess:
            _log(f"[{step}] $ {' '.join(cmd)}")
            if self.dry_run:
                return subprocess.CompletedProcess(cmd, 0, b"", b"")
            result = subprocess.run(cmd, capture_output=True)
            if result.returncode != 0:
                err = result.stderr.decode(errors="replace").strip()
                raise OTAError(f"{step} failed: {err}")
            return result

        if not HAS_ARDUINO_CLI and not self.dry_run:
            raise OTAError(_ARDUINO_CLI_INSTALL_HINT)
        if not HAS_ARDUINO_CLI and self.dry_run:
            _log("(dry-run) arduino-cli not found — would fail here without --dry-run")

        with tempfile.TemporaryDirectory(prefix="castor_ota_") as build_dir:
            hex_path = str(Path(build_dir) / "firmware.hex")

            # Step 1: Compile
            _log(f"Compiling {sketch.name} for {self._fqbn} …")
            compile_cmd = [
                "arduino-cli",
                "compile",
                "--fqbn",
                self._fqbn,
                "--output-dir",
                build_dir,
                str(sketch.parent),
            ]
            _run(compile_cmd, "compile")

            # arduino-cli produces <sketch>.ino.hex — find it
            if not self.dry_run:
                hex_candidates = list(Path(build_dir).glob("*.hex"))
                if not hex_candidates:
                    raise OTAError(f"No .hex produced in {build_dir}")
                hex_path = str(hex_candidates[0])
            _log(f"Compiled OK → {hex_path}")

            # Step 2: SCP .hex to target
            remote_hex = f"{remote_tmp}/firmware.hex"
            _log(f"Uploading {Path(hex_path).name} to {self.host}:{remote_tmp} …")
            _run(
                ["ssh"] + self.ssh_opts + [self.host, f"mkdir -p {remote_tmp}"],
                "ssh-mkdir",
            )
            _run(
                ["scp"] + self.ssh_opts + [hex_path, f"{self.host}:{remote_hex}"],
                "scp",
            )
            _log("Upload complete.")

            # Step 3: Flash via avrdude on target
            avrdude_mcu = self._fqbn_to_mcu(self._fqbn)
            avrdude_cmd = (
                f"avrdude -v -p {avrdude_mcu} -c arduino "
                f"-P {self.arduino_port} -b 115200 "
                f"-U flash:w:{remote_hex}:i"
            )
            _log(f"Flashing on {self.host} via avrdude …")
            _run(
                ["ssh"] + self.ssh_opts + [self.host, avrdude_cmd],
                "avrdude",
            )
            _log("Flash complete ✓")

        return {"ok": True, "hex_path": hex_path, "log": "\n".join(log_lines)}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fqbn_to_mcu(fqbn: str) -> str:
        """Map an Arduino FQBN to the avrdude -p MCU string.

        The FQBN format is ``vendor:arch:board[:options]``.
        We parse the third colon-separated segment as the board identifier.

        Args:
            fqbn: e.g. ``"arduino:avr:uno"`` or ``"arduino:avr:mega:cpu=atmega2560"``

        Returns:
            avrdude MCU string, e.g. ``"m328p"``.
        """
        parts = fqbn.split(":")
        # Board is the 3rd segment (index 2) if present, else last
        board_part = (parts[2] if len(parts) >= 3 else parts[-1]).lower()
        _mcu_map = {
            "uno": "m328p",
            "nano": "m328p",
            "mega": "m2560",
            "mega2560": "m2560",
            "leonardo": "m32u4",
            "micro": "m32u4",
        }
        return _mcu_map.get(board_part, "m328p")

    @staticmethod
    def build_ssh_command(
        host: str, remote_cmd: str, opts: Optional[list[str]] = None
    ) -> list[str]:
        """Construct an SSH command list.

        Args:
            host:       SSH target string.
            remote_cmd: Shell command to run on the target.
            opts:       Extra SSH option flags.

        Returns:
            List suitable for ``subprocess.run``.
        """
        return ["ssh"] + (opts or []) + [host, remote_cmd]

    @staticmethod
    def build_avrdude_command(
        mcu: str,
        port: str,
        hex_path: str,
        programmer: str = "arduino",
        baud: int = 115200,
    ) -> str:
        """Construct the avrdude flash command string.

        Args:
            mcu:        MCU identifier, e.g. ``"m328p"``.
            port:       Serial device, e.g. ``"/dev/ttyACM0"``.
            hex_path:   Path to the .hex file on the target host.
            programmer: avrdude programmer type (default ``"arduino"``).
            baud:       Serial baud rate.

        Returns:
            Shell-ready avrdude command string.
        """
        return f"avrdude -v -p {mcu} -c {programmer} -P {port} -b {baud} -U flash:w:{hex_path}:i"


def flash_arduino_ota(
    host: str,
    sketch_path: str = "firmware/arduino_l298n_bridge.ino",
    arduino_port: str = "/dev/ttyACM0",
    board: str = "uno",
    dry_run: bool = False,
) -> dict:
    """Convenience function — compile and flash Arduino firmware over SSH.

    Args:
        host:          SSH target (``user@host``).
        sketch_path:   Local path to the ``.ino`` sketch.
        arduino_port:  Serial port on target, e.g. ``"/dev/ttyACM0"``.
        board:         Board name or FQBN.
        dry_run:       Print commands without executing.

    Returns:
        ``{"ok": bool, "hex_path": str, "log": str}``
    """
    flasher = OTAFlasher(
        host=host,
        arduino_port=arduino_port,
        board=board,
        dry_run=dry_run,
    )
    return flasher.flash_arduino(sketch_path=sketch_path)

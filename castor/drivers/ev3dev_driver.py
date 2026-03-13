"""EV3dev driver with local SDK mode and remote SSH fallback.

Supports RCAN protocols:
  - ``ev3dev_tacho_motor``
  - ``ev3dev_sensor`` (parsed for config compatibility; movement uses motors)

Modes:
  - hardware-local: OpenCastor runs on EV3dev host with python-ev3dev2 installed.
  - hardware-ssh: OpenCastor runs elsewhere and drives EV3 over SSH + sysfs.
  - mock: no reachable EV3 runtime; commands are logged.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Any, Optional

from castor.drivers.base import DriverBase

logger = logging.getLogger("OpenCastor.EV3")

try:
    from ev3dev2.motor import (  # type: ignore
        OUTPUT_A,
        OUTPUT_B,
        OUTPUT_C,
        OUTPUT_D,
        LargeMotor,
        MediumMotor,
        SpeedPercent,
    )

    HAS_EV3DEV2 = True
except ImportError:
    HAS_EV3DEV2 = False


_OUTPUT_MAP = {
    "outa": "A",
    "outb": "B",
    "outc": "C",
    "outd": "D",
}


class EV3DevDriver(DriverBase):
    """Differential drive adapter for LEGO Mindstorms EV3."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._mode = "mock"
        self._error: Optional[str] = "driver not connected"
        self._ssh_timeout_s = float(config.get("connection", {}).get("timeout_s", 2.5))

        motor_cfgs = [
            d for d in config.get("drivers", []) if d.get("protocol") == "ev3dev_tacho_motor"
        ]
        self._left_port, self._right_port = self._select_drive_ports(motor_cfgs)
        self._left_mult = self._polarity_for_port(motor_cfgs, self._left_port)
        self._right_mult = self._polarity_for_port(motor_cfgs, self._right_port)

        connection = config.get("connection", {})
        self._ssh_host = str(connection.get("host", "ev3dev.local")).strip()
        self._ssh_user = str(connection.get("user", "robot")).strip()
        self._ssh_paths: dict[str, str] = {}
        self._local_motors: dict[str, Any] = {}

        if HAS_EV3DEV2 and os.path.isdir("/sys/class/tacho-motor"):
            if self._init_local_motors(motor_cfgs):
                self._mode = "hardware-local"
                self._error = None
                return

        if self._ssh_host and shutil.which("ssh"):
            if self._discover_remote_motors():
                self._mode = "hardware-ssh"
                self._error = None
                return

        logger.warning("EV3 driver unavailable; running in mock mode")

    @staticmethod
    def _select_drive_ports(motor_cfgs: list[dict[str, Any]]) -> tuple[str, str]:
        left = None
        right = None
        fallback = []
        for cfg in motor_cfgs:
            port = str(cfg.get("port", "")).strip().lower()
            if not port:
                continue
            fallback.append(port)
            cfg_id = str(cfg.get("id", "")).lower()
            if left is None and "left" in cfg_id:
                left = port
            if right is None and "right" in cfg_id:
                right = port
        if left is None and fallback:
            left = fallback[0]
        if right is None:
            right = fallback[1] if len(fallback) > 1 else left
        return left or "outa", right or "outd"

    @staticmethod
    def _polarity_for_port(motor_cfgs: list[dict[str, Any]], port: str) -> int:
        for cfg in motor_cfgs:
            if str(cfg.get("port", "")).strip().lower() != port:
                continue
            polarity = str(cfg.get("polarity", "normal")).strip().lower()
            if polarity in {"inversed", "inverse", "reversed", "-1"}:
                return -1
        return 1

    def _ssh_exec(self, command: str) -> subprocess.CompletedProcess[str]:
        if not self._ssh_host:
            raise RuntimeError("missing ssh host")
        return subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={int(max(1, round(self._ssh_timeout_s)))}",
                f"{self._ssh_user}@{self._ssh_host}",
                command,
            ],
            capture_output=True,
            text=True,
            timeout=self._ssh_timeout_s,
        )

    def _discover_remote_motors(self) -> bool:
        try:
            proc = self._ssh_exec(
                "for m in /sys/class/tacho-motor/motor*; do "
                '[ -e "$m/address" ] || continue; '
                'a=$(cat "$m/address" 2>/dev/null); '
                'echo "$a:$m"; '
                "done"
            )
        except Exception as exc:
            self._error = str(exc)
            return False

        if proc.returncode != 0:
            self._error = proc.stderr.strip() or "ssh discovery failed"
            return False

        discovered: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            if ":" not in line:
                continue
            address, path = line.split(":", 1)
            discovered[address.strip().lower()] = path.strip()

        self._ssh_paths = discovered
        if self._left_port not in self._ssh_paths or self._right_port not in self._ssh_paths:
            self._error = f"drive ports not found over ssh ({self._left_port}, {self._right_port})"
            return False
        logger.info(
            "EV3 SSH mode ready: %s (%s/%s)",
            self._ssh_host,
            self._left_port,
            self._right_port,
        )
        return True

    def _init_local_motors(self, motor_cfgs: list[dict[str, Any]]) -> bool:
        port_lookup = {
            "A": OUTPUT_A,
            "B": OUTPUT_B,
            "C": OUTPUT_C,
            "D": OUTPUT_D,
        }
        motors: dict[str, Any] = {}

        for cfg in motor_cfgs:
            port_raw = str(cfg.get("port", "")).strip().lower()
            port = _OUTPUT_MAP.get(port_raw)
            if port is None:
                continue

            motor_type = str(cfg.get("motor_type", "")).lower()
            motor_cls = MediumMotor if "m-motor" in motor_type else LargeMotor
            try:
                motors[port_raw] = motor_cls(port_lookup[port])
            except Exception:
                continue

        self._local_motors = motors
        if self._left_port not in motors or self._right_port not in motors:
            self._error = "required local motors not found"
            return False

        logger.info("EV3 local mode ready: drive ports %s/%s", self._left_port, self._right_port)
        return True

    @staticmethod
    def _clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))

    @staticmethod
    def _coerce_motion(linear_or_action: Any, angular: float) -> tuple[float, float]:
        if isinstance(linear_or_action, dict):
            linear = float(linear_or_action.get("linear", 0.0))
            ang = float(linear_or_action.get("angular", 0.0))
        else:
            linear = float(linear_or_action)
            ang = float(angular)
        return linear, ang

    def _drive_local(self, left_pct: int, right_pct: int) -> None:
        self._local_motors[self._left_port].on(SpeedPercent(left_pct))
        self._local_motors[self._right_port].on(SpeedPercent(right_pct))

    def _drive_ssh(self, left_pct: int, right_pct: int) -> None:
        left_path = self._ssh_paths[self._left_port]
        right_path = self._ssh_paths[self._right_port]
        cmd = (
            f"echo {left_pct} > {left_path}/speed_sp && echo run-forever > {left_path}/command && "
            f"echo {right_pct} > {right_path}/speed_sp && echo run-forever > {right_path}/command"
        )
        proc = self._ssh_exec(cmd)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "remote drive command failed")

    def move(self, linear: float = 0.0, angular: float = 0.0) -> None:
        linear, angular = self._coerce_motion(linear, angular)
        left = self._clamp(linear - angular, -1.0, 1.0) * self._left_mult
        right = self._clamp(linear + angular, -1.0, 1.0) * self._right_mult
        left_pct = int(round(left * 100))
        right_pct = int(round(right * 100))

        try:
            if self._mode == "hardware-local":
                self._drive_local(left_pct, right_pct)
                return
            if self._mode == "hardware-ssh":
                self._drive_ssh(left_pct, right_pct)
                return
        except Exception as exc:
            self._error = str(exc)
            self._mode = "mock"
            logger.warning("EV3 drive failed, falling back to mock mode: %s", exc)

        logger.info("[MOCK EV3] left=%d right=%d", left_pct, right_pct)

    def _stop_ssh(self) -> None:
        left_path = self._ssh_paths.get(self._left_port)
        right_path = self._ssh_paths.get(self._right_port)
        if not left_path or not right_path:
            return
        cmd = f"echo stop > {left_path}/command ; echo stop > {right_path}/command"
        self._ssh_exec(cmd)

    def stop(self) -> None:
        try:
            if self._mode == "hardware-local":
                self._local_motors[self._left_port].off(brake=True)
                self._local_motors[self._right_port].off(brake=True)
                return
            if self._mode == "hardware-ssh":
                self._stop_ssh()
                return
        except Exception as exc:
            self._error = str(exc)
            self._mode = "mock"
            logger.warning("EV3 stop failed, using mock mode: %s", exc)
        logger.info("[MOCK EV3] stop")

    def close(self) -> None:
        self.stop()

    def health_check(self) -> dict[str, Any]:
        if self._mode == "hardware-local":
            return {"ok": True, "mode": "hardware", "transport": "local", "error": None}
        if self._mode == "hardware-ssh":
            try:
                proc = self._ssh_exec("echo ok")
                if proc.returncode == 0 and "ok" in proc.stdout:
                    return {"ok": True, "mode": "hardware", "transport": "ssh", "error": None}
            except Exception as exc:
                self._error = str(exc)
            return {"ok": False, "mode": "mock", "transport": "ssh", "error": self._error}
        return {"ok": False, "mode": "mock", "error": self._error}

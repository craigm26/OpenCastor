#!/usr/bin/env python3
"""
Server-side gamepad driver for OpenCastor.

Reads a connected gamepad via evdev (works for Bluetooth controllers like the
8bitdo Zero 2 in Switch mode, which appears as a Nintendo Switch Pro Controller)
and sends movement commands to the local OpenCastor API.

Usage:
    python3 scripts/gamepad_driver.py [--device /dev/input/eventN] [--api http://localhost:8000]

Auto-detects the first joystick/gamepad if --device is not specified.

Button mapping (Pro Controller / 8bitdo Zero 2 in Switch mode):
    D-pad (HAT0X/HAT0Y) or left stick  → move / turn
    B (BTN_SOUTH)                       → stop
    A (BTN_EAST)                        → stop
    X (BTN_NORTH)                       → status
    Y (BTN_WEST)                        → e-stop
    L  (BTN_TL)                         → boost on / off
    R  (BTN_TR)                         → slow / precision mode
    ZL (BTN_TL2)                        → reboot (hold 2 s)
    ZR (BTN_TR2)                        → shutdown (hold 2 s)
    + (BTN_START)                       → e-stop
    - (BTN_SELECT)                      → clear e-stop
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gamepad_driver")

try:
    import evdev
    from evdev import InputDevice, ecodes
except ImportError:
    sys.exit("evdev not installed — run: pip install evdev")


# ---------------------------------------------------------------------------
# Axis / button constants (Linux evdev)
# ---------------------------------------------------------------------------
ABS_X = ecodes.ABS_X
ABS_Y = ecodes.ABS_Y
ABS_Z = ecodes.ABS_Z  # right stick X on some controllers
ABS_RX = ecodes.ABS_RX
ABS_RY = ecodes.ABS_RY
ABS_HAT0X = ecodes.ABS_HAT0X  # D-pad left(-1)/right(+1)
ABS_HAT0Y = ecodes.ABS_HAT0Y  # D-pad up(-1)/down(+1)

BTN_SOUTH = ecodes.BTN_SOUTH  # B / Cross
BTN_EAST = ecodes.BTN_EAST  # A / Circle
BTN_NORTH = ecodes.BTN_NORTH  # X / Triangle
BTN_WEST = ecodes.BTN_WEST  # Y / Square
BTN_TL = ecodes.BTN_TL  # L1 / L
BTN_TR = ecodes.BTN_TR  # R1 / R
BTN_TL2 = ecodes.BTN_TL2  # L2 / ZL
BTN_TR2 = ecodes.BTN_TR2  # R2 / ZR
BTN_START = ecodes.BTN_START  # + / Options
BTN_SELECT = ecodes.BTN_SELECT  # - / Share


def find_gamepad() -> Optional[str]:
    """Return the path of the first gamepad/joystick input device."""
    for path in evdev.list_devices():
        dev = InputDevice(path)
        caps = dev.capabilities()
        # Must have absolute axes (joystick/gamepad) or gamepad buttons
        has_abs = evdev.ecodes.EV_ABS in caps
        has_btn = evdev.ecodes.EV_KEY in caps and any(
            c in caps[evdev.ecodes.EV_KEY] for c in (BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST)
        )
        if has_abs and has_btn:
            log.info("Auto-detected gamepad: %s (%s)", dev.name, path)
            return path
    return None


def deadzone(value: float, center: float, maximum: float, dz: float = 0.15) -> float:
    """Normalise an axis value to [-1, 1] with a deadzone."""
    if maximum == center:
        return 0.0
    norm = (value - center) / (maximum - center)
    norm = max(-1.0, min(1.0, norm))
    return 0.0 if abs(norm) < dz else norm


class GamepadDriver:
    def __init__(
        self,
        device_path: str,
        api_url: str,
        token: str = "",
        speed: float = 0.7,
        turn: float = 0.6,
    ):
        self.dev = InputDevice(device_path)
        self.api = api_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.json_headers = {**self.headers, "Content-Type": "application/json"}
        self.speed = speed
        self.turn = turn

        # Runtime state
        self._linear = 0.0
        self._angular = 0.0
        self._boosting = False
        self._slow = False
        self._zl_held = 0.0  # timestamp when ZL was pressed
        self._zr_held = 0.0
        self._last_send = 0.0
        self._send_interval = 0.08  # 12.5 Hz

        # Axis info populated from device
        self._ax_info: dict = {}
        caps = self.dev.capabilities()
        if evdev.ecodes.EV_ABS in caps:
            for code, info in caps[evdev.ecodes.EV_ABS]:
                self._ax_info[code] = info

        log.info("Opened %s (%s)", self.dev.name, device_path)
        log.info("Speed=%.2f  Turn=%.2f", speed, turn)

    def _post(self, path: str, body: dict | None = None, timeout: float = 1.5) -> bool:
        try:
            if body is not None:
                r = requests.post(
                    self.api + path, json=body, headers=self.json_headers, timeout=timeout
                )
            else:
                r = requests.post(self.api + path, headers=self.headers, timeout=timeout)
            if not r.ok:
                log.warning("%s → %s %s", path, r.status_code, r.text[:80])
            return r.ok
        except requests.RequestException as exc:
            log.error("%s failed: %s", path, exc)
            return False

    def _norm_axis(self, code: int, value: int) -> float:
        info = self._ax_info.get(code)
        if info is None:
            return 0.0
        center = (info.max + info.min) / 2
        maximum = info.max
        return deadzone(value, center, maximum)

    def _send_move(self):
        lin = self._linear * (1.5 if self._boosting else 0.4 if self._slow else 1.0)
        ang = self._angular * (1.5 if self._boosting else 0.4 if self._slow else 1.0)
        lin = max(-1.0, min(1.0, lin * self.speed))
        ang = max(-1.0, min(1.0, ang * self.turn))
        self._post(
            "/api/action", {"type": "move", "linear": round(lin, 3), "angular": round(ang, 3)}
        )

    def run(self):
        log.info("Gamepad driver running — press Ctrl-C to stop")
        try:
            for event in self.dev.read_loop():
                self._handle(event)
        except KeyboardInterrupt:
            log.info("Stopped by user")
            self._post("/api/stop")

    def _handle(self, event):
        now = time.monotonic()

        if event.type == evdev.ecodes.EV_ABS:
            code = event.code

            # D-pad hat switch — digital
            if code == ABS_HAT0X:
                self._angular = -float(event.value)  # -1=right, +1=left
                self._maybe_send(now)
            elif code == ABS_HAT0Y:
                self._linear = -float(event.value)  # -1=forward, +1=backward
                self._maybe_send(now)
            # Left stick — analogue fallback
            elif code == ABS_X:
                self._angular = -self._norm_axis(ABS_X, event.value)
                self._maybe_send(now)
            elif code == ABS_Y:
                self._linear = -self._norm_axis(ABS_Y, event.value)
                self._maybe_send(now)

        elif event.type == evdev.ecodes.EV_KEY:
            code = event.code
            pressed = event.value == 1  # 1=press, 0=release, 2=repeat

            if code in (BTN_SOUTH, BTN_EAST) and pressed:  # A / B → stop
                self._linear = self._angular = 0.0
                self._post("/api/stop")
                log.info("STOP")

            elif code == BTN_NORTH and pressed:  # X → status
                try:
                    r = requests.post(
                        self.api + "/api/command",
                        json={"instruction": "what is your current status?"},
                        headers=self.json_headers,
                        timeout=5,
                    )
                    log.info("Status: %s", r.json().get("reply", "")[:120])
                except Exception:
                    pass

            elif code == BTN_WEST and pressed:  # Y → e-stop
                self._linear = self._angular = 0.0
                self._post("/api/stop")
                log.info("E-STOP via Y")

            elif code == BTN_TL:  # L → boost toggle
                if pressed:
                    self._boosting = not self._boosting
                    log.info("Boost: %s", self._boosting)

            elif code == BTN_TR:  # R → slow toggle
                if pressed:
                    self._slow = not self._slow
                    log.info("Slow: %s", self._slow)

            elif code == BTN_TL2:  # ZL → hold 2s reboot
                if pressed:
                    self._zl_held = now
                elif self._zl_held and (now - self._zl_held) >= 2.0:
                    log.warning("Rebooting via ZL hold")
                    self._post("/api/system/reboot")
                    self._zl_held = 0.0
                else:
                    self._zl_held = 0.0

            elif code == BTN_TR2:  # ZR → hold 2s shutdown
                if pressed:
                    self._zr_held = now
                elif self._zr_held and (now - self._zr_held) >= 2.0:
                    log.warning("Shutting down via ZR hold")
                    self._post("/api/system/shutdown")
                    self._zr_held = 0.0
                else:
                    self._zr_held = 0.0

            elif code == BTN_START and pressed:  # + → e-stop
                self._linear = self._angular = 0.0
                self._post("/api/stop")
                log.info("E-STOP via +")

            elif code == BTN_SELECT and pressed:  # - → clear estop
                self._post("/api/estop/clear")
                log.info("Clear e-stop")

    def _maybe_send(self, now: float):
        if now - self._last_send >= self._send_interval:
            self._last_send = now
            self._send_move()


def main():
    parser = argparse.ArgumentParser(description="OpenCastor gamepad driver")
    parser.add_argument("--device", default=None, help="evdev device path (auto-detect if omitted)")
    parser.add_argument("--api", default="http://localhost:8000", help="Gateway URL")
    parser.add_argument("--token", default=os.getenv("OPENCASTOR_API_TOKEN", ""), help="API token")
    parser.add_argument("--speed", type=float, default=0.7, help="Drive speed 0-1")
    parser.add_argument("--turn", type=float, default=0.6, help="Turn speed 0-1")
    args = parser.parse_args()

    device = args.device or find_gamepad()
    if not device:
        sys.exit("No gamepad found. Connect a controller and try again.")

    driver = GamepadDriver(device, args.api, args.token, args.speed, args.turn)
    driver.run()


if __name__ == "__main__":
    main()

"""CircuitPython firmware for Cytron Motion 2350 Pro (RP2350) Mecanum Base.

Copy this file to the Cytron Motion 2350 Pro's `CIRCUITPY` drive as `code.py`.
It drives the 4 built-in brushed DC motor channels (M1..M4 on GP8..GP15) from
JSON lines over USB CDC serial and enforces an authoritative hardware deadman
lease in firmware (`lease_ms`, default 600 ms). If the Raspberry Pi stops
renewing the lease, crashes, or is unplugged, all 4 motors brake to 0 inside
the RP2350 with no Linux involvement.

Pin map (Cytron Motion 2350 Pro):
  * M1 (Front-Left):  GP8 (PWM A), GP9 (PWM B)
  * M2 (Front-Right): GP10 (PWM A), GP11 (PWM B)
  * M3 (Rear-Left):   GP12 (PWM A), GP13 (PWM B)
  * M4 (Rear-Right):  GP14 (PWM A), GP15 (PWM B)
  * VBAT sense:       GP29 (ADC, 11:1 divider)
"""

import json
import sys
import time
import board
import pwmio
import analogio
import supervisor

PWM_FREQ = 20000
DEFAULT_LEASE_MS = 600
MAX_LEASE_MS = 2000

# Initialize 4 dual-PWM H-bridge channels (M1..M4)
_PINS = (
    (board.GP8, board.GP9),
    (board.GP10, board.GP11),
    (board.GP12, board.GP13),
    (board.GP14, board.GP15),
)
_MOTORS = [
    (pwmio.PWMOut(a, frequency=PWM_FREQ, duty_cycle=0),
     pwmio.PWMOut(b, frequency=PWM_FREQ, duty_cycle=0))
    for a, b in _PINS
]

try:
    _VBAT_ADC = analogio.AnalogIn(board.GP29)
except Exception:
    _VBAT_ADC = None

_current = [0.0, 0.0, 0.0, 0.0]
_lease_deadline_ms = 0


def _ticks_ms() -> int:
    return supervisor.ticks_ms()


def _ticks_less(a: int, b: int) -> bool:
    # supervisor.ticks_ms() wraps at 2^29
    return ((a - b) & 0x3FFFFFFF) > 0x1FFFFFFF


def _set_motor(idx: int, speed: float) -> None:
    s = max(-1.0, min(1.0, float(speed)))
    _current[idx] = s
    pwm_a, pwm_b = _MOTORS[idx]
    duty = int(abs(s) * 65535)
    if s > 0.001:
        pwm_a.duty_cycle = duty
        pwm_b.duty_cycle = 0
    elif s < -0.001:
        pwm_a.duty_cycle = 0
        pwm_b.duty_cycle = duty
    else:
        pwm_a.duty_cycle = 0
        pwm_b.duty_cycle = 0


def stop_all() -> None:
    global _lease_deadline_ms
    for i in range(4):
        _set_motor(i, 0.0)
    _lease_deadline_ms = _ticks_ms()


def read_vbat() -> float | None:
    if _VBAT_ADC is None:
        return None
    # 3.3V ref, 11:1 resistor divider on Motion 2350 Pro
    return round((_VBAT_ADC.value / 65535.0) * 3.3 * 11.0, 2)


stop_all()
_buf = ""

while True:
    now = _ticks_ms()
    if any(abs(v) > 0.0 for v in _current) and _ticks_less(_lease_deadline_ms, now):
        stop_all()

    while supervisor.runtime.serial_bytes_available:
        ch = sys.stdin.read(1)
        if not ch:
            break
        if ch == "\n":
            line = _buf.strip()
            _buf = ""
            if not line:
                continue
            try:
                msg = json.loads(line)
                cmd = msg.get("cmd")
                if cmd == "stop":
                    stop_all()
                    print(json.dumps({"ok": True, "stopped": True}))
                elif cmd == "motors":
                    lease = int(msg.get("lease_ms", DEFAULT_LEASE_MS))
                    lease = max(0, min(MAX_LEASE_MS, lease))
                    if lease == 0:
                        stop_all()
                    else:
                        _set_motor(0, msg.get("m1", 0.0))
                        _set_motor(1, msg.get("m2", 0.0))
                        _set_motor(2, msg.get("m3", 0.0))
                        _set_motor(3, msg.get("m4", 0.0))
                        _lease_deadline_ms = (now + lease) & 0x3FFFFFFF
                    print(json.dumps({"ok": True, "m": _current, "lease_ms": lease}))
                elif cmd in ("ping", "status"):
                    print(json.dumps({
                        "ok": True,
                        "board": "cytron-motion-2350-pro",
                        "m": _current,
                        "vbat_v": read_vbat(),
                    }))
                else:
                    print(json.dumps({"ok": False, "error": f"unknown cmd: {cmd}"}))
            except Exception as exc:
                stop_all()
                print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            _buf += ch
    time.sleep(0.005)

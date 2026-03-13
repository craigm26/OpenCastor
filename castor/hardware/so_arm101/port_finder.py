"""
SO-ARM101 USB port detection.

Identifies which /dev/ttyACM* (Linux) or /dev/tty.usbmodem* (macOS) ports
correspond to the follower and leader arm controller boards.

Strategy:
  1. Auto-detect Feetech/Waveshare USB VID:PID via /sys/class/tty or pyserial.
  2. If two ports found → (follower, leader).
  3. If disambiguation needed → interactive unplug/replug flow.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from castor.hardware.so_arm101.constants import FEETECH_USB_IDS

logger = logging.getLogger("OpenCastor.SoArm101.PortFinder")


def list_serial_ports() -> list[str]:
    """Return all available serial ports, sorted."""
    try:
        import serial.tools.list_ports  # type: ignore[import]

        return sorted(p.device for p in serial.tools.list_ports.comports())
    except ImportError:
        pass

    # Fallback: scan /dev on Linux
    candidates = []
    for name in os.listdir("/dev"):
        if name.startswith(("ttyACM", "ttyUSB", "ttyS")):
            candidates.append(f"/dev/{name}")
    return sorted(candidates)


def detect_feetech_ports() -> list[dict]:
    """
    Return list of dicts for ports that match known Feetech/Waveshare USB IDs.

    Each dict: {"port": str, "description": str, "vid_pid": str}
    """
    found = []
    try:
        import serial.tools.list_ports  # type: ignore[import]

        for port in serial.tools.list_ports.comports():
            if port.vid is None or port.pid is None:
                continue
            vid_pid = f"{port.vid:04x}:{port.pid:04x}"
            if vid_pid in FEETECH_USB_IDS:
                found.append(
                    {
                        "port": port.device,
                        "description": FEETECH_USB_IDS[vid_pid],
                        "vid_pid": vid_pid,
                    }
                )
    except ImportError:
        logger.debug("pyserial not available; falling back to /dev scan")

    if not found:
        # Coarse fallback: any ttyACM/ttyUSB
        for p in list_serial_ports():
            if "ACM" in p or "USB" in p:
                found.append({"port": p, "description": "Unknown USB serial device", "vid_pid": ""})

    return found


def interactive_port_identify(
    label: str = "arm",
    print_fn=print,
    input_fn=input,
) -> Optional[str]:
    """
    Interactively identify a port by asking user to unplug/replug.

    Returns the port string, or None if detection failed.
    """
    before = set(list_serial_ports())
    print_fn(f"\n[SO-ARM101] Identifying port for {label}...")
    print_fn("  1. Make sure the controller board is connected and powered.")
    print_fn("  2. Disconnect the USB cable from the controller board.")
    input_fn("     Press Enter once unplugged...")

    time.sleep(0.5)
    after_unplug = set(list_serial_ports())
    removed = before - after_unplug

    if not removed:
        print_fn(
            "  ⚠  No port disappeared. Check cable connections and try again."
        )
        return None

    port = sorted(removed)[0]
    print_fn(f"  ✓ Identified port: {port}")
    input_fn("     Reconnect the USB cable and press Enter...")
    time.sleep(0.5)

    return port


def lerobot_find_port(label: str = "arm", print_fn=print, input_fn=input) -> Optional[str]:
    """
    Run lerobot-find-port interactively, then prompt user to confirm the port.

    Returns the confirmed port string, or None.
    """
    from castor.hardware.so_arm101.lerobot_bridge import find_lerobot_bin

    tool = find_lerobot_bin("lerobot-find-port")
    if not tool:
        return None

    import subprocess

    print_fn(f"\n[LeRobot] Identifying port for {label} arm...")
    print_fn(f"  Running: {tool}")
    print_fn("  When prompted, disconnect the USB cable from the controller board.\n")
    try:
        subprocess.run([str(tool)])
    except Exception as e:
        print_fn(f"  Error: {e}")
        return None

    port = input_fn("\n  Enter the port shown above (e.g. /dev/ttyACM0): ").strip()
    return port or None


def auto_assign_ports(print_fn=print, input_fn=input) -> dict[str, str]:
    """
    Detect and return {'follower': '/dev/...', 'leader': '/dev/...'}.

    If only one port found → assigns to follower only.
    If two found → prompts user to confirm which is which.
    """
    # ── Use lerobot-find-port if available ──
    from castor.hardware.so_arm101.lerobot_bridge import lerobot_available

    if lerobot_available():
        print_fn("\n[SO-ARM101] LeRobot detected — using lerobot-find-port for port identification")
        result = {}
        for arm in (["follower", "leader"] if True else ["follower"]):
            ans = input_fn(f"\n  Identify {arm} arm port? [Y/n]: ").strip().lower()
            if ans != "n":
                port = lerobot_find_port(label=arm, print_fn=print_fn, input_fn=input_fn)
                if port:
                    result[arm] = port
            else:
                break
        if result:
            return result

    ports = detect_feetech_ports()

    if len(ports) >= 2:
        print_fn("\n[SO-ARM101] Found 2 controller boards:")
        for i, p in enumerate(ports[:2]):
            print_fn(f"  [{i+1}] {p['port']}  ({p['description']})")
        ans = input_fn("\n  Which port is the FOLLOWER arm? [1/2]: ").strip()
        if ans == "2":
            follower, leader = ports[1]["port"], ports[0]["port"]
        else:
            follower, leader = ports[0]["port"], ports[1]["port"]
        return {"follower": follower, "leader": leader}

    if len(ports) == 1:
        print_fn(f"\n[SO-ARM101] Found 1 controller board: {ports[0]['port']}")
        ans = input_fn("  Is this the follower or leader arm? [follower/leader]: ").strip().lower()
        key = "leader" if ans.startswith("l") else "follower"
        return {key: ports[0]["port"]}

    # Nothing found — fall back to interactive
    print_fn("\n[SO-ARM101] No Feetech boards auto-detected. Starting interactive port identification...")
    result = {}
    for arm in ("follower", "leader"):
        ans = input_fn(f"\n  Set up {arm} arm port? [y/N]: ").strip().lower()
        if ans == "y":
            port = interactive_port_identify(label=arm, print_fn=print_fn, input_fn=input_fn)
            if port:
                result[arm] = port
    return result


def chmod_ports(ports: dict[str, str]) -> None:
    """Grant read/write access to serial ports (Linux only)."""
    for arm, port in ports.items():
        if os.path.exists(port):
            try:
                os.system(f"sudo chmod 666 {port}")
                logger.info(f"chmod 666 {port} ({arm})")
            except Exception as e:
                logger.warning(f"Could not chmod {port}: {e}")

"""
SO-ARM101 constants: motor layout, gear ratios, joint names, assembly steps.

References:
  - https://huggingface.co/docs/lerobot/so101
  - https://github.com/TheRobotStudio/SO-ARM100
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── Motor layout ─────────────────────────────────────────────────────────────

# Follower arm: all 6 use STS3215 with 1/345 gearing
FOLLOWER_MOTORS = [
    {"id": 1, "joint": "shoulder_pan", "gear": "1/345", "model": "STS3215"},
    {"id": 2, "joint": "shoulder_lift", "gear": "1/345", "model": "STS3215"},
    {"id": 3, "joint": "elbow_flex", "gear": "1/345", "model": "STS3215"},
    {"id": 4, "joint": "wrist_flex", "gear": "1/345", "model": "STS3215"},
    {"id": 5, "joint": "wrist_roll", "gear": "1/345", "model": "STS3215"},
    {"id": 6, "joint": "gripper", "gear": "1/345", "model": "STS3215"},
]

# Leader arm: mixed gear ratios per joint for backdrivability
LEADER_MOTORS = [
    {"id": 1, "joint": "shoulder_pan", "gear": "1/191", "model": "STS3215"},
    {"id": 2, "joint": "shoulder_lift", "gear": "1/345", "model": "STS3215"},
    {"id": 3, "joint": "elbow_flex", "gear": "1/191", "model": "STS3215"},
    {"id": 4, "joint": "wrist_flex", "gear": "1/147", "model": "STS3215"},
    {"id": 5, "joint": "wrist_roll", "gear": "1/147", "model": "STS3215"},
    {"id": 6, "joint": "gripper", "gear": "1/147", "model": "STS3215"},
]

JOINT_NAMES = [m["joint"] for m in FOLLOWER_MOTORS]

DEFAULT_BAUD = 1_000_000
DEFAULT_MOTOR_ID = 1  # factory default for all new STS3215

# Feetech USB VID:PID combos
FEETECH_USB_IDS = {
    "1a86:7523": "Waveshare Serial Bus Servo Board (CH340G)",
    "1a86:55d3": "QinHeng CH343/CH9102 Serial Bus Servo Board — SO-ARM101",
    "0483:5740": "STM32 Servo Board",
    "2e8a:0005": "Raspberry Pi RP2040 (Waveshare variant)",
}

#: Devices that must NEVER be opened as a servo bus, checked BEFORE any
#: allowlist match.
#:
#: This is not hypothetical. Opening a serial port with pyserial asserts DTR and
#: RTS by default, which is exactly esptool's reset-into-download-mode sequence
#: on an ESP32-S3 native-USB CDC — so probing an ESP32 LoRa radio at 1 Mbaud
#: does not merely fail, it can knock the device out of normal operation. A
#: developer board sitting next to a robot arm is the common case, not the edge
#: case.
DENIED_USB_VIDS = {
    "303a": "Espressif (ESP32 family)",
    "239a": "Adafruit",
    "2341": "Arduino",
}

#: Substrings in a USB product/manufacturer string that disqualify a port.
DENIED_PRODUCT_SUBSTRINGS = (
    "heltec",
    "lora",
    "meshtastic",
    "meshcore",
    "esp32",
    "rak",
    "t-beam",
)


def is_denied_serial(vid_pid: str, description: str = "") -> str:
    """Reason this port must not be probed, or "" when it is fair game."""
    vid = (vid_pid or "").split(":")[0].lower()
    if vid in DENIED_USB_VIDS:
        return f"{DENIED_USB_VIDS[vid]} — not a servo controller"
    haystack = (description or "").lower()
    for token in DENIED_PRODUCT_SUBSTRINGS:
        if token in haystack:
            return f"looks like a {token} radio/dev board — not a servo controller"
    return ""


# ── Assembly steps ────────────────────────────────────────────────────────────


@dataclass
class AssemblyStep:
    step: int
    joint: str
    title: str
    description: str
    screws: list[str] = field(default_factory=list)
    tips: list[str] = field(default_factory=list)
    motor_id: int | None = None


FOLLOWER_ASSEMBLY_STEPS: list[AssemblyStep] = [
    AssemblyStep(
        step=0,
        joint="base",
        title="Prepare the controller board",
        description=(
            "Mount the Waveshare Serial Bus Servo Board to the base plate. "
            "Connect the 12V power supply to the board's power input. "
            "Leave the USB cable disconnected until prompted."
        ),
        screws=["4x M2x6mm (board mount)"],
        tips=[
            "If using a Waveshare board, ensure both jumpers are set to channel B (USB).",
            "Do NOT connect USB to your computer yet — this happens per motor during ID setup.",
        ],
    ),
    AssemblyStep(
        step=1,
        joint="shoulder_pan",
        motor_id=1,
        title="Joint 1 — Shoulder Pan (Motor ID 1)",
        description=(
            "Insert motor 1 (STS3215) into the base housing from the bottom. "
            "Attach both motor horns. "
            "Secure the motor with 4x M2x6mm screws. "
            "Use one M3x6mm horn screw on each horn."
        ),
        screws=["4x M2x6mm (motor body)", "2x M3x6mm (motor horns)"],
        tips=["Align the motor cable exit toward the back of the base."],
    ),
    AssemblyStep(
        step=2,
        joint="shoulder_lift",
        motor_id=2,
        title="Joint 2 — Shoulder Lift (Motor ID 2)",
        description=(
            "Slide motor 2 into the upper arm housing from the top. "
            "Fasten with 4x M2x6mm screws. "
            "Attach both motor horns with M3x6mm horn screws. "
            "Connect the upper arm segment with 4x M3x6mm screws on each side."
        ),
        screws=["4x M2x6mm (motor body)", "2x M3x6mm (motor horns)", "8x M3x6mm (upper arm)"],
        tips=["Keep the cable routing channel clear before closing the housing."],
    ),
    AssemblyStep(
        step=3,
        joint="elbow_flex",
        motor_id=3,
        title="Joint 3 — Elbow Flex (Motor ID 3)",
        description=(
            "Insert motor 3 into the forearm housing and fasten with 4x M2x6mm screws. "
            "Attach both motor horns, secure with M3x6mm horn screws. "
            "Connect the forearm segment with 4x M3x6mm screws on each side."
        ),
        screws=["4x M2x6mm (motor body)", "2x M3x6mm (motor horns)", "8x M3x6mm (forearm)"],
        tips=["Route the 3-pin cable through the forearm channel before fastening."],
    ),
    AssemblyStep(
        step=4,
        joint="wrist_flex",
        motor_id=4,
        title="Joint 4 — Wrist Flex (Motor ID 4)",
        description=(
            "Slide motor holder 4 over the wrist section. "
            "Insert motor 4 and fasten with 4x M2x6mm screws. "
            "Attach motor horns, secure with M3x6mm horn screws."
        ),
        screws=["4x M2x6mm (motor body)", "2x M3x6mm (motor horns)"],
    ),
    AssemblyStep(
        step=5,
        joint="wrist_roll",
        motor_id=5,
        title="Joint 5 — Wrist Roll (Motor ID 5)",
        description=(
            "Insert motor 5 into the wrist holder and secure with 2x M2x6mm front screws. "
            "Install ONE motor horn only and secure with a M3x6mm horn screw. "
            "Secure the wrist assembly to motor 4 using 4x M3x6mm screws on both sides."
        ),
        screws=["2x M2x6mm (motor body)", "1x M3x6mm (horn)", "8x M3x6mm (wrist-to-motor4)"],
        tips=["Wrist roll only needs one horn — this is intentional."],
    ),
    AssemblyStep(
        step=6,
        joint="gripper",
        motor_id=6,
        title="Joint 6 — Gripper (Motor ID 6)",
        description=(
            "Attach the gripper body to motor 5 using 4x M3x6mm screws. "
            "Insert the gripper motor 6 and secure with 2x M2x6mm screws on each side. "
            "Attach motor horns with M3x6mm horn screw. "
            "Install the gripper claw and secure with 4x M3x6mm screws on both sides."
        ),
        screws=[
            "4x M3x6mm (gripper body to motor 5)",
            "4x M2x6mm (gripper motor)",
            "1x M3x6mm (horn)",
            "8x M3x6mm (claw)",
        ],
        tips=["Test claw movement by rotating the motor shaft by hand before closing."],
    ),
    AssemblyStep(
        step=7,
        joint="wiring",
        title="Daisy-chain the motors",
        description=(
            "Connect the 3-pin cables in series: controller board → motor 1 → 2 → 3 → 4 → 5 → 6. "
            "Leave the last cable end free (motor 6 only needs one cable). "
            "Attach the controller board to the base plate."
        ),
        tips=[
            "Double-check: each motor has exactly 2 cables connected (in + out), except motor 6 (1 cable).",
            "Secure cables with any clips provided to avoid catching on joints.",
        ],
    ),
]

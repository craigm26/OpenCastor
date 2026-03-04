"""
Natural Language → RCAN Config Generator.

Given a plain-English description of a robot, generates a valid RCAN YAML
configuration using rule-based extraction (always available) or an LLM
(when a brain is provided for richer interpretation).

API:
  POST /api/config/generate        — {description, brain_hint?}
  GET  /api/config/generate/templates

CLI:
  castor wizard --from-description "Raspberry Pi 5 rover with OAK-D camera"
"""

import logging
import re
import uuid
from typing import Optional

logger = logging.getLogger("OpenCastor.RCANGenerator")

_RCAN_TEMPLATE = """\
rcan_version: "1.2.0"

metadata:
  robot_name: "{robot_name}"
  robot_uuid: "{uuid}"
  author: "OpenCastor Auto-Generated"
  license: "Apache-2.0"
  description: >
    {description}

agent:
  provider: "{provider}"
  model: "{model}"
  vision_enabled: {vision_enabled}
  latency_budget_ms: 300
  safety_stop: true

camera:
  type: "{camera_type}"
  fps: 30{depth_line}

drivers:
  - id: wheels
    protocol: "{driver_protocol}"
    note: "Auto-detected from description — verify before deploying"

physics:
  type: "differential_drive"
  dof: 2

safety:
  obstacle_stop_cm: 30
  estop_on_startup: false

rcan_protocol:
  port: 8000
  capabilities: [{capabilities}]
  enable_mdns: true
  enable_jwt: false
"""

# ── Keyword rule tables ────────────────────────────────────────────────────────

_CAMERA_RULES: list[tuple[list[str], str, bool]] = [
    (["oak-4 pro", "oak4 pro", "oak 4 pro"], "oakd", True),
    (["oak-d", "oak d", "oak4", "oakd", "depthai", "depth ai", "luxonis"], "oakd", True),
    (["picamera", "pi cam", "rpi cam", "ribbon cam"], "picamera2", False),
    (["webcam", "usb camera", "logitech", "c920", "c270"], "usb", False),
]

_DRIVER_RULES: list[tuple[list[str], str]] = [
    (["pca9685", "adafruit", "amazon kit", "motor hat", "4wd kit", "l298n"], "pca9685"),
    (["dynamixel", "xl430", "xm430", "ax-12", "servo"], "dynamixel"),
    (["stepper", "nema 17", "nema 23", "drv8825", "tmc2209", "a4988"], "stepper"),
    (["odrive", "bldc", "brushless hoverboard"], "odrive"),
    (["vesc", "flipsky", "unity esc", "skateboard motor"], "vesc"),
    (["gpio", "relay board", "solenoid", "l9110s"], "gpio"),
    (["ros2", "ros 2", "nav2", "twist"], "ros2"),
    (["mock", "simulation", "virtual", "no motors", "test"], "mock"),
]

_PROVIDER_RULES: list[tuple[list[str], str, str]] = [
    (["google", "gemini"], "google", "gemini-2.0-flash-exp"),
    (["openai", "gpt", "chatgpt"], "openai", "gpt-4.1"),
    (["anthropic", "claude"], "anthropic", "claude-sonnet-4-6"),
    (["groq"], "groq", "llama-3.3-70b-versatile"),
    (["ollama", "local llm", "offline", "private", "no cloud"], "ollama", "llama3.2:3b"),
    (["llama.cpp", "llamacpp", "gguf"], "llamacpp", "llama-3.2-3b"),
]


def _match_rules(text: str, rules: list) -> Optional[tuple]:
    """Return the first matching rule entry or None."""
    for rule in rules:
        keywords = rule[0]
        if any(kw in text for kw in keywords):
            return rule
    return None


def _extract_robot_name(desc: str) -> str:
    """Heuristically extract a robot name from the description."""
    patterns = [
        r"(?:my|a|an|the)\s+([\w\s\-]+?)\s+(?:robot|rover|arm|car|vehicle)\b",
        r"building\s+(?:a|an|the)\s+([\w\s\-]+?)\s+(?:robot|rover|arm|car)\b",
        r"^([\w\s\-]{3,30})\s+(?:robot|rover)\b",
    ]
    for pat in patterns:
        m = re.search(pat, desc)
        if m:
            name = m.group(1).strip().title()
            if len(name) > 2:
                return f"{name} Robot"
    return "My Robot"


def extract_config_fields(description: str) -> dict:
    """Rule-based extraction of RCAN fields from a plain-text description."""
    desc = description.lower()

    robot_name = _extract_robot_name(desc)

    cam_rule = _match_rules(desc, _CAMERA_RULES)
    camera_type = cam_rule[1] if cam_rule else "usb"
    depth_enabled = cam_rule[2] if cam_rule else False

    drv_rule = _match_rules(desc, _DRIVER_RULES)
    driver_protocol = drv_rule[1] if drv_rule else "mock"

    prov_rule = _match_rules(desc, _PROVIDER_RULES)
    provider = prov_rule[1] if prov_rule else "google"
    model = prov_rule[2] if prov_rule else "gemini-2.0-flash-exp"

    vision_enabled = camera_type in ("oakd", "usb", "picamera2")

    caps = ["status", "nav", "teleop", "chat"]
    if depth_enabled:
        caps += ["depth", "slam"]
    if any(kw in desc for kw in ("voice", "speak", "listen", "hotword")):
        caps.append("voice")
    if "imu" in desc or "gyro" in desc:
        caps.append("imu")

    depth_line = "\n  depth_enabled: true\n  imu_enabled: false" if depth_enabled else ""

    return {
        "robot_name": robot_name,
        "uuid": str(uuid.uuid4()),
        "description": description.strip()[:200].replace('"', "'"),
        "provider": provider,
        "model": model,
        "vision_enabled": str(vision_enabled).lower(),
        "camera_type": camera_type,
        "depth_line": depth_line,
        "driver_protocol": driver_protocol,
        "capabilities": ", ".join(caps),
    }


def generate_rcan_config(description: str, brain=None) -> str:
    """Generate a RCAN YAML string from a natural language description.

    If *brain* is provided and succeeds, uses the LLM for richer extraction.
    Always falls back to rule-based generation on LLM error.
    """
    if brain is not None:
        try:
            prompt = (
                "You are a robot configuration expert. "
                "Based on this description, generate a complete, valid RCAN YAML configuration.\n\n"
                f"Description: {description}\n\n"
                "Requirements:\n"
                "- rcan_version: '1.2.0'\n"
                "- Include metadata.robot_name, metadata.robot_uuid (new UUID), "
                "metadata.author, metadata.description\n"
                "- Include agent.provider, agent.model, agent.vision_enabled\n"
                "- Include camera.type and camera.fps\n"
                "- Include at least one driver with id and protocol\n"
                "- Include safety.obstacle_stop_cm and safety.estop_on_startup\n"
                "- Include rcan_protocol.capabilities list\n"
                "Return ONLY valid YAML, no markdown fences, no commentary."
            )
            thought = brain.think(b"", prompt)
            raw = (thought.raw_text or "").strip()
            # Strip markdown code fences
            raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"\n?```\s*$", "", raw)
            if "rcan_version" in raw and "drivers" in raw:
                logger.info("RCAN config generated via LLM (%d chars)", len(raw))
                return raw
            logger.warning("LLM output missing required fields — falling back to rule-based")
        except Exception as exc:
            logger.warning("LLM RCAN generation failed: %s — using rule-based fallback", exc)

    fields = extract_config_fields(description)
    result = _RCAN_TEMPLATE.format(**fields)
    logger.info("RCAN config generated via rule-based extraction (%d chars)", len(result))
    return result


# ── Preset templates ──────────────────────────────────────────────────────────

BUILT_IN_TEMPLATES = {
    "rpi_rover_gemini": "Raspberry Pi 5 rover with PCA9685 motor hat and Google Gemini",
    "oak_depth_rover": "Rover with OAK-D depth camera and Ollama local LLM",
    "stepper_arm": "6-axis stepper motor robot arm with Claude Anthropic",
    "groq_speedbot": "High-speed RC car with Groq ultra-low-latency inference",
    "private_local": "Fully private local robot with Ollama and no cloud services",
}


def list_templates() -> dict[str, str]:
    """Return built-in template name → description mapping."""
    return dict(BUILT_IN_TEMPLATES)


def generate_from_template(template_name: str, brain=None) -> str:
    """Generate a RCAN config from a named built-in template."""
    desc = BUILT_IN_TEMPLATES.get(template_name)
    if not desc:
        raise ValueError(
            f"Unknown template '{template_name}'. Available: {list(BUILT_IN_TEMPLATES)}"
        )
    return generate_rcan_config(desc, brain=brain)

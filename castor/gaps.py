"""Capability gaps — what this robot's hardware can do that its software can't, yet.

THE IDEA, stated by the operator: on a new machine, anything "missing" is not a
failure, it is a SKILL THE COMMUNITY SHOULD KNOW ABOUT. A chip on the bus with
no driver, a chassis with no actuator package, a robot with no local model —
each is a seam where somebody (a person, or an AI with the operator's explicit
permission) could build the missing piece and give it back.

For that to work, gaps have to be DATA, not log lines. This module turns what
detection already knows into a structured file (`<home>/gaps.json`) that the
app can render, an AI can read, and a future community registry can match
against existing skills.

THE AUTHORITY RULE, which is not negotiable: a gap NEVER closes itself.
Closing one means new code on the robot and usually a new capability block in
ROBOT.md — and ROBOT.md is signed precisely so that nothing changes it without
the operator. The rail is:

    detect (this file) -> operator sees the gap -> operator ALLOWS a draft
    -> an AI drafts the skill locally -> operator reviews and signs
    -> the manifest is re-signed and the gateway reloads

An AI that could close gaps on its own initiative would be inventing authority
out of an I2C address, which is the exact thing the signed-manifest design
exists to prevent. See docs/SKILL-GAPS.md for the full rail.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

GAPS_VERSION = 1


@dataclass
class Gap:
    """One missing piece, described well enough to act on."""

    #: Stable slug so reruns update rather than duplicate.
    id: str
    #: missing-package | unclaimed-peripheral | no-brain
    kind: str
    #: What was OBSERVED — evidence, not interpretation.
    evidence: str
    #: The one action that closes it, when known.
    suggestion: str
    #: A hint for whoever drafts the skill: entry-point group, config block,
    #: driver protocol. Free-form on purpose; drafting is a human/AI job.
    skill_hint: str = ""
    #: Extra machine-readable context (addresses, device paths).
    detail: dict = field(default_factory=dict)


def collect(*, home: Path) -> list[Gap]:
    """Everything detection can currently notice. Best-effort by design:
    a scanner that cannot run (no bus, no ollama) contributes nothing rather
    than an error — `up` must succeed on the barest host."""
    gaps: list[Gap] = []
    gaps += _actuator_gaps()
    gaps += _peripheral_gaps(home)
    gaps += _brain_gaps()
    return gaps


def _actuator_gaps() -> list[Gap]:
    from castor.up import resolve_actuator

    name, note = resolve_actuator()
    if note is None:
        return []
    return [Gap(
        id="actuator.rc-car.missing",
        kind="missing-package",
        evidence=f"gateway actuator resolved to {name!r}",
        suggestion="pip install rc-car-actuator && castor up",
        skill_hint="entry-point group robot_md_gateway.actuators",
    )]


def _peripheral_gaps(home: Path) -> list[Gap]:
    """Devices the scanners can see that no declared capability appears to use.

    "Appears to" is literal, same as the app's peripherals screen: matching is
    by category prefix, and a robot may drive a device through a capability
    named something this cannot guess. A gap is an invitation to look, never
    an accusation.
    """
    try:
        from castor.peripherals import scan_i2c, scan_serial, scan_usb
    except Exception:  # noqa: BLE001
        return []
    declared = _declared_capabilities(home / "ROBOT.md")
    prefixes = {
        "motor": ("drive.", "arm.", "servo.", "motor."),
        "camera": ("camera.", "vision.", "see."),
        "depth": ("camera.", "vision.", "depth."),
        "lidar": ("lidar.", "scan.", "slam."),
        "imu": ("imu.", "pose."),
        "sensor": ("sensor.",),
    }
    gaps: list[Gap] = []
    for scanner in (scan_i2c, scan_usb, scan_serial):
        try:
            found = scanner()
        except Exception:  # noqa: BLE001
            continue
        for dev in found:
            wanted = prefixes.get(dev.category)
            if wanted is None:
                continue
            if any(c.startswith(wanted) for c in declared):
                continue
            where = dev.device_path or (
                f"i2c 0x{dev.i2c_address:02x}" if dev.i2c_address is not None else "?")
            gaps.append(Gap(
                id=f"peripheral.{dev.category}.{where.replace('/', '_')}",
                kind="unclaimed-peripheral",
                evidence=f"{dev.name} ({dev.confidence}) on {where}; "
                         f"no declared capability starts with {'/'.join(wanted)}",
                suggestion="declare a capability in ROBOT.md and wire a driver "
                           "(operator-signed — see docs/SKILL-GAPS.md)",
                skill_hint=dev.rcan_snippet,
                detail={"category": dev.category, "interface": dev.interface,
                        "driver_hint": dev.driver_hint},
            ))
    return gaps


def _brain_gaps() -> list[Gap]:
    from castor.up import detect_brain

    provider, model = detect_brain()
    if provider == "ollama" and not model:
        return [Gap(
            id="brain.none",
            kind="no-brain",
            evidence="no Ollama daemon answered and no Claude sign-in was found",
            suggestion="install Ollama and `ollama pull qwen3.5:2b`, or sign in "
                       "with `claude` — the phone's own brains work meanwhile",
        )]
    return []


def _declared_capabilities(manifest: Path) -> list[str]:
    try:
        import yaml

        text = manifest.read_text()
        if text.startswith("---"):
            front = text.split("---", 2)[1]
            data = yaml.safe_load(front) or {}
            caps = data.get("capabilities") or []
            names = []
            for c in caps:
                if isinstance(c, dict) and c.get("name"):
                    names.append(str(c["name"]))
                elif isinstance(c, str):
                    names.append(c)
            return names
    except Exception:  # noqa: BLE001
        pass
    return []


def write(gaps: list[Gap], home: Path) -> Path:
    """Persist as data the app and an AI can both consume. Rewritten whole on
    every run — a gap that stopped being detected stops being reported, which
    is exactly what plugging the missing package in should look like."""
    out = home / "gaps.json"
    out.write_text(json.dumps({
        "v": GAPS_VERSION,
        "generated_at": time.time(),
        "gaps": [asdict(g) for g in gaps],
    }, indent=1) + "\n")
    return out

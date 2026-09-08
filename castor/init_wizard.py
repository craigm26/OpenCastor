"""castor.init_wizard — interactive ROBOT.md generator.

Writes a v3.2 ROBOT.md (rcan-spec §8.6 agent.runtimes[]) to the target
path. Entry points:

- ``cmd_init(args)``       — full interactive or flag-driven init
- ``cmd_quickstart(args)`` — abbreviated quickstart (same emission, fewer prompts)

Both accept an ``argparse.Namespace`` and return an exit code.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

#: The shapes `castor init` knows about, and the identity each one implies.
#:
#: WHY THIS EXISTS. Every default in this file used to describe an arm — name
#: `bob`, model `so-arm101`, device id `bob-001`. A newcomer with an RC car
#: who typed the most obvious command got a manifest for somebody else's
#: robot, and there was no question anywhere that would have told them so.
#: Worse, argparse defaults every flag to None, so `castor init
#: --non-interactive` wrote a manifest whose robot_name, manufacturer, model,
#: version and device_id were all `null`; the defaults below were unreachable
#: from the CLI at all. Both are fixed here: the shape is the FIRST question,
#: and the shape's defaults are what a missing flag falls back to.
_SHAPES: dict[str, dict[str, str]] = {
    "arm": {
        "label": "arm      — SO-ARM101 or similar serial-bus servo arm",
        "robot_name": "bob",
        "manufacturer": "craigm26",
        "model": "so-arm101",
        "device_id": "bob-001",
    },
    "rc-car": {
        "label": "rc-car   — RC car: PCA9685 over I2C driving an ESC and a steering servo",
        "robot_name": "car",
        "manufacturer": "craigm26",
        "model": "rpi-rc-car",
        "device_id": "car-001",
    },
    "sim": {
        "label": "sim      — no hardware yet; simulated actuators",
        "robot_name": "sim",
        "manufacturer": "craigm26",
        "model": "simulated",
        "device_id": "sim-001",
    },
}
#: `arm` stays the fallback so every existing flag-driven and non-interactive
#: caller emits exactly what it emitted before. What changed is that an
#: interactive run is ASKED, rather than defaulted into somebody else's robot.
_DEFAULT_SHAPE = "arm"

_ROBOT_MD_TEMPLATE = """\
# {robot_name}

A {manufacturer} {model} declared for the RCAN ecosystem.

## Runtime

Select with:

```bash
castor run --runtime opencastor
```
{bringup}"""

#: Shape-specific tail of the manifest body. The RC-car one is the whole point
#: of the shape question: the catalog names an `rpi_rc_car` preset that has no
#: backing YAML, and `hardware_detect.suggest_preset()` can return it, so the
#: honest answer to "I have a car" is not a preset at all — it is `castor up`,
#: which is where the RC-car shape actually lives (castor/templates/rc_car/).
_BRINGUP: dict[str, str] = {
    "arm": """
## Bring-up

```bash
castor up --home ~/robot --name {robot_name}
```

`castor up` scans the bus, writes the systemd user units and prints a pairing
QR. Ports: gateway 8080, runtime 8081, console 8082.
""",
    "rc-car": """
## Bring-up

This is an RC car: a PCA9685 at 0x40 driving an ESC and a steering servo.
`castor up` is the command that brings that shape up end to end, and it is the
only one that knows the shape — there is no `rpi_rc_car` preset YAML, and the
wizard's preset list cannot produce this robot.

```bash
castor up --home ~/car --name {robot_name}
```

It scans the I2C bus, picks the `rc-car` archetype, writes four systemd user
units and prints a pairing QR. Ports: gateway 8080, runtime 8081, console 8082.

**It starts on simulated wheels on purpose.** Real PWM is a deliberate later
edit in `gateway-policy.env` in the robot home, and the wheels-off-the-ground
warning next to it is not decoration. Every `drive.set` must carry an explicit
`duration_s`: an absent or zero duration is a zero-length lease, which the
actuator treats as a stop.
""",
    "sim": """
## Bring-up

```bash
castor up --home ~/robot --name {robot_name}
```

No hardware is expected, so `castor up` picks the `sim` archetype. Ports:
gateway 8080, runtime 8081, console 8082.
""",
}


def _build_frontmatter(
    *,
    robot_name: str,
    manufacturer: str,
    model: str,
    version: str,
    device_id: str,
    provider: str,
    llm_model: str,
) -> dict[str, Any]:
    """Construct the v3.2 frontmatter dict."""
    return {
        "rcan_version": "3.2",
        "metadata": {
            "robot_name": robot_name,
            "manufacturer": manufacturer,
            "model": model,
            "version": version,
            "device_id": device_id,
        },
        "network": {
            "rrf_endpoint": "https://rcan.dev",
            "signing_alg": "pqc-hybrid-v1",
        },
        "agent": {
            "runtimes": [
                {
                    "id": "opencastor",
                    "harness": "castor-default",
                    "default": True,
                    "models": [
                        {"provider": provider, "model": llm_model, "role": "primary"},
                    ],
                },
            ],
        },
        "safety": {
            "estop": {"software": True, "response_ms": 100},
        },
    }


def _write_robot_md(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    serialized = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
    path.write_text(f"---\n{serialized}---\n\n{body}")


def _prompt(prompt: str, default: str, non_interactive: bool) -> str:
    if non_interactive:
        return default
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def _resolve_shape(args: argparse.Namespace, non_interactive: bool) -> str:
    """Pick the robot's shape: the flag, then the question, then `arm`."""
    supplied = getattr(args, "shape", None)
    if supplied:
        if supplied in _SHAPES:
            return supplied
        sys.stderr.write(
            f"unknown --shape {supplied!r}; expected one of "
            f"{', '.join(_SHAPES)}. Using {_DEFAULT_SHAPE}.\n"
        )
        return _DEFAULT_SHAPE
    if non_interactive:
        return _DEFAULT_SHAPE
    sys.stdout.write("What shape is this robot?\n")
    for spec in _SHAPES.values():
        sys.stdout.write(f"  {spec['label']}\n")
    # Asked once, not looped: a typo at prompt one should not trap anybody in
    # a wizard, and every field it seeds is asked again below anyway.
    chosen = _prompt("Shape", _DEFAULT_SHAPE, non_interactive)
    if chosen not in _SHAPES:
        sys.stdout.write(f"  (not one of {', '.join(_SHAPES)} — using {_DEFAULT_SHAPE})\n")
        return _DEFAULT_SHAPE
    return chosen


def cmd_init(args: argparse.Namespace) -> int:
    """Write a ROBOT.md manifest. Returns a POSIX exit code."""
    non_interactive = bool(getattr(args, "non_interactive", False))
    path = Path(getattr(args, "path", "ROBOT.md"))
    force = bool(getattr(args, "force", False))

    if path.exists() and not force:
        sys.stderr.write(f"refusing to overwrite existing {path}. Pass --force to replace.\n")
        return 2

    shape = _resolve_shape(args, non_interactive)
    d = _SHAPES[shape]

    # `getattr(...) or d[...]` and not `getattr(..., default)`: argparse sets
    # every one of these to None when the flag is absent, so the attribute
    # EXISTS and getattr's default was never once consulted. That is how
    # `castor init --non-interactive` came to write robot_name: null.
    robot_name = _prompt(
        "Robot name", getattr(args, "robot_name", None) or d["robot_name"], non_interactive
    )
    manufacturer = _prompt(
        "Manufacturer", getattr(args, "manufacturer", None) or d["manufacturer"], non_interactive
    )
    model = _prompt("Model", getattr(args, "model", None) or d["model"], non_interactive)
    version = _prompt("Version", getattr(args, "version", None) or "1.0.0", non_interactive)
    device_id = _prompt(
        "Device ID", getattr(args, "device_id", None) or d["device_id"], non_interactive
    )
    provider = _prompt(
        "LLM provider", getattr(args, "provider", None) or "anthropic", non_interactive
    )
    llm_model = _prompt(
        "LLM model", getattr(args, "llm_model", None) or "claude-sonnet-4-6", non_interactive
    )

    fm = _build_frontmatter(
        robot_name=robot_name,
        manufacturer=manufacturer,
        model=model,
        version=version,
        device_id=device_id,
        provider=provider,
        llm_model=llm_model,
    )
    body = _ROBOT_MD_TEMPLATE.format(
        robot_name=robot_name,
        manufacturer=manufacturer,
        model=model,
        bringup=_BRINGUP[shape].format(robot_name=robot_name),
    )
    _write_robot_md(path, fm, body)
    sys.stdout.write(f"wrote {path}\n")
    return 0


def cmd_quickstart(args: argparse.Namespace) -> int:
    """Abbreviated quickstart: same emission, bob/opencastor defaults."""
    args.non_interactive = True
    shape = getattr(args, "shape", None) or _DEFAULT_SHAPE
    d = _SHAPES.get(shape, _SHAPES[_DEFAULT_SHAPE])
    for field, default in [
        ("robot_name", d["robot_name"]),
        ("manufacturer", d["manufacturer"]),
        ("model", d["model"]),
        ("version", "1.0.0"),
        ("device_id", d["device_id"]),
        ("provider", "anthropic"),
        ("llm_model", "claude-sonnet-4-6"),
        ("path", "ROBOT.md"),
    ]:
        if getattr(args, field, None) is None:
            setattr(args, field, default)
    return cmd_init(args)

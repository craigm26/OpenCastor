"""Teach the canvas-to-arm-base transform: ``python -m castor.bench.sacpaint.calibrate``.

Two ways in, both writing the same JSON:

**Typed** — you already know where the corners are in base millimetres (from a
teach pendant, a previous run, or a tape measure)::

    python -m castor.bench.sacpaint.calibrate --out canvas.json \\
        --corner bl=180,-140,-95 --corner br=180,160,-95 --corner tl=480,-140,-95

**Jogged** — the arm shows you. For each corner in turn you nudge the pen tip
in base millimetres until it sits exactly on that corner of the sheet, then
press ``r`` to record the position the gateway reports::

    python -m castor.bench.sacpaint.calibrate --out canvas.json \\
        --pair-payload /path/to/pair-payload.json --start 180,0,-60

Every jog is one ``arm.move_to`` through the gateway, so the teaching session
leaves the same signed receipt trail a run does. Nothing moves without a
keystroke, and the first move waits for a confirmation.

Three corners are enough; four make the reported residual worth reading. A
residual over a couple of millimetres means the pen will miss by that much
everywhere, which the landmark scorer will notice.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

import numpy as np

from castor.bench.sacpaint import calibration as calib
from castor.bench.sacpaint.gateway import (
    MOTION_SCOPE,
    GatewayClient,
    load_pair_payload,
    read_eef_mm,
)

_JOG_HELP = """
  x+ / x- / y+ / y- / z+ / z-   nudge by the step size
  s <mm>                        set the step size (default 5 mm)
  g <x,y,z>                     go to an absolute base position
  r                             record this position as the corner and move on
  q                             give up
"""


def _parse_corner(
    text: str, corners: dict[str, tuple[float, float, float]]
) -> tuple[str, tuple[float, float, float]]:
    """Parse ``bl=180,-140,-95`` into a corner name and its base millimetres."""
    name, _, numbers = text.partition("=")
    name = name.strip().lower()
    if name not in corners:
        raise SystemExit(f"unknown corner {name!r}; expected one of {sorted(corners)}")
    parts = [p for p in numbers.split(",") if p.strip()]
    if len(parts) != 3:
        raise SystemExit(f"corner {name} needs three base-mm numbers, got {numbers!r}")
    try:
        values = tuple(float(p) for p in parts)
    except ValueError as exc:
        raise SystemExit(f"corner {name}: {exc}") from exc
    return name, values  # type: ignore[return-value]


def _jog(
    client: GatewayClient,
    move_tool: str,
    start: np.ndarray,
    corner: str,
    corner_mm: tuple[float, float, float],
    *,
    input_fn: Any = input,
    output_fn: Any = print,
) -> np.ndarray:  # pragma: no cover - an interactive loop against real hardware
    """Nudge the pen to one corner and return the base millimetres it ended at."""
    here = start.astype(float).copy()
    step = 5.0
    output_fn(f"\n--- corner {corner.upper()} ({corner_mm[:2]} mm on the sheet)")
    output_fn(_JOG_HELP)
    while True:
        output_fn(f"  at {here.round(1).tolist()} mm, step {step} mm")
        command = input_fn("  jog> ").strip().lower()
        if command == "q":
            raise SystemExit("calibration abandoned; nothing written")
        if command == "r":
            return here
        if command.startswith("s "):
            step = float(command[2:])
            continue
        if command.startswith("g "):
            here = np.array([float(v) for v in command[2:].split(",")])
        elif len(command) == 2 and command[0] in "xyz" and command[1] in "+-":
            axis = "xyz".index(command[0])
            here[axis] += step if command[1] == "+" else -step
        else:
            output_fn("  ?" + _JOG_HELP)
            continue
        result = client.invoke(
            move_tool, {"x_mm": here[0], "y_mm": here[1], "z_mm": here[2]}, scope=MOTION_SCOPE
        )
        reported = read_eef_mm(result.telemetry)
        if reported is not None:
            here = np.array(reported)


def main(argv: Sequence[str] | None = None) -> int:
    """Teach or type the canvas corners and write the calibration JSON."""
    parser = argparse.ArgumentParser(
        prog="python -m castor.bench.sacpaint.calibrate",
        description="Teach the sacpaint canvas-to-arm-base transform.",
    )
    parser.add_argument("--out", required=True, help="where to write the calibration JSON")
    parser.add_argument(
        "--corner",
        action="append",
        default=[],
        metavar="NAME=X,Y,Z",
        help="a taught corner in base mm, e.g. bl=180,-140,-95 (repeatable; 3 or 4 of bl/br/tr/tl)",
    )
    parser.add_argument(
        "--pair-payload", help="pairing payload JSON: gateway URL, bearer, manifest path"
    )
    parser.add_argument("--gateway-url", help="gateway base URL (overrides the pairing payload)")
    parser.add_argument("--manifest-path", help="ROBOT.md path on the robot")
    parser.add_argument(
        "--token", help="actuate-tier bearer (prefer the pairing payload or the env)"
    )
    parser.add_argument("--actuator-name", default="so-arm101")
    parser.add_argument("--move-tool", default="arm.move_to")
    parser.add_argument("--start", help="base mm to jog from, e.g. 180,0,-60 (enables jog mode)")
    parser.add_argument(
        "--reference", help="reference name, for a sheet that is not the default size"
    )
    parser.add_argument("--no-check-up", action="store_true", help="allow a downward canvas normal")
    args = parser.parse_args(argv)

    # Sheet size comes from the reference, so a smaller custom canvas teaches its own corners.
    corners = calib.corner_canvas_mm(args.reference)
    taught: dict[str, tuple[float, float, float]] = {}
    for text in args.corner:
        name, values = _parse_corner(text, corners)
        taught[name] = values

    if args.start:  # pragma: no cover - hardware path
        payload = load_pair_payload(args.pair_payload) if args.pair_payload else {}
        client = GatewayClient(
            args.gateway_url or payload.get("gateway_url") or "http://127.0.0.1:8080",
            token=args.token or payload.get("bearer"),
            manifest_path=args.manifest_path or payload.get("manifest_path"),
            actuator_name=args.actuator_name,
        )
        start = np.array([float(v) for v in args.start.split(",")])
        print("The arm will move when you press a jog key. Clear the workspace.")
        input("Press Enter when ready, or Ctrl-C to stop: ")
        for corner in calib.CORNER_ORDER:
            if corner in taught:
                continue
            taught[corner] = tuple(_jog(client, args.move_tool, start, corner, corners[corner]))  # type: ignore[arg-type]
            start = np.array(taught[corner])
            if len(taught) >= 3 and input("  enough corners? [y/N] ").strip().lower() == "y":
                break

    if len(taught) < 3:
        parser.error(
            f"need at least 3 corners, got {len(taught)}. Pass --corner NAME=X,Y,Z three times, "
            "or --start X,Y,Z to jog the arm to them."
        )

    pairs = [(corners[name], base) for name, base in taught.items()]
    calibration = calib.fit(pairs, check_up=not args.no_check_up, note=f"corners: {sorted(taught)}")
    out = calib.save(calibration, args.out)

    print(f"\nwrote {out}")
    print(f"  corners      {sorted(taught)}")
    print(f"  rms residual {calibration.rms_residual_mm:.2f} mm")
    print(f"  max residual {calibration.max_residual_mm:.2f} mm")
    if calibration.max_residual_mm > 2.0:
        print(
            "  WARNING: over 2 mm out. The pen will miss by about that much everywhere. "
            "Re-teach the worst corner before running the benchmark."
        )
    print(f"\nUse it with:  -E calibration={out}")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())

"""The stroke primitive: one call, many segments.

Without it, one policy call moves the pen to exactly one target, so a drawing
costs as many model calls as it has corners and a run with a call budget spends
the budget long before the sheet is full. A ``stroke`` is the same motion the
body always made, batched: a list of up to :data:`MAX_POINTS` points in sheet
metres, drawn as one polyline.

Nothing about the wire changes. A stroke is planned here into the very targets
the body already knows how to send (travel height over the first point, down,
each point in turn, up again after the last), and each of those targets is one
ordinary per-target motion: one gateway call with three millimetre coordinates,
one receipt, the same tolerance and the same miss handling. The only things
that batch are the policy's turn and the observation: the body looks at the
sheet once, at the end of the stroke, instead of once per corner.

A stroke is checked before anything moves. A point off the sheet, a
non-finite number, an empty list or more than :data:`MAX_POINTS` points raises
:class:`StrokeError` with the pen exactly where it was, because a half-drawn
stroke leaves the pen somewhere the policy did not ask for.

The colour, on a colour task, belongs to the whole stroke: one number for the
line, exactly as the per-target action's fourth number worked, and exactly as
irrelevant to the arm (the arm holds no pen and swaps no colour).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from castor.bench.sacpaint import palette as pal

#: Most points one stroke may carry. A cap, not a target: it keeps one refused
#: call cheap, keeps the planned target list inside a rollout's step horizon,
#: and keeps a single malformed reply from committing the arm to a long run.
MAX_POINTS = 24

#: The tool name a policy calls to lay a whole stroke in one turn.
TOOL_NAME = "stroke"

#: ``Action.meta`` keys. Every target of a stroke carries the stroke's id, so a
#: recorded step says which stroke it belongs to; the last one is marked, and
#: that is the only target the body takes a fresh observation after.
STROKE_KEY = "stroke"
POINT_KEY = "stroke_point"
FINAL_KEY = "stroke_final"


class StrokeError(ValueError):
    """A stroke the body refuses. Always raised before anything moves."""


def parse_points(raw: Any, *, max_points: int = MAX_POINTS) -> list[tuple[float, float]]:
    """Read a policy's ``points`` argument as a list of ``(x, y)`` sheet metres."""
    try:
        arr = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise StrokeError(
            "points must be a list of [x, y] pairs in sheet metres, "
            "like [[0.02, 0.30], [0.12, 0.30]]"
        ) from exc
    if arr.size == 0:
        raise StrokeError("a stroke needs at least one point")
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise StrokeError(
            "points must be a list of [x, y] pairs in sheet metres (no z: the pen height is "
            f"implied by the stroke), got an array of shape {tuple(arr.shape)}"
        )
    if arr.shape[0] > max_points:
        raise StrokeError(
            f"a stroke carries at most {max_points} points, got {arr.shape[0]}; "
            "split it into several strokes"
        )
    if not bool(np.all(np.isfinite(arr))):
        raise StrokeError("every point must be two finite numbers")
    return [(float(x), float(y)) for x, y in arr]


def plan(
    points: Any,
    *,
    low: Any,
    high: Any,
    pen_down_z: float,
    travel_z: float,
    color: str | int | float | None = None,
    max_points: int = MAX_POINTS,
) -> list[np.ndarray]:
    """The ordinary per-target sequence one stroke becomes, or ``StrokeError``.

    ``low`` and ``high`` are the body's action-space bounds, so the sheet the
    stroke must stay on is the sheet the body declared. The returned vectors
    are three numbers, or four when ``color`` is given, which is exactly what
    the body's per-target path already accepts.
    """
    pts = parse_points(points, max_points=max_points)
    lo = np.asarray(low, dtype=np.float64).reshape(-1)
    hi = np.asarray(high, dtype=np.float64).reshape(-1)
    off = [
        (i, p) for i, p in enumerate(pts) if not (lo[0] <= p[0] <= hi[0] and lo[1] <= p[1] <= hi[1])
    ]
    if off:
        index, (x, y) = off[0]
        raise StrokeError(
            f"point {index} ({x:.4f}, {y:.4f}) is off the sheet "
            f"(x {lo[0]:.3f} to {hi[0]:.3f}, y {lo[1]:.3f} to {hi[1]:.3f}); "
            f"{len(off)} of {len(pts)} points are. Nothing moved: send the stroke again "
            "with every point on the sheet."
        )
    if travel_z <= pen_down_z:
        raise StrokeError(
            f"travel_z ({travel_z}) must be above pen_down_z ({pen_down_z}), "
            "or the travel to the first point would draw"
        )
    index = None if color is None else pal.index_of(color)

    def vec(x: float, y: float, z: float) -> np.ndarray:
        values = [x, y, float(z)] if index is None else [x, y, float(z), float(index)]
        return np.array(values, dtype=np.float64)

    x0, y0 = pts[0]
    xn, yn = pts[-1]
    targets = [vec(x0, y0, travel_z), vec(x0, y0, pen_down_z)]
    targets += [vec(x, y, pen_down_z) for x, y in pts[1:]]
    targets.append(vec(xn, yn, travel_z))
    return targets


def meta_for(stroke_id: int, index: int, total: int) -> dict[str, Any]:
    """The ``Action.meta`` one target of a stroke carries into the trial record."""
    return {
        STROKE_KEY: int(stroke_id),
        POINT_KEY: int(index),
        FINAL_KEY: index == total - 1,
    }


def is_open(meta: Any) -> bool:
    """True for a target that is inside a stroke and not its last one."""
    if not isinstance(meta, dict) or meta.get(STROKE_KEY) is None:
        return False
    return not bool(meta.get(FINAL_KEY))


def docs_paragraph(max_points: int = MAX_POINTS, colored: bool = False) -> str:
    """The prompt paragraph that explains the primitive and asks for long strokes."""
    text = (
        f" You can lay a whole stroke in one call. The '{TOOL_NAME}' tool takes 'points', a list "
        f"of up to {max_points} [x, y] pairs in metres on the sheet, and draws the polyline "
        "through them: the pen travels above the first point, comes down, runs the whole line, "
        "and lifts again after the last point, so a stroke never carries a z. Every point must "
        "be on the sheet; one that is not refuses the whole stroke and the pen does not move. "
        "You see the sheet once, at the end of the stroke. Spend your calls on long strokes that "
        "cross the picture rather than on many short ones: a stroke of twenty points costs one "
        "call, and twenty separate targets cost twenty."
    )
    if colored:
        text += (
            " A stroke carries one 'color' for the whole line, the palette index it is inked in: "
            + pal.describe()
            + "."
        )
    return text


def tool_schema(
    *,
    canvas_mm: tuple[float, float],
    colored: bool = False,
    max_points: int = MAX_POINTS,
) -> dict[str, Any]:
    """The ``stroke`` tool declaration, in the OpenAI function shape the agent speaks."""
    width, height = (float(v) / 1000.0 for v in canvas_mm)
    properties: dict[str, Any] = {
        "points": {
            "type": "array",
            "minItems": 1,
            "maxItems": max_points,
            "items": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": {"type": "number"},
            },
            "description": (
                f"Up to {max_points} [x, y] points in metres on the sheet "
                f"(x 0 to {width:.2f}, y 0 to {height:.2f}). No z: the pen comes down for the "
                "whole polyline and lifts again at the end."
            ),
        },
        "note": {
            "type": "string",
            "description": (
                "What you see right now and why you are drawing this stroke. The user reads "
                "these notes live and in the saved transcript."
            ),
        },
    }
    required = ["points", "note"]
    if colored:
        properties["color"] = {
            "type": "integer",
            "minimum": 0,
            "maximum": len(pal.NAMES) - 1,
            "description": "Palette index for the whole stroke: " + pal.describe() + ".",
        }
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "Draw one polyline through the given points. Each point is a real arm motion, "
                "so prefer long strokes. A point off the sheet refuses the whole stroke and "
                "nothing moves."
            ),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def call(body: Any, arguments: dict[str, Any], *, stroke_id: int | None = None) -> Any:
    """Run one ``stroke`` tool call against a body. Returns the body's ``StepResult``."""
    if not isinstance(arguments, dict):
        raise StrokeError("stroke arguments must be a JSON object with a 'points' list")
    return body.stroke(arguments.get("points"), color=arguments.get("color"), stroke_id=stroke_id)

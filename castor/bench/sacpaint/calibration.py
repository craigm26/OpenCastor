"""The canvas frame to arm base frame transform, taught once from the sheet corners.

The benchmark's action space is the *canvas* frame: metres, x right across the
sheet (0 to 0.30), y up the sheet (0 to 0.40), z above the paper. The gateway's
``arm.move_to`` speaks the *arm base* frame: millimetres, z up, x forward. This
module owns the one rigid transform between them.

It is fitted, not derived. The OAK-D extrinsic in Bob's manifest has a 142 mm
residual, which is fine for a gripper reaching for a block and useless for a pen
on paper, so the operator teaches three or four canvas corners by jogging the
pen tip to each one and recording ``eef_mm``. Three non-collinear corners are
enough; four lets the fit report a residual you can trust.

**Units are asymmetric on purpose, because the two frames are.** The taught
pairs are millimetres on *both* sides (you read millimetres off the gateway and
you measure the sheet in millimetres). The runtime conversions match the wire:
:meth:`CanvasCalibration.canvas_to_base` takes canvas **metres** and returns
base **millimetres**; :meth:`CanvasCalibration.base_to_canvas` does the reverse.
Every function name and argument name says which.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from inspect_robots.errors import ConfigError

from castor.bench.sacpaint.reference import CANVAS_MM

#: Schema version of the calibration JSON. Bumped when the file layout changes.
SCHEMA_VERSION = 1


def corner_canvas_mm(reference: str | None = None) -> dict[str, tuple[float, float, float]]:
    """The four sheet corners in canvas millimetres, for a named reference.

    ``bl`` is the canvas origin; ``tl`` is the top-left as the drawing is read.
    References may declare different sheet sizes, so the corners are resolved
    per reference rather than frozen at import.
    """
    from castor.bench.sacpaint import reference as ref

    get_spec = getattr(ref, "get_spec", None)
    if callable(get_spec):
        name = reference or getattr(ref, "DEFAULT_REFERENCE", None)
        width, height = get_spec(name).canvas_mm if name else ref.CANVAS_MM
    else:  # a sacpaint that predates named references
        if reference is not None:
            raise CalibrationError(
                f"this sacpaint build has a single built-in reference and cannot size {reference!r}"
            )
        width, height = ref.CANVAS_MM
    return {
        "bl": (0.0, 0.0, 0.0),
        "br": (float(width), 0.0, 0.0),
        "tr": (float(width), float(height), 0.0),
        "tl": (0.0, float(height), 0.0),
    }


#: The four sheet corners for the default reference. Convenience for callers that
#: only ever draw the built-in; use :func:`corner_canvas_mm` for anything else.
CORNER_CANVAS_MM: dict[str, tuple[float, float, float]] = {
    "bl": (0.0, 0.0, 0.0),
    "br": (CANVAS_MM[0], 0.0, 0.0),
    "tr": (CANVAS_MM[0], CANVAS_MM[1], 0.0),
    "tl": (0.0, CANVAS_MM[1], 0.0),
}

#: Corner order the teach tool walks, and the order stored in the JSON.
CORNER_ORDER: tuple[str, ...] = ("bl", "br", "tr", "tl")

_MM_PER_M = 1000.0


class CalibrationError(ConfigError):
    """The canvas-to-base transform is missing, unfittable, or physically wrong.

    A subclass of ``ConfigError`` so the CLI fails fast, before any motion.
    """


def _as_canvas_mm(point: Sequence[float]) -> np.ndarray:
    """Coerce a taught canvas point to ``(x, y, z)`` millimetres, defaulting z to the paper."""
    arr = np.asarray(point, dtype=np.float64).reshape(-1)
    if arr.size == 2:
        arr = np.array([arr[0], arr[1], 0.0])
    if arr.size != 3:
        raise CalibrationError(f"canvas point must have 2 or 3 numbers, got {arr.size}: {point!r}")
    if not np.all(np.isfinite(arr)):
        raise CalibrationError(f"canvas point is not finite: {point!r}")
    return arr


def _as_base_mm(point: Sequence[float]) -> np.ndarray:
    """Coerce a taught base point to ``(x, y, z)`` millimetres."""
    arr = np.asarray(point, dtype=np.float64).reshape(-1)
    if arr.size != 3:
        raise CalibrationError(f"base point must have 3 numbers, got {arr.size}: {point!r}")
    if not np.all(np.isfinite(arr)):
        raise CalibrationError(f"base point is not finite: {point!r}")
    return arr


class CanvasCalibration:
    """A rigid canvas-to-base transform plus the points it was fitted from.

    ``rotation`` maps canvas axes onto base axes and ``translation`` is the base
    position of the canvas origin, both in millimetres::

        base_mm = rotation @ canvas_mm + translation
    """

    def __init__(
        self,
        rotation: np.ndarray,
        translation: np.ndarray,
        *,
        points: Sequence[dict[str, Any]] = (),
        rms_residual_mm: float = 0.0,
        max_residual_mm: float = 0.0,
        created: str | None = None,
        note: str | None = None,
    ) -> None:
        self.rotation = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
        self.translation = np.asarray(translation, dtype=np.float64).reshape(3)
        self.points = [dict(p) for p in points]
        self.rms_residual_mm = float(rms_residual_mm)
        self.max_residual_mm = float(max_residual_mm)
        self.created = created or datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.note = note
        if not np.all(np.isfinite(self.rotation)) or not np.all(np.isfinite(self.translation)):
            raise CalibrationError("calibration contains non-finite numbers")
        det = float(np.linalg.det(self.rotation))
        if not math.isclose(det, 1.0, abs_tol=1e-6):
            raise CalibrationError(
                f"calibration rotation is not a proper rotation (det={det:.6f}); "
                "refit from the taught corners rather than hand-editing the file"
            )

    # -- conversions -------------------------------------------------------

    def canvas_to_base(self, canvas_xyz_m: Sequence[float]) -> np.ndarray:
        """Canvas metres to arm-base millimetres, the units ``arm.move_to`` wants."""
        canvas_mm = np.asarray(canvas_xyz_m, dtype=np.float64).reshape(3) * _MM_PER_M
        return self.rotation @ canvas_mm + self.translation

    def base_to_canvas(self, base_xyz_mm: Sequence[float]) -> np.ndarray:
        """Arm-base millimetres (as ``eef_mm`` reports them) back to canvas metres."""
        base_mm = np.asarray(base_xyz_mm, dtype=np.float64).reshape(3)
        return (self.rotation.T @ (base_mm - self.translation)) / _MM_PER_M

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The JSON-serialisable form written by :func:`save`."""
        return {
            "version": SCHEMA_VERSION,
            "frame_in": "canvas_mm",
            "frame_out": "base_mm",
            "rotation": [[float(v) for v in row] for row in self.rotation],
            "translation": [float(v) for v in self.translation],
            "rms_residual_mm": self.rms_residual_mm,
            "max_residual_mm": self.max_residual_mm,
            "points": self.points,
            "created": self.created,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CanvasCalibration:
        """Rebuild from :meth:`to_dict` output, refusing a version this code cannot read."""
        version = data.get("version")
        if version != SCHEMA_VERSION:
            raise CalibrationError(
                f"calibration schema version {version!r} is not the {SCHEMA_VERSION} this "
                "sacpaint understands; re-run `python -m castor.bench.sacpaint.calibrate`"
            )
        for key in ("rotation", "translation"):
            if key not in data:
                raise CalibrationError(f"calibration file is missing {key!r}")
        return cls(
            np.asarray(data["rotation"], dtype=np.float64),
            np.asarray(data["translation"], dtype=np.float64),
            points=data.get("points", ()),
            rms_residual_mm=data.get("rms_residual_mm", 0.0),
            max_residual_mm=data.get("max_residual_mm", 0.0),
            created=data.get("created"),
            note=data.get("note"),
        )

    def __repr__(self) -> str:
        return (
            f"CanvasCalibration(points={len(self.points)}, "
            f"rms={self.rms_residual_mm:.2f}mm, max={self.max_residual_mm:.2f}mm)"
        )


def fit(
    pairs: Iterable[tuple[Sequence[float], Sequence[float]]],
    *,
    check_up: bool = True,
    note: str | None = None,
) -> CanvasCalibration:
    """Fit the rigid canvas-to-base transform from taught corner correspondences.

    ``pairs`` are ``(canvas_mm, base_mm)``: the corner's nominal position on the
    sheet (millimetres, z omitted means on the paper) and the ``eef_mm`` the arm
    reported with the pen tip touching it. Three non-collinear corners suffice.

    Kabsch with a determinant correction, so the result is always a proper
    rotation. The taught points are coplanar, which leaves the canvas normal
    unconstrained by the residual; the determinant fix pins it to
    ``x_axis x y_axis``. ``check_up`` then rejects a fit whose canvas +z points
    into the table, which is what a swapped corner label produces and which
    would drive the pen down when the policy asks to lift it.
    """
    pair_list = list(pairs)
    if len(pair_list) < 3:
        raise CalibrationError(
            f"need at least 3 taught corners to fit a rigid transform, got {len(pair_list)}"
        )
    canvas = np.array([_as_canvas_mm(c) for c, _ in pair_list])
    base = np.array([_as_base_mm(b) for _, b in pair_list])

    c_mean = canvas.mean(axis=0)
    b_mean = base.mean(axis=0)
    c_centred = canvas - c_mean
    b_centred = base - b_mean

    # Rank of the in-plane spread: collinear corners cannot pin a rotation.
    singulars = np.linalg.svd(c_centred, compute_uv=False)
    if singulars.size < 2 or singulars[1] < 1e-6 * max(singulars[0], 1e-9):
        raise CalibrationError(
            "the taught canvas points are collinear (or coincident); teach three "
            "corners that span the sheet, e.g. bottom-left, bottom-right, top-left"
        )

    u, _, vt = np.linalg.svd(c_centred.T @ b_centred)
    d = float(np.sign(np.linalg.det(vt.T @ u.T)))
    if d == 0.0:  # pragma: no cover - only reachable from a singular covariance
        d = 1.0
    rotation = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    translation = b_mean - rotation @ c_mean

    if check_up and rotation[2, 2] <= 0.0:
        raise CalibrationError(
            "the fitted canvas normal points down (canvas +z maps to base "
            f"z={rotation[2, 2]:+.3f}): lifting the pen would drive it into the "
            "sheet. Two corner labels are almost certainly swapped — bl/br/tr/tl "
            "run anticlockwise as the drawing is read. Pass check_up=False only "
            "if the sheet really is mounted upside down."
        )

    predicted = canvas @ rotation.T + translation
    residuals = np.linalg.norm(predicted - base, axis=1)
    points = [
        {
            "canvas_mm": [float(v) for v in c],
            "base_mm": [float(v) for v in b],
            "residual_mm": float(r),
        }
        for c, b, r in zip(canvas, base, residuals, strict=False)
    ]
    return CanvasCalibration(
        rotation,
        translation,
        points=points,
        rms_residual_mm=float(np.sqrt(float(np.mean(residuals**2)))),
        max_residual_mm=float(residuals.max()),
        note=note,
    )


#: Where the virtual easel stands by default, measured on Bob 2026-09-09. With his calibrated
#: joint limits the tip reaches a thin shell roughly 300-370 mm from the base, so no flat sheet
#: lies in a horizontal plane; an upright one 325 mm straight ahead, centred at base height,
#: does (every point of it within 0.5 mm of a reachable pose; 8 of 9 probe points reached
#: within 5 mm, the ninth at 5.9). ``distance`` is the distance from the base to the sheet's
#: centre, ``elevation`` the centre's angle above the base plane (the sheet leans back with
#: it, like a painter's easel), ``azimuth`` its bearing left of straight ahead.
EASEL_DISTANCE_MM = 325.0
EASEL_ELEVATION_DEG = 0.0
EASEL_AZIMUTH_DEG = 0.0
EASEL_NOTE = "virtual easel: a sheet tangent to the arm's reach sphere; no paper, no pen, the ink is telemetry"


def easel(
    reference: str | None = None,
    *,
    distance_mm: float = EASEL_DISTANCE_MM,
    elevation_deg: float = EASEL_ELEVATION_DEG,
    azimuth_deg: float = EASEL_AZIMUTH_DEG,
) -> CanvasCalibration:
    """A calibration for a sheet that is not there: tangent to the reach sphere, facing the arm.

    The sheet's centre sits ``distance_mm`` from the base at ``elevation_deg``
    above the base plane and ``azimuth_deg`` left of the base +x axis. Canvas +x
    (right, as the arm sees it) runs to base -y, canvas +y runs up the leaning
    sheet, and canvas +z comes off the paper towards the base. Only the
    ``virtual`` medium may use it: a real pen driven at an easel that is not
    there is a real arm driven into whatever is.
    """
    width, height = corner_canvas_mm(reference)["tr"][:2]
    el, az = math.radians(elevation_deg), math.radians(azimuth_deg)
    normal = np.array(
        [math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)]
    )  # base -> centre
    up = np.array(
        [-math.sin(el) * math.cos(az), -math.sin(el) * math.sin(az), math.cos(el)]
    )  # up the sheet
    right = np.cross(up, -normal)  # canvas +x; right-handed with up and the off-paper axis
    rotation = np.column_stack([right, up, -normal])
    centre = float(distance_mm) * normal
    translation = centre - right * (width / 2.0) - up * (height / 2.0)
    points = []
    for name, canvas in corner_canvas_mm(reference).items():
        base = rotation @ np.asarray(canvas) + translation
        points.append(
            {
                "corner": name,
                "canvas_mm": list(canvas),
                "base_mm": [float(v) for v in base],
                "residual_mm": 0.0,
            }
        )
    return CanvasCalibration(rotation, translation, points=points, note=EASEL_NOTE)


def save(calibration: CanvasCalibration, path: str | Path) -> Path:
    """Write the calibration as JSON, creating the parent directory."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(calibration.to_dict(), indent=2) + "\n", encoding="utf-8")
    return out


def load(path: str | Path) -> CanvasCalibration:
    """Read a calibration written by :func:`save`, with a remedy in every failure."""
    src = Path(path)
    try:
        text = src.read_text(encoding="utf-8")
    except OSError as exc:
        raise CalibrationError(
            f"no canvas calibration at {src}: teach one with "
            "`python -m castor.bench.sacpaint.calibrate --out <path>` and pass it as "
            "-E calibration=<path>"
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"canvas calibration at {src} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CalibrationError(f"canvas calibration at {src} must be a JSON object")
    return CanvasCalibration.from_dict(data)

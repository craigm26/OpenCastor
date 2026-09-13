"""The pen palette: a small fixed set of named colours the virtual ink can be.

Colour in this benchmark is a property of the **ink**, not of the robot. The
real arm holds no pen, changes no cartridge and moves no differently for one
colour than another: a colour is a number the policy attaches to a stroke, and
the virtual canvas renders that stroke in it. On paper (``medium=pen``) there
is one pen and the palette is not offered at all.

Two rules shape the table below.

* ``black`` is index 0, so every task, policy and run that never mentions a
  colour behaves exactly as it did before colour existed.
* Every entry is dark enough that its luminance is below
  :data:`castor.bench.sacpaint.reference.INK_THRESHOLD`. The line scorers
  binarise the canvas at that threshold, so a stroke in any palette colour is
  ink to them and the composite score is unchanged by the choice of colour.
  A pale palette would have made a yellow stroke invisible to the line
  scorers, which would have quietly punished colour runs.

Distances are plain Euclidean RGB, normalised by the largest distance between
any two entries of this palette, so "completely the wrong colour" is 1.0 and
"the right colour" is 0.0 on a scale the palette itself sets.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

#: Identity of the table below. A change here re-versions every colour reference.
PALETTE_VERSION = "sacpaint-12-v1"

#: ``(name, (r, g, b))``, index 0 first. Every luminance is under 128.
PALETTE: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("black", (0, 0, 0)),
    ("slate", (64, 72, 88)),
    ("crimson", (168, 24, 48)),
    ("rust", (170, 80, 20)),
    ("gold", (160, 124, 8)),
    ("olive", (96, 112, 32)),
    ("green", (24, 120, 64)),
    ("teal", (16, 112, 120)),
    ("azure", (24, 96, 176)),
    ("indigo", (48, 56, 152)),
    ("violet", (104, 48, 152)),
    ("brown", (96, 64, 40)),
)

NAMES: tuple[str, ...] = tuple(name for name, _ in PALETTE)
RGB: np.ndarray = np.array([rgb for _, rgb in PALETTE], dtype=np.int16)
DEFAULT_COLOR = NAMES[0]
DEFAULT_INDEX = 0
#: The action-space dimension a colour task adds. Mono tasks do not have it.
COLOR_DIM_LABEL = "color"
#: Suffix of the stored, downscaled, quantised colour reference.
COLOR_SUFFIX = ".color.png"
#: Pixels per millimetre the colour reference is stored at. Coarse on purpose:
#: it is a colour target, not a second line drawing.
COLOR_PX_PER_MM = 1.0
#: The colour reference's coarse region grid, (columns, rows).
COLOR_GRID = (6, 8)


def _max_distance() -> float:
    d = RGB.astype(np.float64)[:, None, :] - RGB.astype(np.float64)[None, :, :]
    return float(np.sqrt((d * d).sum(axis=2)).max())


#: The largest distance between two palette entries: the scale distances are read on.
MAX_DISTANCE = _max_distance()


def _pairwise() -> np.ndarray:
    d = RGB.astype(np.float64)[:, None, :] - RGB.astype(np.float64)[None, :, :]
    return np.sqrt((d * d).sum(axis=2)) / MAX_DISTANCE


#: ``DISTANCE[i, j]``: normalised distance between palette entries i and j, 0..1.
DISTANCE = _pairwise()


def index_of(color: str | int | float | None) -> int:
    """Palette index for a name, an index, or ``None`` (which is black).

    Floats are accepted and rounded because a colour arrives as one more number
    in an action vector; anything outside the table is a ``ValueError``, never a
    silent fallback to black.
    """
    if color is None:
        return DEFAULT_INDEX
    if isinstance(color, str):
        key = color.strip().lower()
        if key in NAMES:
            return NAMES.index(key)
        if key.isdigit():
            return index_of(int(key))
        raise ValueError(f"unknown pen colour {color!r}; the palette is {list(NAMES)}")
    value = int(round(float(color)))
    if not 0 <= value < len(NAMES):
        raise ValueError(f"pen colour index {color!r} is outside 0..{len(NAMES) - 1}")
    return value


def name_of(index: int | float) -> str:
    """The palette name for an index."""
    return NAMES[index_of(index)]


def rgb_of(color: str | int | float | None) -> tuple[int, int, int]:
    """The RGB triple a stroke in this colour is rendered with."""
    r, g, b = RGB[index_of(color)]
    return int(r), int(g), int(b)


def bgr_of(color: str | int | float | None) -> tuple[int, int, int]:
    """The same triple in OpenCV's channel order, for ``cv2.line``."""
    r, g, b = rgb_of(color)
    return b, g, r


def describe() -> str:
    """One line naming every colour and its index, for a prompt or a help text."""
    return ", ".join(f"{i}={name}" for i, name in enumerate(NAMES))


def quantize(rgb: np.ndarray) -> np.ndarray:
    """Nearest-palette index for every pixel of an RGB image, as uint8 indices."""
    flat = rgb.reshape(-1, 3).astype(np.int32)
    d = flat[:, None, :] - RGB.astype(np.int32)[None, :, :]
    return np.argmin((d * d).sum(axis=2), axis=1).astype(np.uint8).reshape(rgb.shape[:2])


def render(indices: np.ndarray) -> np.ndarray:
    """Turn an index image back into an RGB image of palette colours."""
    return RGB.astype(np.uint8)[indices]


def snap(rgb: np.ndarray) -> np.ndarray:
    """Quantise and re-render in one step: the palette's view of an image."""
    return render(quantize(rgb))


# --- building a colour reference ------------------------------------------------


def color_reference(
    photo_rgb: np.ndarray,
    canvas_mm: tuple[float, float],
    *,
    px_per_mm: float = COLOR_PX_PER_MM,
    grid: tuple[int, int] = COLOR_GRID,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """The colour target for a photograph: a downscaled quantised image and region targets.

    The image is the photograph resized to the canvas's own proportions at
    ``px_per_mm`` and snapped to the palette. The regions are a coarse grid over
    that image; each cell records the palette colour that covers most of it and
    the share of the whole picture the cell's dominant colour accounts for, so a
    scorer can ask "did the robot put roughly the right colour roughly there?"
    without pretending the robot traced a fill.
    """
    w_mm, h_mm = canvas_mm
    w_px, h_px = max(1, round(w_mm * px_per_mm)), max(1, round(h_mm * px_per_mm))
    small = cv2.resize(photo_rgb, (w_px, h_px), interpolation=cv2.INTER_AREA)
    indices = quantize(small)
    cols, rows = grid
    regions: list[dict[str, Any]] = []
    total = float(indices.size)
    for row in range(rows):
        y0, y1 = round(row * h_px / rows), round((row + 1) * h_px / rows)
        for col in range(cols):
            x0, x1 = round(col * w_px / cols), round((col + 1) * w_px / cols)
            cell = indices[y0:y1, x0:x1]
            if cell.size == 0:
                continue
            counts = np.bincount(cell.reshape(-1), minlength=len(NAMES))
            top = int(np.argmax(counts))
            regions.append(
                {
                    # Normalised image coordinates, y down, like the rubric's boxes.
                    "bbox": [
                        round(x0 / w_px, 4),
                        round(y0 / h_px, 4),
                        round(x1 / w_px, 4),
                        round(y1 / h_px, 4),
                    ],
                    "color": NAMES[top],
                    "share": round(float(counts[top]) / total, 5),
                }
            )
    return render(indices), regions


__all__ = [
    "COLOR_DIM_LABEL",
    "COLOR_GRID",
    "COLOR_PX_PER_MM",
    "COLOR_SUFFIX",
    "DEFAULT_COLOR",
    "DEFAULT_INDEX",
    "DISTANCE",
    "MAX_DISTANCE",
    "NAMES",
    "PALETTE",
    "PALETTE_VERSION",
    "RGB",
    "bgr_of",
    "color_reference",
    "describe",
    "index_of",
    "name_of",
    "quantize",
    "render",
    "rgb_of",
    "snap",
]

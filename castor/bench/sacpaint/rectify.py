"""Rectify a photo of the physical canvas into canonical pixels, with no fixture required.

Corner sources, in order of trust:

1. **Given corners**: four ``[x, y]`` pairs, normalized 0..1 or in pixels, ordered
   TL, TR, BR, BL as the canvas appears upright. The iPhone app lets the
   operator tap them; embodiments attach them as ``observation.extra["canvas_corners"]``.
2. **ArUco markers** (DICT_4X4_50 ids 0..3 at TL, TR, BR, BL, inner corner on the
   canvas corner) if someone did print a fixture.
3. **The sheet itself**: the largest bright quadrilateral in the frame. A white
   sheet on a darker desk, photographed roughly upright, is enough.

A homography from those four points to the canonical frame makes the scorer
independent of camera pose, lens, and mounting height.
"""

from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np

from castor.bench.sacpaint.reference import DEFAULT_REFERENCE, canonical_size

MARKER_IDS = (0, 1, 2, 3)
# Which of the four marker corners (TL, TR, BR, BL in ArUco order) touches the canvas.
_INNER_CORNER = {0: 2, 1: 3, 2: 0, 3: 1}


class RectifyError(RuntimeError):
    """Raised when no canvas corners can be established or the homography is degenerate."""


def _dictionary() -> cv2.aruco.Dictionary:
    return cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)


def marker_sheet(marker_px: int = 200, margin_px: int = 40) -> list[np.ndarray]:
    """The four optional marker images, in id order, for anyone who wants a printed fixture."""
    d = _dictionary()
    out = []
    for mid in MARKER_IDS:
        m = cv2.aruco.generateImageMarker(d, mid, marker_px)
        out.append(
            cv2.copyMakeBorder(
                m, margin_px, margin_px, margin_px, margin_px, cv2.BORDER_CONSTANT, value=255
            )
        )
    return out


def order_corners(pts: np.ndarray) -> np.ndarray:
    """Order four points as TL, TR, BR, BL by their position in the image."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    d = pts[:, 0] - pts[:, 1]
    return np.array(
        [pts[np.argmin(s)], pts[np.argmax(d)], pts[np.argmax(s)], pts[np.argmin(d)]],
        dtype=np.float32,
    )


def normalize_corners(
    corners: Sequence[Sequence[float]], image_shape: tuple[int, ...]
) -> np.ndarray:
    """Accept normalized or pixel corners and return ordered pixel corners (TL, TR, BR, BL)."""
    arr = np.asarray(corners, dtype=np.float64).reshape(4, 2)
    h, w = image_shape[:2]
    if arr.max() <= 1.0:
        arr = arr * np.array([w, h], dtype=np.float64)
    return order_corners(arr)


def find_marker_corners(image: np.ndarray) -> np.ndarray | None:
    """Canvas corners from the four ArUco markers, or None if not all are visible."""
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
    detector = cv2.aruco.ArucoDetector(_dictionary(), cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(gray)
    found: dict[int, np.ndarray] = {}
    if ids is not None:
        for quad, mid in zip(corners, ids.reshape(-1), strict=False):
            mid = int(mid)
            if mid in _INNER_CORNER and mid not in found:
                found[mid] = quad.reshape(4, 2)[_INNER_CORNER[mid]]
    if any(m not in found for m in MARKER_IDS):
        return None
    return np.array([found[m] for m in MARKER_IDS], dtype=np.float32)


def find_sheet_corners(image: np.ndarray, min_area_frac: float = 0.08) -> np.ndarray | None:
    """Corners of the largest bright quadrilateral (the sheet of paper), or None."""
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, bright = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Close the ink so the drawing does not break the sheet into pieces.
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = gray.shape[:2]
    best, best_area = None, min_area_frac * h * w
    for c in contours:
        area = cv2.contourArea(c)
        if area < best_area:
            continue
        peri = cv2.arcLength(c, True)
        approx = None
        for eps in (0.02, 0.03, 0.05, 0.08):
            approx = cv2.approxPolyDP(c, eps * peri, True)
            if len(approx) == 4:
                break
        if approx is None or len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        if area > 0.97 * h * w:
            continue  # the whole frame is not a sheet
        mask = np.zeros_like(gray)
        cv2.fillConvexPoly(mask, approx, 255)
        inside, outside = gray[mask > 0], gray[mask == 0]
        if outside.size == 0 or float(inside.mean()) - float(outside.mean()) < 25.0:
            continue  # not brighter than its surroundings: not paper on a desk
        best, best_area = approx.reshape(4, 2).astype(np.float32), area
    return None if best is None else order_corners(best)


def find_canvas_corners(
    image: np.ndarray, corners: Sequence[Sequence[float]] | None = None
) -> tuple[np.ndarray, str]:
    """Establish the canvas corners and say how: ``given``, ``markers``, or ``sheet``."""
    if corners is not None:
        return normalize_corners(corners, image.shape), "given"
    found = find_marker_corners(image)
    if found is not None:
        return found, "markers"
    found = find_sheet_corners(image)
    if found is not None:
        return found, "sheet"
    raise RectifyError(
        "could not find the canvas: no corners given, no ArUco markers, and no bright quadrilateral "
        "covering at least 8% of the frame. Photograph the whole sheet on a darker surface, or pass corners."
    )


def rectify(
    image: np.ndarray,
    corners: Sequence[Sequence[float]] | None = None,
    size: tuple[int, int] | None = None,
    reference: str = DEFAULT_REFERENCE,
) -> np.ndarray:
    """Warp a photo (RGB) into the canonical canvas image of ``reference`` (or an explicit ``size``)."""
    w, h = size if size is not None else canonical_size(reference)
    src, _ = find_canvas_corners(image, corners)
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst)
    if not np.all(np.isfinite(matrix)):
        raise RectifyError("degenerate homography")
    return cv2.warpPerspective(
        image, matrix, (w, h), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255)
    )


def compose_fixture_view(
    canvas: np.ndarray, marker_px: int = 80, gap_px: int = 0, margin_px: int = 24
) -> np.ndarray:
    """A canonical canvas inside a white sheet with the four markers at its corners (marker path tests).

    The marker contract is that each marker's inner corner *touches* the canvas
    corner, so ``gap_px`` defaults to 0: a gap here is a gap the rectification
    cannot know about, and it shrinks the whole drawing by that much.
    """
    h, w = canvas.shape[:2]
    pad = (
        marker_px + gap_px + margin_px
    )  # margin keeps markers off the image border, which the detector rejects
    sheet = np.full((h + 2 * pad, w + 2 * pad, 3), 255, dtype=np.uint8)
    sheet[pad : pad + h, pad : pad + w] = canvas
    d = _dictionary()
    spots = {
        0: (pad - gap_px - marker_px, pad - gap_px - marker_px),
        1: (pad - gap_px - marker_px, pad + w + gap_px),
        2: (pad + h + gap_px, pad + w + gap_px),
        3: (pad + h + gap_px, pad - gap_px - marker_px),
    }
    for mid, (r, c) in spots.items():
        m = cv2.aruco.generateImageMarker(d, mid, marker_px)
        sheet[r : r + marker_px, c : c + marker_px] = np.stack([m] * 3, axis=-1)
    return sheet


def compose_sheet_view(canvas: np.ndarray, margin_px: int = 90, desk_gray: int = 96) -> np.ndarray:
    """A canonical canvas as a white sheet lying on a darker desk, no markers (sheet path tests)."""
    h, w = canvas.shape[:2]
    desk = np.full((h + 2 * margin_px, w + 2 * margin_px, 3), desk_gray, dtype=np.uint8)
    desk[margin_px : margin_px + h, margin_px : margin_px + w] = canvas
    return desk

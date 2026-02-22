"""
OpenCastor Depth Processing — OAK-D depth overlay and obstacle detection.

Provides two public helpers:

    get_depth_overlay(rgb_frame, depth_frame) -> bytes
        Returns a JPEG with a JET-colormap depth map alpha-blended over the
        RGB frame.  Useful for visualising 3-D scene structure in the
        dashboard.

    get_obstacle_zones(depth_frame, width=640) -> dict
        Divides the depth frame into left / centre / right thirds and
        returns the minimum (nearest) depth reading in centimetres for
        each sector, plus an overall ``nearest_cm`` value.

        Returns ``{"available": False}`` when ``depth_frame`` is ``None``
        or when numpy / OpenCV are not installed.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("OpenCastor.Depth")

# ---------------------------------------------------------------------------
# Optional heavy dependencies — fail gracefully if not installed
# ---------------------------------------------------------------------------
try:
    import cv2
    import numpy as np
    _HAS_CV2 = True
except ImportError:  # pragma: no cover
    _HAS_CV2 = False

# Millimetres-to-centimetres scale factor used by DepthAI OAK-D cameras.
# Raw depth values from getFrame() are in millimetres (uint16).
_MM_TO_CM: float = 0.1

# Alpha blend weight: how opaque the depth overlay is on top of the RGB.
_OVERLAY_ALPHA: float = 0.45


def get_depth_overlay(
    rgb_frame: Optional[bytes],
    depth_frame,
) -> bytes:
    """Return a JPEG of *rgb_frame* with a JET-colormap depth overlay.

    The depth colourmap is alpha-blended onto the RGB image so spatial
    depth cues are visible alongside the normal camera view.

    Args:
        rgb_frame:   Raw JPEG bytes (or BGR numpy array) for the RGB image.
                     When ``None`` or empty a blank 640×480 black image is used.
        depth_frame: Depth data.  Accepted forms:
                     - 2-D ``numpy.ndarray`` of uint16 millimetre values.
                     - Any object with a ``.getFrame()`` method (DepthAI frame).
                     - ``None`` — returns plain RGB JPEG with no overlay.

    Returns:
        JPEG-encoded bytes of the composited image.

    Raises:
        RuntimeError: If OpenCV / NumPy are not available.
    """
    if not _HAS_CV2:
        raise RuntimeError(
            "OpenCV and NumPy are required for depth overlay. "
            "Install with: pip install opencv-python-headless numpy"
        )

    # ── Decode RGB ─────────────────────────────────────────────────────────
    if rgb_frame is not None and len(rgb_frame) > 0:
        if isinstance(rgb_frame, (bytes, bytearray)):
            arr = np.frombuffer(rgb_frame, dtype=np.uint8)
            bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if bgr is None:
                bgr = np.zeros((480, 640, 3), dtype=np.uint8)
        elif isinstance(rgb_frame, np.ndarray):
            bgr = rgb_frame.copy()
            if bgr.ndim == 2:
                bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
        else:
            bgr = np.zeros((480, 640, 3), dtype=np.uint8)
    else:
        bgr = np.zeros((480, 640, 3), dtype=np.uint8)

    h, w = bgr.shape[:2]

    # ── No depth data — return plain RGB ───────────────────────────────────
    if depth_frame is None:
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return buf.tobytes() if ok else b""

    # ── Extract depth array ────────────────────────────────────────────────
    if hasattr(depth_frame, "getFrame"):
        depth_arr = depth_frame.getFrame()
    elif isinstance(depth_frame, np.ndarray):
        depth_arr = depth_frame
    else:
        # Unsupported type — return plain RGB
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return buf.tobytes() if ok else b""

    # Normalise depth to 8-bit range for colormap
    depth_arr = depth_arr.astype(np.float32)
    valid_mask = depth_arr > 0
    if valid_mask.any():
        d_min = float(depth_arr[valid_mask].min())
        d_max = float(depth_arr[valid_mask].max())
    else:
        d_min, d_max = 0.0, 1.0
    d_range = max(d_max - d_min, 1.0)

    norm = np.zeros_like(depth_arr, dtype=np.uint8)
    norm[valid_mask] = np.clip(
        255.0 * (depth_arr[valid_mask] - d_min) / d_range, 0, 255
    ).astype(np.uint8)
    # Invert so *near* = warm colour (red/yellow), *far* = cool (blue)
    norm[valid_mask] = 255 - norm[valid_mask]

    colored = cv2.applyColorMap(norm, cv2.COLORMAP_JET)

    # Resize depth colourmap to match RGB if sizes differ
    if colored.shape[:2] != (h, w):
        colored = cv2.resize(colored, (w, h), interpolation=cv2.INTER_NEAREST)

    # Zero out invalid pixels so they blend to black (transparent-ish)
    mask_resized = cv2.resize(
        valid_mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST
    )
    colored[mask_resized == 0] = 0

    # Alpha blend
    overlay = cv2.addWeighted(bgr, 1.0 - _OVERLAY_ALPHA, colored, _OVERLAY_ALPHA, 0)

    ok, buf = cv2.imencode(".jpg", overlay, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return buf.tobytes() if ok else b""


def get_obstacle_zones(
    depth_frame,
    width: int = 640,
) -> dict:
    """Divide the depth frame into left / centre / right thirds and report min depth.

    The frame is split horizontally into three equal sectors.  For each
    sector the minimum depth value (i.e. the closest obstacle) is
    returned in centimetres.

    Args:
        depth_frame: 2-D ``numpy.ndarray`` (uint16, millimetres) **or**
                     an object with ``.getFrame()`` returning such an array,
                     **or** ``None``.
        width:       Expected frame width used for sector boundaries.
                     Ignored when *depth_frame* already carries its own
                     shape; present for forward-compatibility.

    Returns:
        On success::

            {
                "available": True,
                "left_cm":   float,   # min depth in left third
                "center_cm": float,   # min depth in centre third
                "right_cm":  float,   # min depth in right third
                "nearest_cm": float,  # overall minimum
            }

        When depth is unavailable::

            {"available": False}
    """
    if depth_frame is None:
        return {"available": False}

    if not _HAS_CV2:
        logger.warning("NumPy not available — cannot compute obstacle zones")
        return {"available": False}

    # ── Extract numpy array ────────────────────────────────────────────────
    if hasattr(depth_frame, "getFrame"):
        try:
            arr = np.array(depth_frame.getFrame(), dtype=np.float32)
        except Exception as exc:
            logger.warning("depth_frame.getFrame() failed: %s", exc)
            return {"available": False}
    elif isinstance(depth_frame, np.ndarray):
        arr = depth_frame.astype(np.float32)
    else:
        logger.warning("Unsupported depth_frame type: %s", type(depth_frame))
        return {"available": False}

    if arr.ndim != 2 or arr.size == 0:
        return {"available": False}

    rows, cols = arr.shape
    third = cols // 3

    def _min_cm(sector: np.ndarray) -> float:
        """Return minimum positive depth in centimetres."""
        valid = sector[sector > 0]
        if valid.size == 0:
            return 0.0
        return round(float(valid.min()) * _MM_TO_CM, 1)

    left_cm   = _min_cm(arr[:, :third])
    center_cm = _min_cm(arr[:, third: 2 * third])
    right_cm  = _min_cm(arr[:, 2 * third:])

    # nearest_cm is the overall minimum across all three sectors
    candidates = [v for v in (left_cm, center_cm, right_cm) if v > 0]
    nearest_cm = round(min(candidates), 1) if candidates else 0.0

    return {
        "available": True,
        "left_cm":   left_cm,
        "center_cm": center_cm,
        "right_cm":  right_cm,
        "nearest_cm": nearest_cm,
    }

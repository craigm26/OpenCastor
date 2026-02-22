"""
castor/camera.py — Multi-camera manager (issue #112).

``CameraManager`` manages N camera captures in the same process, providing
composite frames (tiled grid, primary-only, most-recent, depth overlay) for
the robot brain.

RCAN config example::

    cameras:
    - id: front
      type: usb
      index: 0
      resolution: [640, 480]
      framerate: 30
      role: primary
    - id: rear
      type: usb
      index: 1
      resolution: [320, 240]
      role: secondary
    - id: depth
      type: oak_d
      resolution: [640, 480]
      role: depth

Backwards-compatible: if no ``cameras:`` block is present, the legacy
``CAMERA_INDEX`` env var (default 0) is used as a single primary camera.

Usage::

    mgr = CameraManager(config.get("cameras", []))
    mgr.open()
    frame_bytes = mgr.get_composite()     # → JPEG bytes for brain
    mgr.close()
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Camera")

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    logger.debug("cv2 not available — CameraManager in stub mode")

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


# ---------------------------------------------------------------------------
# CompositeMode
# ---------------------------------------------------------------------------

COMPOSITE_MODES = ("primary_only", "tile", "most_recent", "depth_overlay")


class _CameraSource:
    """Thin wrapper around a single OpenCV capture with thread-safe last-frame cache."""

    def __init__(self, cam_id: str, index: int, width: int, height: int) -> None:
        self.cam_id = cam_id
        self.index = index
        self.width = width
        self.height = height
        self._cap: Optional[Any] = None
        self._last_frame: Optional[Any] = None
        self._lock = threading.Lock()

    def open(self) -> bool:
        if not HAS_CV2:
            return False
        self._cap = cv2.VideoCapture(self.index)
        if self._cap.isOpened():
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            logger.info("Camera '%s' opened (index=%d, %dx%d)", self.cam_id, self.index, self.width, self.height)
            return True
        logger.warning("Camera '%s' (index=%d) could not be opened", self.cam_id, self.index)
        return False

    def read(self) -> Optional[Any]:
        """Return the latest BGR frame, or None on failure."""
        if not self._cap or not self._cap.isOpened():
            return self._last_frame
        ok, frame = self._cap.read()
        if ok:
            with self._lock:
                self._last_frame = frame
        return self._last_frame

    def close(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None

    @property
    def last_frame(self) -> Optional[Any]:
        with self._lock:
            return self._last_frame


# ---------------------------------------------------------------------------
# CameraManager
# ---------------------------------------------------------------------------


class CameraManager:
    """Manages N cameras and produces composite frames for the AI brain.

    Args:
        camera_configs: List of camera config dicts from RCAN ``cameras:`` block.
        composite_mode: How to combine multiple frames:
            ``primary_only`` — only the primary camera (default)
            ``tile``         — 2×N grid of all cameras resized to equal size
            ``most_recent``  — whichever camera last updated
            ``depth_overlay`` — primary + depth overlay (OAK-D)
        jpeg_quality: JPEG encoding quality 1-100 (default: 85)
    """

    def __init__(
        self,
        camera_configs: Optional[List[Dict[str, Any]]] = None,
        composite_mode: str = "primary_only",
        jpeg_quality: int = 85,
    ) -> None:
        self._mode = composite_mode if composite_mode in COMPOSITE_MODES else "primary_only"
        self._jpeg_quality = jpeg_quality
        self._sources: Dict[str, _CameraSource] = {}
        self._primary_id: Optional[str] = None
        self._is_open = False

        configs = camera_configs or []

        if not configs:
            # Backwards-compatible: single camera from CAMERA_INDEX
            idx = int(os.environ.get("CAMERA_INDEX", "0"))
            configs = [{"id": "primary", "type": "usb", "index": idx, "role": "primary"}]

        for cam_cfg in configs:
            cam_id = cam_cfg.get("id", f"cam{len(self._sources)}")
            index = int(cam_cfg.get("index", len(self._sources)))
            res = cam_cfg.get("resolution", [640, 480])
            width, height = (res[0], res[1]) if isinstance(res, (list, tuple)) else (640, 480)
            role = cam_cfg.get("role", "secondary")

            src = _CameraSource(cam_id, index, width, height)
            self._sources[cam_id] = src

            if role == "primary" and self._primary_id is None:
                self._primary_id = cam_id

        if self._primary_id is None and self._sources:
            self._primary_id = next(iter(self._sources))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open all camera captures."""
        for src in self._sources.values():
            src.open()
        self._is_open = True

    def close(self) -> None:
        """Release all camera captures."""
        for src in self._sources.values():
            src.close()
        self._is_open = False

    # ------------------------------------------------------------------
    # Frame access
    # ------------------------------------------------------------------

    def get_frame(self, camera_id: Optional[str] = None) -> Optional[bytes]:
        """Return a JPEG frame from a specific camera (or primary if None)."""
        cam_id = camera_id or self._primary_id
        if not cam_id or cam_id not in self._sources:
            logger.warning("Camera '%s' not found", cam_id)
            return None
        frame = self._sources[cam_id].read()
        if frame is None:
            return None
        return self._encode_jpeg(frame)

    def get_composite(self) -> Optional[bytes]:
        """Return a composite JPEG frame based on the configured mode."""
        if self._mode == "primary_only" or len(self._sources) <= 1:
            return self.get_frame(self._primary_id)
        elif self._mode == "tile":
            return self._composite_tile()
        elif self._mode == "most_recent":
            return self._composite_most_recent()
        else:
            return self.get_frame(self._primary_id)

    def available_cameras(self) -> List[str]:
        """Return list of configured camera IDs."""
        return list(self._sources.keys())

    @property
    def primary_id(self) -> Optional[str]:
        return self._primary_id

    # ------------------------------------------------------------------
    # Composite strategies
    # ------------------------------------------------------------------

    def _composite_tile(self) -> Optional[bytes]:
        """Tile all camera frames into a single image."""
        if not HAS_NUMPY or not HAS_CV2:
            return self.get_frame(self._primary_id)

        frames = []
        for src in self._sources.values():
            f = src.read()
            if f is not None:
                f = cv2.resize(f, (320, 240))
                frames.append(f)

        if not frames:
            return None

        # Pad to even count for 2-column grid
        if len(frames) % 2:
            frames.append(np.zeros_like(frames[0]))

        rows = []
        for i in range(0, len(frames), 2):
            rows.append(np.hstack(frames[i : i + 2]))
        composite = np.vstack(rows)
        return self._encode_jpeg(composite)

    def _composite_most_recent(self) -> Optional[bytes]:
        """Return the most recently updated frame across all cameras."""
        for src in reversed(list(self._sources.values())):
            f = src.read()
            if f is not None:
                return self._encode_jpeg(f)
        return None

    # ------------------------------------------------------------------
    # JPEG encoding
    # ------------------------------------------------------------------

    def _encode_jpeg(self, frame: Any) -> Optional[bytes]:
        """Encode a BGR numpy array to JPEG bytes."""
        if not HAS_CV2 or not HAS_NUMPY:
            return None
        ok, buf = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality]
        )
        return bytes(buf) if ok else None

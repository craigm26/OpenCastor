"""Time-lapse video generator for OpenCastor (issue #139).

Compiles saved recordings from castor/recorder.py into condensed
time-lapse MP4 files using OpenCV.  Falls back to a mock mode when
OpenCV is not available.

Usage::

    from castor.timelapse import get_generator

    gen = get_generator()
    result = gen.generate(recording_ids=["rec1", "rec2"], speed_factor=4.0)
    listing = gen.list()

REST API:
    POST /api/timelapse/generate  — {recording_ids, speed_factor}
    GET  /api/timelapse/list      — list generated timelapses

Install OpenCV for full functionality:
    pip install opencv-python-headless
"""

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.Timelapse")

try:
    import cv2 as _cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

_TIMELAPSE_DIR = Path(
    os.getenv("CASTOR_TIMELAPSE_DIR", str(Path.home() / ".castor" / "timelapses"))
)
_DEFAULT_SPEED = 4.0
_DEFAULT_FPS = 24


class TimelapseGenerator:
    """Compile recording frames into time-lapse MP4 files.

    Args:
        output_dir: Directory to store generated timelapses.
    """

    def __init__(self, output_dir: Path = _TIMELAPSE_DIR):
        self._dir = output_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "index.json"
        self._index: dict[str, dict[str, Any]] = self._load_index()

    # ------------------------------------------------------------------
    # Index persistence
    # ------------------------------------------------------------------

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if self._index_path.exists():
            try:
                with open(self._index_path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_index(self) -> None:
        try:
            with open(self._index_path, "w") as f:
                json.dump(self._index, f, indent=2)
        except Exception as exc:
            logger.warning("Timelapse index save error: %s", exc)

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate(
        self,
        recording_ids: Optional[list[str]] = None,
        speed_factor: float = _DEFAULT_SPEED,
        output_fps: int = _DEFAULT_FPS,
    ) -> dict[str, Any]:
        """Generate a time-lapse from the specified recordings.

        Args:
            recording_ids: IDs of recordings to include.  If None/empty,
                           uses all available recordings.
            speed_factor: Playback speed multiplier (e.g. 4.0 = 4× faster).
            output_fps: Output video frame rate.

        Returns:
            Dict with timelapse_id, path, duration_s, frame_count, mode.
        """
        from castor.recorder import get_recorder

        recorder = get_recorder()
        all_recordings = recorder.list_recordings()

        # Filter by requested IDs
        if recording_ids:
            selected = [r for r in all_recordings if r["id"] in recording_ids]
        else:
            selected = all_recordings

        if not selected:
            raise ValueError("No matching recordings found")

        timelapse_id = uuid.uuid4().hex[:12]
        output_path = self._dir / f"timelapse_{timelapse_id}.mp4"

        if not HAS_CV2:
            return self._mock_generate(
                timelapse_id, str(output_path), selected, speed_factor, output_fps
            )

        return self._cv2_generate(
            timelapse_id, str(output_path), selected, speed_factor, output_fps
        )

    def _cv2_generate(
        self,
        timelapse_id: str,
        output_path: str,
        recordings: list[dict[str, Any]],
        speed_factor: float,
        output_fps: int,
    ) -> dict[str, Any]:
        """Generate time-lapse using OpenCV."""
        writer = None
        frame_count = 0
        width, height = 640, 480

        for recording in recordings:
            rec_path = recording.get("path")
            if not rec_path or not os.path.exists(rec_path):
                logger.warning("Timelapse: recording file not found: %s", rec_path)
                continue

            cap = _cv2.VideoCapture(rec_path)
            orig_fps = cap.get(_cv2.CAP_PROP_FPS) or 30.0
            frame_interval = max(1, int(orig_fps * speed_factor / output_fps))

            # Init writer from first frame
            w = int(cap.get(_cv2.CAP_PROP_FRAME_WIDTH)) or width
            h = int(cap.get(_cv2.CAP_PROP_FRAME_HEIGHT)) or height

            if writer is None:
                fourcc = _cv2.VideoWriter_fourcc(*"mp4v")
                writer = _cv2.VideoWriter(output_path, fourcc, output_fps, (w, h))
                width, height = w, h

            idx = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                if idx % frame_interval == 0:
                    if frame.shape[1] != width or frame.shape[0] != height:
                        frame = _cv2.resize(frame, (width, height))
                    writer.write(frame)
                    frame_count += 1
                idx += 1
            cap.release()

        if writer:
            writer.release()

        duration_s = frame_count / max(output_fps, 1)
        meta = {
            "timelapse_id": timelapse_id,
            "path": output_path,
            "recording_ids": [r["id"] for r in recordings],
            "speed_factor": speed_factor,
            "output_fps": output_fps,
            "frame_count": frame_count,
            "duration_s": round(duration_s, 2),
            "created_at": time.time(),
            "mode": "cv2",
        }
        self._index[timelapse_id] = meta
        self._save_index()
        logger.info(
            "Timelapse generated: id=%s frames=%d duration=%.1fs path=%s",
            timelapse_id,
            frame_count,
            duration_s,
            output_path,
        )
        return meta

    def _mock_generate(
        self,
        timelapse_id: str,
        output_path: str,
        recordings: list[dict[str, Any]],
        speed_factor: float,
        output_fps: int,
    ) -> dict[str, Any]:
        """Mock generate (OpenCV not available)."""
        meta = {
            "timelapse_id": timelapse_id,
            "path": output_path,
            "recording_ids": [r["id"] for r in recordings],
            "speed_factor": speed_factor,
            "output_fps": output_fps,
            "frame_count": 0,
            "duration_s": 0.0,
            "created_at": time.time(),
            "mode": "mock",
            "note": "OpenCV not installed; install with: pip install opencv-python-headless",
        }
        self._index[timelapse_id] = meta
        self._save_index()
        logger.info("Timelapse (mock): id=%s no cv2", timelapse_id)
        return meta

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        """Return all generated timelapses, newest first."""
        items = list(self._index.values())
        items.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        return items

    def get(self, timelapse_id: str) -> Optional[dict[str, Any]]:
        """Return metadata for a specific timelapse."""
        return self._index.get(timelapse_id)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_generator: Optional[TimelapseGenerator] = None


def get_generator() -> TimelapseGenerator:
    """Return the process-wide TimelapseGenerator."""
    global _generator
    if _generator is None:
        _generator = TimelapseGenerator()
    return _generator

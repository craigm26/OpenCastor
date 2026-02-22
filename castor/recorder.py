"""Video episode recording for OpenCastor.

Saves MP4 video alongside robot episodes so you can replay the exact camera
stream that informed every LLM decision.  Recordings are stored under
``~/.castor/recordings/`` by default (override with ``CASTOR_RECORDINGS_DIR``).

Usage::

    from castor.recorder import VideoRecorder
    rec = VideoRecorder()
    rec.start("my-session")

    # In the perception-action loop:
    rec.write_frame(jpeg_bytes)

    episode_id = rec.stop()   # returns the recording ID

REST API:
    POST /api/recording/start   — {session_name?}
    POST /api/recording/stop    — {}  → {id, path, frames, duration_s}
    GET  /api/recording/list    — [{id, name, path, size_bytes, duration_s, created_at}]
    GET  /api/recording/{id}    — metadata
    GET  /api/recording/{id}/download — MP4 stream
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Recorder")

try:
    import cv2  # type: ignore

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    logger.info("opencv not available; VideoRecorder will save JPEG frames instead of MP4")

_DEFAULT_DIR = Path(os.getenv("CASTOR_RECORDINGS_DIR", Path.home() / ".castor" / "recordings"))
_DEFAULT_FPS = 5
_DEFAULT_RESOLUTION = (640, 480)

# MP4 fourcc for compatibility
_FOURCC = "mp4v"


class RecordingMeta:
    """Metadata for a single recording."""

    def __init__(self, rec_id: str, name: str, path: Path, fps: int):
        self.id = rec_id
        self.name = name
        self.path = path
        self.fps = fps
        self.frames: int = 0
        self.started_at: float = time.time()
        self.ended_at: Optional[float] = None

    @property
    def duration_s(self) -> float:
        end = self.ended_at or time.time()
        return round(end - self.started_at, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": str(self.path),
            "frames": self.frames,
            "fps": self.fps,
            "duration_s": self.duration_s,
            "size_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "created_at": self.started_at,
            "finished": self.ended_at is not None,
        }


class VideoRecorder:
    """Thread-safe MP4 video recorder.

    Writes JPEG camera frames (from the perception-action loop) to an MP4 file
    using OpenCV.  Degrades gracefully to individual JPEG dumps when OpenCV is
    unavailable.

    Args:
        output_dir: Directory for saved recordings.
        fps: Frames per second for the output video.
        resolution: (width, height) tuple. Frames are resized to fit.
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        fps: int = _DEFAULT_FPS,
        resolution: tuple = _DEFAULT_RESOLUTION,
    ):
        self._dir = Path(output_dir or _DEFAULT_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._fps = fps
        self._resolution = resolution
        self._lock = threading.Lock()

        self._current: Optional[RecordingMeta] = None
        self._writer: Optional[Any] = None  # cv2.VideoWriter

        # Load index from disk
        self._index_path = self._dir / "index.json"
        self._index: Dict[str, Dict[str, Any]] = self._load_index()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, session_name: Optional[str] = None) -> str:
        """Start a new recording.

        Args:
            session_name: Human-readable label (default: timestamp).

        Returns:
            Recording ID string.
        """
        with self._lock:
            if self._current is not None:
                raise RuntimeError(
                    f"Recording already in progress (id={self._current.id}). "
                    "Call stop() first."
                )

            rec_id = f"rec_{int(time.time() * 1000)}"
            name = session_name or f"episode_{rec_id}"
            ext = ".mp4" if HAS_CV2 else ".jpeg_frames"
            path = self._dir / f"{rec_id}{ext}"

            meta = RecordingMeta(rec_id, name, path, self._fps)
            self._current = meta

            if HAS_CV2:
                fourcc = cv2.VideoWriter_fourcc(*_FOURCC)
                self._writer = cv2.VideoWriter(
                    str(path), fourcc, self._fps, self._resolution
                )
                if not self._writer.isOpened():
                    logger.warning("VideoWriter failed to open %s; falling back to frame dump", path)
                    self._writer = None
            else:
                self._writer = None
                # Create a directory to hold individual JPEG frames
                frame_dir = self._dir / rec_id
                frame_dir.mkdir(exist_ok=True)
                meta.path = frame_dir

            logger.info("Recording started: id=%s name=%s path=%s", rec_id, name, path)
            return rec_id

    def write_frame(self, jpeg_bytes: bytes) -> bool:
        """Write a JPEG frame to the current recording.

        Args:
            jpeg_bytes: Raw JPEG image bytes.

        Returns:
            True if frame was written; False if not recording.
        """
        with self._lock:
            if self._current is None:
                return False

            self._current.frames += 1

            if HAS_CV2 and self._writer is not None:
                try:
                    import numpy as np  # type: ignore

                    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is not None:
                        frame = cv2.resize(frame, self._resolution)
                        self._writer.write(frame)
                        return True
                except Exception as exc:
                    logger.debug("Frame write error: %s", exc)
            else:
                # Dump individual JPEG
                frame_path = self._current.path / f"frame_{self._current.frames:06d}.jpg"
                try:
                    frame_path.write_bytes(jpeg_bytes)
                    return True
                except Exception as exc:
                    logger.debug("JPEG dump error: %s", exc)

            return False

    def stop(self) -> Optional[Dict[str, Any]]:
        """Stop the current recording and flush to disk.

        Returns:
            Recording metadata dict, or None if nothing was recording.
        """
        with self._lock:
            if self._current is None:
                return None

            meta = self._current
            meta.ended_at = time.time()

            if self._writer is not None:
                self._writer.release()
                self._writer = None

            self._current = None
            self._index[meta.id] = meta.to_dict()
            self._save_index()

            logger.info(
                "Recording stopped: id=%s frames=%d duration=%.1fs",
                meta.id, meta.frames, meta.duration_s,
            )
            return meta.to_dict()

    @property
    def is_recording(self) -> bool:
        """True if a recording is in progress."""
        return self._current is not None

    @property
    def current_info(self) -> Optional[Dict[str, Any]]:
        """Metadata for the active recording, or None."""
        with self._lock:
            return self._current.to_dict() if self._current else None

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_recordings(self) -> List[Dict[str, Any]]:
        """Return all recordings sorted newest-first."""
        recs = list(self._index.values())
        # Re-stat sizes
        for r in recs:
            p = Path(r["path"])
            if p.exists():
                r["size_bytes"] = p.stat().st_size if p.is_file() else sum(
                    f.stat().st_size for f in p.rglob("*") if f.is_file()
                )
        return sorted(recs, key=lambda r: r.get("created_at", 0), reverse=True)

    def get_recording(self, rec_id: str) -> Optional[Dict[str, Any]]:
        """Return metadata for a specific recording."""
        return self._index.get(rec_id)

    def delete_recording(self, rec_id: str) -> bool:
        """Delete a recording from disk and the index."""
        meta = self._index.pop(rec_id, None)
        if meta is None:
            return False
        p = Path(meta["path"])
        try:
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                import shutil
                shutil.rmtree(p)
        except Exception as exc:
            logger.warning("Could not delete recording %s: %s", rec_id, exc)
        self._save_index()
        return True

    # ------------------------------------------------------------------
    # Index persistence
    # ------------------------------------------------------------------

    def _load_index(self) -> Dict[str, Dict[str, Any]]:
        try:
            if self._index_path.exists():
                return json.loads(self._index_path.read_text())
        except Exception as exc:
            logger.warning("Could not load recording index: %s", exc)
        return {}

    def _save_index(self) -> None:
        try:
            self._index_path.write_text(json.dumps(self._index, indent=2))
        except Exception as exc:
            logger.warning("Could not save recording index: %s", exc)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_recorder: Optional[VideoRecorder] = None


def get_recorder() -> VideoRecorder:
    """Return the process-wide VideoRecorder singleton."""
    global _recorder
    if _recorder is None:
        _recorder = VideoRecorder()
    return _recorder

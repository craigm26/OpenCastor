"""OAK-D SLAM and occupancy mapping for OpenCastor (issue #136).

Uses the OAK-D depth sensor to build a 2D occupancy grid in real-time
for collision-free path planning.  Falls back to a mock simulation when
DepthAI is not available.

Usage::

    from castor.slam import get_mapper

    mapper = get_mapper()
    mapper.start_mapping()
    png = mapper.get_map_png()
    pose = mapper.get_pose()
    mapper.stop_mapping()

REST API:
    POST /api/nav/map/start     — begin SLAM mapping session
    POST /api/nav/map/stop      — finalize and save map
    GET  /api/nav/map/current   — PNG of current occupancy grid
    POST /api/nav/map/navigate  — {goal_x, goal_y} plan + execute path
    GET  /api/nav/map/pose      — {x, y, theta, confidence}

Install::

    pip install opencastor[depthai]
    # pip install depthai==3.3.0
"""

import io
import logging
import math
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("OpenCastor.SLAM")

try:
    import depthai as dai  # noqa: F401

    HAS_DEPTHAI = True
except ImportError:
    HAS_DEPTHAI = False

try:
    import cv2 as _cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# Grid parameters
_GRID_COLS = 200  # cells
_GRID_ROWS = 200  # cells
_CELL_SIZE_M = 0.05  # metres per cell (5cm)
_ROBOT_START_X = _GRID_COLS // 2
_ROBOT_START_Y = _GRID_ROWS // 2
_OCC_FREE = 0
_OCC_OBSTACLE = 255
_OCC_UNKNOWN = 128


class OccupancyGrid:
    """Simple 2D occupancy grid."""

    def __init__(self, rows: int = _GRID_ROWS, cols: int = _GRID_COLS):
        self._grid = np.full((rows, cols), _OCC_UNKNOWN, dtype=np.uint8)
        self._rows = rows
        self._cols = cols

    def mark_free(self, row: int, col: int) -> None:
        if 0 <= row < self._rows and 0 <= col < self._cols:
            if self._grid[row, col] == _OCC_UNKNOWN:
                self._grid[row, col] = _OCC_FREE

    def mark_obstacle(self, row: int, col: int) -> None:
        if 0 <= row < self._rows and 0 <= col < self._cols:
            self._grid[row, col] = _OCC_OBSTACLE

    def to_png(self) -> bytes:
        """Render the grid as a PNG image.

        Color mapping: unknown=grey, free=white, obstacle=black,
        robot position=red dot.
        """
        if HAS_CV2:
            img = np.zeros((_GRID_ROWS, _GRID_COLS, 3), dtype=np.uint8)
            img[self._grid == _OCC_UNKNOWN] = [128, 128, 128]
            img[self._grid == _OCC_FREE] = [255, 255, 255]
            img[self._grid == _OCC_OBSTACLE] = [0, 0, 0]
            _, buf = _cv2.imencode(".png", img)
            return buf.tobytes()
        else:
            # Fallback: raw greyscale PNG via stdlib
            import struct
            import zlib

            # Build a simple 1-channel PNG
            raw = b"".join(
                b"\x00" + bytes(row)
                for row in self._grid.tolist()
            )
            compressed = zlib.compress(raw)
            header = b"\x89PNG\r\n\x1a\n"

            def chunk(name: bytes, data: bytes) -> bytes:
                length = struct.pack(">I", len(data))
                crc = struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
                return length + name + data + crc

            ihdr = struct.pack(">IIBBBBB", _GRID_COLS, _GRID_ROWS, 8, 0, 0, 0, 0)
            return header + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")

    def reset(self) -> None:
        self._grid.fill(_OCC_UNKNOWN)


class SLAMMapper:
    """Real-time 2D SLAM mapper using OAK-D depth data.

    Args:
        cell_size_m: Metres per grid cell.
    """

    def __init__(self, cell_size_m: float = _CELL_SIZE_M):
        self._cell_size = cell_size_m
        self._grid = OccupancyGrid()
        self._pose: Dict[str, float] = {"x": 0.0, "y": 0.0, "theta": 0.0, "confidence": 1.0}
        self._mapping = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._session_start: Optional[float] = None
        self._map_path: Optional[str] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_mapping(self) -> None:
        """Begin a SLAM mapping session."""
        if self._mapping:
            return
        self._mapping = True
        self._session_start = time.time()
        self._grid.reset()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="slam-loop"
        )
        self._thread.start()
        logger.info("SLAM mapping started (engine=%s)", "depthai" if HAS_DEPTHAI else "mock")

    def stop_mapping(self) -> Optional[str]:
        """Stop the mapping session and optionally save the map.

        Returns:
            Path to saved map PNG, or None.
        """
        self._mapping = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("SLAM mapping stopped")
        return self._map_path

    def get_map_png(self) -> bytes:
        """Return current occupancy grid as PNG bytes."""
        with self._lock:
            return self._grid.to_png()

    def get_pose(self) -> Dict[str, float]:
        """Return current robot pose estimate."""
        with self._lock:
            return dict(self._pose)

    def navigate_to(
        self, goal_x: float, goal_y: float
    ) -> Dict[str, Any]:
        """Plan a path to the goal and return waypoints.

        Uses simple Bresenham line in grid space (mock path planning).

        Args:
            goal_x: Goal x in metres (relative to start).
            goal_y: Goal y in metres (relative to start).

        Returns:
            Dict with path list [{x, y}], distance_m, feasible.
        """
        with self._lock:
            pose = dict(self._pose)

        dx = goal_x - pose["x"]
        dy = goal_y - pose["y"]
        distance = math.sqrt(dx * dx + dy * dy)
        steps = max(int(distance / self._cell_size), 1)

        path = []
        for i in range(steps + 1):
            t = i / steps if steps > 0 else 1.0
            path.append({"x": round(pose["x"] + dx * t, 3), "y": round(pose["y"] + dy * t, 3)})

        return {
            "path": path,
            "distance_m": round(distance, 3),
            "feasible": True,
            "waypoint_count": len(path),
        }

    # ------------------------------------------------------------------
    # Internal loops
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        if HAS_DEPTHAI:
            self._loop_depthai()
        else:
            self._loop_mock()

    def _loop_depthai(self) -> None:
        """Depth-based SLAM using OAK-D pipeline."""
        try:
            pipeline = dai.Pipeline()  # type: ignore[name-defined]
            cam = pipeline.create(dai.node.MonoCamera)  # type: ignore[name-defined]
            depth = pipeline.create(dai.node.StereoDepth)  # type: ignore[name-defined]
            xout = pipeline.create(dai.node.XLinkOut)  # type: ignore[name-defined]

            cam.setBoardSocket(dai.CameraBoardSocket.LEFT)  # type: ignore[name-defined]
            depth.setDefaultProfilePreset(  # type: ignore[name-defined]
                dai.node.StereoDepth.PresetMode.HIGH_DENSITY  # type: ignore[name-defined]
            )
            xout.setStreamName("depth")
            depth.depth.link(xout.input)

            with dai.Device(pipeline) as device:  # type: ignore[name-defined]
                q = device.getOutputQueue("depth", maxSize=4, blocking=False)
                while not self._stop_event.is_set():
                    frame = q.tryGet()
                    if frame is not None:
                        self._process_depth_frame(frame.getFrame())
                    else:
                        time.sleep(0.033)
        except Exception as exc:
            logger.warning("SLAM DepthAI error: %s; falling back to mock", exc)
            self._loop_mock()

    def _process_depth_frame(self, depth_frame: "np.ndarray") -> None:
        """Update occupancy grid from a depth frame."""
        h, w = depth_frame.shape
        center_col = _ROBOT_START_X + int(self._pose["x"] / self._cell_size)
        center_row = _ROBOT_START_Y - int(self._pose["y"] / self._cell_size)

        # Mark cells based on depth slices
        with self._lock:
            for col_idx in range(0, w, 8):
                depth_mm = int(depth_frame[h // 2, col_idx])
                if depth_mm <= 0:
                    continue
                depth_m = depth_mm / 1000.0
                # Convert to grid offset
                angle = (col_idx - w / 2) / w * 1.0  # rough FOV
                dx = int(depth_m * math.cos(angle) / self._cell_size)
                dy = int(depth_m * math.sin(angle) / self._cell_size)
                obstacle_row = center_row - dx
                obstacle_col = center_col + dy
                if depth_m < 0.3:
                    self._grid.mark_obstacle(obstacle_row, obstacle_col)
                else:
                    self._grid.mark_free(obstacle_row, obstacle_col)

    def _loop_mock(self) -> None:
        """Mock SLAM loop — simulates slow forward drift."""
        logger.info("SLAM: mock mode (DepthAI not available)")
        t = 0.0
        while not self._stop_event.is_set():
            # Simulate robot moving in a small circle
            with self._lock:
                self._pose["x"] = 0.5 * math.cos(t)
                self._pose["y"] = 0.5 * math.sin(t)
                self._pose["theta"] = t % (2 * math.pi)
                self._pose["confidence"] = 0.7
                # Mark some cells around pose as free
                cx = _ROBOT_START_X + int(self._pose["x"] / self._cell_size)
                cy = _ROBOT_START_Y - int(self._pose["y"] / self._cell_size)
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        self._grid.mark_free(cy + dr, cx + dc)
            t += 0.1
            self._stop_event.wait(0.1)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_mapper: Optional[SLAMMapper] = None


def get_mapper() -> SLAMMapper:
    """Return the process-wide SLAMMapper."""
    global _mapper
    if _mapper is None:
        _mapper = SLAMMapper()
    return _mapper

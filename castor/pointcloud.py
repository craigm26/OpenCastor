"""
3D Point Cloud module for OAK-4 Pro / OAK-D depth data.

Converts DepthAI stereo depth frames into XYZ point clouds and exports
them as PLY, JSON, or NumPy arrays for visualization and navigation.

API:
  GET /api/depth/pointcloud          — JSON {points: [[x,y,z], ...]}
  GET /api/depth/pointcloud.ply      — binary PLY download
  GET /api/depth/pointcloud/stats    — {point_count, bounds, density}

Env:
  CASTOR_POINTCLOUD_MAX_POINTS — max points returned (default 10000)
  CASTOR_POINTCLOUD_VOXEL_SIZE — voxel downsampling size in mm (default 10)

Install:  pip install depthai   (already in requirements for OAK cameras)
          pip install numpy      (already a dep)
          pip install open3d     (optional — for advanced processing)
"""

import logging
import os
import struct
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger("OpenCastor.PointCloud")

_MAX_POINTS = int(os.getenv("CASTOR_POINTCLOUD_MAX_POINTS", "10000"))
_VOXEL_SIZE = int(os.getenv("CASTOR_POINTCLOUD_VOXEL_SIZE", "10"))  # mm

try:
    import depthai as dai

    HAS_DEPTHAI = True
except ImportError:
    HAS_DEPTHAI = False

try:
    import open3d as o3d

    HAS_OPEN3D = True
except ImportError:
    HAS_OPEN3D = False

_singleton: Optional["PointCloudCapture"] = None
_lock = threading.Lock()


class PointCloudCapture:
    """Captures and processes point clouds from OAK-D/OAK-4 Pro depth data."""

    # OAK-D intrinsics defaults (override with actual calibration data)
    _FX = 882.3
    _FY = 882.3
    _CX = 640.0
    _CY = 360.0

    def __init__(self):
        self._pipeline = None
        self._device = None
        self._depth_q = None
        self._mode = "mock"
        self._last_points: Optional[np.ndarray] = None
        self._lock = threading.Lock()

        if HAS_DEPTHAI:
            try:
                self._pipeline = self._build_pipeline()
                self._device = dai.Device(self._pipeline)
                self._depth_q = self._device.getOutputQueue(
                    name="depth", maxSize=4, blocking=False
                )
                self._mode = "hardware"
                # Try to read real intrinsics
                try:
                    calib = self._device.readCalibration()
                    w, h = 1280, 720
                    intr = calib.getCameraIntrinsics(dai.CameraBoardSocket.LEFT, w, h)
                    self._FX = intr[0][0]
                    self._FY = intr[1][1]
                    self._CX = intr[0][2]
                    self._CY = intr[1][2]
                except Exception:
                    pass
                logger.info("PointCloudCapture ready (hardware, fx=%.1f)", self._FX)
            except Exception as exc:
                logger.warning("PointCloud hardware init failed: %s — mock mode", exc)

    # ── Internal ──────────────────────────────────────────────────────

    @staticmethod
    def _build_pipeline():
        pipeline = dai.Pipeline()
        mono_left = pipeline.create(dai.node.MonoCamera)
        mono_right = pipeline.create(dai.node.MonoCamera)
        stereo = pipeline.create(dai.node.StereoDepth)
        xout = pipeline.create(dai.node.XLinkOut)
        xout.setStreamName("depth")

        mono_left.setResolution(dai.MonoCameraProperties.SensorResolution.THE_720_P)
        mono_left.setBoardSocket(dai.CameraBoardSocket.LEFT)
        mono_right.setResolution(dai.MonoCameraProperties.SensorResolution.THE_720_P)
        mono_right.setBoardSocket(dai.CameraBoardSocket.RIGHT)

        stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.HIGH_DENSITY)
        stereo.setDepthAlign(dai.CameraBoardSocket.LEFT)
        stereo.setOutputSize(1280, 720)

        mono_left.out.link(stereo.left)
        mono_right.out.link(stereo.right)
        stereo.depth.link(xout.input)
        return pipeline

    def _depth_to_xyz(self, depth_mm: np.ndarray) -> np.ndarray:
        """Convert a depth image (mm) to Nx3 XYZ array (metres)."""
        h, w = depth_mm.shape
        u, v = np.meshgrid(np.arange(w), np.arange(h))
        z = depth_mm.astype(np.float32) / 1000.0  # mm → m
        valid = (z > 0.1) & (z < 10.0)
        x = ((u - self._CX) * z / self._FX)[valid]
        y = ((v - self._CY) * z / self._FY)[valid]
        z = z[valid]
        return np.column_stack([x, y, z])

    def _voxel_downsample(self, pts: np.ndarray, voxel_m: float) -> np.ndarray:
        """Simple voxel grid downsampling (no open3d required)."""
        if pts.shape[0] == 0:
            return pts
        idxs = (pts / voxel_m).astype(int)
        _, unique = np.unique(idxs, axis=0, return_index=True)
        return pts[unique]

    # ── Public API ────────────────────────────────────────────────────

    def capture(self) -> np.ndarray:
        """Return latest Nx3 point cloud in metres. Uses mock data if no hardware."""
        if self._mode == "hardware" and self._depth_q is not None:
            try:
                frame = self._depth_q.tryGet()
                if frame is not None:
                    depth_mm = frame.getFrame()
                    pts = self._depth_to_xyz(depth_mm)
                    voxel_m = _VOXEL_SIZE / 1000.0
                    pts = self._voxel_downsample(pts, voxel_m)
                    if pts.shape[0] > _MAX_POINTS:
                        idx = np.random.choice(pts.shape[0], _MAX_POINTS, replace=False)
                        pts = pts[idx]
                    with self._lock:
                        self._last_points = pts
                    return pts
                # Return last cached if no new frame
                with self._lock:
                    if self._last_points is not None:
                        return self._last_points
            except Exception as exc:
                logger.error("PointCloud capture error: %s", exc)

        # Mock: generate a small hemisphere point cloud
        n = min(500, _MAX_POINTS)
        theta = np.random.uniform(0, np.pi / 2, n)
        phi = np.random.uniform(0, 2 * np.pi, n)
        r = np.random.uniform(0.5, 3.0, n)
        x = r * np.sin(theta) * np.cos(phi)
        y = r * np.sin(theta) * np.sin(phi)
        z = r * np.cos(theta)
        return np.column_stack([x, y, z])

    def to_json_dict(self) -> dict:
        """Return point cloud as JSON-serializable dict."""
        pts = self.capture()
        points = pts.tolist()
        bounds = {
            "x": [float(pts[:, 0].min()), float(pts[:, 0].max())],
            "y": [float(pts[:, 1].min()), float(pts[:, 1].max())],
            "z": [float(pts[:, 2].min()), float(pts[:, 2].max())],
        } if len(pts) > 0 else {}
        return {
            "point_count": len(points),
            "points": points,
            "bounds": bounds,
            "mode": self._mode,
        }

    def to_ply_bytes(self) -> bytes:
        """Return point cloud as binary PLY file bytes."""
        pts = self.capture()
        n = len(pts)
        header = (
            "ply\n"
            "format binary_little_endian 1.0\n"
            f"element vertex {n}\n"
            "property float x\n"
            "property float y\n"
            "property float z\n"
            "end_header\n"
        ).encode()
        body = pts.astype(np.float32).tobytes()
        return header + body

    def stats(self) -> dict:
        pts = self.capture()
        if len(pts) == 0:
            return {"point_count": 0, "mode": self._mode}
        return {
            "point_count": len(pts),
            "mode": self._mode,
            "bounds_m": {
                "x": [round(float(pts[:, 0].min()), 3), round(float(pts[:, 0].max()), 3)],
                "y": [round(float(pts[:, 1].min()), 3), round(float(pts[:, 1].max()), 3)],
                "z": [round(float(pts[:, 2].min()), 3), round(float(pts[:, 2].max()), 3)],
            },
            "density_pts_per_m3": round(
                len(pts) / max(
                    1e-6,
                    (pts[:, 0].max() - pts[:, 0].min())
                    * (pts[:, 1].max() - pts[:, 1].min())
                    * (pts[:, 2].max() - pts[:, 2].min()),
                ),
                1,
            ),
        }


def get_capture() -> PointCloudCapture:
    """Return the process-wide PointCloudCapture singleton."""
    global _singleton
    with _lock:
        if _singleton is None:
            _singleton = PointCloudCapture()
    return _singleton

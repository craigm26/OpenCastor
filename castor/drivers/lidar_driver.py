"""
2D LIDAR driver for OpenCastor — RPLidar A1/A2/C1/S2.

Env:
  LIDAR_PORT     — serial port (default /dev/ttyUSB0)
  LIDAR_BAUD     — baud rate (default 115200)
  LIDAR_TIMEOUT  — read timeout seconds (default 3)
  LIDAR_HISTORY_DB — SQLite path for scan history
                     (default ~/.castor/lidar_history.db; set to "none" to disable)

REST API:
  GET /api/lidar/scan      — {scan: [{angle_deg, distance_mm, quality}], latency_ms, mode}
  GET /api/lidar/obstacles — {min_distance_mm, nearest_angle_deg, sectors: {front,left,right,rear}}

Install: pip install rplidar-roboticia
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Lidar")

try:
    from rplidar import RPLidar as _RPLidar

    HAS_RPLIDAR = True
except ImportError:
    HAS_RPLIDAR = False

# ── Singleton ─────────────────────────────────────────────────────────────────

_singleton: Optional[LidarDriver] = None
_singleton_lock = threading.Lock()

# ── Sector definitions ────────────────────────────────────────────────────────
# Each sector: (name, min_angle_deg, max_angle_deg) — all values 0–360.
# Front covers ±45° around 0°/360° (wraps around), others are contiguous ranges.
_SECTORS = {
    "front": (315.0, 45.0),  # wraps around 0°
    "right": (45.0, 135.0),
    "rear": (135.0, 225.0),
    "left": (225.0, 315.0),
}

# ── History constants ──────────────────────────────────────────────────────────
_HISTORY_PRUNE_INTERVAL = 1000  # prune every N inserts
_HISTORY_DEFAULT_WINDOW_S = 86400.0  # 24 hours

_HISTORY_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS scans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              REAL NOT NULL,
    min_distance_mm REAL,
    front_mm        REAL,
    left_mm         REAL,
    right_mm        REAL,
    rear_mm         REAL,
    point_count     INTEGER
);
"""
_HISTORY_CREATE_INDEX = "CREATE INDEX IF NOT EXISTS scans_ts ON scans (ts);"


def _angle_in_sector(angle: float, lo: float, hi: float) -> bool:
    """Return True if *angle* falls within [lo, hi], handling 360° wrap."""
    if lo <= hi:
        return lo <= angle <= hi
    # Wrapping sector (e.g. front: 315–360 ∪ 0–45)
    return angle >= lo or angle <= hi


def _resolve_history_db_path() -> Optional[str]:
    """Resolve the history DB path from env or default.

    Returns None when logging is disabled ("none").
    """
    raw = os.getenv("LIDAR_HISTORY_DB", "").strip()
    if raw == "":
        # Use default path
        raw = os.path.join(os.path.expanduser("~"), ".castor", "lidar_history.db")
    if raw.lower() == "none":
        return None
    return raw


class LidarDriver:
    """2D LIDAR driver for RPLidar A1/A2/C1/S2 series.

    Performs a single full rotation scan on demand. Falls back to a mock
    sine-wave wall pattern when the rplidar library is unavailable or the
    device cannot be opened.
    """

    def __init__(
        self,
        port: Optional[str] = None,
        baud: Optional[int] = None,
        timeout: Optional[float] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        cfg = config or {}
        self._port: str = port or cfg.get("port") or os.getenv("LIDAR_PORT", "/dev/ttyUSB0")
        self._baud: int = int(baud or cfg.get("baud") or os.getenv("LIDAR_BAUD", "115200"))
        self._timeout: float = float(
            timeout or cfg.get("timeout") or os.getenv("LIDAR_TIMEOUT", "3")
        )
        self._mode = "mock"
        self._lidar: Optional[object] = None  # _RPLidar instance
        self._lock = threading.Lock()
        self._scan_count: int = 0
        self._last_scan: list = []
        self._prev_scan_points: list = []  # ── Issue #358: moving_objects() history
        # Issue #376: accumulated SLAM occupancy map
        self._slam_map: Optional[List[List[float]]] = None
        self._slam_map_size_m: float = 5.0
        self._slam_map_resolution_m: float = 0.05

        # ── History DB ────────────────────────────────────────────────────────
        self._history_db_path: Optional[str] = _resolve_history_db_path()
        self._history_con: Optional[Any] = None  # sqlite3.Connection
        self._history_insert_count: int = 0

        if not HAS_RPLIDAR:
            logger.info(
                "LidarDriver: rplidar not installed — mock mode "
                "(install: pip install rplidar-roboticia)"
            )
            return

        try:
            self._lidar = _RPLidar(self._port, baudrate=self._baud, timeout=self._timeout)
            info = self._lidar.get_info()
            logger.info(
                "RPLidar connected on %s: model=%s firmware=%s hardware=%s",
                self._port,
                info.get("model", "?"),
                info.get("firmware", "?"),
                info.get("hardware", "?"),
            )
            self._mode = "hardware"
        except Exception as exc:
            logger.warning("LidarDriver: could not open %s: %s — mock mode", self._port, exc)
            self._lidar = None

    # ── Mock data generation ──────────────────────────────────────────────────

    def _mock_scan(self) -> list:
        """Generate a fake 360-point scan: sine-wave wall + obstacle at 90°."""
        points = []
        for i in range(360):
            angle = float(i)
            # Base wall 2000 mm away with gentle undulation
            dist = 2000.0 + math.sin(math.radians(angle * 2)) * 150.0
            # Simulated obstacle at ~90° (right side), range 400 mm, ±15° wide
            if 75 <= angle <= 105:
                obstacle_dist = 400.0 + math.sin(math.radians((angle - 90) * 12)) * 30.0
                dist = min(dist, obstacle_dist)
            points.append(
                {
                    "angle_deg": round(angle, 1),
                    "distance_mm": round(dist, 1),
                    "quality": 15,
                }
            )
        return points

    # ── History DB helpers ────────────────────────────────────────────────────

    def _ensure_history_db(self) -> bool:
        """Open and initialise the history SQLite DB if not already done.

        Returns True when the connection is ready, False on any error.
        """
        if self._history_con is not None:
            return True
        if self._history_db_path is None:
            return False
        try:
            db_dir = os.path.dirname(self._history_db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            con = sqlite3.connect(self._history_db_path, check_same_thread=False)
            con.execute(_HISTORY_CREATE_TABLE)
            con.execute(_HISTORY_CREATE_INDEX)
            con.commit()
            self._history_con = con
            logger.debug("LidarDriver: history DB opened at %s", self._history_db_path)
            return True
        except Exception as exc:
            logger.warning("LidarDriver: could not open history DB: %s", exc)
            return False

    def _log_scan(self, obstacles: dict, point_count: int) -> None:
        """Append one scan summary row to the history DB.

        Silently swallows all exceptions so that a DB failure never
        propagates into ``scan()``.
        """
        if self._history_db_path is None:
            return
        try:
            if not self._ensure_history_db():
                return
            ts = time.time()
            sectors = obstacles.get("sectors", {})
            self._history_con.execute(  # type: ignore[union-attr]
                "INSERT INTO scans "
                "(ts, min_distance_mm, front_mm, left_mm, right_mm, rear_mm, point_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    obstacles.get("min_distance_mm"),
                    sectors.get("front"),
                    sectors.get("left"),
                    sectors.get("right"),
                    sectors.get("rear"),
                    point_count,
                ),
            )
            self._history_con.commit()  # type: ignore[union-attr]
            self._history_insert_count += 1

            # Auto-prune every _HISTORY_PRUNE_INTERVAL inserts
            if self._history_insert_count % _HISTORY_PRUNE_INTERVAL == 0:
                cutoff = ts - _HISTORY_DEFAULT_WINDOW_S
                self._history_con.execute(  # type: ignore[union-attr]
                    "DELETE FROM scans WHERE ts < ?", (cutoff,)
                )
                self._history_con.commit()  # type: ignore[union-attr]
                logger.debug(
                    "LidarDriver: pruned history rows older than %.0f s",
                    _HISTORY_DEFAULT_WINDOW_S,
                )
        except Exception as exc:
            logger.warning("LidarDriver: history log error: %s", exc)

    def get_scan_history(self, window_s: float = 60.0, limit: int = 500) -> List[Dict[str, Any]]:
        """Return recent scan summaries from the history DB.

        Args:
            window_s: Time window in seconds to look back (default 60 s).
            limit:    Maximum number of rows to return (default 500).

        Returns:
            List of dicts with keys
            ``{ts, min_distance_mm, front_mm, left_mm, right_mm, rear_mm, point_count}``,
            ordered newest-first. Returns an empty list when history is disabled or on error.
        """
        if self._history_db_path is None:
            return []
        try:
            if not self._ensure_history_db():
                return []
            cutoff = time.time() - window_s
            cur = self._history_con.execute(  # type: ignore[union-attr]
                "SELECT ts, min_distance_mm, front_mm, left_mm, right_mm, rear_mm, point_count "
                "FROM scans WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
                (cutoff, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "ts": row[0],
                    "min_distance_mm": row[1],
                    "front_mm": row[2],
                    "left_mm": row[3],
                    "right_mm": row[4],
                    "rear_mm": row[5],
                    "point_count": row[6],
                }
                for row in rows
            ]
        except Exception as exc:
            logger.warning("LidarDriver: get_scan_history error: %s", exc)
            return []

    # ── Core scan ─────────────────────────────────────────────────────────────

    def scan(self) -> list:
        """Perform one full rotation scan.

        Returns a list of dicts: {angle_deg, distance_mm, quality}.
        Caches the last scan so obstacles() can use it without re-scanning.
        After completing the scan, logs a summary row to the history DB.
        """
        if self._mode != "hardware" or self._lidar is None:
            result = self._mock_scan()
            self._prev_scan_points = list(self._last_scan)
            self._last_scan = result
            obs = self.obstacles()
            self._log_scan(obs, len(result))
            return result

        with self._lock:
            try:
                points = []
                # iter_scans() yields complete 360° sweeps; we take the first one
                for _scan_no, scan_data in enumerate(self._lidar.iter_scans()):
                    for quality, angle, distance in scan_data:
                        if distance > 0:
                            points.append(
                                {
                                    "angle_deg": round(float(angle), 1),
                                    "distance_mm": round(float(distance), 1),
                                    "quality": int(quality),
                                }
                            )
                    break  # one sweep is enough
                self._scan_count += 1
                self._prev_scan_points = list(self._last_scan)
                self._last_scan = points
                obs = self.obstacles()
                self._log_scan(obs, len(points))
                return points
            except Exception as exc:
                logger.error("LidarDriver scan error: %s", exc)
                return self._last_scan  # return stale data rather than empty

    # ── Obstacle analysis ─────────────────────────────────────────────────────

    def obstacles(self) -> dict:
        """Analyse the most recent scan and return per-sector minimum distances.

        Returns:
            {
              min_distance_mm: float,
              nearest_angle_deg: float,
              sectors: {front, right, rear, left}  — min mm per sector
            }

        If no scan has been taken yet, triggers one automatically.
        """
        data = self._last_scan if self._last_scan else self.scan()

        if not data:
            empty = {s: None for s in _SECTORS}
            return {"min_distance_mm": None, "nearest_angle_deg": None, "sectors": empty}

        sector_min: dict = {name: float("inf") for name in _SECTORS}
        global_min = float("inf")
        global_angle = 0.0

        for point in data:
            angle = point["angle_deg"]
            dist = point["distance_mm"]
            if dist <= 0:
                continue
            if dist < global_min:
                global_min = dist
                global_angle = angle
            for name, (lo, hi) in _SECTORS.items():
                if _angle_in_sector(angle, lo, hi):
                    if dist < sector_min[name]:
                        sector_min[name] = dist

        # Replace inf with None for clean JSON
        sector_result = {
            k: (round(v, 1) if v != float("inf") else None) for k, v in sector_min.items()
        }

        return {
            "min_distance_mm": round(global_min, 1) if global_min != float("inf") else None,
            "nearest_angle_deg": round(global_angle, 1),
            "sectors": sector_result,
        }

    # ── Zone map ──────────────────────────────────────────────────────────────

    def zone_map(self, resolution_m: float = 0.05, size_m: float = 5.0) -> dict:
        """Convert the latest scan to a 2D occupancy grid.

        Args:
            resolution_m: Grid cell size in metres (default 0.05 m = 5 cm).
            size_m:       Physical extent of the grid in metres (default 5 m).
                          The robot sits at the centre of the grid.

        Returns:
            {
                "grid": List[List[int]],   # NxN, 0=free, 100=occupied, -1=unknown
                "width": int,
                "height": int,
                "resolution_m": float,
                "origin": {"x": int, "y": int},  # robot position in grid coords
                "available": bool,
            }

        Never raises.
        """
        _unavailable_result: dict = {
            "grid": [],
            "width": 0,
            "height": 0,
            "resolution_m": resolution_m,
            "origin": {"x": 0, "y": 0},
            "available": False,
        }

        try:
            n = max(10, int(size_m / resolution_m))
            n = min(n, 200)  # cap to limit memory usage

            # Fill grid with -1 (unknown)
            grid = [[-1] * n for _ in range(n)]

            origin_x = n // 2
            origin_y = n // 2

            # Sector → Cartesian direction mapping (unit vector per sector)
            _sector_directions = {
                "front": (0.0, 1.0),
                "rear": (0.0, -1.0),
                "left": (-1.0, 0.0),
                "right": (1.0, 0.0),
            }

            try:
                scan_data = self.scan()
                available = bool(scan_data)
            except Exception as exc:
                logger.warning("LidarDriver.zone_map: scan failed: %s", exc)
                _unavailable_result["grid"] = [[-1] * n for _ in range(n)]
                _unavailable_result["width"] = n
                _unavailable_result["height"] = n
                _unavailable_result["origin"] = {"x": origin_x, "y": origin_y}
                return _unavailable_result

            if not available:
                return {
                    "grid": grid,
                    "width": n,
                    "height": n,
                    "resolution_m": resolution_m,
                    "origin": {"x": origin_x, "y": origin_y},
                    "available": False,
                }

            for point in scan_data:
                distance_mm = point.get("distance_mm")
                angle_deg = point.get("angle_deg", 0.0)

                if distance_mm is None or distance_mm <= 0:
                    continue

                distance_m = distance_mm / 1000.0

                # Convert polar (angle_deg, distance_m) to Cartesian (x_m, y_m)
                # Convention: 0° = front (+Y), 90° = right (+X), 180° = rear (-Y), 270° = left (-X)
                angle_rad = math.radians(angle_deg)
                x_m = distance_m * math.sin(angle_rad)
                y_m = distance_m * math.cos(angle_rad)

                # Endpoint cell (occupied)
                gx_end = origin_x + int(x_m / resolution_m)
                gy_end = origin_y + int(y_m / resolution_m)

                # Mark ray cells as free using Bresenham-style stepping
                steps = max(1, int(distance_m / resolution_m))
                for step in range(steps):
                    frac = step / max(steps, 1)
                    gx = origin_x + int((x_m * frac) / resolution_m)
                    gy = origin_y + int((y_m * frac) / resolution_m)
                    gx = max(0, min(n - 1, gx))
                    gy = max(0, min(n - 1, gy))
                    if grid[gy][gx] != 100:
                        grid[gy][gx] = 0  # free

                # Mark endpoint as occupied (clamp to grid)
                gx_end = max(0, min(n - 1, gx_end))
                gy_end = max(0, min(n - 1, gy_end))
                grid[gy_end][gx_end] = 100

            return {
                "grid": grid,
                "width": n,
                "height": n,
                "resolution_m": resolution_m,
                "origin": {"x": origin_x, "y": origin_y},
                "available": True,
            }

        except Exception as exc:
            logger.warning("LidarDriver.zone_map error: %s", exc)
            return _unavailable_result

    # ── Obstacle velocity ─────────────────────────────────────────────────────

    def obstacle_velocity(self, window_s: float = 2.0) -> dict:
        """Estimate how fast obstacles are approaching or receding per sector.

        Fetches the scan history for *window_s* seconds and fits a linear
        regression (least-squares slope) of distance_mm vs. time for each
        sector.  A positive slope means the obstacle is receding; a negative
        slope means it is approaching.

        Args:
            window_s: Time window in seconds to look back (default 2 s).

        Returns:
            {
                "front_mm_per_s": float,
                "left_mm_per_s":  float,
                "right_mm_per_s": float,
                "rear_mm_per_s":  float,
                "window_s":       float,
                "samples":        int,
            }

        Returns all-zero velocities when there are fewer than 2 samples or
        on any error.  Never raises.
        """
        _zero = {
            "front_mm_per_s": 0.0,
            "left_mm_per_s": 0.0,
            "right_mm_per_s": 0.0,
            "rear_mm_per_s": 0.0,
            "window_s": window_s,
            "samples": 0,
        }
        try:
            if self._history_db_path is None:
                return _zero
            if not self._ensure_history_db():
                return _zero

            cutoff = time.time() - window_s
            cur = self._history_con.execute(  # type: ignore[union-attr]
                "SELECT ts, front_mm, left_mm, right_mm, rear_mm "
                "FROM scans WHERE ts >= ? ORDER BY ts ASC",
                (cutoff,),
            )
            rows = cur.fetchall()
            n = len(rows)
            if n < 2:
                _zero["samples"] = n
                return _zero

            def _slope(xs: list, ys: list) -> float:
                """Least-squares slope for paired x/y lists, skipping None y."""
                pairs = [(x, y) for x, y in zip(xs, ys, strict=False) if y is not None]
                m = len(pairs)
                if m < 2:
                    return 0.0
                sx = sum(p[0] for p in pairs)
                sy = sum(p[1] for p in pairs)
                sxx = sum(p[0] * p[0] for p in pairs)
                sxy = sum(p[0] * p[1] for p in pairs)
                denom = m * sxx - sx * sx
                if denom == 0.0:
                    return 0.0
                return (m * sxy - sx * sy) / denom

            # Normalise timestamps to avoid floating-point catastrophic cancellation
            # when computing m*Σx² − (Σx)² with large Unix epoch values.
            t0 = rows[0][0]
            ts_list = [row[0] - t0 for row in rows]
            front_slope = _slope(ts_list, [row[1] for row in rows])
            left_slope = _slope(ts_list, [row[2] for row in rows])
            right_slope = _slope(ts_list, [row[3] for row in rows])
            rear_slope = _slope(ts_list, [row[4] for row in rows])

            return {
                "front_mm_per_s": round(front_slope, 4),
                "left_mm_per_s": round(left_slope, 4),
                "right_mm_per_s": round(right_slope, 4),
                "rear_mm_per_s": round(rear_slope, 4),
                "window_s": window_s,
                "samples": n,
            }
        except Exception as exc:
            logger.warning("LidarDriver.obstacle_velocity error: %s", exc)
            return _zero

    # ── Moving objects (#358) ─────────────────────────────────────────────────

    def moving_objects(self, min_delta_m: float = 0.05) -> List[Dict[str, Any]]:
        """Detect objects that moved between the last two scans.

        Compares per-angle distances between ``_prev_scan_points`` and
        ``_last_scan``.  Requires at least two scans in history
        (``get_scan_history(window_s=5, limit=2)``) as a guard.

        Args:
            min_delta_m: Minimum absolute distance change in metres to report
                         (default 0.05 m).

        Returns:
            List of ``{"angle_deg": int, "delta_m": float,
            "direction": "approaching"|"receding"}`` dicts, one per angle
            bucket that exceeded *min_delta_m*.  Returns ``[]`` when fewer
            than 2 scans are available or on any error.  Never raises.
        """
        try:
            history = self.get_scan_history(window_s=5, limit=2)
            if len(history) < 2:
                return []
            prev = self._prev_scan_points
            curr = self._last_scan
            if not prev or not curr:
                return []

            def _bucket(points: list) -> Dict[int, float]:
                buckets: Dict[int, float] = {}
                for pt in points:
                    try:
                        angle = pt.get("angle_deg")
                        dist = pt.get("distance_mm")
                        if angle is None or dist is None or dist <= 0:
                            continue
                        deg = int(round(float(angle))) % 360
                        if deg not in buckets or dist < buckets[deg]:
                            buckets[deg] = float(dist)
                    except Exception:
                        continue
                return buckets

            prev_b = _bucket(prev)
            curr_b = _bucket(curr)
            results: List[Dict[str, Any]] = []
            for deg in range(360):
                if deg not in prev_b or deg not in curr_b:
                    continue
                delta_mm = curr_b[deg] - prev_b[deg]
                delta_m = delta_mm / 1000.0
                if abs(delta_m) < min_delta_m:
                    continue
                results.append(
                    {
                        "angle_deg": deg,
                        "delta_m": round(delta_m, 4),
                        "direction": "approaching" if delta_m < 0.0 else "receding",
                    }
                )
            return results
        except Exception as exc:
            logger.warning("LidarDriver.moving_objects error: %s", exc)
            return []

    # ── Issue #366 — per-zone velocity ────────────────────────────────────────

    def zone_velocity(self, zone: str = "front", window_s: float = 2.0) -> Dict[str, Any]:
        """Estimate the approaching velocity (m/s) in a named angular zone.

        Fetches recent scan history and computes the linear regression slope of
        median zone distance vs time.  A negative slope means objects are
        approaching; positive means receding.

        Zones (using signed angles from -180..180):
            ``front``  : -45 ≤ angle < 45
            ``left``   : 45 ≤ angle < 135
            ``rear``   : ±135 ≤ |angle| ≤ 180
            ``right``  : -135 ≤ angle < -45

        Args:
            zone:     One of ``"front"``, ``"left"``, ``"rear"``, ``"right"``.
            window_s: History window in seconds (default 2.0).

        Returns:
            ``{zone, velocity_m_s, samples, window_s, direction}``
            where *direction* is ``"approaching"`` / ``"receding"`` / ``"stationary"``.
        """
        _zone_bounds = {
            "front": (-45.0, 45.0),
            "left": (45.0, 135.0),
            "rear": (135.0, 180.0),  # special: |angle| ≥ 135
            "right": (-135.0, -45.0),
        }
        _result_base: Dict[str, Any] = {
            "zone": zone,
            "velocity_m_s": 0.0,
            "samples": 0,
            "window_s": window_s,
            "direction": "stationary",
        }
        if zone not in _zone_bounds:
            logger.warning("LidarDriver.zone_velocity: unknown zone %r", zone)
            return _result_base

        try:
            history = self.get_scan_history(window_s=window_s, limit=200)
            if len(history) < 2:
                return _result_base

            import statistics as _stats

            times: list = []
            medians: list = []

            for entry in history:
                ts = entry.get("timestamp", 0.0)
                points = entry.get("points", [])
                zone_dists: list = []
                for pt in points:
                    try:
                        raw_angle = float(pt.get("angle", 0.0))
                        # normalise to -180..180
                        signed = ((raw_angle + 180.0) % 360.0) - 180.0
                        dist_m = float(pt.get("distance", 0.0)) / 1000.0
                        if dist_m <= 0.0:
                            continue
                        lo, hi = _zone_bounds[zone]
                        if zone == "rear":
                            if abs(signed) >= 135.0:
                                zone_dists.append(dist_m)
                        else:
                            if lo <= signed < hi:
                                zone_dists.append(dist_m)
                    except (TypeError, ValueError):
                        continue
                if zone_dists:
                    times.append(float(ts))
                    medians.append(_stats.median(zone_dists))

            n = len(times)
            if n < 2:
                return _result_base

            # Simple linear regression: slope = cov(t, d) / var(t)
            t_mean = sum(times) / n
            d_mean = sum(medians) / n
            num = sum((times[i] - t_mean) * (medians[i] - d_mean) for i in range(n))
            den = sum((times[i] - t_mean) ** 2 for i in range(n))
            slope = num / den if den != 0.0 else 0.0

            if slope < -0.005:
                direction = "approaching"
            elif slope > 0.005:
                direction = "receding"
            else:
                direction = "stationary"

            return {
                "zone": zone,
                "velocity_m_s": round(slope, 4),
                "samples": n,
                "window_s": window_s,
                "direction": direction,
            }
        except Exception as exc:
            logger.warning("LidarDriver.zone_velocity error: %s", exc)
            return _result_base

    # ── SLAM hint ─────────────────────────────────────────────────────────────

    def slam_hint(self) -> dict:
        """Return a lightweight SLAM wall-detection hint for each angular sector.

        Groups scan points into three sectors using **signed** angles
        (normalised from the 0-360 scan output into -180..180):

        * ``front``:  -30° to +30°
        * ``left``:   +31° to +150°
        * ``right``: -150° to  -31°

        For each sector with ≥ 3 valid points the method computes:

        * ``distance_m`` — median distance of points in that sector (converted
          from mm to metres).
        * ``angle_deg``  — mean signed angle of points in that sector.
        * ``confidence`` — ``min(1.0, n_points / 10.0)``.

        Sectors with fewer than 3 valid points are omitted from ``walls``.

        Returns:
            {
                "available": bool,
                "walls": [
                    {
                        "sector":      "front" | "left" | "right",
                        "distance_m":  float,
                        "angle_deg":   float,
                        "confidence":  float,
                    },
                    ...
                ],
            }

        Never raises.
        """
        try:
            try:
                points = self.scan()
            except Exception as exc:
                logger.warning("LidarDriver.slam_hint: scan failed: %s", exc)
                return {"available": False, "walls": []}

            if not points:
                return {"available": False, "walls": []}

            # Bucket lists: angle (signed, deg) and distance (mm) per sector
            sector_buckets: dict = {"front": [], "left": [], "right": []}

            for point in points:
                raw_angle = point.get("angle_deg", 0.0)
                dist_mm = point.get("distance_mm", 0.0)
                if dist_mm is None or dist_mm <= 0:
                    continue

                # Normalise 0-360 → -180..180
                signed = raw_angle if raw_angle <= 180.0 else raw_angle - 360.0

                if -30.0 <= signed <= 30.0:
                    sector_buckets["front"].append((signed, dist_mm))
                elif 31.0 <= signed <= 150.0:
                    sector_buckets["left"].append((signed, dist_mm))
                elif -150.0 <= signed <= -31.0:
                    sector_buckets["right"].append((signed, dist_mm))

            walls: list = []
            for sector_name, bucket in sector_buckets.items():
                if len(bucket) < 3:
                    continue
                angles = [p[0] for p in bucket]
                dists_mm = [p[1] for p in bucket]

                # Median distance (mm → m)
                sorted_dists = sorted(dists_mm)
                mid = len(sorted_dists) // 2
                if len(sorted_dists) % 2 == 1:
                    median_mm = sorted_dists[mid]
                else:
                    median_mm = (sorted_dists[mid - 1] + sorted_dists[mid]) / 2.0
                distance_m = median_mm / 1000.0

                mean_angle = sum(angles) / len(angles)
                confidence = min(1.0, len(bucket) / 10.0)

                walls.append(
                    {
                        "sector": sector_name,
                        "distance_m": round(distance_m, 4),
                        "angle_deg": round(mean_angle, 4),
                        "confidence": round(confidence, 4),
                    }
                )

            return {"available": True, "walls": walls}

        except Exception as exc:
            logger.warning("LidarDriver.slam_hint error: %s", exc)
            return {"available": False, "walls": []}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        """Connect to the LIDAR device (no-op in mock mode)."""
        if self._mode != "hardware" or self._lidar is None:
            logger.debug("LidarDriver.start(): mock mode — skipping")
            return
        with self._lock:
            try:
                self._lidar.start_motor()
                logger.info("LidarDriver: motor started on %s", self._port)
            except Exception as exc:
                logger.warning("LidarDriver.start() failed: %s", exc)

    def stop(self):
        """Stop the motor and disconnect from the device."""
        if self._lidar is None:
            return
        with self._lock:
            try:
                self._lidar.stop()
                self._lidar.stop_motor()
                self._lidar.disconnect()
                logger.info("LidarDriver: disconnected from %s", self._port)
            except Exception as exc:
                logger.warning("LidarDriver.stop() error: %s", exc)

    def close(self) -> None:
        """Stop the motor, disconnect, and close the history DB."""
        self.stop()
        if self._history_con is not None:
            try:
                self._history_con.close()
            except Exception:
                pass
            self._history_con = None
        logger.info("LidarDriver: closed (port=%s)", self._port)

    def health_check(self) -> dict:
        """Return driver health information."""
        return {
            "ok": True,
            "mode": self._mode,
            "port": self._port,
            "baud": self._baud,
            "scan_count": self._scan_count,
            "error": None,
        }

    # ── Issue #344: Map persistence ───────────────────────────────────────────

    _MAP_CREATE_DDL = """
    CREATE TABLE IF NOT EXISTS lidar_maps (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        ts        REAL NOT NULL,
        label     TEXT,
        metadata  TEXT,
        map_blob  BLOB NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_lidar_maps_ts ON lidar_maps (ts DESC);
    """

    def _open_map_db(self, path: str) -> sqlite3.Connection:
        """Open (and initialise) the map SQLite database at *path*."""
        con = sqlite3.connect(path, check_same_thread=False)
        con.executescript(self._MAP_CREATE_DDL)
        con.commit()
        return con

    def occupancy_grid(
        self,
        size_m: float = 5.0,
        resolution_m: float = 0.05,
    ) -> Dict[str, Any]:
        """Build a 2-D occupancy grid from the current scan history.

        In mock mode returns an empty grid of the requested dimensions.
        In hardware mode populates cells from the most recent scan.

        Args:
            size_m:       Physical extent of the square grid in metres.
            resolution_m: Cell size in metres.

        Returns:
            Dict with keys ``grid`` (list[list[float]]), ``origin``
            ([x_m, y_m] of the grid's lower-left corner), ``size_m``,
            ``resolution_m``, ``cells`` (grid dimension), and ``mode``.
        """
        import math

        cells = max(1, int(size_m / resolution_m))
        grid: List[List[float]] = [[0.0] * cells for _ in range(cells)]
        origin = [-size_m / 2.0, -size_m / 2.0]

        if self._mode == "hardware":
            with self._lock:
                scan = list(self._last_scan)
            for point in scan:
                angle_deg = float(point.get("angle", 0.0))
                dist_m = float(point.get("distance", 0.0)) / 1000.0  # mm → m
                if dist_m <= 0.0:
                    continue
                rad = math.radians(angle_deg)
                x = dist_m * math.cos(rad) - origin[0]
                y = dist_m * math.sin(rad) - origin[1]
                col = int(x / resolution_m)
                row = int(y / resolution_m)
                if 0 <= row < cells and 0 <= col < cells:
                    grid[row][col] = 1.0

        return {
            "grid": grid,
            "origin": origin,
            "size_m": size_m,
            "resolution_m": resolution_m,
            "cells": cells,
            "mode": self._mode,
        }

    def slam_update(
        self,
        reset: bool = False,
        size_m: float = 5.0,
        resolution_m: float = 0.05,
    ) -> Dict[str, Any]:
        """Incrementally accumulate the current scan into a persistent occupancy map.

        Merges new scan points into ``_slam_map`` using logical-OR: a cell is
        occupied once set and stays occupied across subsequent calls.

        In mock mode returns zeros with ``cells_updated=0``.

        Args:
            reset:        Clear the accumulated map before merging (default False).
            size_m:       Physical extent of the square map in metres.
            resolution_m: Cell size in metres.

        Returns:
            Dict with keys ``cells_updated`` (int), ``total_occupied`` (int),
            ``cells`` (grid dimension), ``mode`` (str), ``reset`` (bool).
        """
        import math

        cells = max(1, int(size_m / resolution_m))
        origin_x = -size_m / 2.0
        origin_y = -size_m / 2.0

        with self._lock:
            # Reinitialise map on reset or size change
            if (
                reset
                or self._slam_map is None
                or len(self._slam_map) != cells
                or len(self._slam_map[0]) != cells
            ):
                self._slam_map = [[0.0] * cells for _ in range(cells)]
                self._slam_map_size_m = size_m
                self._slam_map_resolution_m = resolution_m

            cells_updated = 0

            if self._mode == "hardware":
                scan = list(self._last_scan)
                for point in scan:
                    angle_deg = float(point.get("angle", 0.0))
                    dist_m = float(point.get("distance", 0.0)) / 1000.0
                    if dist_m <= 0.0:
                        continue
                    rad = math.radians(angle_deg)
                    x = dist_m * math.cos(rad) - origin_x
                    y = dist_m * math.sin(rad) - origin_y
                    col = int(x / resolution_m)
                    row = int(y / resolution_m)
                    if 0 <= row < cells and 0 <= col < cells:
                        if self._slam_map[row][col] == 0.0:
                            cells_updated += 1
                        self._slam_map[row][col] = 1.0

            total_occupied = sum(
                1 for row in self._slam_map for v in row if v > 0.0
            )

        return {
            "cells_updated": cells_updated,
            "total_occupied": total_occupied,
            "cells": cells,
            "mode": self._mode,
            "reset": reset,
        }

    def save_map(
        self,
        path: str,
        label: Optional[str] = None,
        size_m: float = 5.0,
        resolution_m: float = 0.05,
    ) -> dict:
        """Capture the current occupancy grid and persist it to a SQLite file.

        The grid is serialised as a JSON-encoded BLOB so that it can be loaded
        without any special binary codec.  Metadata (timestamp, resolution, label)
        is stored in a companion ``metadata`` column.

        Args:
            path:         File path for the SQLite map database.
            label:        Human-readable label for the map snapshot (optional).
            size_m:       Physical extent of the occupancy grid in metres
                          (passed through to :meth:`occupancy_grid`).
            resolution_m: Grid cell size in metres.

        Returns:
            Dict with ``ok``, ``map_id`` (row ID), ``ts``, and ``label``.
        """
        try:
            grid_result = self.occupancy_grid(size_m=size_m, resolution_m=resolution_m)
            ts = time.time()
            metadata = json.dumps(
                {
                    "ts": ts,
                    "label": label,
                    "size_m": size_m,
                    "resolution_m": resolution_m,
                    "rows": len(grid_result.get("grid", [])),
                    "origin": grid_result.get("origin"),
                    "mode": self._mode,
                }
            )
            map_blob = json.dumps(grid_result.get("grid", [])).encode("utf-8")

            con = self._open_map_db(path)
            try:
                cur = con.execute(
                    "INSERT INTO lidar_maps (ts, label, metadata, map_blob) VALUES (?, ?, ?, ?)",
                    (ts, label, metadata, map_blob),
                )
                con.commit()
                map_id = cur.lastrowid
            finally:
                con.close()

            logger.info("LidarDriver.save_map: saved map_id=%d to %s", map_id, path)
            return {"ok": True, "map_id": map_id, "ts": ts, "label": label}
        except Exception as exc:
            logger.error("LidarDriver.save_map error: %s", exc)
            return {"ok": False, "error": str(exc)}

    def load_map(self, path: str, map_id: Optional[int] = None) -> dict:
        """Load an occupancy grid from a SQLite map file.

        Args:
            path:   File path of the SQLite map database written by :meth:`save_map`.
            map_id: Row ID to load.  When ``None``, the most recent map is loaded.

        Returns:
            Dict with ``ok``, ``map_id``, ``ts``, ``label``, ``metadata``, and
            ``grid`` (the 2-D occupancy grid list).  On error returns
            ``{"ok": False, "error": "<message>"}``.
        """
        try:
            con = self._open_map_db(path)
            try:
                if map_id is not None:
                    row = con.execute(
                        "SELECT id, ts, label, metadata, map_blob FROM lidar_maps WHERE id = ?",
                        (map_id,),
                    ).fetchone()
                else:
                    row = con.execute(
                        "SELECT id, ts, label, metadata, map_blob FROM lidar_maps"
                        " ORDER BY ts DESC LIMIT 1"
                    ).fetchone()
            finally:
                con.close()

            if row is None:
                return {"ok": False, "error": "map not found"}

            row_id, ts, label, metadata_json, map_blob = row
            grid = json.loads(map_blob.decode("utf-8") if isinstance(map_blob, bytes) else map_blob)
            metadata = json.loads(metadata_json) if metadata_json else {}

            logger.info("LidarDriver.load_map: loaded map_id=%d from %s", row_id, path)
            return {
                "ok": True,
                "map_id": row_id,
                "ts": ts,
                "label": label,
                "metadata": metadata,
                "grid": grid,
            }
        except Exception as exc:
            logger.error("LidarDriver.load_map error: %s", exc)
            return {"ok": False, "error": str(exc)}


# ── Singleton factory ─────────────────────────────────────────────────────────


def get_lidar(
    port: Optional[str] = None,
    baud: Optional[int] = None,
    timeout: Optional[float] = None,
) -> LidarDriver:
    """Return the process-wide LidarDriver singleton."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = LidarDriver(port=port, baud=baud, timeout=timeout)
    return _singleton

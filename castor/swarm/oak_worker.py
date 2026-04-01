"""OAK-D session analysis worker — runs as an isolated subprocess.

Reads a WorkerTask context from stdin (JSON), analyses the OAK-D session
directory, then writes a structured result to stdout::

    {"summary": "Session ...: 300 frames. ...", "error": ""}

Completely self-contained — no imports from castor to avoid circular
dependencies when launched as a subprocess.

Run via::

    python -m castor.swarm.oak_worker
"""

from __future__ import annotations

import json
import os
import sys


def _compute_depth_stats(session_path: str) -> dict:
    """Count .npy frames and compute depth statistics.

    Checks for an existing ``stats.json`` first (written by the capture
    pipeline) and falls back to sampling the first and last 10 frames.

    Args:
        session_path: Filesystem path to the session directory.

    Returns:
        Dict with keys: ``frame_count``, ``min_mm``, ``median_mm``,
        ``max_mm``, ``anomalies``.  Returns ``{"error": "..."}`` on failure.
    """
    try:
        all_files = os.listdir(session_path)
    except OSError as exc:
        return {"error": f"cannot list session directory: {exc}"}

    npy_files = sorted(f for f in all_files if f.endswith(".npy"))
    frame_count = len(npy_files)

    if frame_count == 0:
        return {
            "frame_count": 0,
            "min_mm": None,
            "median_mm": None,
            "max_mm": None,
            "anomalies": 0,
        }

    # Prefer pre-computed stats when available (avoids loading all frames).
    stats_path = os.path.join(session_path, "stats.json")
    if os.path.exists(stats_path):
        try:
            with open(stats_path) as fh:
                cached = json.load(fh)
            cached["frame_count"] = frame_count
            return cached
        except Exception:
            pass  # fall through to frame sampling

    # Sample first + last 10 frames (deduped for short sessions).
    sample_names = list(dict.fromkeys(npy_files[:10] + npy_files[-10:]))

    try:
        import numpy as np

        depths: list[float] = []
        for fname in sample_names:
            arr = np.load(os.path.join(session_path, fname))
            depths.extend(arr.flatten().tolist())

        depths_arr = np.array(depths, dtype=np.float32)
        anomaly_count = int(np.sum((depths_arr < 200) | (depths_arr > 5000)))

        return {
            "frame_count": frame_count,
            "min_mm": float(np.min(depths_arr)),
            "median_mm": float(np.median(depths_arr)),
            "max_mm": float(np.max(depths_arr)),
            "anomalies": anomaly_count,
        }
    except ImportError:
        return {
            "frame_count": frame_count,
            "min_mm": None,
            "median_mm": None,
            "max_mm": None,
            "anomalies": 0,
            "warning": "numpy not available; depth stats not computed",
        }
    except Exception as exc:
        return {"error": f"depth computation failed: {exc}"}


def main() -> None:
    """Entry point: read stdin JSON, analyse session, write result to stdout."""
    raw = sys.stdin.buffer.read()
    try:
        ctx = json.loads(raw)
    except Exception as exc:
        sys.stdout.write(json.dumps({"summary": "", "error": f"invalid stdin JSON: {exc}"}))
        sys.stdout.flush()
        sys.exit(1)

    session_path = ctx.get("context", {}).get("session_path", "")
    session_id = ctx.get("context", {}).get("session_id", "unknown")

    if not session_path or not os.path.isdir(session_path):
        sys.stdout.write(
            json.dumps({"summary": "", "error": f"session_path not found: {session_path!r}"})
        )
        sys.stdout.flush()
        sys.exit(1)

    stats = _compute_depth_stats(session_path)

    if "error" in stats:
        sys.stdout.write(json.dumps({"summary": "", "error": stats["error"]}))
        sys.stdout.flush()
        sys.exit(1)

    warning_suffix = f" Warning: {stats['warning']}." if stats.get("warning") else ""
    summary = (
        f"Session {session_id}: {stats['frame_count']} frames. "
        f"Depth stats — "
        f"min={stats.get('min_mm')}mm, "
        f"median={stats.get('median_mm')}mm, "
        f"max={stats.get('max_mm')}mm. "
        f"Anomalies detected: {stats.get('anomalies', 0)}."
        f"{warning_suffix}"
    )

    sys.stdout.write(json.dumps({"summary": summary, "error": ""}))
    sys.stdout.flush()


if __name__ == "__main__":
    main()

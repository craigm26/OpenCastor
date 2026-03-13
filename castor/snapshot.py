"""Robot state snapshot manager for OpenCastor (issue #148).

Captures a full-state diagnostic snapshot at configurable intervals
(CPU, RAM, temperature, provider/driver/channel health, last episode,
VFS state, loop metrics).

Usage::

    from castor.snapshot import get_manager

    mgr = get_manager()
    mgr.start(interval_s=60, state_getter=lambda: app_state)
    snap = mgr.latest()

REST API:
    GET  /api/snapshot/latest          — most recent snapshot
    GET  /api/snapshot/history?limit=N — recent snapshots (default 20)
    POST /api/snapshot/take            — take a snapshot immediately
"""

import collections
import logging
import platform
import threading
import time
from typing import Any, Optional
from collections.abc import Callable

logger = logging.getLogger("OpenCastor.Snapshot")

_MAX_HISTORY = 100
_DEFAULT_INTERVAL_S = int(__import__("os").getenv("CASTOR_SNAPSHOT_INTERVAL_S", "60"))


def _system_metrics() -> dict[str, Any]:
    """Collect CPU/RAM/temperature using stdlib where possible."""
    metrics: dict[str, Any] = {"platform": platform.machine()}
    try:
        import psutil  # optional

        metrics["cpu_percent"] = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        metrics["ram_used_mb"] = round(vm.used / 1_048_576, 1)
        metrics["ram_total_mb"] = round(vm.total / 1_048_576, 1)
        metrics["ram_percent"] = vm.percent
    except ImportError:
        pass

    # RPi CPU temperature via sysfs
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as _f:
            metrics["cpu_temp_c"] = round(int(_f.read().strip()) / 1000, 1)
    except Exception:
        pass

    return metrics


def _safe_health(obj: Any) -> Any:
    """Call health_check() on an object; return error dict on failure."""
    if obj is None:
        return None
    try:
        return obj.health_check()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class SnapshotManager:
    """Periodic full-state snapshot capture.

    Args:
        max_history: Maximum snapshots to retain in the ring buffer.
    """

    def __init__(self, max_history: int = _MAX_HISTORY):
        self._history: collections.deque[dict[str, Any]] = collections.deque(maxlen=max_history)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._state_getter: Optional[Callable[[], Any]] = None
        self._interval_s: float = _DEFAULT_INTERVAL_S

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        interval_s: float = _DEFAULT_INTERVAL_S,
        state_getter: Optional[Callable[[], Any]] = None,
    ) -> None:
        """Start the background snapshot thread.

        Args:
            interval_s: Seconds between automatic snapshots.
            state_getter: Callable returning the AppState object.
        """
        if self._thread and self._thread.is_alive():
            return
        self._interval_s = interval_s
        self._state_getter = state_getter
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="snapshot-loop")
        self._thread.start()
        logger.info("Snapshot manager started (interval=%ss)", interval_s)

    def stop(self) -> None:
        """Stop the background snapshot thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Snapshot manager stopped")

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def take(self, state: Any = None) -> dict[str, Any]:
        """Capture a snapshot of the current system state.

        Args:
            state: AppState object (uses state_getter if None).

        Returns:
            Snapshot dict.
        """
        if state is None and self._state_getter:
            state = self._state_getter()

        snap: dict[str, Any] = {
            "timestamp": time.time(),
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "system": _system_metrics(),
        }

        if state is not None:
            snap["provider"] = _safe_health(getattr(state, "brain", None))
            snap["driver"] = _safe_health(getattr(state, "driver", None))
            channels = getattr(state, "channels", {}) or {}
            snap["channels"] = {name: _safe_health(ch) for name, ch in channels.items()}
            last_thought = getattr(state, "last_thought", None)
            snap["last_thought"] = last_thought
            snap["paused"] = getattr(state, "paused", False)

            # VFS proc snapshot
            fs = getattr(state, "fs", None)
            if fs is not None:
                try:
                    snap["vfs"] = {
                        "camera": fs.proc.read_camera(),
                        "speaker": fs.proc.read_speaker(),
                    }
                except Exception:
                    snap["vfs"] = None

            # Most recent episode
            try:
                from castor.memory import EpisodeMemory

                eps = EpisodeMemory().query_recent(limit=1)
                snap["last_episode"] = eps[0] if eps else None
            except Exception:
                snap["last_episode"] = None

        with self._lock:
            self._history.append(snap)

        return snap

    def latest(self) -> Optional[dict[str, Any]]:
        """Return the most recent snapshot, or None."""
        with self._lock:
            return self._history[-1] if self._history else None

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return up to *limit* recent snapshots (newest first)."""
        with self._lock:
            items = list(self._history)
        return list(reversed(items))[:limit]

    def clear(self) -> None:
        with self._lock:
            self._history.clear()

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop_event.wait(self._interval_s):
            try:
                self.take()
            except Exception as exc:
                logger.warning("Snapshot loop error: %s", exc)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manager: Optional[SnapshotManager] = None


def get_manager() -> SnapshotManager:
    """Return the process-wide SnapshotManager."""
    global _manager
    if _manager is None:
        _manager = SnapshotManager()
    return _manager

"""
Continuous sensor monitoring with configurable thresholds and auto e-stop.

Monitors CPU temperature, memory usage, disk usage, CPU load, and
force/torque sensors (placeholder). Runs as a background thread with
configurable interval.

Three consecutive critical readings trigger an automatic e-stop callback.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Safety.Monitor")

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class SensorReading:
    """A single sensor reading with status classification."""

    name: str
    value: float
    unit: str
    status: str = "normal"  # normal | warning | critical | unavailable


@dataclass
class MonitorSnapshot:
    """Complete snapshot of all sensor readings."""

    timestamp: float = 0.0
    cpu_temp_c: Optional[float] = None
    memory_percent: Optional[float] = None
    disk_percent: Optional[float] = None
    cpu_load_1m: Optional[float] = None
    cpu_count: Optional[int] = None
    force_n: Optional[float] = None
    readings: List[SensorReading] = field(default_factory=list)
    overall_status: str = "normal"

    def to_dict(self) -> Dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


@dataclass
class MonitorThresholds:
    """Configurable warning/critical thresholds."""

    cpu_temp_warn: float = 60.0
    cpu_temp_critical: float = 80.0
    memory_warn: float = 80.0
    memory_critical: float = 95.0
    disk_warn: float = 85.0
    disk_critical: float = 95.0
    load_warn_multiplier: float = 2.0
    force_max_n: float = 50.0
    force_warn_n: float = 40.0


# ---------------------------------------------------------------------------
# Sensor readers (pure functions, easy to mock)
# ---------------------------------------------------------------------------


def read_cpu_temp() -> Optional[float]:
    """Read CPU temperature in °C from sysfs or vcgencmd."""
    # Try sysfs first (works on all Linux with thermal zones)
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except (FileNotFoundError, PermissionError, ValueError, OSError):
        pass

    # Try vcgencmd (Raspberry Pi)
    try:
        import subprocess

        result = subprocess.run(
            ["vcgencmd", "measure_temp"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            # Output: "temp=42.5'C"
            temp_str = result.stdout.strip().split("=")[1].split("'")[0]
            return float(temp_str)
    except (FileNotFoundError, IndexError, ValueError, OSError, subprocess.TimeoutExpired):
        pass

    return None


def read_memory_percent() -> Optional[float]:
    """Read memory usage percentage from /proc/meminfo."""
    try:
        with open("/proc/meminfo") as f:
            info: Dict[str, int] = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    info[key] = int(parts[1])
            total = info.get("MemTotal", 0)
            available = info.get("MemAvailable", 0)
            if total > 0:
                return (1.0 - available / total) * 100.0
    except (FileNotFoundError, PermissionError, ValueError, OSError):
        pass
    return None


def read_disk_percent(path: str = "/") -> Optional[float]:
    """Read disk usage percentage."""
    try:
        usage = shutil.disk_usage(path)
        if usage.total > 0:
            return (usage.used / usage.total) * 100.0
    except OSError:
        pass
    return None


def read_cpu_load() -> Optional[float]:
    """Read 1-minute load average."""
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):
        return None


def get_cpu_count() -> int:
    """Get number of CPUs."""
    return os.cpu_count() or 1


# ---------------------------------------------------------------------------
# SensorMonitor
# ---------------------------------------------------------------------------


class SensorMonitor:
    """Continuous background sensor monitoring with auto e-stop.

    Args:
        thresholds: Configurable thresholds. Defaults provided.
        interval: Seconds between readings (default 5).
        consecutive_critical: Number of consecutive critical readings
            before triggering e-stop (default 3).
    """

    def __init__(
        self,
        thresholds: Optional[MonitorThresholds] = None,
        interval: float = 5.0,
        consecutive_critical: int = 3,
    ):
        self.thresholds = thresholds or MonitorThresholds()
        self.interval = interval
        self.consecutive_critical = consecutive_critical

        self._warning_callbacks: List[Callable[[MonitorSnapshot], None]] = []
        self._critical_callbacks: List[Callable[[MonitorSnapshot], None]] = []
        self._estop_callback: Optional[Callable[[], None]] = None

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._consecutive_critical_count = 0
        self._last_snapshot: Optional[MonitorSnapshot] = None
        self._lock = threading.Lock()

        # Force sensor interface (placeholder)
        self._force_reader: Optional[Callable[[], Optional[float]]] = None

        # Sensor reader functions (overridable for testing)
        self._read_cpu_temp = read_cpu_temp
        self._read_memory_percent = read_memory_percent
        self._read_disk_percent = read_disk_percent
        self._read_cpu_load = read_cpu_load
        self._get_cpu_count = get_cpu_count

    def on_warning(self, callback: Callable[[MonitorSnapshot], None]) -> None:
        """Register a callback for warning-level readings."""
        self._warning_callbacks.append(callback)

    def on_critical(self, callback: Callable[[MonitorSnapshot], None]) -> None:
        """Register a callback for critical-level readings."""
        self._critical_callbacks.append(callback)

    def set_estop_callback(self, callback: Callable[[], None]) -> None:
        """Set the emergency stop callback."""
        self._estop_callback = callback

    def set_force_reader(self, reader: Callable[[], Optional[float]]) -> None:
        """Register an external force/torque sensor reader."""
        self._force_reader = reader

    @property
    def last_snapshot(self) -> Optional[MonitorSnapshot]:
        with self._lock:
            return self._last_snapshot

    def read_once(self) -> MonitorSnapshot:
        """Take a single snapshot of all sensors."""
        snap = MonitorSnapshot(timestamp=time.time())
        readings: List[SensorReading] = []
        worst = "normal"

        def classify(
            val: Optional[float], warn: float, crit: float, name: str, unit: str
        ) -> SensorReading:
            if val is None:
                return SensorReading(name=name, value=0.0, unit=unit, status="unavailable")
            status = "normal"
            if val >= crit:
                status = "critical"
            elif val >= warn:
                status = "warning"
            return SensorReading(name=name, value=val, unit=unit, status=status)

        def update_worst(status: str) -> str:
            nonlocal worst
            if status == "critical":
                worst = "critical"
            elif status == "warning" and worst != "critical":
                worst = "warning"
            return worst

        # CPU Temperature
        snap.cpu_temp_c = self._read_cpu_temp()
        r = classify(
            snap.cpu_temp_c,
            self.thresholds.cpu_temp_warn,
            self.thresholds.cpu_temp_critical,
            "cpu_temp",
            "°C",
        )
        readings.append(r)
        update_worst(r.status)

        # Memory
        snap.memory_percent = self._read_memory_percent()
        r = classify(
            snap.memory_percent,
            self.thresholds.memory_warn,
            self.thresholds.memory_critical,
            "memory",
            "%",
        )
        readings.append(r)
        update_worst(r.status)

        # Disk
        snap.disk_percent = self._read_disk_percent()
        r = classify(
            snap.disk_percent, self.thresholds.disk_warn, self.thresholds.disk_critical, "disk", "%"
        )
        readings.append(r)
        update_worst(r.status)

        # CPU Load
        snap.cpu_load_1m = self._read_cpu_load()
        snap.cpu_count = self._get_cpu_count()
        load_warn = snap.cpu_count * self.thresholds.load_warn_multiplier if snap.cpu_count else 2.0
        load_crit = load_warn * 2  # critical at 4x CPU count
        r = classify(snap.cpu_load_1m, load_warn, load_crit, "cpu_load", "")
        readings.append(r)
        update_worst(r.status)

        # Force/Torque
        if self._force_reader:
            try:
                snap.force_n = self._force_reader()
            except Exception:
                snap.force_n = None
        r = classify(
            snap.force_n, self.thresholds.force_warn_n, self.thresholds.force_max_n, "force", "N"
        )
        readings.append(r)
        if r.status != "unavailable":
            update_worst(r.status)

        snap.readings = readings
        snap.overall_status = worst
        return snap

    def _monitor_loop(self) -> None:
        """Background monitoring loop."""
        while not self._stop_event.is_set():
            try:
                snap = self.read_once()
                with self._lock:
                    self._last_snapshot = snap

                if snap.overall_status == "critical":
                    self._consecutive_critical_count += 1
                    for cb in self._critical_callbacks:
                        try:
                            cb(snap)
                        except Exception:
                            logger.exception("Critical callback error")
                    if (
                        self._consecutive_critical_count >= self.consecutive_critical
                        and self._estop_callback
                    ):
                        logger.critical(
                            "Auto e-stop: %d consecutive critical readings",
                            self._consecutive_critical_count,
                        )
                        try:
                            self._estop_callback()
                        except Exception:
                            logger.exception("E-stop callback error")
                        self._consecutive_critical_count = 0
                elif snap.overall_status == "warning":
                    self._consecutive_critical_count = 0
                    for cb in self._warning_callbacks:
                        try:
                            cb(snap)
                        except Exception:
                            logger.exception("Warning callback error")
                else:
                    self._consecutive_critical_count = 0

            except Exception:
                logger.exception("Monitor loop error")

            self._stop_event.wait(self.interval)

    def start(self) -> None:
        """Start background monitoring."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._consecutive_critical_count = 0
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="SensorMonitor"
        )
        self._thread.start()
        logger.info("Sensor monitor started (interval=%.1fs)", self.interval)

    def stop(self, timeout: float = 5.0) -> None:
        """Stop background monitoring."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("Sensor monitor stopped")

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# ---------------------------------------------------------------------------
# ProcFS integration
# ---------------------------------------------------------------------------


def register_with_procfs(proc_fs, monitor: SensorMonitor) -> None:
    """Register sensor snapshot at /proc/sensors in virtual FS."""
    try:
        proc_fs.ns.mkdir("/proc/sensors")
    except Exception:
        pass

    def _update():
        snap = monitor.last_snapshot
        if snap:
            proc_fs.ns.write("/proc/sensors", snap.to_dict())

    monitor.on_warning(lambda _s: _update())
    monitor.on_critical(lambda _s: _update())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cli_monitor(args) -> None:
    """CLI handler for `castor monitor`."""
    monitor = SensorMonitor()

    def _print_snapshot(snap: MonitorSnapshot) -> None:
        print(f"\n{'─' * 50}")
        print(
            f"  Sensor Monitor  [{time.strftime('%H:%M:%S')}]  Status: {snap.overall_status.upper()}"
        )
        print(f"{'─' * 50}")
        for r in snap.readings:
            if r.status == "unavailable":
                val_str = "n/a"
            else:
                val_str = f"{r.value:.1f}{r.unit}"
            indicator = {"normal": "✓", "warning": "⚠", "critical": "✗", "unavailable": "–"}.get(
                r.status, "?"
            )
            print(f"  {indicator} {r.name:<12} {val_str:<12} [{r.status}]")
        print()

    if getattr(args, "watch", False):
        interval = getattr(args, "interval", 5.0)
        monitor.interval = interval
        print(f"Watching sensors every {interval}s (Ctrl-C to stop)...")
        try:
            while True:
                snap = monitor.read_once()
                _print_snapshot(snap)
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        snap = monitor.read_once()
        _print_snapshot(snap)

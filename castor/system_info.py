"""
castor/system_info.py — hardware + model runtime info for /api/status.

Returns two blocks:
  system       — RAM, CPU, disk, temp, NPU detection
  model_runtime — active model, provider, size, context window,
                  TurboQuant status, llmfit result, tokens/sec
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import Any

# psutil is optional — falls back to /proc/meminfo on Linux
try:
    import psutil as _psutil

    _HAS_PSUTIL = True
except ImportError:
    _psutil = None  # type: ignore[assignment]
    _HAS_PSUTIL = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meminfo() -> dict[str, int]:
    """Parse /proc/meminfo into {key: kB} dict."""
    out: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                out[parts[0].rstrip(":")] = int(parts[1])
    except Exception:
        pass
    return out


def _cpu_temp_c() -> float | None:
    """Read CPU temperature (°C). Tries psutil, then /sys thermals."""
    if _HAS_PSUTIL:
        try:
            temps = _psutil.sensors_temperatures()
            for key in ("cpu_thermal", "coretemp", "k10temp", "cpu-thermal"):
                if key in temps and temps[key]:
                    return round(temps[key][0].current, 1)
            # Take first available
            for entries in temps.values():
                if entries:
                    return round(entries[0].current, 1)
        except Exception:
            pass
    # Fallback: /sys/class/thermal
    try:
        zones = sorted(Path("/sys/class/thermal").glob("thermal_zone*"))
        for zone in zones:
            try:
                t = int((zone / "temp").read_text().strip()) / 1000.0
                if 10 < t < 120:
                    return round(t, 1)
            except Exception:
                continue
    except Exception:
        pass
    return None


def _cpu_model() -> str:
    """Return a human-readable CPU model string."""
    try:
        # ARM / Pi — check /proc/cpuinfo
        # Pi 4/5 use "Model", x86 uses "model name", some ARM boards use "Hardware"
        for prefix in ("Model name", "model name", "Hardware", "Model"):
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith(prefix):
                    _, _, val = line.partition(":")
                    val = val.strip()
                    if val:
                        return val
    except Exception:
        pass
    return platform.processor() or platform.machine() or "unknown"


def _detect_npu() -> tuple[str | None, float]:
    """Detect NPU hardware. Returns (name, tops) or (None, 0.0)."""
    if Path("/dev/hailo0").exists():
        return "hailo-8", 26.0
    if Path("/dev/hailo1").exists():
        return "hailo-8l", 13.0
    # Coral Edge TPU
    if Path("/dev/apex_0").exists() or list(Path("/dev").glob("apex_*")):
        return "coral-tpu", 4.0
    # Qualcomm HTP (AI Hub)
    try:
        result = subprocess.run(["qnn-net-run", "--version"], capture_output=True, timeout=1)
        if result.returncode == 0:
            return "qualcomm-htp", 0.0
    except Exception:
        pass
    return None, 0.0


def _detect_gpu() -> str | None:
    """Detect discrete GPU if present."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_system_info() -> dict[str, Any]:
    """
    Return hardware snapshot for /api/status system block.

    All fields are safe to serialize to JSON and push to Firestore.
    Missing sensors return None rather than raising.
    """
    # RAM
    if _HAS_PSUTIL:
        try:
            mem = _psutil.virtual_memory()
            ram_total = round(mem.total / 1e9, 1)
            ram_avail = round(mem.available / 1e9, 1)
            ram_used_pct = round(mem.percent, 1)
        except Exception:
            ram_total = ram_avail = ram_used_pct = None
    else:
        mi = _meminfo()
        ram_total = round(mi.get("MemTotal", 0) * 1024 / 1e9, 1) or None
        ram_avail_kb = mi.get("MemAvailable", mi.get("MemFree", 0))
        ram_avail = round(ram_avail_kb * 1024 / 1e9, 1) or None
        ram_used_pct = (
            round((1 - ram_avail_kb / max(1, mi.get("MemTotal", 1))) * 100, 1) if mi else None
        )

    # Disk
    if _HAS_PSUTIL:
        try:
            disk = _psutil.disk_usage("/")
            disk_total = round(disk.total / 1e9, 1)
            disk_free = round(disk.free / 1e9, 1)
            disk_used_pct = round(disk.percent, 1)
        except Exception:
            disk_total = disk_free = disk_used_pct = None
    else:
        disk_total = disk_free = disk_used_pct = None
        try:
            result = subprocess.run(
                ["df", "-B1", "/"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                parts = result.stdout.splitlines()[1].split()
                disk_total = round(int(parts[1]) / 1e9, 1)
                disk_free = round(int(parts[3]) / 1e9, 1)
                disk_used_pct = round(int(parts[4].rstrip("%")), 1)
        except Exception:
            pass

    npu_name, npu_tops = _detect_npu()

    return {
        "platform": f"{platform.system().lower()}-{platform.machine()}",
        "cpu_model": _cpu_model(),
        "cpu_count": (_psutil.cpu_count(logical=False) if _HAS_PSUTIL else None),
        "ram_total_gb": ram_total,
        "ram_available_gb": ram_avail,
        "ram_used_pct": ram_used_pct,
        "disk_total_gb": disk_total,
        "disk_free_gb": disk_free,
        "disk_used_pct": disk_used_pct,
        "cpu_temp_c": _cpu_temp_c(),
        "npu_detected": npu_name is not None,   # bool — True if NPU found
        "npu_model": npu_name,                    # str | None — e.g. "hailo-8"
        "npu_tops": npu_tops if npu_name else None,
        "gpu_detected": _detect_gpu(),
    }


def get_model_runtime_info(state: Any, config: dict | None = None) -> dict[str, Any]:
    """
    Return active model runtime details for /api/status model_runtime block.

    Queries Ollama manifest for model size when provider=ollama.
    Reads kv_compression config and runs llmfit check if enabled.
    """
    cfg = config or (state.config if state and state.config else {})
    agent_cfg = cfg.get("agent", {})
    local_cfg = cfg.get("local_inference", {})

    provider = agent_cfg.get("provider", "unknown")
    model = agent_cfg.get("model", "unknown")
    kv_compression = local_cfg.get("kv_compression", "none")
    kv_bits = int(local_cfg.get("kv_bits", 3))
    context_window = int(agent_cfg.get("context_window", 8192))

    # Active model from running brain
    if state is not None:
        from castor.api import _get_active_brain  # avoid circular at module level

        brain = _get_active_brain()
        if brain:
            model = getattr(brain, "model_name", model) or model
            provider = getattr(brain, "provider_name", provider) or provider

    # Ollama: query manifest for actual model size + context
    model_size_gb: float | None = None
    tokens_per_sec: float | None = None
    load_time_ms: int | None = None

    if provider == "ollama":
        try:
            import httpx

            resp = httpx.get(
                "http://localhost:11434/api/show",
                json={"name": model},
                timeout=2.0,
            )
            if resp.status_code == 200:
                info = resp.json()
                # model_info.general.size_label or details
                size_bytes = info.get("size") or (
                    info.get("model_info", {}).get("general.parameter_count", 0)
                    * 2  # fp16 estimate
                )
                if size_bytes:
                    model_size_gb = round(size_bytes / 1e9, 2)
                ctx = info.get("model_info", {}).get("llama.context_length") or info.get(
                    "details", {}
                ).get("context_length")
                if ctx:
                    context_window = int(ctx)
        except Exception:
            pass

    # llmfit check
    llmfit_status: str | None = None
    llmfit_headroom_gb: float | None = None
    llmfit_max_ctx: int | None = None

    try:
        from castor.llmfit import check_fit

        result = check_fit(
            model_id=model,
            context_tokens=context_window,
            kv_compression=kv_compression,
            kv_bits=kv_bits,
            provider=provider,
        )
        llmfit_status = "ok" if result.fits else "oom"
        llmfit_headroom_gb = result.headroom_gb
        llmfit_max_ctx = result.max_context_tokens
        if model_size_gb is None:
            model_size_gb = result.weights_gb
    except Exception:
        pass

    return {
        "active_model": model,
        "provider": provider,
        "model_size_gb": model_size_gb,
        "context_window": context_window,
        "kv_compression": kv_compression,
        "kv_bits": kv_bits if kv_compression != "none" else None,
        "llmfit_status": llmfit_status,
        "llmfit_headroom_gb": llmfit_headroom_gb,
        "llmfit_max_ctx": llmfit_max_ctx,
        "tokens_per_sec": tokens_per_sec,
        "load_time_ms": load_time_ms,
    }

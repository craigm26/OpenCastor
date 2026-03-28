"""castor.doctor — health check: hardware, config, deps, gateway, RCAN compliance."""

from __future__ import annotations

import glob
import importlib
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.table import Table

    HAS_RICH = True
except ImportError:
    HAS_RICH = False


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail" | "skip"
    detail: str = ""
    fix: str = ""


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "ok")

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "warn")

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "fail")

    @property
    def all_ok(self) -> bool:
        return self.fail_count == 0


# ── Individual checks ────────────────────────────────────────────────────────


def _read_proc_swaps(path: str = "/proc/swaps") -> Optional[str]:
    """Read /proc/swaps content. Extracted for monkeypatching in tests."""
    if not os.path.exists(path):
        return None
    try:
        with open(path) as _f:
            return _f.read()
    except Exception:
        return None


def _read_proc_meminfo(path: str = "/proc/meminfo") -> Optional[str]:
    """Read /proc/meminfo content. Extracted for monkeypatching in tests."""
    if not os.path.exists(path):
        return None
    try:
        with open(path) as _f:
            return _f.read()
    except Exception:
        return None


def _check_python() -> CheckResult:
    v = sys.version_info
    if v >= (3, 10):
        return CheckResult("Python version", "ok", f"{v.major}.{v.minor}.{v.micro}")
    return CheckResult(
        "Python version",
        "fail",
        f"{v.major}.{v.minor}.{v.micro} — requires 3.10+",
        fix="Install Python 3.10 or newer",
    )


def _check_dep(pkg: str, import_name: Optional[str] = None) -> CheckResult:
    import_name = import_name or pkg
    try:
        mod = importlib.import_module(import_name)
        ver = getattr(mod, "__version__", "?")
        return CheckResult(f"dep:{pkg}", "ok", f"v{ver}")
    except ImportError:
        return CheckResult(f"dep:{pkg}", "warn", "not installed", fix=f"pip install {pkg}")


def _check_config() -> CheckResult:
    candidates = [
        Path.cwd() / "bob.rcan.yaml",
        Path.cwd() / "robot.rcan.yaml",
        Path.home() / ".opencastor" / "config.yaml",
    ]
    for p in candidates:
        if p.exists():
            return CheckResult("RCAN config", "ok", str(p))
    return CheckResult(
        "RCAN config",
        "warn",
        "no .rcan.yaml found",
        fix="Run: castor wizard  or  castor wizard --web",
    )


def _check_opencastor_dir() -> CheckResult:
    d = Path.home() / ".opencastor"
    if d.exists():
        files = list(d.iterdir())
        return CheckResult("~/.opencastor/", "ok", f"{len(files)} files")
    return CheckResult(
        "~/.opencastor/", "warn", "directory missing", fix="Run: castor wizard to create it"
    )


def _check_signing_key() -> CheckResult:
    key = Path.home() / ".opencastor" / "signing_key.pem"
    if key.exists():
        return CheckResult("Ed25519 signing key", "ok", str(key))
    return CheckResult(
        "Ed25519 signing key",
        "warn",
        "not generated",
        fix="Enable signing in RCAN YAML: agent.signing.enabled: true",
    )


def _check_pq_signing_key() -> CheckResult:
    """RCAN v2.2 — ML-DSA-65 post-quantum signing key (FIPS 204)."""
    import os

    pq_path = os.environ.get("OPENCASTOR_PQ_KEY_PATH") or str(
        Path.home() / ".opencastor" / "pq_signing.key"
    )
    if Path(pq_path).exists():
        return CheckResult("ML-DSA-65 PQ signing key (v2.2)", "ok", pq_path)
    return CheckResult(
        "ML-DSA-65 PQ signing key (v2.2)",
        "warn",
        "not generated — run `castor keygen --pq` to create",
        fix=(
            "castor keygen --pq  (generates ~/.opencastor/pq_signing.key). "
            "Q-Day 2029: all firmware/RCAN messages should carry ML-DSA-65 signature."
        ),
    )


def _check_env_var(var: str, sensitive: bool = True) -> CheckResult:
    val = os.environ.get(var)
    if val:
        display = f"{val[:4]}…" if sensitive and len(val) > 4 else val
        return CheckResult(f"env:{var}", "ok", display)
    # Check ~/.opencastor/env
    env_file = Path.home() / ".opencastor" / "env"
    if env_file.exists() and var in env_file.read_text():
        return CheckResult(f"env:{var}", "ok", "set in ~/.opencastor/env")
    return CheckResult(f"env:{var}", "warn", "not set")


def _check_hardware_hailo() -> CheckResult:
    if Path("/dev/hailo0").exists():
        return CheckResult("Hailo-8 NPU", "ok", "/dev/hailo0")
    return CheckResult("Hailo-8 NPU", "skip", "not detected (optional)")


def _check_hardware_oakd() -> CheckResult:
    try:
        import depthai as dai  # noqa: F401

        return CheckResult("OAK-D (DepthAI)", "ok", f"depthai v{dai.__version__}")
    except ImportError:
        if shutil.which("lsusb"):
            res = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5)
            if "03e7" in res.stdout:  # Intel Myriad X VID
                return CheckResult(
                    "OAK-D (DepthAI)",
                    "warn",
                    "device detected, depthai not installed",
                    fix="pip install depthai",
                )
        return CheckResult("OAK-D (DepthAI)", "skip", "not detected (optional)")


def _check_gateway(port: int = 18789) -> CheckResult:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return CheckResult("Gateway port", "ok", f"localhost:{port} reachable")
    except (ConnectionRefusedError, OSError):
        return CheckResult(
            "Gateway port",
            "warn",
            f"localhost:{port} not reachable",
            fix="castor run --config <yaml>",
        )


def _check_rcan_compliance() -> CheckResult:
    try:
        from castor.rcan.sdk_bridge import check_compliance

        level = check_compliance()
        status = "ok" if level >= 1 else "warn"
        return CheckResult("RCAN compliance", status, f"L{level}")
    except Exception as e:
        return CheckResult(
            "RCAN compliance", "warn", f"could not check: {e}", fix="castor compliance"
        )


def _check_commitments() -> CheckResult:
    try:
        from castor.rcan.commitment_chain import get_commitment_chain

        chain = get_commitment_chain()
        count = chain.count() if hasattr(chain, "count") else "?"
        return CheckResult("Commitment chain", "ok", f"{count} records")
    except Exception:
        return CheckResult("Commitment chain", "skip", "rcan not installed (optional)")


# ── Main ─────────────────────────────────────────────────────────────────────



def _check_llmfit() -> CheckResult:
    """Check if the active model fits in device RAM (LLMFit)."""
    try:
        from castor.llmfit import check_fit

        result = check_fit()
        status = result.get("status", "unknown")
        model = result.get("active_model", "unknown")
        headroom = result.get("headroom_gb")
        max_ctx = result.get("max_context_tokens")

        if status == "ok":
            detail = (
                f"Model '{model}' fits — "
                f"{headroom:.1f} GB headroom, "
                f"max ctx {max_ctx:,} tokens"
            )
            return CheckResult(status="ok", name="LLMFit", detail=detail)
        elif status == "oom":
            detail = (
                f"Model '{model}' may OOM — "
                f"headroom: {headroom:.1f} GB"
            )
            return CheckResult(status="warn", name="LLMFit", detail=detail)
        else:
            return CheckResult(status="skip", name="LLMFit", detail=f"Status: {status}")
    except ImportError:
        return CheckResult(status="skip", name="LLMFit", detail="castor.llmfit not available")
    except Exception as exc:
        return CheckResult(status="skip", name="LLMFit", detail=str(exc))


def run_doctor(full: bool = False) -> DoctorReport:
    report = DoctorReport()
    add = report.checks.append

    # Core
    add(_check_python())
    add(_check_config())
    add(_check_opencastor_dir())
    add(_check_signing_key())
    add(_check_pq_signing_key())

    # Core deps
    for pkg in ["anthropic", "openai", "httpx", "yaml", "rich", "zeroconf"]:
        add(_check_dep(pkg, import_name="yaml" if pkg == "yaml" else pkg))

    # Optional SDK
    add(_check_dep("rcan"))

    # Env vars
    for var in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "RCAN_API_KEY"]:
        add(_check_env_var(var))

    # Hardware (optional)
    add(_check_hardware_hailo())
    add(_check_hardware_oakd())

    # Runtime
    add(_check_gateway())
    add(_check_llmfit())

    if full:
        add(_check_rcan_compliance())
        add(_check_commitments())

    return report


def print_report(report) -> None:
    """Print a doctor report.

    Accepts either a :class:`DoctorReport` instance or a ``list`` of
    ``(ok_or_status, name, detail)`` tuples as returned by
    :func:`run_all_checks` — both forms are normalised internally so that
    callers don't have to convert manually.
    """
    # ── Normalise list-of-tuples → DoctorReport ────────────────────────────
    if isinstance(report, list):
        _dr = DoctorReport()
        for _r in report:
            if not (isinstance(_r, (list, tuple)) and len(_r) >= 3):
                continue
            _ok_or_status, _name, _detail = _r[0], _r[1], _r[2]
            if isinstance(_ok_or_status, bool):
                _status = "ok" if _ok_or_status else "fail"
            else:
                _s = str(_ok_or_status).upper()
                if _s in ("PASS", "OK"):
                    _status = "ok"
                elif _s in ("WARN", "WARNING"):
                    _status = "warn"
                elif _s in ("FAIL", "ERROR"):
                    _status = "fail"
                elif _s in ("SKIP",):
                    _status = "skip"
                else:
                    _status = "warn"
            _dr.checks.append(CheckResult(name=str(_name), status=_status, detail=str(_detail)))
        report = _dr
    # ── Render ──────────────────────────────────────────────────────────────
    STATUS_ICON = {"ok": "✅", "warn": "⚠️ ", "fail": "❌", "skip": "⏭️ "}
    STATUS_COLOR = {"ok": "green", "warn": "yellow", "fail": "red", "skip": "dim"}

    if HAS_RICH:
        con = Console()
        t = Table(show_header=True, header_style="bold dim", box=None, pad_edge=False)
        t.add_column("", width=2)
        t.add_column("Check", style="bold")
        t.add_column("Detail")
        t.add_column("Fix", style="dim")
        for c in report.checks:
            icon = STATUS_ICON.get(c.status, "?")
            color = STATUS_COLOR.get(c.status, "white")
            t.add_row(icon, f"[{color}]{c.name}[/{color}]", c.detail, c.fix)
        con.print(t)
        con.print()
        summary_color = (
            "green" if report.all_ok else ("yellow" if report.fail_count == 0 else "red")
        )
        con.print(
            f"[{summary_color}]{'✅ All good' if report.all_ok else '⚠️  Issues found'}[/{summary_color}]"
            f" — {report.ok_count} ok, {report.warn_count} warnings, {report.fail_count} failures"
        )
    else:
        for c in report.checks:
            icon = STATUS_ICON.get(c.status, "?")
            line = f"{icon} {c.name}: {c.detail}"
            if c.fix:
                line += f"  → {c.fix}"
            print(line)
        print(f"\n{report.ok_count} ok, {report.warn_count} warnings, {report.fail_count} failures")


# ── Backward-compatible tuple-returning check functions ───────────────────────
# These preserve the (ok: bool, name: str, detail: str) API used by existing tests.


def _read_thermal_zone_file(path: str) -> Optional[str]:
    """Read a thermal zone file. Extracted for monkeypatching in tests."""
    try:
        with open(path) as _f:
            return _f.read()
    except Exception:
        return None


def check_cpu_temperature() -> tuple[bool, str, str]:
    """Return (ok, 'CPU temperature', detail_str)."""
    WARN_C = 75.0
    try:
        if sys.platform == "linux":
            paths = glob.glob("/sys/class/thermal/thermal_zone*/temp")
            if not paths:
                return True, "CPU temperature", "No CPU temperature data available"
            temps_c: list[float] = []
            for p in paths:
                raw = _read_thermal_zone_file(p)
                if raw is None:
                    continue
                try:
                    temps_c.append(int(raw.strip()) / 1000.0)
                except (ValueError, TypeError):
                    continue
            if not temps_c:
                return True, "CPU temperature", "No CPU temperature data available"
            max_t = max(temps_c)
            ok = max_t < WARN_C
            detail = f"{max_t:.1f}°C"
            if not ok:
                detail += f" (HIGH — >{WARN_C:.0f}°C)"
            return ok, "CPU temperature", detail
        # non-Linux: skip psutil (re-importing with a patched sys.platform causes errors)
        return True, "CPU temperature", "No CPU temperature data available"
    except Exception as exc:
        return True, "CPU temperature", f"error: {exc}"


def check_gpu_memory() -> tuple[bool, str, str]:
    """Return (ok, 'GPU memory', detail_str)."""
    try:
        import subprocess as _sp

        result = _sp.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            used, total = (int(x.strip()) for x in result.stdout.strip().split(","))
            pct = used / total * 100 if total else 0
            ok = pct < 80
            detail = f"{used}/{total} MiB ({pct:.1f}%)"
            if not ok:
                detail += " (>80% full)"
            return ok, "GPU memory", detail
    except Exception:
        pass
    return True, "GPU memory", "no NVIDIA GPU detected"


def check_memory_usage() -> tuple[bool, str, str]:
    """Return (ok, 'Memory usage', detail_str). Warns above 85%."""
    WARN_PCT = 85.0
    try:
        _raw = _read_proc_meminfo()
        if _raw is not None:
            info = {}
            for line in _raw.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k.strip()] = int(v.strip().split()[0])
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", 0)
            if total > 0:
                used_pct = (1 - avail / total) * 100
                ok = used_pct < WARN_PCT
                detail = f"{used_pct:.1f}% used"
                if not ok:
                    detail += " (>85% — consider freeing memory)"
                return ok, "Memory usage", detail
    except Exception:
        pass
    # psutil fallback
    try:
        import psutil

        vm = psutil.virtual_memory()
        ok = vm.percent < WARN_PCT
        return (
            ok,
            "Memory usage",
            f"{vm.percent:.1f}% used ({vm.available // 1024 // 1024} MB free)",
        )
    except Exception:
        pass
    return True, "Memory usage", "unavailable"


def check_swap_usage() -> tuple[bool, str, str]:
    """Return (ok, 'Swap usage', detail_str). Warns when >50% used."""
    WARN_PCT = 50.0
    # /proc/swaps path — uses _read_proc_swaps() which is monkeypatchable in tests
    try:
        _raw = _read_proc_swaps()
        if _raw is not None:
            lines = _raw.strip().splitlines()
            data_lines = [ln for ln in lines[1:] if len(ln.split()) >= 4]
            if not data_lines:
                return True, "Swap usage", "no swap configured"
            total_kb = sum(int(ln.split()[2]) for ln in data_lines)
            used_kb = sum(int(ln.split()[3]) for ln in data_lines)
            if total_kb == 0:
                return True, "Swap usage", "no swap configured"
            pct = used_kb / total_kb * 100
            ok = pct < WARN_PCT
            detail = f"{pct:.1f}% used ({used_kb // 1024} MB / {total_kb // 1024} MB)"
            if not ok:
                detail = f"swap >50% full — {pct:.1f}% used"
            return ok, "Swap usage", detail
    except Exception:
        pass
    # psutil fallback — patchable via patch.dict(sys.modules, {"psutil": mock})
    try:
        import psutil as _psutil

        sw = _psutil.swap_memory()
        if sw.total == 0:
            return True, "Swap usage", "no swap configured"
        ok = sw.percent < WARN_PCT
        detail = f"{sw.percent:.1f}% used"
        if not ok:
            detail = f"swap >50% full — {sw.percent:.1f}% used"
        return ok, "Swap usage", detail
    except ImportError:
        pass
    except Exception:
        pass
    return True, "Swap usage", "unavailable"


def check_disk_space(path: str = "/") -> tuple[bool, str, str]:
    """Return (ok, 'Disk space', detail_str). Warns when >90% used."""
    import shutil as _shutil

    try:
        usage = _shutil.disk_usage(path)
        free_pct = usage.free / usage.total * 100 if usage.total else 100
        ok = free_pct > 10
        detail = f"{free_pct:.1f}% free ({usage.free // 1024 // 1024} MB)"
        if not ok:
            used_pct = usage.used / usage.total * 100
            detail = f"disk {used_pct:.0f}% full — only {free_pct:.1f}% free"
        return ok, "Disk space", detail
    except Exception as exc:
        return False, "Disk space", f"error: {exc}"


def check_ble_driver() -> tuple[bool, str, str]:
    """Return (ok, 'BLE driver', detail_str)."""
    import shutil as _sh
    import sys as _sys

    # Check if bleak is importable (sys.modules["bleak"] = None means explicitly not installed)
    _not_set = "NOT_SET"
    bleak_entry = _sys.modules.get("bleak", _not_set)
    if bleak_entry is None or bleak_entry == _not_set:
        # bleak_entry is None → test injected None; _not_set → try real import
        if bleak_entry is None:
            return True, "BLE driver", "bleak not installed (optional)"
        try:
            import bleak  # noqa: F401
        except ImportError:
            return True, "BLE driver", "bleak not installed (optional)"
    if _sh.which("hciconfig") or Path("/sys/class/bluetooth").exists():
        return True, "BLE driver", "detected"
    return True, "BLE driver", "not detected (optional)"


def check_memory_db_size() -> tuple[bool, str, str]:
    """Return (ok, 'Memory DB size', detail_str). Warns when >100 MB."""
    WARN_MB = 100.0
    env_path = os.environ.get("CASTOR_MEMORY_DB", "")
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path))
    candidates += [
        Path(".opencastor") / "memory.db",
        Path.home() / ".opencastor" / "memory.db",
    ]
    for p in candidates:
        if p.exists():
            size_mb = p.stat().st_size / 1024 / 1024
            ok = size_mb < WARN_MB
            detail = f"{size_mb:.1f} MB"
            if not ok:
                detail += f" (large — >{WARN_MB:.0f} MB, consider running castor fix)"
            return ok, "Memory DB size", detail
    return True, "Memory DB size", "not found"


def check_signal_channel() -> tuple[bool, str, str]:
    """Return (ok, 'Signal channel', detail_str)."""
    env_file = Path.home() / ".opencastor" / "env"
    if env_file.exists() and "SIGNAL" in env_file.read_text():
        return True, "Signal channel", "configured"
    if os.environ.get("SIGNAL_NUMBER") or os.environ.get("SIGNAL_PHONE"):
        return True, "Signal channel", "configured via env"
    return True, "Signal channel", "not configured (optional)"


def check_rcan_compliance_version(config_path: Optional[str] = None) -> tuple[bool, str, str]:
    """Return (ok, 'RCAN compliance', detail_str).

    Reads rcan_version from the RCAN YAML, fetches the compatibility matrix from
    https://rcan-spec.pages.dev/compatibility.json (cached to ~/.opencastor/compat-cache.json
    with a 24-hour TTL), and validates the claimed version against the matrix.
    Falls back gracefully on network or parse errors.
    """
    import json as _json
    import time as _time

    # ── resolve config path ──────────────────────────────────────────────────
    cfg_path_str = config_path or os.environ.get("CASTOR_CONFIG", "")
    candidates: list[Path] = []
    if cfg_path_str:
        candidates.append(Path(cfg_path_str))
    candidates += [
        Path.cwd() / "bob.rcan.yaml",
        Path.cwd() / "robot.rcan.yaml",
        Path.home() / ".opencastor" / "config.yaml",
    ]

    rcan_version: Optional[str] = None
    for p in candidates:
        if p.exists():
            try:
                import yaml as _yaml  # type: ignore[import]

                data = _yaml.safe_load(p.read_text())
                if isinstance(data, dict):
                    rcan_version = (
                        str(data.get("rcan_version", ""))
                        or str(data.get("rcan", {}).get("version", ""))
                        if isinstance(data.get("rcan"), dict)
                        else str(data.get("rcan_version", ""))
                    )
                    rcan_version = rcan_version.strip() or None
            except Exception:
                pass
            break

    if not rcan_version:
        return True, "RCAN compliance", "no rcan_version in config (skipped)"

    # ── fetch/cache compatibility.json ──────────────────────────────────────
    cache_dir = Path.home() / ".opencastor"
    cache_file = cache_dir / "compat-cache.json"
    COMPAT_URL = "https://rcan-spec.pages.dev/compatibility.json"
    TTL = 86400  # 24 h

    compat_data: Optional[dict] = None

    # Try cache first
    try:
        if cache_file.exists():
            cached = _json.loads(cache_file.read_text())
            if _time.time() - cached.get("_cached_at", 0) < TTL:
                compat_data = cached
    except Exception:
        pass

    if compat_data is None:
        try:
            import urllib.request as _req

            with _req.urlopen(COMPAT_URL, timeout=5) as resp:
                raw = resp.read().decode()
            compat_data = _json.loads(raw)
            compat_data["_cached_at"] = _time.time()
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(_json.dumps(compat_data))
            except Exception:
                pass
        except Exception as exc:
            return True, "RCAN compliance", f"could not fetch compatibility matrix: {exc}"

    # ── validate ─────────────────────────────────────────────────────────────
    try:
        spec_versions = compat_data.get("spec_versions", [])
        matched = next(
            (sv for sv in spec_versions if sv.get("version") == rcan_version),
            None,
        )
        if matched is None:
            # Check if any current/supported spec supports this version
            all_versions = [sv.get("version", "") for sv in spec_versions]
            return (
                False,
                "RCAN compliance",
                f"rcan_version '{rcan_version}' not in compatibility matrix {all_versions}",
            )
        status = matched.get("status", "unknown")
        ok = status in ("current", "supported")
        detail = f"spec v{rcan_version} — {status}"
        if not ok:
            detail += " (upgrade recommended)"
        return ok, "RCAN compliance", detail
    except Exception as exc:
        return True, "RCAN compliance", f"could not parse compatibility matrix: {exc}"


def check_rcan_registry_reachable() -> tuple[str, str, str]:
    """Check rcan.dev registry reachability and latency.

    Returns ('PASS'|'WARN'|'FAIL', 'check_rcan_registry_reachable', detail)
    """
    try:
        from castor.rcan.node_resolver import NodeResolver

        resolver = NodeResolver()
        ok, latency_ms = resolver.is_reachable(timeout=5)
        if ok and latency_ms < 500:
            return (
                "PASS",
                "check_rcan_registry_reachable",
                f"rcan.dev reachable in {latency_ms:.0f}ms",
            )
        elif ok:
            return (
                "WARN",
                "check_rcan_registry_reachable",
                f"rcan.dev slow: {latency_ms:.0f}ms (>500ms)",
            )
        else:
            return ("FAIL", "check_rcan_registry_reachable", "rcan.dev unreachable")
    except Exception as e:
        return ("WARN", "check_rcan_registry_reachable", f"Could not check rcan.dev: {e}")


def check_rrn_valid(rrn: Optional[str] = None) -> tuple[str, str, str]:
    """Verify the configured RRN resolves in the RCAN federation.

    Returns ('PASS'|'WARN'|'SKIP', 'check_rrn_valid', detail)
    """
    if rrn is None:
        try:
            import os

            import yaml  # type: ignore[import]

            cfg_path = os.environ.get("RCAN_CONFIG", "rcan.yaml")
            if not os.path.exists(cfg_path):
                return ("SKIP", "check_rrn_valid", "No RCAN config found")
            with open(cfg_path) as _f:
                config = yaml.safe_load(_f)
            rrn = config.get("metadata", {}).get("device_id")
            if not rrn or not str(rrn).startswith("RRN-"):
                return ("SKIP", "check_rrn_valid", "No RRN found in RCAN config")
        except Exception:
            return ("SKIP", "check_rrn_valid", "Could not read RCAN config")

    try:
        from castor.rcan.node_resolver import NodeResolver

        resolver = NodeResolver()
        robot = resolver.resolve(str(rrn))
        source = "stale" if robot.stale else ("cached" if robot.from_cache else "live")
        return (
            "PASS",
            "check_rrn_valid",
            f"RRN {rrn} valid ({source}, resolved by {robot.resolved_by})",
        )
    except Exception as e:
        return ("WARN", "check_rrn_valid", f"RRN {rrn} could not be resolved: {e}")


def check_hardware_deps(hw: dict | None = None) -> list:
    """Check that optional hardware dependencies are installed (#548).

    Args:
        hw: Pre-computed result from :func:`castor.hardware_detect.detect_hardware`.
            When ``None`` the check runs ``detect_hardware()`` itself.

    Returns:
        List of ``(ok, name, detail)`` tuples compatible with :func:`run_all_checks`.
    """
    if hw is None:
        try:
            from castor.hardware_detect import detect_hardware

            hw = detect_hardware()
        except Exception as exc:
            return [(False, "hardware_deps", f"detect_hardware() failed: {exc}")]

    try:
        from castor.hardware_detect import suggest_extras

        extras = suggest_extras(hw)
    except Exception as exc:
        return [(False, "hardware_deps", f"suggest_extras() failed: {exc}")]

    if not extras:
        return [(True, "hardware_deps", "All hardware deps installed")]

    return [
        (
            False,
            f"dep_{pkg.replace('-', '_')}",
            f"Optional package not installed: {pkg} — run: pip install {pkg}",
        )
        for pkg in extras
    ]


def run_all_checks(config_path: Optional[str] = None) -> list[tuple[bool, str, str]]:
    """Run all checks and return list of (ok, name, detail) tuples.

    Check order (first-class RCAN checks run after system checks):
      1. System: CPU temp, GPU memory, RAM, swap, disk
      2. RCAN:   registry reachability, RRN validation, compliance version
      3. Optional: BLE driver, memory DB size, Signal channel, hardware deps
    """
    checks = [
        # ── System checks ─────────────────────────────────────────────────────
        check_cpu_temperature,
        check_gpu_memory,
        check_memory_usage,
        check_swap_usage,
        lambda: check_disk_space("/"),
        # ── RCAN first-class checks (§17) ─────────────────────────────────────
        check_rcan_registry_reachable,
        check_rrn_valid,
        lambda: check_rcan_compliance_version(config_path),
        # ── Optional / hardware checks ────────────────────────────────────────
        check_ble_driver,
        check_memory_db_size,
        check_signal_channel,
    ]
    results = []
    for fn in checks:
        try:
            results.append(fn())
        except Exception as exc:
            results.append((False, fn.__name__, f"error: {exc}"))

    # Hardware dep checks return a list — extend results
    try:
        results.extend(check_hardware_deps())
    except Exception as exc:
        results.append((False, "hardware_deps", f"error: {exc}"))

    return results


# ── Auto-fix helpers ──────────────────────────────────────────────────────────


def _fix_env_file() -> bool:
    """Copy .env.example → .env if .env missing. Prints FIXED/SKIP. Returns True if fixed."""
    env = Path(".env")
    example = Path(".env.example")
    if env.exists():
        print("SKIP  .env file — already exists")
        return False
    if not example.exists():
        print("SKIP  .env file — no .env.example found")
        return False
    import shutil as _shutil

    _shutil.copy(example, env)
    print("FIXED .env file — copied from .env.example")
    return True


def _fix_memory_db() -> bool:
    """Delete episodes older than 30 days from memory DB. Returns True if fixed."""
    import sqlite3 as _sq
    import time as _t

    db_path_str = os.environ.get("CASTOR_MEMORY_DB", "")
    candidates: list[Path] = []
    if db_path_str:
        candidates.append(Path(db_path_str))
    candidates += [
        Path(".opencastor/memory.db"),
        Path.home() / ".opencastor" / "memory.db",
    ]

    for p in candidates:
        if not p.exists():
            continue
        try:
            cutoff = int(_t.time()) - 30 * 86400
            with _sq.connect(str(p)) as conn:
                cur = conn.execute("DELETE FROM episodes WHERE timestamp < ?", (cutoff,))
                deleted = cur.rowcount
                conn.commit()
            print(f"FIXED Memory DB — deleted {deleted} episodes older than 30 days from {p}")
            return True
        except Exception as exc:
            print(f"SKIP  Memory DB — {exc}")
            return False

    print("SKIP  Memory DB — no database found")
    return False


_AUTO_FIX_MAP = {
    ".env file": _fix_env_file,
    "Memory DB": _fix_memory_db,
}


def run_auto_fix(results: list) -> None:
    """Run auto-fixers for failing checks.

    Args:
        results: list of (ok, name, detail) tuples from run_all_checks()
    """
    failed = [r for r in results if not r[0]]
    if not failed:
        print("No automatic fixes needed — all checks passed.")
        return

    for _ok, name, _detail in failed:
        fn = next(
            (v for k, v in _AUTO_FIX_MAP.items() if name.startswith(k) or k in name),
            None,
        )
        if fn:
            try:
                fn()
            except Exception as exc:
                print(f"Auto-fix for '{name}' failed: {exc}")
        # No handler for this check name — silently skip (do not raise)

"""castor.doctor — health check: hardware, config, deps, gateway, RCAN compliance."""

from __future__ import annotations

import errno
import glob
import importlib
import os
import shutil
import socket
import subprocess
import sys
import time
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
    #: True when this failing means the robot CANNOT MOVE. `castor doctor`
    #: exits non-zero on any of these, and only on these: a host that is merely
    #: missing an optional package still has a car that drives, and an exit
    #: code that fires on everything is one nobody can put in a script.
    blocking: bool = False


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

    @property
    def blocking_failures(self) -> list[CheckResult]:
        """The checks that mean the robot cannot move."""
        return [c for c in self.checks if c.blocking and c.status == "fail"]

    @property
    def can_move(self) -> bool:
        return not self.blocking_failures

    @property
    def exit_code(self) -> int:
        return 1 if self.blocking_failures else 0


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


def _check_gateway(port: Optional[int] = None) -> CheckResult:
    """Probe the gateway.

    `port` used to default to 18789, which nothing in this repository has ever
    served — `castor up` puts the gateway at --base-port (8080 by default) and
    the reference rover runs on 8081. The result was "Gateway not reachable" on
    a healthy robot, plus a fix line (`castor run --config <yaml>`) that starts
    a different program. With no port given the robot's own units are asked
    instead; see :func:`_check_gateway_port`.
    """
    if port is None:
        return _check_gateway_port(resolve_robot())
    if _probe_port(port):
        return CheckResult("Gateway port", "ok", f"localhost:{port} reachable")
    return CheckResult(
        "Gateway port",
        "warn",
        f"localhost:{port} not reachable",
        fix=f"systemctl --user restart <name>-gateway   # nothing is listening on {port}",
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
                f"Model '{model}' fits — {headroom:.1f} GB headroom, max ctx {max_ctx:,} tokens"
            )
            return CheckResult(status="ok", name="LLMFit", detail=detail)
        elif status == "oom":
            detail = f"Model '{model}' may OOM — headroom: {headroom:.1f} GB"
            return CheckResult(status="warn", name="LLMFit", detail=detail)
        else:
            return CheckResult(status="skip", name="LLMFit", detail=f"Status: {status}")
    except ImportError:
        return CheckResult(status="skip", name="LLMFit", detail="castor.llmfit not available")
    except Exception as exc:
        return CheckResult(status="skip", name="LLMFit", detail=str(exc))


def _check_key_age() -> CheckResult:
    """Check the age of the PQ signing key; warn if older than 180 days."""
    import time
    from pathlib import Path

    # Try to locate pq_key_path from config
    pq_key_path: str = ""
    config_candidates = [
        Path("robot.rcan.yaml"),
        Path.home() / ".opencastor" / "robot.rcan.yaml",
    ]
    for candidate in config_candidates:
        if candidate.exists():
            try:
                import yaml

                cfg = yaml.safe_load(candidate.read_text()) or {}
                pq_key_path = cfg.get("agent", {}).get("signing", {}).get("pq_key_path", "")
                if pq_key_path:
                    break
            except Exception:
                pass

    # Fall back to default path
    if not pq_key_path:
        pq_key_path = str(Path.home() / ".opencastor" / "pq_signing.key")

    key_file = Path(pq_key_path)
    if not key_file.exists():
        return CheckResult("PQ key age", "skip", f"key not found: {pq_key_path}")

    age_days = (time.time() - key_file.stat().st_mtime) / 86400
    if age_days > 180:
        return CheckResult(
            "PQ key age",
            "warn",
            f"{age_days:.0f} days old — rotation recommended (castor key-rotation rotate)",
        )
    return CheckResult("PQ key age", "ok", f"{age_days:.0f} days old")


def _check_turboquant() -> CheckResult:
    """Check TurboQuant KV cache compression configuration.

    If a GGUF model is configured, reports estimated KV savings.
    Returns ok if TurboQuant is configured, skip (info) if not.
    """
    try:
        from castor.llmfit import _MODEL_WEIGHT_GB, turboquant_analysis

        # Try to detect a GGUF model from RCAN config
        config_candidates = [
            Path("robot.rcan.yaml"),
            Path("bob.rcan.yaml"),
            Path.home() / ".opencastor" / "robot.rcan.yaml",
        ]
        for candidate in config_candidates:
            if not candidate.exists():
                continue
            try:
                import yaml

                cfg = yaml.safe_load(candidate.read_text()) or {}
                provider_cfg = cfg.get("provider", cfg.get("llm", {}))
                if not isinstance(provider_cfg, dict):
                    continue
                model = provider_cfg.get("model", "")
                kv_comp = provider_cfg.get("kv_compression", "none")
                is_gguf = (
                    ".gguf" in model.lower()
                    or "-gguf" in model.lower()
                    or provider_cfg.get("format") == "gguf"
                )
                if not is_gguf:
                    continue
                analysis = turboquant_analysis(model)
                if kv_comp == "turboquant":
                    detail = (
                        f"GGUF model '{model}' — "
                        f"KV cache: {analysis['kv_cache_base_gb']:.2f} GB → "
                        f"{analysis['kv_cache_compressed_gb']:.2f} GB "
                        f"(saves {analysis['savings_gb']:.2f} GB)"
                    )
                    return CheckResult("TurboQuant KV compression", "ok", detail)
                else:
                    detail = (
                        f"GGUF model '{model}' — TurboQuant not configured; "
                        f"potential savings: {analysis['savings_gb']:.2f} GB "
                        f"(set kv_compression: turboquant)"
                    )
                    return CheckResult("TurboQuant KV compression", "skip", detail)
            except Exception:
                pass

        # No config found — check if any GGUF model is in the known list
        gguf_models = [m for m in _MODEL_WEIGHT_GB if "gguf" in m]
        if gguf_models:
            sample = gguf_models[0]
            analysis = turboquant_analysis(sample)
            detail = (
                f"No GGUF model in RCAN config. "
                f"Example savings for '{sample}': {analysis['savings_gb']:.2f} GB."
            )
            return CheckResult("TurboQuant KV compression", "skip", detail)

        return CheckResult(
            "TurboQuant KV compression",
            "skip",
            "No GGUF model detected — TurboQuant applies to GGUF/llama.cpp/Ollama models",
        )
    except ImportError:
        return CheckResult("TurboQuant KV compression", "skip", "castor.llmfit not available")
    except Exception as exc:
        return CheckResult("TurboQuant KV compression", "skip", str(exc))


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
    add(_check_turboquant())
    add(_check_key_age())

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
        t.add_column("Check", style="bold", overflow="fold")
        t.add_column("Detail", overflow="fold")
        # `fold`, not the default `ellipsis`: the Fix column is the only thing
        # on this page the owner is supposed to paste, and a fix cut off at the
        # terminal width is not a fix.
        t.add_column("Fix", style="dim", overflow="fold")
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

    # The verdict the owner came for. A doctor that prints twelve green rows and
    # says nothing about a car that will not move has answered the wrong question.
    blocking = report.blocking_failures
    if blocking:
        print("\n  ❌ THIS ROBOT CANNOT MOVE — " f"{len(blocking)} blocking check(s):")
        for c in blocking:
            print(f"     • {c.name}: {c.detail}")
            if c.fix:
                print(f"       fix: {c.fix}")


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


# ═══════════════════════════════════════════════════════════════════════════
# THE ROBOT `castor up` PROVISIONED
#
# Everything above this line checks the HOST. None of it can tell you whether
# the car in front of you can move, and a clean bill of health on a car that
# cannot move is worse than no check at all — it sends the owner looking for
# the fault somewhere it is not.
#
# So this section reads the robot itself: the systemd user units `castor up`
# wrote, the `gateway-policy.env` beside them (the one file where simulated
# wheels become real ones), the ports those units actually bind, the I2C bus
# the PCA9685 needs, the advertiser the phone browses for, and the USB current
# budget a streaming camera spends. It calls `castor gaps`, which already knows
# how to look at an up-provisioned robot, rather than growing a second opinion.
#
# Every finding carries ONE line the owner can paste. Findings that mean the
# car cannot move are marked `blocking`, and `castor doctor` exits non-zero on
# any of them: a health check that always exits 0 is a health check nobody can
# put in a script.
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"

#: The address every PCA9685 hat and breakout ships at.
PCA9685_DEFAULT_ADDRESS = 0x40

#: The mDNS type the iOS app browses (RobotDiscovery.swift). `castor/rcan/mdns.py`
#: publishes `_rcan._tcp` instead, and `castor up` starts no advertiser at all —
#: which is why "find it on my network" has never worked for an `up` robot.
OPENCASTOR_MDNS_TYPE = "_opencastor._tcp.local."

#: Values of OPENCASTOR_DRIVE that mean "nothing will turn".
SIMULATED_DRIVE_VALUES = ("", "simulated", "sim", "mock", "none", "noop", "off")


@dataclass
class RobotUnits:
    """One robot as its systemd user units describe it.

    Read from the units rather than recomputed from `--base-port`, because the
    units are what is actually running: this bench's rover has its gateway on
    8081 and its runtime on 8003, a layout no formula in `castor up` produces.
    """

    name: str
    home: Optional[Path] = None
    gateway_port: Optional[int] = None
    runtime_port: Optional[int] = None
    console_port: Optional[int] = None
    policy_env: Optional[Path] = None
    units: list[str] = field(default_factory=list)

    @property
    def policy_path(self) -> Optional[Path]:
        if self.policy_env is not None:
            return self.policy_env
        if self.home is not None:
            return self.home / "gateway-policy.env"
        return None


def read_env_file(path) -> dict[str, str]:
    """Parse a systemd `EnvironmentFile` the way systemd does, near enough.

    Quotes are stripped because `gateway-policy.env` quotes the tier bindings
    (they contain '|', which a sourcing shell would read as a pipeline), and a
    caller comparing OPENCASTOR_DRIVE against "simulated" must not have to
    know that.
    """
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text()
    except (OSError, TypeError):
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def _unit_env(text: str) -> dict[str, str]:
    """`Environment=K=V` lines from a unit file."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("Environment="):
            continue
        body = line.split("=", 1)[1]
        if "=" in body:
            k, v = body.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def discover_robots(unit_dir=None) -> list[RobotUnits]:
    """Every robot with a `*-gateway.service` in the user unit directory."""
    import re as _re

    unit_dir = Path(unit_dir) if unit_dir is not None else DEFAULT_UNIT_DIR
    if not unit_dir.is_dir():
        return []

    robots: dict[str, RobotUnits] = {}
    for unit in sorted(unit_dir.glob("*-gateway.service")):
        name = unit.name[: -len("-gateway.service")]
        try:
            text = unit.read_text()
        except OSError:
            continue
        robot = RobotUnits(name=name, units=[unit.name])
        m = _re.search(r"--port\s+(\d+)", text)
        if m:
            robot.gateway_port = int(m.group(1))
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("EnvironmentFile=") and line.endswith("gateway-policy.env"):
                # A leading '-' makes the file optional to systemd. carbot's
                # units do that and it is the anti-pattern the rover's units
                # argue against in a comment; strip it and check for real.
                policy = Path(line.split("=", 1)[1].lstrip("-"))
                robot.policy_env = policy
                robot.home = policy.parent
        if robot.home is None:
            m = _re.search(r"--robot-md\s+(\S+)", text)
            if m:
                robot.home = Path(m.group(1)).parent
        robots[name] = robot

    for suffix, attr, key_suffix in (
        ("-castor.service", "runtime_port", "RUNTIME_PORT"),
        ("-console.service", "console_port", "CONSOLE_PORT"),
    ):
        for unit in sorted(unit_dir.glob(f"*{suffix}")):
            name = unit.name[: -len(suffix)]
            robot = robots.get(name)
            if robot is None:
                continue
            try:
                text = unit.read_text()
            except OSError:
                continue
            robot.units.append(unit.name)
            # Environment= AND every EnvironmentFile= the unit pulls in. The
            # console's port lives in console.env, whose header invites hand
            # edits, and a hand-written robot (this bench's rover) names its
            # runtime port ROVER_RUNTIME_PORT rather than ROBOT_RUNTIME_PORT.
            # Matching on the SUFFIX reads both without pretending a
            # hand-written robot is malformed.
            env = _unit_env(text)
            for raw in text.splitlines():
                line = raw.strip()
                if line.startswith("EnvironmentFile="):
                    merged = read_env_file(Path(line.split("=", 1)[1].lstrip("-")))
                    for k, v in merged.items():
                        env.setdefault(k, v)
            for key, value in env.items():
                if key.endswith(key_suffix) and value.isdigit():
                    setattr(robot, attr, int(value))
                    break
            if robot.home is None and env.get("ROBOT_HOME"):
                robot.home = Path(env["ROBOT_HOME"])

    return list(robots.values())


def resolve_robot(home=None, unit_dir=None, env=None) -> Optional[RobotUnits]:
    """The robot this run of doctor is about.

    `--home` wins, then $ROBOT_HOME, then the single robot the units describe,
    then ~/robot (the `castor up` default). A host with two robots and no
    --home is reported as ambiguous by the check, not guessed at silently.
    """
    env = os.environ if env is None else env
    found = discover_robots(unit_dir)
    wanted = home or env.get("ROBOT_HOME")
    if wanted:
        target = Path(wanted).expanduser()
        for robot in found:
            if robot.home is not None and robot.home == target:
                return robot
        return RobotUnits(name=target.name, home=target)
    if len(found) == 1:
        return found[0]
    if found:
        default = Path.home() / "robot"
        for robot in found:
            if robot.home == default:
                return robot
        return found[0]
    default = Path.home() / "robot"
    if default.is_dir():
        return RobotUnits(name=default.name, home=default)
    return None


# ── Individual robot checks ──────────────────────────────────────────────────


def _check_robot_home(robot: Optional[RobotUnits], others: int = 0) -> CheckResult:
    if robot is None or robot.home is None:
        return CheckResult(
            "Robot home",
            "fail",
            "no robot found — no *-gateway.service unit and no ~/robot",
            fix="castor up",
            blocking=True,
        )
    if not robot.home.is_dir():
        return CheckResult(
            "Robot home",
            "fail",
            f"{robot.home} does not exist",
            fix=f"castor up --home {robot.home}",
            blocking=True,
        )
    missing = [f for f in ("ROBOT.md", "bearers.yaml") if not (robot.home / f).exists()]
    detail = f"{robot.home} ({robot.name}, {len(robot.units)} units)"
    if others:
        detail += f" — {others} other robot(s) on this host; pick one with --home"
    if missing:
        return CheckResult(
            "Robot home",
            "fail",
            f"{detail} — missing {', '.join(missing)}",
            fix=f"castor up --home {robot.home}",
            blocking=True,
        )
    return CheckResult("Robot home", "ok", detail)


def _probe_port(port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> bool:
    """True when something accepts a TCP connection there. Patchable in tests."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _check_gateway_port(robot: Optional[RobotUnits], probe=None) -> CheckResult:
    """The port the gateway unit actually binds — not a constant.

    The old check probed 18789, a port nothing in this repository has ever
    served, and printed "Gateway not reachable" plus `castor run --config
    <yaml>` on a perfectly healthy robot. Both halves were wrong.
    """
    probe = probe or _probe_port
    if robot is None or robot.gateway_port is None:
        return CheckResult(
            "Gateway port",
            "skip",
            "no gateway unit found — cannot tell which port to probe",
            fix="castor up",
        )
    name, port = robot.name, robot.gateway_port
    if probe(port):
        return CheckResult("Gateway port", "ok", f"127.0.0.1:{port} answers ({name}-gateway)")
    return CheckResult(
        "Gateway port",
        "fail",
        f"127.0.0.1:{port} refused — nothing signs a drive command without it",
        fix=f"systemctl --user restart {name}-gateway && journalctl --user -u {name}-gateway -n 40",
        blocking=True,
    )


def _check_runtime_port(robot: Optional[RobotUnits], probe=None) -> CheckResult:
    probe = probe or _probe_port
    if robot is None or robot.runtime_port is None:
        return CheckResult("Runtime port", "skip", "no castor runtime unit found")
    name, port = robot.name, robot.runtime_port
    if probe(port):
        return CheckResult("Runtime port", "ok", f"127.0.0.1:{port} answers ({name}-castor)")
    return CheckResult(
        "Runtime port",
        "warn",
        f"127.0.0.1:{port} refused — telemetry and /api/stop are down",
        fix=f"systemctl --user restart {name}-castor",
    )


def _check_console_port(robot: Optional[RobotUnits], probe=None) -> CheckResult:
    probe = probe or _probe_port
    if robot is None or robot.console_port is None:
        return CheckResult("Console port", "skip", "no console unit found")
    name, port = robot.name, robot.console_port
    if probe(port):
        return CheckResult("Console port", "ok", f"127.0.0.1:{port} answers ({name}-console)")
    return CheckResult(
        "Console port",
        "warn",
        f"127.0.0.1:{port} refused — chat, /surface and /gaps are down",
        fix=f"systemctl --user restart {name}-console",
    )


def _check_gateway_policy(robot: Optional[RobotUnits]) -> CheckResult:
    """The file where simulated wheels become real ones must at least exist."""
    path = robot.policy_path if robot is not None else None
    if path is None:
        return CheckResult("gateway-policy.env", "skip", "no robot home resolved")
    if not path.exists():
        return CheckResult(
            "gateway-policy.env",
            "fail",
            f"{path} missing — the gateway starts with no tool allowlist and denies every tool",
            fix=f"castor up --home {path.parent}",
            blocking=True,
        )
    policy = read_env_file(path)
    allow = policy.get("ROBOT_MD_TOOL_ALLOWLIST", "")
    if "drive.set" not in allow:
        return CheckResult(
            "gateway-policy.env",
            "fail",
            f"{path} — drive.set is not in ROBOT_MD_TOOL_ALLOWLIST, every drive is a signed DENY",
            fix=f"$EDITOR {path}  # add drive.set to ROBOT_MD_TOOL_ALLOWLIST",
            blocking=True,
        )
    return CheckResult("gateway-policy.env", "ok", f"{path} ({len(policy)} settings)")


def i2c_addresses(bus: int = 1) -> set[int]:
    """Addresses answering on the bus. Empty when there is no bus at all."""
    try:
        from castor.peripherals import scan_i2c

        return {p.i2c_address for p in scan_i2c(bus=bus) if p.i2c_address is not None}
    except Exception:  # noqa: BLE001 — no bus is a valid machine state
        return set()


def _check_i2c_bus(policy: dict, exists=None, bus: int = 1) -> CheckResult:
    """/dev/i2c-1, the one step the flashable image is forbidden from taking.

    Raspberry Pi OS ships `dtparam=i2c_arm=on` commented out and
    `scripts/image/selftest.sh` asserts that no script may write config.txt,
    so a freshly flashed card has no bus. Nothing told the owner that; this
    does, with the command.
    """
    path = f"/dev/i2c-{bus}"
    present = Path(path).exists() if exists is None else exists(path)
    drive = policy.get("OPENCASTOR_DRIVE", "").strip().lower()
    wants_i2c = drive not in SIMULATED_DRIVE_VALUES
    if present:
        return CheckResult("I2C bus", "ok", path)
    return CheckResult(
        "I2C bus",
        "fail" if wants_i2c else "warn",
        f"{path} missing — I2C is off, so no PCA9685 can ever answer"
        + (f" (OPENCASTOR_DRIVE={drive} will refuse to start)" if wants_i2c else ""),
        fix="sudo raspi-config nonint do_i2c 0 && sudo reboot",
        blocking=wants_i2c,
    )


def _check_drive_mode(
    robot: Optional[RobotUnits], policy: dict, addresses: Optional[set] = None
) -> CheckResult:
    """"Your wheels are simulated."

    `castor up` ships OPENCASTOR_DRIVE commented out on purpose, and the rule
    behind that is right: a driver built by accident must not move a real
    vehicle. What was missing is anybody SAYING SO to the owner holding the
    phone, whose stick moves, whose receipts sign, whose badge is green, and
    whose car is still. That sentence is this check.
    """
    if robot is None or robot.policy_path is None:
        return CheckResult("Drive mode", "skip", "no robot home resolved")
    addresses = i2c_addresses() if addresses is None else addresses
    policy_path = robot.policy_path
    try:
        address = int(policy.get("OPENCASTOR_DRIVE_I2C_ADDRESS", "0x40"), 0)
    except ValueError:
        address = PCA9685_DEFAULT_ADDRESS
    chip_present = address in addresses
    drive = policy.get("OPENCASTOR_DRIVE", "").strip().lower()

    enable = (
        f"sed -i 's/^#*OPENCASTOR_DRIVE=.*/OPENCASTOR_DRIVE=pca9685/' {policy_path} "
        f"&& systemctl --user restart {robot.name}-gateway   # WHEELS OFF THE GROUND FIRST"
    )

    if drive in SIMULATED_DRIVE_VALUES:
        shown = drive or "unset"
        if chip_present:
            return CheckResult(
                "Drive mode",
                "fail",
                f"PCA9685 answering at 0x{address:02x} but OPENCASTOR_DRIVE={shown} — "
                "YOUR WHEELS ARE SIMULATED. The stick moves, the receipts sign, "
                "the badge is green, and nothing turns.",
                fix=enable,
                blocking=True,
            )
        return CheckResult(
            "Drive mode",
            "warn",
            f"OPENCASTOR_DRIVE={shown} (simulated wheels) and nothing answers at "
            f"0x{address:02x} — wire the PCA9685 before flipping it",
            fix=f"i2cdetect -y 1   # expect {address:02x}; then: {enable}",
        )

    if not chip_present:
        return CheckResult(
            "Drive mode",
            "fail",
            f"OPENCASTOR_DRIVE={drive} but nothing answers at 0x{address:02x} — "
            "the actuator refuses to construct and the gateway will not start",
            fix="i2cdetect -y 1   # check wiring, 5V and the address jumpers",
            blocking=True,
        )

    throttle = policy.get("OPENCASTOR_DRIVE_THROTTLE_CHANNEL", "")
    steering = policy.get("OPENCASTOR_DRIVE_STEERING_CHANNEL", "")
    if (throttle, steering) == ("0", "1"):
        # Both real cars on this bench (the rover and carbot) wire throttle 1 /
        # steering 0. The shipped template says the opposite, and a cross-plugged
        # harness passes every register-level bench test there is, because
        # "steering command produces a pulse on the steering channel" is true no
        # matter what is on the other end of that pin.
        return CheckResult(
            "Drive mode",
            "warn",
            f"OPENCASTOR_DRIVE={drive} at 0x{address:02x}, but channels are throttle 0 / "
            "steering 1 — the template default, reversed vs both known cars",
            fix=f"$EDITOR {policy_path}   # THROTTLE_CHANNEL=1, STEERING_CHANNEL=0",
        )
    return CheckResult(
        "Drive mode",
        "ok",
        f"OPENCASTOR_DRIVE={drive} at 0x{address:02x} "
        f"(throttle ch{throttle or '?'}, steering ch{steering or '?'}) — real wheels",
    )


# ── "does the configuration persist" — the 2026-09-08 bench trap ────────────

#: PCA9685 registers. MODE1 carries the SLEEP bit; PRESCALE carries the frame
#: rate. Both are one-byte reads, and reading them is the only way anything in
#: this stack finds out what the chip actually holds.
PCA9685_MODE1 = 0x00
PCA9685_PRESCALE = 0xFE
PCA9685_MODE1_SLEEP = 0x10
#: Power-on defaults: MODE1 0x11 (SLEEP + ALLCALL), prescale 30 (~197 Hz).
PCA9685_POWER_ON_MODE1 = 0x11
PCA9685_POWER_ON_PRESCALE = 0x1E


def pca9685_prescale(frame_hz: int, oscillator_hz: int = 25_000_000) -> int:
    """The datasheet prescale the driver would write, clamped to the legal 3..255.

    Same arithmetic as `rc_car_actuator.pca9685.PCA9685Drive.prescale`, copied
    rather than imported so doctor can run on a host where the actuator package
    is the thing that is missing. 25 MHz / 50 Hz → 121.
    """
    if frame_hz <= 0:
        raise ValueError("frame_hz must be positive")
    return max(3, min(255, round(oscillator_hz / (4096.0 * frame_hz)) - 1))


def _read_pca9685_registers(address: int, bus: int = 1) -> tuple[int, int]:
    """MODE1 and PRESCALE, read only. Raises on any bus error.

    doctor NEVER writes to this chip. The gateway may own the bus, an ESC may
    be armed, and a health check that reconfigures a live PWM controller is a
    health check that can move a car.
    """
    from smbus2 import SMBus

    with SMBus(bus) as b:
        return b.read_byte_data(address, PCA9685_MODE1), b.read_byte_data(
            address, PCA9685_PRESCALE
        )


def _check_pca9685_persistence(
    robot: Optional[RobotUnits],
    policy: dict,
    addresses: Optional[set] = None,
    read_registers=None,
    probe=None,
) -> CheckResult:
    """Does the chip still hold what the driver wrote?

    2026-09-08, first real car: a PCA9685 answered `i2cdetect` at 0x40,
    accepted every write, the driver logged "ESC arming: held neutral",
    `status.report` said `hardware_reachable: true`, and the rover's smoke
    suite passed 27/27 — while the chip sat at power-on defaults (MODE1 0x11,
    SLEEP set, prescale 30) because it reset itself within a second of being
    configured, later returning `[Errno 121]`. Every layer was truthfully
    reporting a write into a chip that then forgot, because NOTHING IN THE
    STACK READS THE CHIP BACK. This does. See docs/hardware/pca9685-bringup.md
    step 3b.
    """
    if robot is None or robot.policy_path is None:
        return CheckResult("PCA9685 configuration persists", "skip", "no robot home resolved")
    drive = policy.get("OPENCASTOR_DRIVE", "").strip().lower()
    if "pca9685" not in drive:
        return CheckResult(
            "PCA9685 configuration persists",
            "skip",
            f"OPENCASTOR_DRIVE={drive or 'unset'} does not name the PCA9685",
        )
    try:
        address = int(policy.get("OPENCASTOR_DRIVE_I2C_ADDRESS", "0x40"), 0)
    except ValueError:
        address = PCA9685_DEFAULT_ADDRESS
    try:
        bus = int(policy.get("OPENCASTOR_DRIVE_I2C_BUS", "1"), 0)
    except ValueError:
        bus = 1
    addresses = i2c_addresses(bus=bus) if addresses is None else addresses
    if address not in addresses:
        # Drive mode already says this, and says it as a blocking failure.
        return CheckResult(
            "PCA9685 configuration persists",
            "skip",
            f"nothing answers at 0x{address:02x} — see Drive mode",
        )

    try:
        frame_hz = int(policy.get("OPENCASTOR_DRIVE_FRAME_HZ", "50"), 0)
        oscillator_hz = int(policy.get("OPENCASTOR_DRIVE_OSCILLATOR_HZ", "25000000"), 0)
        expected = pca9685_prescale(frame_hz, oscillator_hz)
    except ValueError:
        frame_hz, oscillator_hz, expected = 50, 25_000_000, 121

    bringup = "see docs/hardware/pca9685-bringup.md step 3b"
    meter = "the board is resetting; measure VCC and V+ with the ESC arming, " + bringup
    fix = (
        f"systemctl --user stop {robot.name}-gateway   # then measure PCA9685 VCC and V+ "
        f"with a meter while the ESC arms — {bringup}"
    )

    read_registers = read_registers or _read_pca9685_registers
    try:
        mode1, prescale = read_registers(address, bus)
    except ImportError:
        return CheckResult(
            "PCA9685 configuration persists",
            "skip",
            "smbus2 not installed — cannot read the chip back",
            fix="pip install smbus2",
        )
    except OSError as exc:
        # ENOENT/EACCES are the HOST's problem — no bus node, or a user who is
        # not in the i2c group — and neither says anything about the chip.
        # Errno 121 (EREMOTEIO) and its neighbours are the chip, and on the
        # 2026-09-08 bench that was the fault three seconds after bring-up.
        if exc.errno in (errno.ENOENT, errno.EACCES, errno.EPERM):
            return CheckResult(
                "PCA9685 configuration persists",
                "skip",
                f"cannot open the I2C bus to read 0x{address:02x} back ({exc})",
                fix="sudo usermod -aG i2c $USER   # or: sudo raspi-config nonint do_i2c 0",
            )
        return CheckResult(
            "PCA9685 configuration persists",
            "fail",
            f"PCA9685 at 0x{address:02x} answers the scan but its registers cannot be "
            f"read ({exc}): {meter}",
            fix=fix,
            blocking=True,
        )
    except Exception as exc:  # noqa: BLE001 — a bus error IS the symptom
        return CheckResult(
            "PCA9685 configuration persists",
            "fail",
            f"PCA9685 at 0x{address:02x} answers the scan but its registers cannot be "
            f"read ({exc}): {meter}",
            fix=fix,
            blocking=True,
        )

    sleeping = bool(mode1 & PCA9685_MODE1_SLEEP)
    if not sleeping and prescale == expected:
        return CheckResult(
            "PCA9685 configuration persists",
            "ok",
            f"0x{address:02x} MODE1=0x{mode1:02x} (awake), prescale={prescale} "
            f"({frame_hz} Hz) — the chip holds what the driver wrote",
        )

    probe = probe or _probe_port
    gateway_up = bool(robot.gateway_port is not None and probe(robot.gateway_port))
    at_power_on = sleeping and prescale == PCA9685_POWER_ON_PRESCALE
    if at_power_on and not gateway_up:
        return CheckResult(
            "PCA9685 configuration persists",
            "warn",
            f"0x{address:02x} MODE1=0x{mode1:02x}, prescale={prescale} — unconfigured "
            "(no driver has written it yet); the gateway is not running",
            fix=f"systemctl --user start {robot.name}-gateway   # then re-run castor doctor",
        )
    return CheckResult(
        "PCA9685 configuration persists",
        "fail",
        f"PCA9685 configuration does not persist (MODE1=0x{mode1:02x}, "
        f"prescale={prescale}): {meter}"
        + ("" if sleeping else f" — expected prescale {expected} for {frame_hz} Hz"),
        fix=fix,
        blocking=True,
    )


def _browse_mdns(service_type: str = OPENCASTOR_MDNS_TYPE, timeout: float = 2.0) -> list[str]:
    """Names answering on an mDNS service type. Raises ImportError with no zeroconf."""
    from zeroconf import ServiceBrowser, Zeroconf

    found: list[str] = []

    class _Listener:
        def add_service(self, zc, type_, name):  # noqa: ANN001
            found.append(name)

        def update_service(self, zc, type_, name):  # noqa: ANN001
            pass

        def remove_service(self, zc, type_, name):  # noqa: ANN001
            pass

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, service_type, _Listener())
        time.sleep(timeout)
    finally:
        zc.close()
    return found


def _check_mdns_advertiser(browse=None, timeout: float = 2.0) -> CheckResult:
    """Is anything answering on the type the phone actually browses?

    The app browses `_opencastor._tcp`; `castor/rcan/mdns.py` publishes
    `_rcan._tcp`; and `castor up` writes four units, none of which advertises
    at all. "No robots found" on a robot that is answering has been the
    reported symptom since 2026-08-14. Note avahi-browse will not show this
    either way — python-zeroconf is the only thing that has ever seen it.
    """
    browse = browse or _browse_mdns
    try:
        names = browse(OPENCASTOR_MDNS_TYPE, timeout)
    except ImportError:
        return CheckResult(
            "mDNS advertiser",
            "skip",
            "python-zeroconf not installed — cannot check LAN discovery",
            fix="pip install zeroconf",
        )
    except Exception as exc:  # noqa: BLE001 — a host with no multicast is valid
        return CheckResult("mDNS advertiser", "skip", f"browse failed: {exc}")
    if names:
        return CheckResult(
            "mDNS advertiser", "ok", f"{len(names)} on {OPENCASTOR_MDNS_TYPE}: {names[0]}"
        )
    return CheckResult(
        "mDNS advertiser",
        "warn",
        f"nothing answers on {OPENCASTOR_MDNS_TYPE} — the phone's "
        '"find it on your network" will report no robots. The QR still pairs.',
        fix="castor pair --home <home>   # the QR still works; LAN discovery needs an advertiser",
    )


def _read_boot_config(paths=("/boot/firmware/config.txt", "/boot/config.txt")) -> tuple:
    for candidate in paths:
        p = Path(candidate)
        if p.exists():
            try:
                return p, p.read_text()
            except OSError:
                return p, ""
    return None, ""


def usb_video_devices(sys_v4l="/sys/class/video4linux") -> list[str]:
    """V4L2 device names whose parent device sits on the USB bus.

    A CSI camera's parent resolves to an on-SoC codec node; a webcam's or an
    OAK-D's resolves under /sys/bus/usb. Only the second kind spends the
    current budget this check is about.
    """
    import re as _re

    root = Path(sys_v4l)
    if not root.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(root.iterdir()):
        link = entry / "device"
        try:
            target = link.resolve()
        except OSError:
            continue
        # A path SEGMENT, never a substring: a temp directory called
        # "test_usb_camera" is not a USB bus, and a check that cannot tell the
        # difference is a check that reports webcams that are not there.
        if any(part == "usb" or _re.fullmatch(r"usb\d+", part) for part in target.parts):
            out.append(entry.name)
    return out


def _check_usb_power_budget(config_text=None, cameras=None, config_path=None) -> CheckResult:
    """The carbot lesson, in software.

    On a Pi 5 at the default `usb_max_current_enable=0` the USB ports share
    600 mA. An OAK-D plus a USB speaker trips it, the kernel logs
    `over-current change`, EVERY USB device resets at once, and what the owner
    sees is a camera that stopped and a microphone that stopped, together, for
    no reason. It is invisible from software after the fact — so say it before.
    """
    if config_text is None:
        config_path, config_text = _read_boot_config()
    cameras = usb_video_devices() if cameras is None else cameras
    enabled = any(
        line.strip().replace(" ", "").startswith("usb_max_current_enable=1")
        for line in config_text.splitlines()
    )
    where = str(config_path) if config_path else "/boot/firmware/config.txt"
    if enabled:
        return CheckResult("USB power budget", "ok", f"usb_max_current_enable=1 in {where}")
    if not cameras:
        return CheckResult(
            "USB power budget",
            "warn",
            f"usb_max_current_enable is not set in {where} — USB is capped at 600 mA "
            "shared; add a USB camera or speaker and every USB device resets together",
            fix=f"echo usb_max_current_enable=1 | sudo tee -a {where} && sudo reboot",
        )
    return CheckResult(
        "USB power budget",
        "fail",
        f"USB camera(s) {', '.join(cameras)} streaming with usb_max_current_enable unset in "
        f"{where} — an over-current trip resets every USB device at once (camera AND mic die "
        "together, with nothing in the application logs)",
        fix=f"echo usb_max_current_enable=1 | sudo tee -a {where} && sudo reboot",
    )


def _check_gaps(robot: Optional[RobotUnits], collect=None) -> list[CheckResult]:
    """Ask `castor gaps`, which already understands an up-provisioned robot.

    doctor never called it and gaps never called doctor, so the one health
    check that knew how to print `pip install rc-car-actuator` was the one
    nobody ran. A missing actuator package is blocking: `resolve_actuator()`
    falls back to `noop`, which is a gateway that signs a receipt for a car
    that never moved.
    """
    if robot is None or robot.home is None:
        return [CheckResult("Capability gaps", "skip", "no robot home resolved")]
    if collect is None:
        try:
            from castor.gaps import collect as collect  # noqa: PLC0414
        except Exception as exc:  # noqa: BLE001
            return [CheckResult("Capability gaps", "skip", f"castor.gaps unavailable: {exc}")]
    try:
        gaps = collect(home=robot.home)
    except Exception as exc:  # noqa: BLE001
        return [CheckResult("Capability gaps", "skip", f"gap scan failed: {exc}")]
    if not gaps:
        return [CheckResult("Capability gaps", "ok", "none — every peripheral is claimed")]
    out: list[CheckResult] = []
    for gap in gaps:
        blocking = gap.kind == "missing-package"
        out.append(
            CheckResult(
                f"gap:{gap.id}",
                "fail" if blocking else "warn",
                gap.evidence,
                fix=gap.suggestion,
                blocking=blocking,
            )
        )
    return out


def run_robot_checks(home=None, unit_dir=None) -> DoctorReport:
    """The whole robot section, in the order an owner debugs in.

    Home and services first (is there a robot, is it answering), then the
    physical layer (bus, wheels), then the two things that are invisible until
    they bite (discovery, USB current), then the gaps rail.
    """
    report = DoctorReport()
    add = report.checks.append

    found = discover_robots(unit_dir)
    robot = resolve_robot(home=home, unit_dir=unit_dir)
    others = max(0, len(found) - 1) if robot is not None else 0

    add(_check_robot_home(robot, others=others))
    add(_check_gateway_port(robot))
    add(_check_runtime_port(robot))
    add(_check_console_port(robot))
    add(_check_gateway_policy(robot))

    policy = read_env_file(robot.policy_path) if (robot and robot.policy_path) else {}
    add(_check_i2c_bus(policy))
    add(_check_drive_mode(robot, policy))
    add(_check_pca9685_persistence(robot, policy))
    add(_check_mdns_advertiser())
    add(_check_usb_power_budget())
    for result in _check_gaps(robot):
        add(result)

    return report

"""
OpenCastor Doctor -- system health checks.

Validates the local environment: Python version, .env file, API keys,
RCAN config schema, hardware SDKs, and camera availability.

Usage:
    castor doctor
    castor doctor --config robot.rcan.yaml
"""

import os
import sys


def check_mac_seccomp():
    """Check whether daemon MAC and seccomp hardening are active."""
    try:
        from castor.daemon import daemon_security_status

        status = daemon_security_status()
    except Exception as exc:
        return False, "MAC/seccomp", f"status unavailable: {exc}"

    if not status.get("profiles_installed"):
        return False, "MAC/seccomp", "profiles not installed (/etc/opencastor/security missing)"

    apparmor = status.get("apparmor_profile") or "n/a"
    seccomp = status.get("seccomp_mode") or "n/a"
    in_unit = status.get("enabled_in_unit", False)
    seccomp_active = seccomp == "2"
    apparmor_active = apparmor not in {"n/a", "unconfined"}
    ok = bool(in_unit and seccomp_active and apparmor_active)
    detail = f"unit={'on' if in_unit else 'off'}, apparmor={apparmor}, seccomp={seccomp}"
    return ok, "MAC/seccomp", detail


def check_python_version():
    """Check Python >= 3.10."""
    ok = sys.version_info >= (3, 10)
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    detail = ver if ok else f"{ver} (requires 3.10+)"
    return ok, "Python version", detail


def check_env_file():
    """Check that a .env file exists."""
    ok = os.path.exists(".env")
    detail = "found" if ok else "missing -- run: cp .env.example .env"
    return ok, ".env file", detail


def check_provider_keys(config=None):
    """Check which AI provider keys are available.

    If *config* is provided and has ``agent.provider``, only check that
    provider.  Otherwise check all known providers.
    """
    from castor.auth import list_available_providers, load_dotenv_if_available

    load_dotenv_if_available()
    providers = list_available_providers()

    # If a specific provider is requested via config, check only that one
    if config:
        agent = config.get("agent", {})
        name = agent.get("provider", "").lower()
        if name and name in providers:
            ok = providers[name]
            detail = "key found" if ok else "no key set"
            return [(ok, f"Provider key ({name})", detail)]

    # Otherwise report all
    results = []
    for name, ready in providers.items():
        detail = "key found" if ready else "no key"
        if name == "ollama":
            detail = "no key needed" if ready else "no key"
        results.append((ready, f"Provider key ({name})", detail))
    return results


def check_cache_stats(planner) -> tuple[bool, str, str]:
    """Check prompt cache statistics from the planner provider (if Anthropic).

    Returns a (ok, name, detail) tuple suitable for the doctor report.
    """
    if planner is None:
        return True, "Prompt cache", "no planner configured"

    # Check cache stats if planner is an AnthropicProvider
    if hasattr(planner, "cache_stats"):
        stats = planner.cache_stats
        hit_rate = stats.get("hit_rate", 0)
        total_calls = stats.get("total_calls", 0)
        tokens_saved = stats.get("tokens_saved", 0)

        if total_calls == 0:
            return True, "Prompt cache", "no calls recorded yet"

        # Warm-up period: < 10 calls is inconclusive
        if total_calls < 10:
            detail = (
                f"warming up ({total_calls} calls, "
                f"hit rate {hit_rate:.1%}, {tokens_saved:,} tokens saved)"
            )
            return True, "Prompt cache", detail

        ok = hit_rate >= 0.5
        status = "healthy" if ok else "low — check system prompt stability"
        detail = (
            f"hit rate {hit_rate:.1%} ({stats.get('cache_hits', 0)}/{total_calls} calls), "
            f"{tokens_saved:,} tokens saved — {status}"
        )
        return ok, "Prompt cache", detail

    return True, "Prompt cache", "N/A (provider does not support caching)"


def check_rcan_config(config_path):
    """Validate an RCAN config file against the JSON schema."""
    if not config_path:
        return False, "RCAN config", "no config path provided"

    if not os.path.exists(config_path):
        return False, "RCAN config", f"{config_path} not found"

    try:
        import jsonschema
        import yaml

        schema_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "config", "rcan.schema.json"
        )
        if not os.path.exists(schema_path):
            return False, "RCAN config", "schema file not found"

        import json

        with open(schema_path) as f:
            schema = json.load(f)

        with open(config_path) as f:
            data = yaml.safe_load(f)

        jsonschema.validate(data, schema)
        return True, "RCAN config", f"{config_path} valid"

    except jsonschema.ValidationError as exc:
        return False, "RCAN config", f"validation error: {exc.message}"
    except Exception as exc:
        return False, "RCAN config", str(exc)


def check_hardware_sdks():
    """Try importing hardware SDKs and report which are available."""
    sdks = [
        ("dynamixel_sdk", "Dynamixel SDK"),
        ("adafruit_pca9685", "Adafruit PCA9685"),
        ("picamera2", "PiCamera2"),
        ("cv2", "OpenCV"),
        ("depthai", "DepthAI (OAK cameras)"),
        ("websocket", "WebSocket Client (ESP32)"),
        ("ev3dev2", "python-ev3dev2 (EV3)"),
        ("bleak", "Bleak BLE (SPIKE optional)"),
    ]
    results = []
    for module, label in sdks:
        try:
            __import__(module)
            results.append((True, f"SDK: {label}", "installed"))
        except ImportError:
            results.append((False, f"SDK: {label}", "not installed"))
    return results


def check_camera():
    """Quick check whether a camera is accessible via OpenCV."""
    try:
        import cv2

        cap = cv2.VideoCapture(0)
        ok = cap.isOpened()
        cap.release()
        detail = "accessible" if ok else "not accessible"
        return ok, "Camera", detail
    except ImportError:
        return False, "Camera", "OpenCV not installed"
    except Exception as exc:
        return False, "Camera", str(exc)


def check_memory_db_size() -> tuple:
    """Check the episode memory database size (Issue #280).

    Warns when the database exceeds 100 MB; passes otherwise.

    Returns:
        (ok, name, detail) tuple where ``ok`` is ``True`` when the DB is
        absent (no issue) or smaller than 100 MB.
    """
    import os
    from pathlib import Path

    db_path = os.getenv("CASTOR_MEMORY_DB") or str(Path.home() / ".castor" / "memory.db")
    if not os.path.exists(db_path):
        return True, "Memory DB", f"not found at {db_path} (will be created on first use)"
    size_mb = os.path.getsize(db_path) / (1024 * 1024)
    if size_mb > 100:
        return (
            False,
            "Memory DB",
            f"large ({size_mb:.1f} MB) — consider running `castor improve --prune` to trim",
        )
    return True, "Memory DB", f"ok ({size_mb:.1f} MB)"


def check_ble_driver() -> tuple:
    """Check whether the ESP32 BLE driver dependency (bleak) is installed (Issue #280).

    Returns:
        (ok, name, detail) tuple. ``ok`` is ``True`` regardless — bleak is
        optional, but we surface its availability for diagnostics.
    """
    try:
        import bleak  # noqa: F401

        return True, "BLE Driver (bleak)", f"available (v{bleak.__version__})"
    except ImportError:
        return (
            True,  # Not a hard failure — just informational
            "BLE Driver (bleak)",
            "not installed (optional — install with: pip install opencastor[ble])",
        )


def check_signal_channel() -> tuple:
    """Check whether the Signal channel can be imported (Issue #280).

    Returns:
        (ok, name, detail) tuple.
    """
    try:
        from castor.channels.signal_channel import SignalChannel  # noqa: F401

        return True, "Signal Channel", "importable"
    except Exception as exc:
        return False, "Signal Channel", str(exc)


# ── Runner functions ──────────────────────────────────────────────────


def check_disk_space(path: str = "/") -> tuple:
    """Check available disk space on the root partition (Issue #371).

    Warns when the partition is >90% full.

    Args:
        path: Filesystem path to check (default: ``"/"``).

    Returns:
        ``(ok, name, detail)`` tuple where ``ok`` is ``True`` when usage <90%.
    """
    import shutil

    try:
        usage = shutil.disk_usage(path)
        pct = usage.used / usage.total * 100.0
        free_gb = usage.free / (1024**3)
        if pct >= 90.0:
            return (
                False,
                "Disk space",
                f"{pct:.1f}% used ({free_gb:.1f} GB free) — partition is >90% full",
            )
        return True, "Disk space", f"{pct:.1f}% used ({free_gb:.1f} GB free)"
    except Exception as exc:
        return False, "Disk space", str(exc)


def check_memory_usage() -> tuple:
    """Check system RAM usage, warn when ≥85% used (Issue #382).

    Reads ``/proc/meminfo`` on Linux; falls back to ``psutil`` if available,
    then to ``resource.getrusage`` for a rough estimate.  Returns a safe
    ``(False, 'Memory usage', error_str)`` on any failure.

    Returns:
        ``(ok, 'Memory usage', detail_str)`` where ``ok`` is ``True`` when
        usage < 85%.
    """
    _NAME = "Memory usage"
    _THRESHOLD = 85.0

    try:
        # ── Linux: parse /proc/meminfo ────────────────────────────────────
        import os as _os

        if _os.path.exists("/proc/meminfo"):
            meminfo: dict = {}
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 2:
                        key = parts[0].rstrip(":")
                        try:
                            meminfo[key] = int(parts[1])  # kB
                        except ValueError:
                            pass
            total_kb = meminfo.get("MemTotal", 0)
            avail_kb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
            if total_kb > 0:
                used_kb = total_kb - avail_kb
                pct = used_kb / total_kb * 100.0
                free_mb = avail_kb / 1024.0
                if pct >= _THRESHOLD:
                    return (
                        False,
                        _NAME,
                        f"{pct:.1f}% used ({free_mb:.0f} MB free) — RAM >85% full",
                    )
                return True, _NAME, f"{pct:.1f}% used ({free_mb:.0f} MB free)"
    except Exception:
        pass

    try:
        # ── psutil fallback ───────────────────────────────────────────────
        import psutil as _psutil  # type: ignore[import-untyped]

        vm = _psutil.virtual_memory()
        pct = vm.percent
        free_mb = vm.available / (1024 * 1024)
        if pct >= _THRESHOLD:
            return (
                False,
                _NAME,
                f"{pct:.1f}% used ({free_mb:.0f} MB free) — RAM >85% full",
            )
        return True, _NAME, f"{pct:.1f}% used ({free_mb:.0f} MB free)"
    except ImportError:
        pass
    except Exception as exc:
        return False, _NAME, str(exc)

    try:
        # ── resource.getrusage: very rough RSS estimate ───────────────────
        import resource as _resource

        usage_bytes = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
        usage_mb = usage_bytes / 1024  # Linux: kB, macOS: bytes
        return True, _NAME, f"~{usage_mb:.0f} MB RSS (exact usage unavailable)"
    except Exception as exc:
        return False, _NAME, str(exc)


def check_gpu_memory() -> tuple:
    """Check GPU VRAM usage, warn when ≥80% used (Issue #406).

    Tries nvidia-smi first, then torch.cuda, then returns a skip result.

    Returns:
        ``(ok, 'GPU memory', detail_str)``
    """
    _NAME = "GPU memory"
    _THRESHOLD = 80.0

    # ── nvidia-smi ──────────────────────────────────────────────────────
    try:
        import subprocess as _sub

        result = _sub.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            lines = [ln.strip() for ln in result.stdout.strip().splitlines() if ln.strip()]
            if lines:
                # Take the first GPU
                parts = lines[0].split(",")
                used_mb = float(parts[0].strip())
                total_mb = float(parts[1].strip())
                if total_mb > 0:
                    pct = used_mb / total_mb * 100.0
                    free_mb = total_mb - used_mb
                    if pct >= _THRESHOLD:
                        return (
                            False,
                            _NAME,
                            f"{pct:.1f}% VRAM used ({free_mb:.0f} MB free) — GPU >80% full",
                        )
                    return True, _NAME, f"{pct:.1f}% VRAM used ({free_mb:.0f} MB free)"
    except (FileNotFoundError, Exception):
        pass

    # ── torch.cuda fallback ─────────────────────────────────────────────
    try:
        import torch as _torch  # type: ignore[import-untyped]

        if _torch.cuda.is_available():
            used_bytes = _torch.cuda.memory_allocated()
            total_bytes = _torch.cuda.get_device_properties(0).total_memory
            if total_bytes > 0:
                pct = used_bytes / total_bytes * 100.0
                free_mb = (total_bytes - used_bytes) / (1024 * 1024)
                if pct >= _THRESHOLD:
                    return (
                        False,
                        _NAME,
                        f"{pct:.1f}% VRAM used ({free_mb:.0f} MB free) — GPU >80% full",
                    )
                return True, _NAME, f"{pct:.1f}% VRAM used ({free_mb:.0f} MB free)"
    except ImportError:
        pass
    except Exception as exc:
        return False, _NAME, str(exc)

    return True, _NAME, "no GPU detected — skipping"


def check_swap_usage() -> tuple:
    """Check swap usage, warn when >50% used (Issue #412).

    Reads /proc/swaps on Linux; falls back to psutil.swap_memory().
    Returns (True, 'Swap usage', detail) when no swap or usage is fine.

    Returns:
        (ok, 'Swap usage', detail_str)
    """
    _NAME = "Swap usage"
    _THRESHOLD = 50.0

    # ── /proc/swaps (Linux) ──────────────────────────────────────────
    try:
        import os as _os

        if _os.path.exists("/proc/swaps"):
            with open("/proc/swaps", encoding="utf-8") as fh:
                lines = fh.read().strip().splitlines()
            # First line is header; each subsequent line is a swap device
            swap_lines = [ln for ln in lines[1:] if ln.strip()]
            if not swap_lines:
                return True, _NAME, "no swap configured"
            total_kb = 0
            used_kb = 0
            for ln in swap_lines:
                parts = ln.split()
                if len(parts) >= 4:
                    try:
                        total_kb += int(parts[2])
                        used_kb += int(parts[3])
                    except ValueError:
                        pass
            if total_kb > 0:
                pct = used_kb / total_kb * 100.0
                free_mb = (total_kb - used_kb) / 1024.0
                if pct >= _THRESHOLD:
                    return (
                        False,
                        _NAME,
                        f"{pct:.1f}% swap used ({free_mb:.0f} MB free) — swap >50% full",
                    )
                return True, _NAME, f"{pct:.1f}% swap used ({free_mb:.0f} MB free)"
    except Exception:
        pass

    # ── psutil fallback ──────────────────────────────────────────────
    try:
        import psutil as _psutil

        sw = _psutil.swap_memory()
        if sw.total == 0:
            return True, _NAME, "no swap configured"
        pct = sw.percent
        free_mb = (sw.total - sw.used) / (1024 * 1024)
        if pct >= _THRESHOLD:
            return (
                False,
                _NAME,
                f"{pct:.1f}% swap used ({free_mb:.0f} MB free) — swap >50% full",
            )
        return True, _NAME, f"{pct:.1f}% swap used ({free_mb:.0f} MB free)"
    except ImportError:
        pass
    except Exception as exc:
        return False, _NAME, str(exc)

    return True, _NAME, "swap info unavailable — skipping"


def run_all_checks(config_path=None):
    """Run every health check.  Returns a flat list of (ok, name, detail) tuples."""
    results = []

    results.append(check_python_version())
    results.append(check_env_file())

    # Load config if a path was given, for provider-specific checks
    config = None
    if config_path and os.path.exists(config_path):
        try:
            import yaml

            with open(config_path) as f:
                config = yaml.safe_load(f)
        except Exception:
            pass

    provider_results = check_provider_keys(config)
    results.extend(provider_results)

    if config_path:
        results.append(check_rcan_config(config_path))

    results.extend(check_hardware_sdks())
    results.append(check_camera())
    results.append(check_mac_seccomp())
    # Issue #280: additional checks
    results.append(check_memory_db_size())
    results.append(check_ble_driver())
    results.append(check_signal_channel())
    # Issue #371: disk space check
    results.append(check_disk_space())
    # Issue #382: memory usage check
    results.append(check_memory_usage())
    # Issue #406: GPU VRAM check
    results.append(check_gpu_memory())
    # Issue #412: swap usage check
    results.append(check_swap_usage())

    return results


def run_post_wizard_checks(config_path, config, provider_name):
    """Run the subset of checks relevant after wizard completion."""
    results = []

    # Validate the config just written
    results.append(check_rcan_config(config_path))

    # Check the chosen provider key
    stub_config = {"agent": {"provider": provider_name}}
    provider_results = check_provider_keys(stub_config)
    results.extend(provider_results)

    return results


# ── Auto-fix (#362) ───────────────────────────────────────────────────


def _fix_env_file() -> bool:
    """Copy .env.example → .env if .env is missing.  Returns True on fix."""
    if os.path.exists(".env"):
        print("  SKIP   .env file — already exists")
        return False
    if not os.path.exists(".env.example"):
        print("  SKIP   .env file — .env.example not found (run `castor wizard` to create)")
        return False
    import shutil

    shutil.copy(".env.example", ".env")
    print("  FIXED  .env file created from .env.example")
    print("         → Edit .env to add your API keys")
    return True


def _fix_memory_db() -> bool:
    """Prune episodes older than 30 days to reduce DB size.  Returns True on fix."""
    import sqlite3
    import time

    db_path = os.getenv("CASTOR_MEMORY_DB", os.path.expanduser("~/.castor/memory.db"))
    if not os.path.exists(db_path):
        print("  SKIP   Memory DB — not found (will be created on first use)")
        return False
    cutoff = int(time.time()) - 30 * 86400
    try:
        con = sqlite3.connect(db_path)
        cur = con.execute("DELETE FROM episodes WHERE timestamp < ?", (cutoff,))
        deleted = cur.rowcount
        con.commit()  # commit DELETE before VACUUM (VACUUM cannot run inside a transaction)
        con.execute("VACUUM")
        con.close()
        print(f"  FIXED  Memory DB — deleted {deleted} episodes older than 30 days + VACUUM")
        return True
    except Exception as exc:
        print(f"  FAIL   Memory DB — {exc}")
        return False


def run_auto_fix(results, config_path=None) -> None:
    """Attempt to auto-fix common issues found by :func:`run_all_checks`.

    Prints a line for each check, showing FIXED / SKIP / FAIL.

    Args:
        results:     List of ``(ok, name, detail)`` tuples from
                     :func:`run_all_checks`.
        config_path: Optional RCAN config path (unused currently; reserved for
                     future config-level fixes).
    """
    print("  Auto-Fix\n")
    fixed_any = False
    for ok, name, detail in results:
        if ok:
            continue
        if name == ".env file":
            if _fix_env_file():
                fixed_any = True
        elif "Memory DB" in name and "large" in detail:
            if _fix_memory_db():
                fixed_any = True
        else:
            pass  # other checks not auto-fixable

    print()
    if not fixed_any:
        print("  No automatic fixes were applied.")
        print("  For provider keys, run `castor wizard` or edit .env manually.")
    print()


# ── Output ────────────────────────────────────────────────────────────


def print_report(results, colors_class=None):
    """Print a pass/fail report.

    Uses Rich if available for styled output, otherwise falls back to
    ANSI codes via *colors_class* (e.g. the wizard's ``Colors`` class).
    """
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        table.add_column("", width=6)
        table.add_column("Check")
        table.add_column("Detail")

        passed = failed = 0
        for ok, name, detail in results:
            if ok:
                table.add_row("[green]PASS[/]", name, detail)
                passed += 1
            else:
                table.add_row("[red]FAIL[/]", name, detail)
                failed += 1

        console.print(table)
        status_color = "green" if failed == 0 else "yellow"
        console.print(f"\n  [{status_color}]{passed} passed, {failed} failed[/]")
        console.print("  Tip: Run 'castor validate --config X' for deep RCAN conformance checks.")
        return failed == 0

    except ImportError:
        pass

    # Fallback: ANSI colors
    green = getattr(colors_class, "GREEN", "")
    red = getattr(colors_class, "FAIL", "")
    end = getattr(colors_class, "ENDC", "")

    passed = failed = 0
    for ok, name, detail in results:
        if ok:
            tag = f"{green}PASS{end}"
            passed += 1
        else:
            tag = f"{red}FAIL{end}"
            failed += 1
        print(f"  [{tag}] {name}: {detail}")

    print(f"\n  {passed} passed, {failed} failed")
    print("  Tip: Run 'castor validate --config X' for deep RCAN conformance checks.")
    return failed == 0

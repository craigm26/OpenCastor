"""
OpenCastor Update Check -- check PyPI for newer versions.

Runs silently on startup and prints a one-line hint if an update
is available.  Caches the result to avoid hitting PyPI on every run.

Usage:
    castor update-check           # Explicit check
    # Also runs automatically on `castor run` / `castor gateway`
"""

import json
import logging
import os
import time

logger = logging.getLogger("OpenCastor.UpdateCheck")

_CACHE_FILE = os.path.expanduser("~/.opencastor/update-cache.json")
_CACHE_TTL = 3600 * 6  # 6 hours


def check_for_update(quiet: bool = False) -> dict:
    """Check PyPI for a newer version of OpenCastor.

    Args:
        quiet: If True, only return the result dict without printing.

    Returns:
        Dict with keys: current, latest, update_available.
    """
    from castor import __version__ as current

    result = {
        "current": current,
        "latest": current,
        "update_available": False,
    }

    # Check cache first
    cached = _read_cache()
    if cached and cached.get("current") == current:
        result["latest"] = cached.get("latest", current)
        result["update_available"] = cached.get("update_available", False)
        if not quiet and result["update_available"]:
            _print_hint(current, result["latest"])
        return result

    # Query PyPI
    try:
        import httpx
        resp = httpx.get(
            "https://pypi.org/pypi/opencastor/json",
            timeout=3.0,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            data = resp.json()
            latest = data.get("info", {}).get("version", current)
            result["latest"] = latest
            result["update_available"] = _is_newer(latest, current)
            _write_cache(result)
    except Exception:
        pass  # Network errors are expected -- fail silently

    if not quiet and result["update_available"]:
        _print_hint(current, result["latest"])

    return result


def _is_newer(latest: str, current: str) -> bool:
    """Compare version strings (supports semver and date-based versions)."""
    try:
        from packaging.version import Version
        return Version(latest) > Version(current)
    except Exception:
        pass

    # Fallback: string comparison (works for date-based versions)
    return latest > current


def _print_hint(current: str, latest: str):
    """Print a one-line update hint."""
    try:
        from rich.console import Console
        console = Console(stderr=True)
        console.print(
            f"  [dim]Update available: {current} -> {latest} "
            f"(run: castor upgrade)[/]"
        )
    except ImportError:
        import sys
        print(
            f"  Update available: {current} -> {latest} "
            f"(run: castor upgrade)",
            file=sys.stderr,
        )


def _read_cache() -> dict:
    """Read cached update check result."""
    try:
        if not os.path.exists(_CACHE_FILE):
            return None
        with open(_CACHE_FILE) as f:
            data = json.load(f)
        # Check TTL
        if time.time() - data.get("checked_at", 0) > _CACHE_TTL:
            return None
        return data
    except Exception:
        return None


def _write_cache(result: dict):
    """Cache update check result."""
    try:
        cache_dir = os.path.dirname(_CACHE_FILE)
        os.makedirs(cache_dir, exist_ok=True)
        data = {**result, "checked_at": time.time()}
        with open(_CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def print_update_status():
    """Print full update status (for explicit `castor update-check`)."""
    result = check_for_update(quiet=True)

    try:
        from rich.console import Console
        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False
        console = None

    if has_rich:
        console.print(f"\n[bold cyan]  OpenCastor Update Check[/]\n")
        console.print(f"  Current version:  {result['current']}")
        console.print(f"  Latest on PyPI:   {result['latest']}")
        if result["update_available"]:
            console.print(f"\n  [yellow]Update available![/]")
            console.print(f"  Run: [cyan]castor upgrade[/]\n")
        else:
            console.print(f"\n  [green]You're up to date.[/]\n")
    else:
        print(f"\n  OpenCastor Update Check\n")
        print(f"  Current version:  {result['current']}")
        print(f"  Latest on PyPI:   {result['latest']}")
        if result["update_available"]:
            print(f"\n  Update available! Run: castor upgrade\n")
        else:
            print(f"\n  You're up to date.\n")

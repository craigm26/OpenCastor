"""castor.updater — self-update from PyPI or git."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

try:
    from packaging.version import Version
    HAS_PACKAGING = True
except ImportError:
    HAS_PACKAGING = False

try:
    import httpx as _httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


@dataclass
class VersionInfo:
    current: str
    latest: str
    up_to_date: bool
    release_url: str


def _current_version() -> str:
    try:
        from importlib.metadata import version
        return version("opencastor")
    except Exception:
        try:
            import castor
            return getattr(castor, "__version__", "unknown")
        except Exception:
            return "unknown"


def check_latest_pypi(package: str = "opencastor", timeout: int = 8) -> Optional[str]:
    """Return latest version string from PyPI or None on failure."""
    if HAS_HTTPX:
        try:
            r = _httpx.get(f"https://pypi.org/pypi/{package}/json", timeout=timeout)
            if r.status_code == 200:
                return r.json()["info"]["version"]
        except Exception:
            pass
    # stdlib fallback
    try:
        import json as _json
        import urllib.request  # noqa: E401
        with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/json", timeout=timeout) as resp:
            return _json.loads(resp.read())["info"]["version"]
    except Exception:
        return None


def get_version_info(package: str = "opencastor") -> VersionInfo:
    current = _current_version()
    latest = check_latest_pypi(package) or current
    up_to_date = True
    if HAS_PACKAGING and current != "unknown":
        try:
            up_to_date = Version(current) >= Version(latest)
        except Exception:
            up_to_date = current == latest
    else:
        up_to_date = current == latest
    return VersionInfo(
        current=current,
        latest=latest,
        up_to_date=up_to_date,
        release_url=f"https://github.com/craigm26/OpenCastor/releases/tag/v{latest}",
    )


def do_upgrade(package: str = "opencastor", yes: bool = False) -> int:
    """Upgrade via pip. Returns exit code."""
    if not yes:
        resp = input(f"Upgrade {package} to latest? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted.")
            return 0
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", package]
    result = subprocess.run(cmd, timeout=120)
    return result.returncode

"""
SDK compatibility check for castor register.
Verifies rcan SDK versions are compatible with the spec before registering.
"""

from __future__ import annotations

import json
import re
import urllib.request

SPEC_VERSION = "1.3"
RCAN_DEV_COMPAT_URL = "https://rcan.dev/api/v1/compatibility"  # if available
RCAN_DEV_STATUS_URL = "https://rcan.dev/public/sdk-status.json"


def check_sdk_compat(rcan_version: str = SPEC_VERSION) -> dict:
    """
    Check if installed SDK versions are compatible with the spec.
    Returns dict: {compatible: bool, warnings: list[str], info: dict}
    """
    warnings = []
    info = {}

    # Check rcan Python package
    try:
        import rcan

        rcan_py_version = getattr(rcan, "__version__", "unknown")
        info["rcan_py"] = rcan_py_version
        # Check compatibility (simple semver major match)
        if not _versions_compatible(rcan_py_version, "0.3.0"):
            warnings.append(f"rcan Python SDK {rcan_py_version} may be outdated (expected >=0.3.0)")
    except ImportError:
        warnings.append("rcan Python SDK not installed — install with: pip install rcan")
        info["rcan_py"] = None

    # Check rcan-validate is available
    try:
        import subprocess

        result = subprocess.run(
            ["rcan-validate", "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            info["rcan_validate"] = result.stdout.strip()
        else:
            warnings.append("rcan-validate not working correctly")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        warnings.append("rcan-validate not found — install with: pip install rcan")
        info["rcan_validate"] = None

    # Try to fetch live compat status from rcan.dev
    try:
        req = urllib.request.Request(RCAN_DEV_STATUS_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = json.loads(resp.read())
            info["sdk_status"] = status
    except Exception:
        pass  # not critical

    compatible = len([w for w in warnings if "not installed" in w]) == 0
    return {"compatible": compatible, "warnings": warnings, "info": info}


def _versions_compatible(installed: str, minimum: str) -> bool:
    """Simple version comparison — returns True if installed >= minimum."""

    def parse(v: str) -> tuple:
        try:
            return tuple(int(x) for x in re.findall(r"\d+", v)[:3])
        except Exception:
            return (0,)

    return parse(installed) >= parse(minimum)


def validate_before_register(config: dict, strict: bool = False) -> tuple[bool, list[str]]:
    """
    Run pre-registration validation. Returns (ok, list_of_issues).
    If strict=True, SDK absence is a hard failure. Otherwise just a warning.
    """
    issues: list[str] = []
    result = check_sdk_compat()

    if not result["compatible"] and strict:
        issues.extend(result["warnings"])
        return False, issues

    issues.extend(result["warnings"])  # warnings, not blockers

    # Validate the RCAN config itself
    try:
        from rcan.validate import validate_config  # type: ignore[import]

        valid, errors = validate_config(config)
        if not valid:
            issues.extend([f"Config error: {e}" for e in errors])
            return False, issues
    except ImportError:
        issues.append("rcan SDK not available for config validation")

    return True, issues

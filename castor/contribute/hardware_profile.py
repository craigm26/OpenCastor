"""Hardware profile detection for contribute skill."""

from __future__ import annotations

import os


def get_hw_profile() -> dict:
    """Return hardware capabilities relevant to contribution."""
    profile: dict = {"cpu_cores": os.cpu_count() or 1}
    try:
        import subprocess

        r = subprocess.run(
            ["hailortcli", "fw-control", "identify"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            profile["npu"] = "hailo-8l"
            profile["tops"] = 26
    except Exception:
        pass
    return profile

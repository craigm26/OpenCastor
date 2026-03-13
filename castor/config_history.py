"""Config diff and rollback for OpenCastor (issue #146).

Tracks up to 20 versioned snapshots of robot.rcan.yaml automatically on
every POST /api/config/reload.  Supports one-click rollback to any version.

Usage::

    from castor.config_history import get_history

    hist = get_history()
    hist.record(config_dict, config_path="/path/to/robot.rcan.yaml")
    versions = hist.list()
    hist.rollback(version_id, config_path="/path/to/robot.rcan.yaml")

REST API:
    GET  /api/config/history                 — list version summaries
    POST /api/config/rollback {version_id}   — restore a version
"""

import copy
import difflib
import logging
import time
import uuid
from collections import deque
from typing import Any, Optional

import yaml

logger = logging.getLogger("OpenCastor.ConfigHistory")

_MAX_VERSIONS = 20


def _config_summary(config: dict[str, Any]) -> dict[str, Any]:
    """Extract a brief summary of a config for display."""
    agent = config.get("agent", {}) or {}
    meta = config.get("metadata", {}) or {}
    drivers = config.get("drivers", []) or []
    return {
        "robot_name": meta.get("robot_name", "unknown"),
        "provider": agent.get("provider", "unknown"),
        "model": agent.get("model", "unknown"),
        "driver_count": len(drivers),
    }


class ConfigHistoryManager:
    """Stores versioned config snapshots and supports rollback.

    Args:
        max_versions: Maximum snapshots to retain.
    """

    def __init__(self, max_versions: int = _MAX_VERSIONS):
        self._versions: deque[dict[str, Any]] = deque(maxlen=max_versions)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        config: dict[str, Any],
        config_path: str = "robot.rcan.yaml",
        label: str = "",
    ) -> str:
        """Store a versioned snapshot of *config*.

        Args:
            config: Config dict (deep-copied for safety).
            config_path: Source file path (stored for reference).
            label: Optional human-readable label.

        Returns:
            The version_id string.
        """
        version_id = f"v{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        entry: dict[str, Any] = {
            "version_id": version_id,
            "timestamp": time.time(),
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "config_path": config_path,
            "label": label or f"reload at {time.strftime('%H:%M:%S')}",
            "summary": _config_summary(config),
            "config": copy.deepcopy(config),
        }
        self._versions.append(entry)
        logger.info("Config version recorded: id=%s path=%s", version_id, config_path)
        return version_id

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        """Return version summaries (newest first, config excluded)."""
        result = []
        for entry in reversed(list(self._versions)):
            d = {k: v for k, v in entry.items() if k != "config"}
            result.append(d)
        return result

    def get(self, version_id: str) -> Optional[dict[str, Any]]:
        """Return the full version entry (including config) for *version_id*."""
        for entry in self._versions:
            if entry["version_id"] == version_id:
                return entry
        return None

    def diff(self, version_id_a: str, version_id_b: str) -> str:
        """Return a unified diff between two config versions.

        Args:
            version_id_a: Earlier version ID.
            version_id_b: Later version ID.

        Returns:
            Unified diff string (empty string if no differences).
        """
        a = self.get(version_id_a)
        b = self.get(version_id_b)
        if a is None or b is None:
            raise ValueError("One or both version IDs not found")
        yaml_a = yaml.dump(a["config"], default_flow_style=False).splitlines(keepends=True)
        yaml_b = yaml.dump(b["config"], default_flow_style=False).splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                yaml_a,
                yaml_b,
                fromfile=f"{version_id_a} ({a['timestamp_iso']})",
                tofile=f"{version_id_b} ({b['timestamp_iso']})",
            )
        )

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def rollback(
        self,
        version_id: str,
        config_path: str = "robot.rcan.yaml",
    ) -> dict[str, Any]:
        """Restore a config version by writing it back to disk.

        Args:
            version_id: The version to restore.
            config_path: Path to write the YAML to.

        Returns:
            The restored config dict.

        Raises:
            ValueError: If version_id not found.
            OSError: If the file cannot be written.
        """
        entry = self.get(version_id)
        if entry is None:
            raise ValueError(f"Version '{version_id}' not found")

        restored = copy.deepcopy(entry["config"])
        with open(config_path, "w") as f:
            yaml.dump(restored, f, default_flow_style=False)

        logger.info("Config rolled back to version %s → %s", version_id, config_path)
        return restored

    def clear(self) -> None:
        """Clear all stored versions (useful for tests)."""
        self._versions.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_history: Optional[ConfigHistoryManager] = None


def get_history() -> ConfigHistoryManager:
    """Return the process-wide ConfigHistoryManager."""
    global _history
    if _history is None:
        _history = ConfigHistoryManager()
    return _history

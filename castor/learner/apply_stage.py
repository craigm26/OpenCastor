"""Apply Stage — applies approved patches and supports rollback."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from .patches import BehaviorPatch, ConfigPatch, Patch
from .qa_stage import QAResult

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

DEFAULT_CONFIG_DIR = Path.home() / ".opencastor"
HISTORY_FILE = DEFAULT_CONFIG_DIR / "improvement_history.json"
BEHAVIORS_FILE = DEFAULT_CONFIG_DIR / "learned_behaviors.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML file, falling back to empty dict."""
    try:
        import yaml
    except ImportError:
        # Fallback: treat as JSON if PyYAML not available
        if path.exists():
            return json.loads(path.read_text())
        return {}
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    """Save data as YAML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml

        with open(path, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False)
    except ImportError:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


class ApplyStage:
    """Applies approved patches to configuration and behavior files."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self.config_dir = config_dir or DEFAULT_CONFIG_DIR
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.config_dir / "improvement_history.json"
        self.behaviors_file = self.config_dir / "learned_behaviors.yaml"

    def apply(self, patch: Patch, qa_result: QAResult) -> bool:
        """Apply a patch if QA approved it. Returns True on success."""
        if not qa_result.approved:
            logger.info("Patch %s rejected by QA", patch.id)
            return False

        try:
            if isinstance(patch, ConfigPatch):
                self._apply_config(patch)
            elif isinstance(patch, BehaviorPatch):
                self._apply_behavior(patch)
            else:
                logger.warning("Unsupported patch type: %s", patch.type)
                return False

            patch.applied = True
            self._log_history(patch, success=True)
            return True
        except Exception as e:
            logger.error("Failed to apply patch %s: %s", patch.id, e)
            self._log_history(patch, success=False, error=str(e))
            return False

    def rollback(self, patch_id: str) -> bool:
        """Rollback a previously applied patch by restoring old values."""
        history = self._load_history()
        entry = None
        for item in history:
            if item.get("patch_id") == patch_id and item.get("success"):
                entry = item
                break

        if not entry:
            logger.warning("No applied patch found with id %s", patch_id)
            return False

        patch_data = entry.get("patch", {})
        patch_type = patch_data.get("type")

        try:
            if patch_type == "config":
                config_path = self.config_dir / patch_data.get("file", "config.yaml")
                config = _load_yaml(config_path)
                config[patch_data["key"]] = patch_data.get("old_value")
                _save_yaml(config_path, config)
            elif patch_type == "behavior":
                behaviors = _load_yaml(self.behaviors_file)
                rules = behaviors.get("rules", [])
                behaviors["rules"] = [
                    r for r in rules if r.get("rule_name") != patch_data.get("rule_name")
                ]
                _save_yaml(self.behaviors_file, behaviors)

            # Mark rollback in history
            entry["rolled_back"] = True
            self._save_history(history)
            return True
        except Exception as e:
            logger.error("Rollback failed for %s: %s", patch_id, e)
            return False

    def _apply_config(self, patch: ConfigPatch) -> None:
        config_path = self.config_dir / (patch.file or "config.yaml")
        config = _load_yaml(config_path)
        config[patch.key] = patch.new_value
        _save_yaml(config_path, config)

    def _apply_behavior(self, patch: BehaviorPatch) -> None:
        behaviors = _load_yaml(self.behaviors_file)
        if "rules" not in behaviors:
            behaviors["rules"] = []
        behaviors["rules"].append(
            {
                "rule_name": patch.rule_name,
                "conditions": patch.conditions,
                "action": patch.action,
                "priority": patch.priority,
                "patch_id": patch.id,
            }
        )
        _save_yaml(self.behaviors_file, behaviors)

    def _log_history(self, patch: Patch, success: bool, error: Optional[str] = None) -> None:
        import time

        history = self._load_history()
        history.append(
            {
                "patch_id": patch.id,
                "patch": patch.to_dict(),
                "success": success,
                "error": error,
                "timestamp": time.time(),
            }
        )
        self._save_history(history)

    def _load_history(self) -> list[dict[str, Any]]:
        if self.history_file.exists():
            try:
                return json.loads(self.history_file.read_text())
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def _save_history(self, history: list[dict[str, Any]]) -> None:
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.history_file.write_text(json.dumps(history, indent=2))

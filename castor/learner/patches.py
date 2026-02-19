"""Patch types for self-improvement."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Patch:
    """Base class for all improvement patches."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = "base"
    rationale: str = ""
    created_at: float = field(default_factory=time.time)
    applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "rationale": self.rationale,
            "created_at": self.created_at,
            "applied": self.applied,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Patch:
        patch_type = data.get("type", "base")
        if patch_type == "config":
            return ConfigPatch.from_dict(data)
        if patch_type == "behavior":
            return BehaviorPatch.from_dict(data)
        if patch_type == "prompt":
            return PromptPatch.from_dict(data)
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            type=patch_type,
            rationale=data.get("rationale", ""),
            created_at=data.get("created_at", 0.0),
            applied=data.get("applied", False),
        )


@dataclass
class ConfigPatch(Patch):
    """Patch that modifies a configuration value."""

    type: str = "config"
    file: str = ""
    key: str = ""
    old_value: Any = None
    new_value: Any = None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            {
                "file": self.file,
                "key": self.key,
                "old_value": self.old_value,
                "new_value": self.new_value,
            }
        )
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConfigPatch:  # type: ignore[override]
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            rationale=data.get("rationale", ""),
            created_at=data.get("created_at", 0.0),
            applied=data.get("applied", False),
            file=data.get("file", ""),
            key=data.get("key", ""),
            old_value=data.get("old_value"),
            new_value=data.get("new_value"),
        )


@dataclass
class BehaviorPatch(Patch):
    """Patch that adds or modifies a behavior rule."""

    type: str = "behavior"
    rule_name: str = ""
    conditions: dict[str, Any] = field(default_factory=dict)
    action: dict[str, Any] = field(default_factory=dict)
    priority: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            {
                "rule_name": self.rule_name,
                "conditions": self.conditions,
                "action": self.action,
                "priority": self.priority,
            }
        )
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BehaviorPatch:  # type: ignore[override]
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            rationale=data.get("rationale", ""),
            created_at=data.get("created_at", 0.0),
            applied=data.get("applied", False),
            rule_name=data.get("rule_name", ""),
            conditions=data.get("conditions", {}),
            action=data.get("action", {}),
            priority=data.get("priority", 0),
        )


@dataclass
class PromptPatch(Patch):
    """Patch that modifies a prompt template."""

    type: str = "prompt"
    layer: str = ""
    old_template: str = ""
    new_template: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            {
                "layer": self.layer,
                "old_template": self.old_template,
                "new_template": self.new_template,
            }
        )
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromptPatch:  # type: ignore[override]
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            rationale=data.get("rationale", ""),
            created_at=data.get("created_at", 0.0),
            applied=data.get("applied", False),
            layer=data.get("layer", ""),
            old_template=data.get("old_template", ""),
            new_template=data.get("new_template", ""),
        )

"""
Fleet group policies for OpenCastor (issue #442).

Allows applying RCAN config overrides to named groups of robots.
Groups are defined in robot.rcan.yaml under `fleet.groups`.

Config (robot.rcan.yaml):
    fleet:
      groups:
        production:
          robots: ["RRN-00000001", "RRN-00000002"]
          policy:
            agent:
              confidence_gates: [{threshold: 0.92}]
              hitl_gates: [{action_types: ["stop_emergency"]}]
            rcan_protocol:
              jwt_auth: {enabled: true}
        staging:
          robots: ["RRN-00000003"]
          policy:
            agent:
              confidence_gates: [{threshold: 0.7}]

Usage:
    from castor.fleet.group_policy import FleetManager

    fm = FleetManager.from_config(config)
    merged = fm.resolve_config("RRN-00000001", base_config)
    groups = fm.get_robot_groups("RRN-00000001")
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class GroupPolicy:
    """A named policy group with a robot list and config overrides."""

    name: str
    robots: list[str] = field(default_factory=list)
    policy: dict = field(default_factory=dict)
    description: str = ""
    tags: list[str] = field(default_factory=list)
    enabled: bool = True

    def matches(self, rrn: str) -> bool:
        """Return True if this group applies to the given RRN (case-insensitive)."""
        if not self.enabled:
            return False
        rrn_norm = rrn.upper().strip()
        return any(r.upper().strip() == rrn_norm for r in self.robots)

    def matches_any(self, rrns: list[str]) -> bool:
        return any(self.matches(r) for r in rrns)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "robots": self.robots,
            "policy": self.policy,
            "description": self.description,
            "tags": self.tags,
            "enabled": self.enabled,
        }


class FleetManager:
    """
    Manages fleet group policies and config resolution.

    Policies are applied in definition order; later groups override earlier ones.
    Deep-merge semantics: nested dicts are merged, scalars are replaced.
    """

    def __init__(self, groups: list[GroupPolicy] | None = None) -> None:
        self._groups: list[GroupPolicy] = groups or []

    @classmethod
    def from_config(cls, config: dict) -> FleetManager:
        """Build a FleetManager from a robot.rcan.yaml config dict."""
        fleet_cfg = config.get("fleet", {})
        groups_cfg = fleet_cfg.get("groups", {})
        groups = []
        for name, group_data in (groups_cfg or {}).items():
            if isinstance(group_data, dict):
                groups.append(
                    GroupPolicy(
                        name=name,
                        robots=group_data.get("robots", []),
                        policy=group_data.get("policy", {}),
                        description=group_data.get("description", ""),
                        tags=group_data.get("tags", []),
                        enabled=group_data.get("enabled", True),
                    )
                )
            else:
                logger.warning(
                    "FleetManager: skipping malformed group entry '%s' (expected dict, got %s)",
                    name,
                    type(group_data).__name__,
                )
        return cls(groups=groups)

    def get_robot_groups(self, rrn: str) -> list[GroupPolicy]:
        """Return all groups that contain this robot."""
        return [g for g in self._groups if g.matches(rrn)]

    def resolve_config(self, rrn: str, base_config: dict) -> dict:
        """
        Return a merged config for the given robot.

        Applies all matching group policies on top of base_config via deep-merge.
        """
        result = copy.deepcopy(base_config)
        for group in self.get_robot_groups(rrn):
            result = _deep_merge(result, group.policy)
            logger.debug("Applied group policy '%s' to %s", group.name, rrn)
        return result

    def apply_to_all(self, rrns: list[str], base_config: dict) -> dict[str, dict]:
        """
        Return per-robot resolved configs for a list of RRNs.

        Returns {rrn: merged_config}.
        """
        return {rrn: self.resolve_config(rrn, base_config) for rrn in rrns}

    def list_groups(self) -> list[GroupPolicy]:
        return list(self._groups)

    def add_group(self, group: GroupPolicy) -> None:
        self._groups.append(group)

    def remove_group(self, name: str) -> bool:
        before = len(self._groups)
        self._groups = [g for g in self._groups if g.name != name]
        return len(self._groups) < before

    def add_robot_to_group(self, group_name: str, rrn: str) -> bool:
        for g in self._groups:
            if g.name == group_name:
                if rrn not in g.robots:
                    g.robots.append(rrn)
                return True
        return False

    def remove_robot_from_group(self, group_name: str, rrn: str) -> bool:
        for g in self._groups:
            if g.name == group_name:
                if rrn in g.robots:
                    g.robots.remove(rrn)
                    return True
        return False

    def to_dict(self) -> dict:
        return {"groups": {g.name: g.to_dict() for g in self._groups}}

    def summary(self) -> str:
        lines = [f"Fleet: {len(self._groups)} group(s)"]
        for g in self._groups:
            status = "✅" if g.enabled else "⏸️"
            lines.append(f"  {status} {g.name}: {len(g.robots)} robot(s)")
        return "\n".join(lines)


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Deep-merge override into base. Dicts are recursively merged; scalars replaced.
    Lists are replaced (not extended) to keep policy semantics clear.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result

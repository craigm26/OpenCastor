"""Orchestration patterns for AgentHarness (#742)."""

from __future__ import annotations

import abc
import json
from pathlib import Path
from typing import Any


class PatternBase(abc.ABC):
    """Base class for orchestration patterns."""

    @abc.abstractmethod
    def run(self, **kwargs: Any) -> dict[str, Any]: ...

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> PatternBase:
        return cls()


class SingleAgentSupervisor(PatternBase):
    """Default: single agent with supervisor retry loop."""

    def __init__(self, max_retries: int = 3) -> None:
        self.max_retries = max_retries

    def run(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "pattern": "single_agent_supervisor",
            "status": "ok",
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> SingleAgentSupervisor:
        return cls(max_retries=cfg.get("max_retries", 3))


class InitializerExecutor(PatternBase):
    """Two-phase: init writes JSON ledger, executor reads it to skip preamble."""

    def __init__(self, ledger_dir: str = "/tmp") -> None:
        self.ledger_dir = ledger_dir

    def ledger_path(self, session_id: str) -> Path:
        return Path(self.ledger_dir) / f"castor_ledger_{session_id}.json"

    def write_ledger(self, session_id: str, data: dict[str, Any]) -> Path:
        p = self.ledger_path(session_id)
        p.write_text(json.dumps(data))
        return p

    def read_ledger(self, session_id: str) -> dict[str, Any]:
        p = self.ledger_path(session_id)
        return json.loads(p.read_text()) if p.exists() else {}

    def run(self, **kwargs: Any) -> dict[str, Any]:
        return {"pattern": "initializer_executor", "status": "ok"}

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> InitializerExecutor:
        return cls(ledger_dir=cfg.get("ledger_dir", "/tmp"))


class MultiAgent(PatternBase):
    """Named roles (planner/executor/verifier), sequential or parallel."""

    DEFAULT_ROLES: list[str] = ["planner", "executor", "verifier"]

    def __init__(self, roles: list[str] | None = None, mode: str = "sequential") -> None:
        self.roles = roles or list(self.DEFAULT_ROLES)
        self.mode = mode

    def run(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "pattern": "multi_agent",
            "roles": self.roles,
            "mode": self.mode,
            "status": "ok",
        }

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> MultiAgent:
        return cls(roles=cfg.get("roles"), mode=cfg.get("mode", "sequential"))


PATTERN_REGISTRY: dict[str, type[PatternBase]] = {
    "single_agent_supervisor": SingleAgentSupervisor,
    "initializer_executor": InitializerExecutor,
    "multi_agent": MultiAgent,
}


def get_pattern(cfg: dict[str, Any]) -> PatternBase:
    """Instantiate a pattern from a config dict."""
    name = cfg.get("name", "single_agent_supervisor")
    cls = PATTERN_REGISTRY.get(name, SingleAgentSupervisor)
    return cls.from_config(cfg)

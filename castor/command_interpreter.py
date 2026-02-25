"""Intermediate command interpreter for channel/voice pathways.

Produces structured intents plus safety rationale before command execution,
supports dry-run confirmation flows, and tracks explanation IDs mapped to
policy decision records.
"""

from __future__ import annotations

import itertools
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OpenCastor.CommandInterpreter")

_INTENT_ROUTING: List[tuple[str, str]] = [
    ("grasp", "manipulator"),
    ("grab", "manipulator"),
    ("pick up", "manipulator"),
    ("pick", "manipulator"),
    ("place", "manipulator"),
    ("put down", "manipulator"),
    ("put", "manipulator"),
    ("push", "manipulator"),
    ("arm", "manipulator"),
    ("emergency", "guardian"),
    ("estop", "guardian"),
    ("e-stop", "guardian"),
    ("halt", "guardian"),
    ("stop", "guardian"),
    ("abort", "guardian"),
    ("navigate", "navigator"),
    ("patrol", "navigator"),
    ("go to", "navigator"),
    ("move to", "navigator"),
    ("go", "navigator"),
    ("move", "navigator"),
    ("turn", "navigator"),
    ("forward", "navigator"),
    ("backward", "navigator"),
    ("left", "navigator"),
    ("right", "navigator"),
    ("detect", "observer"),
    ("scan", "observer"),
    ("watch", "observer"),
    ("observe", "observer"),
    ("look", "observer"),
    ("what do you see", "observer"),
    ("status", "communicator"),
    ("report", "communicator"),
    ("help", "communicator"),
    ("what can you", "communicator"),
]


class CommandInterpreter:
    """Shared command interpreter used by channel/voice/agent pathways."""

    def __init__(self):
        self._explain_counter = itertools.count(1)
        self._decision_records: Dict[str, Dict[str, Any]] = {}

    @property
    def decision_records(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._decision_records)

    def parse_intent(self, text: str) -> Dict[str, Any]:
        lower = (text or "").lower().strip()
        for keyword, target in _INTENT_ROUTING:
            if keyword in lower:
                return {"keyword": keyword, "target_agent": target}
        return {"keyword": "unknown", "target_agent": None}

    def _policy_check(self, text: str) -> Dict[str, Any]:
        lower = (text or "").lower()
        blocked = "restricted lab" in lower or (
            "restricted" in lower and any(k in lower for k in ("enter", "go", "move"))
        )
        explanation_id = f"EXP-{next(self._explain_counter):05d}"

        if blocked:
            record = {
                "policy": "access_control.restricted_area",
                "decision": "deny",
                "rationale": "Command attempts entry into restricted area without authorization.",
                "alternatives": [
                    "Wait at the lab door and notify an authorized operator.",
                    "Provide status update from current safe location.",
                ],
            }
            self._decision_records[explanation_id] = record
            return {
                "allowed": False,
                "policy_id": record["policy"],
                "rationale": record["rationale"],
                "explanation_id": explanation_id,
                "alternatives": record["alternatives"],
            }

        record = {
            "policy": "access_control.default",
            "decision": "allow",
            "rationale": "No policy constraints triggered.",
            "alternatives": [],
        }
        self._decision_records[explanation_id] = record
        return {
            "allowed": True,
            "policy_id": record["policy"],
            "rationale": record["rationale"],
            "explanation_id": explanation_id,
            "alternatives": [],
        }

    def build_plan(self, text: str, intent: Dict[str, Any]) -> List[str]:
        kw = intent.get("keyword") or "command"
        target = intent.get("target_agent") or "system"
        return [
            f"Interpret request as '{kw}' intent.",
            f"Route to {target} subsystem.",
            "Execute action safely and report outcome.",
        ]

    def interpret(self, text: str, *, dry_run: bool = False) -> Dict[str, Any]:
        intent = self.parse_intent(text)
        safety = self._policy_check(text)
        plan = self.build_plan(text, intent)
        return {
            "intent": intent,
            "safety": safety,
            "plan": plan,
            "dry_run": dry_run,
            "execution_allowed": safety["allowed"],
        }


_interpreter_singleton: Optional[CommandInterpreter] = None


def get_command_interpreter() -> CommandInterpreter:
    global _interpreter_singleton
    if _interpreter_singleton is None:
        _interpreter_singleton = CommandInterpreter()
    return _interpreter_singleton

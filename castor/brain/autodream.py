"""autoDream LLM brain — nightly KAIROS memory consolidation.

Reads session logs and health data, distills learnings, and updates
robot-memory.md with structured insights. Designed to run nightly via
autodream.sh and castor.brain.autodream_runner.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from castor.providers.base import BaseProvider

logger = logging.getLogger("OpenCastor.AutoDream")

# Module-level constant — byte-for-byte stable across calls → cache-eligible.
AUTODREAM_SYSTEM_PROMPT = (
    "You are the autoDream brain for an OpenCastor robot. Your job is to:\n"
    "1. Read recent session logs and health data\n"
    "2. Distill new learnings (patterns, recurring errors, calibration notes)\n"
    "3. Update the robot memory file with only what is NEW and USEFUL — prune stale entries\n"
    "4. Identify actionable issues that warrant a GitHub issue or PR\n"
    "5. Be concise — robot-memory.md should stay under 200 lines\n"
    "Format your response as JSON with keys: updated_memory, learnings, issues_detected, summary"
)


@dataclass
class DreamSession:
    """Input data for a single autoDream run."""

    session_logs: list[str]
    robot_memory: str
    health_report: dict
    date: str


@dataclass
class DreamResult:
    """Output of a single autoDream run."""

    updated_memory: str
    learnings: list[str] = field(default_factory=list)
    issues_detected: list[str] = field(default_factory=list)
    summary: str = ""


class AutoDreamBrain:
    """LLM-powered nightly memory consolidation brain.

    Wraps any BaseProvider to distill session logs and health data into
    structured robot-memory.md updates and learning summaries.
    """

    def __init__(self, provider: "BaseProvider") -> None:
        self._provider = provider

    def run(self, session: DreamSession) -> DreamResult:
        """Run the dream cycle for *session*.

        Calls the provider with the stable AUTODREAM_SYSTEM_PROMPT (cache-
        eligible) and the session-specific user prompt, then parses the JSON
        response into a DreamResult.

        Falls back to a DreamResult preserving the original memory on any
        error — memory is never corrupted.
        """
        user_prompt = self._build_session_prompt(session)

        try:
            # Temporarily swap in the autoDream system prompt so the provider
            # uses it for this call, then restore the original.
            original_system = self._provider.system_prompt
            self._provider.system_prompt = AUTODREAM_SYSTEM_PROMPT
            try:
                thought = self._provider.think(b"", user_prompt, surface="terminal")
            finally:
                self._provider.system_prompt = original_system

            raw_text = thought.raw_text
            parsed = self._parse_response(raw_text)
            if parsed is not None:
                return parsed

            logger.warning(
                "AutoDream: could not parse LLM response — falling back to original memory"
            )
        except Exception as exc:
            logger.error("AutoDream: provider error — falling back to original memory: %s", exc)

        return DreamResult(
            updated_memory=session.robot_memory,
            learnings=[],
            issues_detected=[],
            summary="autoDream brain unavailable — memory unchanged.",
        )

    def _build_session_prompt(self, session: DreamSession) -> str:
        """Build the user-turn prompt from *session* data."""
        error_lines = "\n".join(session.session_logs[-50:]) if session.session_logs else "(none)"
        return (
            "<dream-session>\n"
            f"<date>{session.date}</date>\n"
            f"<health>{json.dumps(session.health_report)}</health>\n"
            "<recent-errors>\n"
            f"{error_lines}\n"
            "</recent-errors>\n"
            "<current-memory>\n"
            f"{session.robot_memory}\n"
            "</current-memory>\n"
            "</dream-session>\n"
            "\n"
            "Analyze the above and return updated robot memory + learnings as JSON."
        )

    def _parse_response(self, text: str) -> DreamResult | None:
        """Parse a JSON LLM response into a DreamResult.

        Returns None if the text cannot be parsed or required keys are missing.
        """
        try:
            clean = text.replace("```json", "").replace("```", "").strip()
            data = json.loads(clean)
        except json.JSONDecodeError:
            # Try to extract the outermost JSON object from noisy output.
            data = self._extract_json_object(text)
            if data is None:
                return None

        if not isinstance(data, dict):
            return None

        updated_memory = data.get("updated_memory")
        if not isinstance(updated_memory, str) or not updated_memory.strip():
            return None

        learnings = data.get("learnings", [])
        if not isinstance(learnings, list):
            learnings = []

        issues_detected = data.get("issues_detected", [])
        if not isinstance(issues_detected, list):
            issues_detected = []

        summary = data.get("summary", "")
        if not isinstance(summary, str):
            summary = ""

        return DreamResult(
            updated_memory=updated_memory,
            learnings=learnings,
            issues_detected=issues_detected,
            summary=summary,
        )

    @staticmethod
    def _extract_json_object(text: str) -> dict | None:
        """Walk backwards from the last '}' to find the outermost JSON object."""
        try:
            end = text.rfind("}")
            if end == -1:
                return None
            depth = 0
            for i in range(end, -1, -1):
                if text[i] == "}":
                    depth += 1
                elif text[i] == "{":
                    depth -= 1
                    if depth == 0:
                        return json.loads(text[i : end + 1])
        except Exception:
            pass
        return None

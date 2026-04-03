"""autoDream LLM brain — nightly KAIROS memory consolidation.

Reads session logs and health data, distills learnings, and updates
robot-memory.md with structured insights. Designed to run nightly via
autodream.sh and castor.brain.autodream_runner.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from castor.providers.base import BaseProvider

logger = logging.getLogger("OpenCastor.AutoDream")

# Module-level constant — byte-for-byte stable across calls → cache-eligible.
AUTODREAM_SYSTEM_PROMPT = (
    "You are the autoDream brain for an OpenCastor robot. Your job is to:\n"
    "1. Read recent session logs and health data\n"
    "2. Distill new learnings (hardware patterns, environment notes, behavior adjustments)\n"
    "3. Return ONLY new or updated observations — not a full memory rewrite\n"
    "4. Identify actionable issues that warrant a GitHub issue or PR\n"
    "5. Be concise — one observation per entry, max 500 chars each\n"
    "\n"
    "Format your response as JSON with keys:\n"
    "  entries: list of {type, text, confidence, tags} where:\n"
    "    type: 'hardware_observation' | 'environment_note' | 'behavior_pattern' | 'resolved'\n"
    "    text: concise observation string (max 500 chars)\n"
    "    confidence: float 0.0–1.0 (how certain you are)\n"
    "    tags: list of string labels (optional)\n"
    "  learnings: list of short learning strings (for the dream log)\n"
    "  issues_detected: list of issue descriptions to file as GitHub issues\n"
    "  summary: one-line summary of the dream session\n"
    "\n"
    "Return empty entries list if no new observations. Never repeat existing entries verbatim."
)


@dataclass
class DreamSession:
    """Input data for a single autoDream run."""

    session_logs: list[str]
    robot_memory: str
    health_report: dict
    date: str
    recent_commits: list[str] = field(default_factory=list)  # last 5 git commits
    bridge_log_tail: list[str] = field(default_factory=list)  # last 20 lines of bridge log
    cron_outcomes: list[str] = field(default_factory=list)  # last 3 dream-log.jsonl summaries


@dataclass
class DreamResult:
    """Output of a single autoDream run."""

    # Legacy free-form memory (kept for backward compat; prefer entries)
    updated_memory: str = ""
    # Structured entries from new schema-aware prompt (preferred)
    entries: Optional[list[dict]] = None
    learnings: list[str] = field(default_factory=list)
    issues_detected: list[str] = field(default_factory=list)
    summary: str = ""


class AutoDreamBrain:
    """LLM-powered nightly memory consolidation brain.

    Wraps any BaseProvider to distill session logs and health data into
    structured robot-memory.md updates and learning summaries.
    """

    def __init__(self, provider: BaseProvider) -> None:
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
            entries=None,
            learnings=[],
            issues_detected=[],
            summary="autoDream brain unavailable — memory unchanged.",
        )

    def _build_session_prompt(self, session: DreamSession) -> str:
        """Build the user-turn prompt from *session* data."""
        error_lines = "\n".join(session.session_logs[-50:]) if session.session_logs else "(none)"

        # Format existing memory for context — support both structured and free-form
        existing_memory = session.robot_memory
        try:
            import os
            import tempfile

            from castor.brain.memory_schema import (
                apply_confidence_decay,
                filter_for_context,
                format_entries_for_context,
                load_memory,
            )

            fd, tmp = tempfile.mkstemp(suffix=".md")
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(session.robot_memory)
                mem = load_memory(tmp)
                if mem.entries:
                    mem = apply_confidence_decay(mem)
                    eligible = filter_for_context(mem)
                    existing_memory = format_entries_for_context(eligible)
            finally:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
        except Exception:
            pass  # Fall back to raw text

        commits_text = "\n".join(session.recent_commits) if session.recent_commits else "(none)"
        bridge_text = "\n".join(session.bridge_log_tail) if session.bridge_log_tail else "(none)"
        outcomes_text = "\n".join(session.cron_outcomes) if session.cron_outcomes else "(none)"

        return (
            "<dream-session>\n"
            f"<date>{session.date}</date>\n"
            f"<health>{json.dumps(session.health_report)}</health>\n"
            "<recent-errors>\n"
            f"{error_lines}\n"
            "</recent-errors>\n"
            "<recent-code-changes>\n"
            f"{commits_text}\n"
            "</recent-code-changes>\n"
            "<bridge-activity>\n"
            f"{bridge_text}\n"
            "</bridge-activity>\n"
            "<recent-dream-outcomes>\n"
            f"{outcomes_text}\n"
            "</recent-dream-outcomes>\n"
            "<existing-memory>\n"
            f"{existing_memory}\n"
            "</existing-memory>\n"
            "</dream-session>\n"
            "\n"
            "Return new or updated observations as structured JSON entries. "
            "Do not repeat existing entries. Focus on what is new or changed."
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

        # New structured format: entries list (preferred)
        raw_entries = data.get("entries")
        entries: list[dict] | None = None
        if isinstance(raw_entries, list) and raw_entries:
            validated = []
            for e in raw_entries:
                if not isinstance(e, dict):
                    continue
                text = e.get("text", "")
                etype = e.get("type", "hardware_observation")
                confidence = float(e.get("confidence", 0.7))
                tags = e.get("tags", [])
                if not isinstance(tags, list):
                    tags = []
                if text and isinstance(text, str):
                    validated.append(
                        {
                            "type": etype,
                            "text": str(text)[:500],
                            "confidence": max(0.0, min(1.0, confidence)),
                            "tags": [str(t) for t in tags if t],
                        }
                    )
            if validated:
                entries = validated

        # Legacy format: updated_memory string (backward compat)
        updated_memory = data.get("updated_memory", "")
        if not isinstance(updated_memory, str):
            updated_memory = ""

        # Require at least one of entries or updated_memory
        if entries is None and not updated_memory.strip():
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
            entries=entries,
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

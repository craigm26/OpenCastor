"""Rolling context window compactor with LLM-based summarization.

Replaces the oldest messages with a concise summary when the context
exceeds a configurable token threshold, enabling 24/7 continuous operation
without hitting provider context window limits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from castor.providers.base import BaseProvider

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4  # conservative estimate

SUMMARY_PROMPT = (
    "You are summarizing a robot's conversation history for long-term memory. "
    "Produce a concise summary (max 200 words) preserving: all decisions made, "
    "all errors encountered, current robot state, and any important context. "
    "Discard filler, redundant observations, and chitchat. "
    "Begin with: [Compacted session summary]"
)


@dataclass
class CompactionConfig:
    enabled: bool = False
    max_tokens: int = 80_000
    target_tokens: int = 20_000
    preserve_last_n: int = 10
    summary_provider: str = "auto"  # cheapest available


class ContextCompactor:
    """Rolling context compactor. Thread-safe (uses no shared mutable state)."""

    def __init__(self, config: CompactionConfig | None = None) -> None:
        self.config = config or CompactionConfig()

    def estimate_tokens(self, messages: list[dict]) -> int:
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        return total_chars // _CHARS_PER_TOKEN

    def needs_compaction(self, messages: list[dict]) -> bool:
        if not self.config.enabled:
            return False
        return self.estimate_tokens(messages) > self.config.max_tokens

    def compact(
        self,
        messages: list[dict],
        provider: BaseProvider | None = None,
    ) -> list[dict]:
        """Summarize oldest messages until under target_tokens.

        Args:
            messages: Full conversation history (list of {role, content} dicts).
            provider: Optional provider for LLM summarization. If None, uses
                      a simple truncation fallback with a note.

        Returns:
            Compacted message list with a summary message prepended.
        """
        if not self.needs_compaction(messages):
            return messages

        preserve = self.config.preserve_last_n
        to_summarize = messages[:-preserve] if preserve else messages
        to_keep = messages[-preserve:] if preserve else []

        logger.info(
            "Compacting context: %d messages → summary + %d recent",
            len(to_summarize),
            len(to_keep),
        )

        summary_text = self._summarize(to_summarize, provider)
        summary_msg = {
            "role": "system",
            "content": summary_text,
        }
        return [summary_msg] + to_keep

    def maybe_compact(
        self,
        messages: list[dict],
        provider: BaseProvider | None = None,
    ) -> tuple[list[dict], bool]:
        """Compact if needed. Returns (messages, did_compact)."""
        if self.needs_compaction(messages):
            return self.compact(messages, provider), True
        return messages, False

    def _summarize(
        self,
        messages: list[dict],
        provider: BaseProvider | None,
    ) -> str:
        """Produce a summary string from messages."""
        if provider is None:
            return self._fallback_summary(messages)
        try:
            history_text = "\n".join(
                f"{m.get('role', 'unknown').upper()}: {m.get('content', '')}" for m in messages
            )
            instruction = f"{SUMMARY_PROMPT}\n\nConversation to summarize:\n{history_text}"
            thought = provider.think(b"", instruction)
            return thought.raw_text.strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM summarization failed, using fallback: %s", e)
            return self._fallback_summary(messages)

    def _fallback_summary(self, messages: list[dict]) -> str:
        """Rule-based summary when LLM is unavailable."""
        roles: dict[str, int] = {}
        for m in messages:
            r = m.get("role", "unknown")
            roles[r] = roles.get(r, 0) + 1
        breakdown = ", ".join(f"{v} {k}" for k, v in roles.items())
        return (
            f"[Compacted session summary] {len(messages)} messages ({breakdown}) "
            f"from earlier in this session were compacted. "
            f"Exact content not available — continuing from current state."
        )

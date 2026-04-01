from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger("OpenCastor.Compaction")


@dataclass
class CompactionStrategy:
    """Configuration for session compaction behaviour."""

    threshold_tokens: int = 160_000
    preserve_recent: int = 4
    suppress_follow_up: bool = True


def estimate_tokens(messages: list) -> int:
    """Cheap token estimate: total characters divided by 4."""
    return sum(len(str(m)) // 4 for m in messages)


def should_compact(messages: list, strategy: CompactionStrategy) -> bool:
    """Return True when the estimated token count meets or exceeds the threshold."""
    return estimate_tokens(messages) >= strategy.threshold_tokens


def compact_session(
    messages: list,
    strategy: CompactionStrategy,
    summarizer_fn: Callable[[list], str],
) -> tuple[list, str]:
    """Compact a message list by summarising the older portion.

    Keeps the last ``strategy.preserve_recent`` messages verbatim and passes
    everything else to *summarizer_fn*.

    Returns:
        (new_messages, summary) where *new_messages* is the compacted list
        ready to be sent to the next LLM call.
    """
    preserve = strategy.preserve_recent
    if preserve >= len(messages):
        recent = messages
        to_summarise = []
    else:
        recent = messages[-preserve:]
        to_summarise = messages[:-preserve]

    summary = summarizer_fn(to_summarise) if to_summarise else ""
    return recent, summary


def build_continuation_message(summary: str, suppress_follow_up: bool = True) -> dict:
    """Build the system message injected after compaction.

    Args:
        summary:           Text produced by the summariser.
        suppress_follow_up: When True, appends an instruction telling the model
                            not to acknowledge the compaction or ask follow-up
                            questions about the summarised context.

    Returns:
        A ``{"role": "system", "content": "..."}`` dict.
    """
    content = f"<compaction-summary>\n{summary}\n</compaction-summary>"
    if suppress_follow_up:
        content += (
            "\nDo not acknowledge this summary or ask follow-up questions about "
            "the compacted context. Continue the conversation naturally."
        )
    return {"role": "system", "content": content}

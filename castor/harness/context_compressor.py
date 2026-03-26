"""
castor/harness/context_compressor.py — Context window compressor.

When conversation history approaches the model's context limit, summarises
older turns using a cheap fast model instead of silently dropping them.

RCAN config::

    context_compressor:
      enabled: true
      trigger_at: 0.8          # fraction of context budget before compressing
      summary_model: gemma3:1b # cheap fast local model for summarisation
      keep_recent: 5           # always keep last N turns verbatim
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

__all__ = ["ContextCompressor"]

logger = logging.getLogger("OpenCastor.ContextCompressor")


class ContextCompressor:
    """Compresses conversation history when approaching the context budget.

    Args:
        config:           ``context_compressor`` section from RCAN config.
        provider_factory: Callable ``(model_name: str) -> provider`` used to
                          instantiate a cheap summarisation provider.  If None
                          the compressor falls back to a simple truncation
                          summary.
    """

    def __init__(self, config: dict, provider_factory: Optional[Any] = None) -> None:
        self._trigger_at: float = float(config.get("trigger_at", 0.8))
        self._summary_model: str = str(config.get("summary_model", "gemma3:1b"))
        self._keep_recent: int = int(config.get("keep_recent", 5))
        self._provider_factory = provider_factory
        self._summary_provider: Any = None  # lazily initialised

    async def maybe_compress(
        self,
        history: list[dict],
        context_budget_tokens: int,
        current_usage_tokens: int,
    ) -> list[dict]:
        """Return (possibly compressed) history.

        If ``current_usage_tokens > trigger_at * context_budget_tokens``:
          - Summarise all but the last ``keep_recent`` turns.
          - Return [summary_system_message, ...last_N_turns].

        If below the threshold, returns ``history`` unchanged.
        """
        trigger_tokens = self._trigger_at * context_budget_tokens

        if current_usage_tokens <= trigger_tokens:
            return history  # no compression needed

        if len(history) <= self._keep_recent:
            return history  # nothing to compress

        older = history[: -self._keep_recent]
        recent = history[-self._keep_recent :]

        summary_text = await self._summarise(older)
        compressed = [
            {
                "role": "system",
                "content": f"Summary of earlier conversation: {summary_text}",
            },
            *recent,
        ]
        logger.info(
            "ContextCompressor: compressed %d older turns → 1 summary + %d recent",
            len(older),
            len(recent),
        )
        return compressed

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _summarise(self, messages: list[dict]) -> str:
        """Summarise ``messages`` using the cheap summary model."""
        try:
            provider = await self._get_provider()
            text = "\n".join(
                f"{m.get('role', '?')}: {str(m.get('content', ''))[:300]}" for m in messages
            )
            prompt = (
                "Summarise this conversation concisely in 2–4 sentences, "
                "preserving all key facts and decisions:\n\n" + text
            )
            thought = await asyncio.to_thread(provider.think, b"", prompt, "context_compressor")
            return thought.raw_text[:800]
        except Exception as exc:
            logger.warning("ContextCompressor: summarisation failed (%s), using fallback", exc)
            return "[earlier conversation summarised — content omitted due to context limit]"

    async def _get_provider(self) -> Any:
        """Lazily instantiate the summarisation provider."""
        if self._summary_provider is not None:
            return self._summary_provider

        if self._provider_factory is not None:
            self._summary_provider = self._provider_factory(self._summary_model)
            return self._summary_provider

        # Fallback: try to get a shared brain or raise
        try:
            from castor.main import get_shared_brain

            brain = get_shared_brain()
            if brain is None:
                raise RuntimeError("No shared brain available")
            self._summary_provider = brain
            return self._summary_provider
        except Exception as exc:
            raise RuntimeError(
                f"ContextCompressor: no provider available for model {self._summary_model!r}"
            ) from exc

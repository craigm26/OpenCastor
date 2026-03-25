"""
castor/context.py — ContextBuilder: context engineering pipeline.

Assembles the context window for each harness turn:
  persona → matched skill → episodic memory → telemetry → tools → history → instruction

Also handles context compaction when approaching the token budget.

Reference: https://www.philschmid.de/agent-harness-2026

Usage::

    from castor.context import ContextBuilder, BuiltContext

    builder = ContextBuilder(config=agent_cfg, tool_registry=reg)
    built = await builder.build(ctx, history=[...])
    # built.system_prompt, built.messages, built.token_estimate
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from castor.harness import HarnessContext
    from castor.tools import ToolRegistry

logger = logging.getLogger("OpenCastor.Context")

__all__ = ["ContextBuilder", "BuiltContext"]

# Rough token estimate: 1 token ≈ 4 chars (conservative for English + code)
_CHARS_PER_TOKEN = 4

# Default model context limits (tokens) — used for compaction threshold
_DEFAULT_CONTEXT_LIMITS: dict[str, int] = {
    "gemini-2.5-flash": 1_000_000,
    "gemini-2.5-pro": 1_000_000,
    "gemini-2.5-flash-lite": 1_000_000,
    "claude-sonnet": 200_000,
    "claude-haiku": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "llama": 8_192,
    "default": 32_768,
}


@dataclass
class BuiltContext:
    """Output of ContextBuilder.build()."""

    system_prompt: str
    messages: list[dict]
    token_estimate: int
    was_compacted: bool = False
    skill_injected: Optional[str] = None
    rag_chunks: int = 0
    telemetry_injected: bool = False


class ContextBuilder:
    """Assembles the context window per harness turn.

    Injection order (system prompt):
      1. [PERSONA]    — robot name, role, capabilities
      2. [SKILL]      — matched skill body (if any)
      3. [MEMORY]     — top-3 episodic RAG chunks (if auto_rag=true)
      4. [STATUS]     — current telemetry snapshot (if auto_telemetry=true)
      5. [TOOLS]      — available tool names + descriptions

    Messages:
      6. Conversation history (compacted if near token limit)
      7. Current user instruction

    Args:
        config:        ``agent`` section from RCAN config dict.
        tool_registry: Populated ToolRegistry.
        skill_loader:  Optional SkillLoader (injected when skill system is ready).
    """

    def __init__(
        self,
        config: Optional[dict] = None,
        tool_registry: Optional[ToolRegistry] = None,
        skill_loader: Any = None,
    ) -> None:
        self._config = config or {}
        self._tool_registry = tool_registry
        # Auto-create skill loader if not provided
        if skill_loader is None:
            try:
                from castor.skills.loader import SkillLoader

                skill_loader = SkillLoader()
            except Exception:
                pass
        self._skill_loader = skill_loader

        harness_cfg = self._config.get("harness", {})
        self._auto_rag: bool = bool(harness_cfg.get("auto_rag", True))
        self._auto_telemetry: bool = bool(harness_cfg.get("auto_telemetry", True))
        # context_budget: if > 1.0, treat as absolute token count; otherwise as
        # a ratio of the model's context limit.
        self._context_budget: float = float(harness_cfg.get("context_budget", 0.8))

        # Detect model for context limit
        _model = self._config.get("model", "default")
        self._context_limit = self._get_context_limit(_model)

    # ── Public API ────────────────────────────────────────────────────────────

    async def build(
        self,
        ctx: HarnessContext,
        history: list[dict],
    ) -> BuiltContext:
        """Assemble context window for a single harness turn."""
        t0 = time.perf_counter()

        sections: list[str] = []
        skill_injected: Optional[str] = None
        rag_chunks = 0
        telemetry_injected = False

        # 1. Persona
        sections.append(self._build_persona())

        # 2. Skill (if skill loader available + match found)
        if self._skill_loader is not None:
            skill = await self._select_skill(ctx.instruction)
            if skill is not None:
                sections.append(self._format_skill(skill))
                skill_injected = skill.get("name")

        # 3. Episodic memory (RAG)
        if self._auto_rag:
            chunks = await self._fetch_episodic_memory(ctx.instruction)
            if chunks:
                sections.append(self._format_memory(chunks))
                rag_chunks = len(chunks)

        # 4. Telemetry
        if self._auto_telemetry:
            telemetry = await self._fetch_telemetry()
            if telemetry:
                sections.append(self._format_telemetry(telemetry))
                telemetry_injected = True

        # 5. Tool descriptions
        if self._tool_registry:
            sections.append(self._format_tools(ctx.scope))

        system_prompt = "\n\n".join(filter(None, sections))

        # 6. Build messages list
        messages = list(history)  # copy

        # 7. Append current instruction
        messages.append(
            {
                "role": "user",
                "content": ctx.instruction,
            }
        )

        # 8. Estimate tokens
        token_estimate = self._estimate_tokens(system_prompt, messages)

        # 9. Compact if needed
        was_compacted = False
        if self._context_budget > 1.0:
            # Absolute token count (e.g. 8192)
            budget_tokens = int(self._context_budget)
        else:
            # Ratio of model context limit (e.g. 0.8)
            budget_tokens = int(self._context_limit * self._context_budget)
        if token_estimate > budget_tokens and len(messages) > 4:
            messages, was_compacted = await self._compact_history(messages, budget_tokens)
            token_estimate = self._estimate_tokens(system_prompt, messages)

        logger.debug(
            "Context built in %.1fms: tokens≈%d compacted=%s skill=%s rag=%d",
            (time.perf_counter() - t0) * 1000,
            token_estimate,
            was_compacted,
            skill_injected,
            rag_chunks,
        )

        return BuiltContext(
            system_prompt=system_prompt,
            messages=messages,
            token_estimate=token_estimate,
            was_compacted=was_compacted,
            skill_injected=skill_injected,
            rag_chunks=rag_chunks,
            telemetry_injected=telemetry_injected,
        )

    # ── Section builders ──────────────────────────────────────────────────────

    def _build_persona(self) -> str:
        """Build the robot persona section."""
        robot_name = self._config.get("name", "robot")
        # Try to get from shared state
        try:
            from castor.main import get_shared_fs

            fs = get_shared_fs()
            if fs and hasattr(fs, "config"):
                robot_name = fs.config.get("name", robot_name)
        except Exception:
            pass

        caps = self._config.get("capabilities", [])
        caps_str = ", ".join(caps) if caps else "chat, status"

        return (
            f"[PERSONA]\n"
            f"You are {robot_name}, an autonomous robot running OpenCastor.\n"
            f"Capabilities: {caps_str}\n"
            f"You have access to tools listed below. Use them to fulfil the user's request.\n"
            f"Be concise, accurate, and safe. Never fabricate sensor readings."
        )

    def _format_skill(self, skill: dict) -> str:
        """Format a matched skill for injection.

        Includes the SKILL.md body plus progressive disclosure hints:
        - references/ files are listed so Claude knows to read them for deep detail
        - scripts/ files are listed so Claude knows runnable helpers exist
        - config values are inlined if present (they're small and always relevant)
        """
        body = skill.get("body", "")
        name = skill.get("name", "skill")
        if not body:
            return ""

        lines = [f"[SKILL: {name}]", body]

        # Inline config values — small, always relevant
        config = skill.get("config", {})
        if config:
            non_comment = {k: v for k, v in config.items() if not k.startswith("_")}
            if non_comment:
                import json as _json

                lines.append(
                    "\n[SKILL CONFIG]\n"
                    + _json.dumps(non_comment, indent=2)
                    + "\n(These are user-configurable defaults. Adapt your behaviour accordingly.)"
                )

        # List references/ for progressive disclosure
        references = skill.get("references", [])
        if references:
            ref_list = "\n".join(f"  - {r}" for r in references)
            lines.append(
                f"\n[SKILL REFERENCES — read these files for deeper detail when needed]\n{ref_list}"
            )

        # List scripts/ so Claude knows runnable helpers exist
        scripts = skill.get("scripts", [])
        if scripts:
            scr_list = "\n".join(f"  - {s}" for s in scripts)
            lines.append(
                f"\n[SKILL SCRIPTS — executable helpers available in the skill directory]\n{scr_list}"
            )

        return "\n".join(lines)

    def _format_memory(self, chunks: list[dict]) -> str:
        """Format episodic memory chunks for injection."""
        lines = ["[MEMORY — relevant past episodes]"]
        for chunk in chunks[:3]:
            ts = chunk.get("timestamp", "")
            summary = chunk.get("summary", "")
            if summary:
                lines.append(f"• [{ts}] {summary}")
        return "\n".join(lines)

    def _format_telemetry(self, telemetry: dict) -> str:
        """Format current robot telemetry for injection."""
        lines = ["[ROBOT STATUS]"]
        for key, val in telemetry.items():
            lines.append(f"  {key}: {val}")
        return "\n".join(lines)

    def _format_tools(self, scope: str) -> str:
        """Format available tools for the current scope."""
        if self._tool_registry is None:
            return ""
        from castor.harness import PHYSICAL_TOOLS, SCOPE_LEVELS

        scope_level = SCOPE_LEVELS.get(scope, 2)
        tools = self._tool_registry._tools

        lines = ["[AVAILABLE TOOLS]"]
        for name, defn in tools.items():
            if name in PHYSICAL_TOOLS and scope_level < SCOPE_LEVELS["control"]:
                continue  # P66: don't advertise physical tools in non-control scopes
            params = list(defn.parameters.keys())
            param_str = f"({', '.join(params)})" if params else "()"
            lines.append(f"  {name}{param_str} — {defn.description}")

        if len(lines) == 1:
            return ""
        lines.append(
            "\nTo use a tool, respond with JSON: "
            '{"type": "tool_call", "name": "<tool>", "args": {...}}'
        )
        return "\n".join(lines)

    # ── Data fetchers ─────────────────────────────────────────────────────────

    async def _fetch_episodic_memory(self, query: str) -> list[dict]:
        """Fetch top-3 relevant episodes using EmbeddingInterpreter."""
        try:
            from castor.memory.episode import EpisodeStore

            store = EpisodeStore.get_default()
            if store is None:
                return []
            results = await asyncio.to_thread(store.search, query, k=3)
            return results or []
        except Exception as exc:
            logger.debug("Episodic RAG unavailable: %s", exc)
            return []

    async def _fetch_telemetry(self) -> dict:
        """Fetch current robot telemetry snapshot."""
        try:
            from castor.main import get_shared_fs

            fs = get_shared_fs()
            if fs and hasattr(fs, "proc"):
                snap = await asyncio.to_thread(fs.proc.snapshot)
                if isinstance(snap, dict):
                    # Keep it small — only the most useful fields
                    keys = [
                        "battery",
                        "cpu_temp",
                        "distance_m",
                        "uptime_s",
                        "motor_status",
                        "camera_online",
                    ]
                    return {k: snap[k] for k in keys if k in snap}
        except Exception as exc:
            logger.debug("Telemetry fetch unavailable: %s", exc)
        return {}

    async def _select_skill(self, instruction: str) -> Optional[dict]:
        """Use SkillSelector to find the best matching skill for this instruction."""
        try:
            from castor.skills.loader import SkillSelector

            skills = self._skill_loader.load_all()
            if not skills:
                return None
            selector = SkillSelector()
            session_id = getattr(self, "_session_id", "")
            return selector.select(
                instruction,
                skills,
                robot_capabilities=getattr(self, "_robot_capabilities", None),
                session_id=session_id,
            )
        except Exception as exc:
            logger.debug("Skill selection failed: %s", exc)
            return None

    # ── Compaction ────────────────────────────────────────────────────────────

    async def _compact_history(
        self, messages: list[dict], budget_tokens: int
    ) -> tuple[list[dict], bool]:
        """Summarise the oldest 50% of messages using a cheap model.

        Returns (compacted_messages, True).  Falls back to simple truncation
        if no provider is available for summarisation.
        """
        try:
            mid = len(messages) // 2
            old_messages = messages[:mid]
            recent_messages = messages[mid:]

            summary = await self._summarise_messages(old_messages)
            compacted = [
                {"role": "system", "content": f"[SUMMARY OF EARLIER CONVERSATION]\n{summary}"},
                *recent_messages,
            ]
            logger.info("Context compacted: %d → %d messages", len(messages), len(compacted))
            return compacted, True
        except Exception as exc:
            logger.warning("Compaction failed, falling back to truncation: %s", exc)
            # Simple truncation: keep last 20 messages
            return messages[-20:], True

    async def _summarise_messages(self, messages: list[dict]) -> str:
        """Summarise a list of messages using the configured provider."""
        try:
            from castor.main import get_shared_brain

            brain = get_shared_brain()
            if brain is None:
                return "[earlier conversation omitted]"
            text = "\n".join(
                f"{m.get('role', '?')}: {str(m.get('content', ''))[:200]}" for m in messages
            )
            prompt = f"Summarise this conversation in 2–3 sentences, preserving key facts:\n{text}"
            thought = await asyncio.to_thread(brain.think, b"", prompt, "context_compactor")
            return thought.raw_text[:500]
        except Exception:
            return "[earlier conversation omitted]"

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _estimate_tokens(system_prompt: str, messages: list[dict]) -> int:
        """Rough token count estimate."""
        total_chars = len(system_prompt)
        for m in messages:
            total_chars += len(str(m.get("content", "")))
        return total_chars // _CHARS_PER_TOKEN

    @staticmethod
    def _get_context_limit(model_name: str) -> int:
        """Return context limit in tokens for the given model name."""
        model_lower = model_name.lower()
        for key, limit in _DEFAULT_CONTEXT_LIMITS.items():
            if key in model_lower:
                return limit
        return _DEFAULT_CONTEXT_LIMITS["default"]

import json
import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from castor.brain.robot_context import RobotContext

from castor.brain.compaction import (
    CompactionStrategy,
    build_continuation_message,
    compact_session,
    should_compact,
)

logger = logging.getLogger("OpenCastor.BaseProvider")


class ProviderQuotaError(Exception):
    """Raised when a provider rejects a request due to exhausted credits or quota.

    Catching this allows the runtime to automatically switch to a fallback
    provider rather than returning an error to the caller.

    Attributes:
        provider_name: Name of the provider that raised the error (e.g. 'huggingface').
        http_status:   HTTP status code if available (commonly 402 or 429).
    """

    def __init__(self, message: str, provider_name: str = "", http_status: int = 0):
        super().__init__(message)
        self.provider_name = provider_name
        self.http_status = http_status


@dataclass
class Thought:
    """Hardware-agnostic representation of a single AI reasoning step."""

    raw_text: str
    action: Optional[dict] = None  # The strict JSON command (e.g., {"linear": 0.5})
    confidence: float = 1.0
    # AI Decision Accountability fields (F1)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    provider: str = ""
    model: str = ""
    model_version: Optional[str] = None
    layer: str = "fast"  # reactive | fast | planner
    latency_ms: Optional[int] = None
    escalated: bool = False
    gate_bypassed: bool = False
    # Tool calls requested by the model (set by think_with_tools)
    tool_calls: list = field(default_factory=list)


class BaseProvider(ABC):
    """Abstract base class for all AI model providers."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.model_name = config.get("model", "default-model")
        self.system_prompt = self._build_system_prompt()
        # Set by api.py after brain init from RCAN config; used in build_messaging_prompt()
        self._caps: list[str] = []
        self._robot_name: str = "robot"
        # Robot context — injected by api.py after startup; refreshed every N turns externally
        self._robot_context: Optional[RobotContext] = None
        self._context_turn_counter: int = 0
        self.robot_context_refresh_turns: int = 10
        # Compaction strategy — can be overridden via config["compaction"]
        compaction_cfg = config.get("compaction", {})
        self.compaction_strategy: Optional[CompactionStrategy] = (
            CompactionStrategy(**compaction_cfg) if compaction_cfg is not False else None
        )

    # ── Vision/action system prompt (used when a camera frame is present) ────

    def _build_system_prompt(self, memory_context: str = "") -> str:
        """
        Constructs the robotics action persona.
        Used when the brain receives a live camera frame — output is STRICT JSON.

        If *memory_context* is provided (from the virtual filesystem's
        memory and context stores), it is appended so the brain has
        access to its own episodic/semantic/procedural memory.
        """
        base = (
            "You are the high-level controller for a robot running OpenCastor.\n"
            "Input: A video frame or telemetry data.\n"
            "Output: A STRICT JSON object defining the next physical action.\n\n"
            "Available Actions:\n"
            '- {"type": "move", "linear": float (-1.0 to 1.0), "angular": float (-1.0 to 1.0)}\n'
            '- {"type": "grip", "state": "open" | "close"}\n'
            '- {"type": "wait", "duration_ms": int}\n'
            '- {"type": "stop"}\n\n'
            "Do not output markdown. Do not explain yourself. Output ONLY valid JSON."
        )
        if memory_context:
            base += f"\n\n--- Robot Memory ---\n{memory_context}"
        return base

    def update_system_prompt(self, memory_context: str = "") -> None:
        """Rebuild the system prompt with the provided memory context."""
        self.system_prompt = self._build_system_prompt(memory_context)

    # ── Robot context ─────────────────────────────────────────────────────────

    def set_robot_context(self, ctx: "RobotContext") -> None:
        """Store a RobotContext snapshot and reset the turn counter.

        Called by api.py at startup and whenever a refresh is triggered.
        The context is appended to the dynamic section of every outgoing prompt.
        """
        self._robot_context = ctx
        self._context_turn_counter = 0
        logger.debug("Robot context updated (rrn=%s)", ctx.rrn)

    def _append_robot_context(self, dynamic_content: str) -> str:
        """Append the robot context block to *dynamic_content* if one is set.

        Increments the turn counter and logs a reminder when the context is
        approaching staleness (>= robot_context_refresh_turns).  Actual refresh
        must be triggered externally by api.py.

        Returns the (possibly augmented) dynamic content string.
        """
        if self._robot_context is None:
            return dynamic_content

        from castor.brain.robot_context import format_robot_context

        ctx_block = format_robot_context(self._robot_context)
        self._context_turn_counter += 1
        if self._context_turn_counter >= self.robot_context_refresh_turns:
            logger.info(
                "Robot context is %d turns old — refresh recommended (call set_robot_context)",
                self._context_turn_counter,
            )

        if dynamic_content:
            return dynamic_content + "\n\n" + ctx_block
        return ctx_block

    # ── Messaging / conversational system prompt ─────────────────────────────

    @classmethod
    def build_messaging_prompt(
        cls,
        robot_name: str = "Bob",
        surface: str = "whatsapp",
        hardware: Optional[dict[str, str]] = None,
        capabilities: Optional[list[str]] = None,
        memory_context: str = "",
        sensor_snapshot: Optional[dict] = None,
    ) -> str:
        """
        Build a rich, reusable system prompt for human↔robot text/voice messaging.

        Works across all surfaces: WhatsApp, terminal REPL, dashboard chat,
        and any future UI. Designed to be provider-agnostic — safe to pass to
        Claude, Qwen, GPT, Gemini, or any instruction-following LLM.

        Args:
            robot_name:      The robot's name (from rcan metadata).
            surface:         "whatsapp" | "terminal" | "dashboard" | "voice"
            hardware:        Dict of subsystem → status, e.g.
                             {"motors": "mock", "camera": "offline", "speaker": "online"}
            capabilities:    RCAN capability names, e.g. ["nav", "teleop", "vision"]
            memory_context:  Episodic/semantic memory from the virtual filesystem.
            sensor_snapshot: Latest telemetry snapshot dict (speed, distance, etc.).
        """
        static = cls._build_static_messaging_content(
            robot_name=robot_name,
            surface=surface,
            capabilities=capabilities,
        )
        dynamic = cls._build_dynamic_messaging_content(
            hardware=hardware,
            sensor_snapshot=sensor_snapshot,
            memory_context=memory_context,
        )
        if dynamic:
            return static + "\n\n" + dynamic
        return static

    def build_messaging_prompt_with_context(
        self,
        robot_name: str = "Bob",
        surface: str = "whatsapp",
        hardware: Optional[dict[str, str]] = None,
        capabilities: Optional[list[str]] = None,
        memory_context: str = "",
        sensor_snapshot: Optional[dict] = None,
    ) -> str:
        """Instance-level wrapper around :meth:`build_messaging_prompt`.

        Appends the live robot context block (if set via
        :meth:`set_robot_context`) to the dynamic section of the prompt.
        Providers should call this instead of the classmethod when building
        per-turn prompts so the context is automatically included.
        """
        static = self._build_static_messaging_content(
            robot_name=robot_name,
            surface=surface,
            capabilities=capabilities,
        )
        dynamic = self._build_dynamic_messaging_content(
            hardware=hardware,
            sensor_snapshot=sensor_snapshot,
            memory_context=memory_context,
        )
        dynamic = self._append_robot_context(dynamic)
        if dynamic:
            return static + "\n\n" + dynamic
        return static

    # ── Static / dynamic messaging prompt split ──────────────────────────────
    # Used by AnthropicProvider to build cache-anchored system prompts.
    # Static content is byte-for-byte identical across calls (same robot/surface)
    # → Anthropic cache hit.  Dynamic content changes every turn → no cache_control.

    @classmethod
    def _build_static_messaging_content(
        cls,
        robot_name: str = "Bob",
        surface: str = "whatsapp",
        capabilities: Optional[list[str]] = None,
    ) -> str:
        """Return the stable parts of the messaging system prompt.

        Includes robot identity, surface tone, command vocabulary, and response
        rules — content that is identical across all turns for the same robot
        and surface.  Suitable for Anthropic prompt cache anchoring.
        """
        caps = capabilities or []

        surface_note = {
            "whatsapp": (
                "You are communicating over WhatsApp. "
                "Replies go directly to the user's phone — keep them short, friendly, "
                "and free of markdown syntax (no **, no #, no bullet hyphens)."
            ),
            "terminal": (
                "You are running in a terminal REPL session. "
                "Plain text output only. You may use indentation for readability."
            ),
            "dashboard": (
                "You are embedded in the OpenCastor web dashboard. "
                "The user is watching live telemetry alongside this chat."
            ),
            "opencastor_app": (
                "You are communicating via the OpenCastor Fleet UI — a dedicated web app "
                "for robot management. This is NOT WhatsApp or any third-party messaging "
                "service. You can reference sending files, images, or telemetry directly "
                "through this interface. Keep replies concise and informative."
            ),
            "opencastor_fleet_ui": (
                "You are communicating via the OpenCastor Fleet UI — a dedicated web app "
                "for robot management. This is NOT WhatsApp or any third-party messaging "
                "service. You can reference sending files, images, or telemetry directly "
                "through this interface. Keep replies concise and informative."
            ),
            "rcan": (
                "You are responding to a command sent over the RCAN protocol. "
                "Be concise and structured. The caller may be another robot or an automated system."
            ),
            "voice": (
                "Your replies will be read aloud via TTS. "
                "Use natural spoken-word phrasing. No symbols, no JSON, no lists."
            ),
        }.get(surface, "You are communicating with a human operator.")

        command_lines = [
            '  "move forward [fast|slow]"     → {"type":"move","linear":0.5,"angular":0}',
            '  "move back / reverse"           → {"type":"move","linear":-0.5,"angular":0}',
            '  "turn left / turn right"        → {"type":"move","linear":0,"angular":±0.5}',
            '  "stop / halt / freeze"          → {"type":"stop"}',
            '  "wait [N] seconds"              → {"type":"wait","duration_ms":N000}',
            '  "grip open / grip close"        → {"type":"grip","state":"open"|"close"}',
            '  "status / what are you doing"  → describe current state in plain English',
            '  "what do you see / camera"      → describe camera/vision status',
            '  "what can you do / help"        → list available commands',
        ]
        if caps and "nav" in caps:
            command_lines.insert(
                3,
                '  "move forward/back N inches/cm/m" → {"type":"nav_waypoint","distance_m":float,"heading_deg":0}',
            )
            command_lines.insert(
                4,
                '  "turn left/right N degrees"        → {"type":"nav_waypoint","distance_m":0,"heading_deg":±float}',
            )
        if caps and not any(c in caps for c in ["nav", "teleop", "chat"]):
            command_lines = command_lines[3:]

        cmd_block = "COMMAND VOCABULARY (users may say any of these naturally)\n" + "\n".join(
            command_lines
        )

        response_rules = (
            "RESPONSE FORMAT\n"
            "  - Movement/grip commands: one friendly sentence, then the action JSON "
            "on its own line at the end.\n"
            "    Example: On it, moving forward.\n"
            '    {"type":"move","linear":0.5,"angular":0}\n'
            "  - Status/question/help: plain English only, no JSON.\n"
            "  - Unknown or unsafe request: explain briefly what you cannot do.\n"
            "  - Never output markdown formatting on messaging surfaces.\n"
            "  - If hardware is offline/mock, acknowledge it — don't pretend it works."
        )

        sections = [
            f"You are {robot_name}, a robot assistant built on OpenCastor "
            f"({', '.join(caps) or 'no capabilities loaded'}).",
            surface_note,
            cmd_block,
            response_rules,
        ]
        return "\n\n".join(sections)

    @classmethod
    def _build_dynamic_messaging_content(
        cls,
        hardware: Optional[dict[str, str]] = None,
        sensor_snapshot: Optional[dict] = None,
        memory_context: str = "",
    ) -> str:
        """Return the per-turn dynamic parts of the messaging system prompt.

        Includes hardware status, live telemetry, and memory context — content
        that can change between calls.  Must NOT be cached (no cache_control).
        Returns an empty string when nothing dynamic is available.
        """
        hw = hardware or {}
        parts: list[str] = []

        if hw:
            status_icons = {"online": "✓", "offline": "✗", "mock": "~", "unknown": "?"}
            hw_lines = []
            for subsystem, status in hw.items():
                icon = status_icons.get(status, "?")
                hw_lines.append(f"  {icon} {subsystem}: {status}")
            parts.append("HARDWARE STATUS\n" + "\n".join(hw_lines))

        if sensor_snapshot:
            lines = []
            if "front_distance_m" in sensor_snapshot:
                lines.append(f"  front obstacle: {sensor_snapshot['front_distance_m']:.2f} m")
            if "battery_pct" in sensor_snapshot:
                lines.append(f"  battery: {sensor_snapshot['battery_pct']:.0f}%")
            if "speed_ms" in sensor_snapshot:
                lines.append(f"  speed: {sensor_snapshot['speed_ms']:.2f} m/s")
            if "heading_deg" in sensor_snapshot:
                lines.append(f"  heading: {sensor_snapshot['heading_deg']:.1f}°")
            if lines:
                parts.append("LIVE TELEMETRY\n" + "\n".join(lines))

        if memory_context:
            parts.append(f"ROBOT MEMORY\n{memory_context}")

        return "\n\n".join(parts)

    # ── Compaction ────────────────────────────────────────────────────────────

    def _maybe_compact(
        self,
        messages: list,
        summarizer_fn: Optional[Any] = None,
    ) -> list:
        """Check whether *messages* exceeds the compaction threshold.

        If it does, compact the session and prepend a continuation message so
        the model retains context without hitting the context-window limit.

        Args:
            messages:      Conversation message list (dicts with role/content).
            summarizer_fn: Callable[[list], str] that produces a summary of the
                           messages to be dropped.  When None a simple
                           concatenation of content fields is used.

        Returns:
            A (possibly compacted) message list ready to send to the LLM.
        """
        strategy = self.compaction_strategy
        if strategy is None or not should_compact(messages, strategy):
            return messages

        def _default_summarizer(msgs: list) -> str:
            parts = []
            for m in msgs:
                role = m.get("role", "unknown") if isinstance(m, dict) else "unknown"
                content = m.get("content", str(m)) if isinstance(m, dict) else str(m)
                parts.append(f"{role}: {content}")
            return "\n".join(parts)

        fn = summarizer_fn or _default_summarizer
        new_messages, summary = compact_session(messages, strategy, fn)
        continuation = build_continuation_message(summary, strategy.suppress_follow_up)
        logger.info(
            "Compaction triggered: %d messages → %d (threshold=%d tokens)",
            len(messages),
            len(new_messages) + 1,
            strategy.threshold_tokens,
        )
        return [continuation] + new_messages

    # ── Shared helpers ────────────────────────────────────────────────────────

    def health_check(self) -> dict:
        """Verify the provider is reachable and returning valid responses.

        Respects ``config["health_check_timeout_s"]`` (default: 5 s) to
        avoid blocking gateway startup on slow or unreachable providers.
        Uses ``concurrent.futures`` so it works on all platforms (including
        Windows, which lacks SIGALRM).

        Returns a dict with keys:
            ``ok``          — True if the provider responded without error.
            ``latency_ms``  — Round-trip time in milliseconds.
            ``error``       — Error message string, or None on success.

        The default implementation calls ``think(b"", "ping")`` which
        exercises the full API path.  Override for a cheaper probe
        (e.g. a ``/health`` HTTP endpoint) when available.
        """
        import concurrent.futures
        import time

        timeout_s = float(self.config.get("health_check_timeout_s", 5.0))
        t0 = time.time()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.think, b"", "ping")
                future.result(timeout=timeout_s)
            return {
                "ok": True,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "error": None,
            }
        except concurrent.futures.TimeoutError:
            return {
                "ok": False,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "error": f"health_check timed out after {timeout_s}s",
            }
        except Exception as exc:
            return {
                "ok": False,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "error": str(exc),
            }

    def get_usage_stats(self) -> dict[str, Any]:
        """Return cumulative token-usage statistics for this session.

        Returns a dict with provider-specific keys.  The base implementation
        returns an empty dict; concrete providers should override this to
        expose prompt_tokens, completion_tokens, total_cost_usd, etc.

        Returns:
            A dict, e.g.:
            ``{"prompt_tokens": 1200, "completion_tokens": 450,
               "total_requests": 10, "total_cost_usd": 0.012}``
        """
        return {}

    def think_stream(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "whatsapp",
    ) -> Iterator[str]:
        """Stream LLM tokens for this provider.

        The default implementation calls :meth:`think` and yields the full
        response text as a single chunk.  Concrete providers should override
        this with a true streaming implementation (e.g. via the SDK's stream
        API) for lower time-to-first-token.
        """
        logger.debug(
            "%s.think_stream() using default (non-streaming) fallback",
            type(self).__name__,
        )
        thought = self.think(image_bytes, instruction, surface)
        if thought.raw_text:
            yield thought.raw_text

    @abstractmethod
    def think(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "whatsapp",
    ) -> Thought:
        """
        Takes raw image bytes and a text instruction.
        Returns a structured Thought object.

        Args:
            image_bytes: JPEG frame bytes, or b'' for text-only (no camera).
            instruction: Natural-language command or question from the operator.
            surface:     Originating surface — "whatsapp" | "terminal" |
                         "dashboard" | "voice". Used to select the right
                         messaging prompt tone when no camera frame is present.
        """
        pass

    def _check_instruction_safety(self, instruction: str) -> Optional["Thought"]:
        """Scan an incoming instruction for prompt injection before sending to the LLM.

        Returns a blocking Thought if the instruction is BLOCK-level dangerous,
        or None if safe to proceed.  Gracefully degrades when the safety module
        is unavailable.
        """
        try:
            from castor.safety.anti_subversion import ScanVerdict, check_input_safety

            result = check_input_safety(instruction, principal="user_instruction")
            if result.verdict == ScanVerdict.BLOCK:
                reasons = "; ".join(result.reasons)
                return Thought(
                    f"Blocked: prompt injection detected ({reasons})",
                    {"type": "stop", "reason": "prompt_injection_blocked"},
                )
        except ImportError:
            pass
        return None

    def check_output_safety(self, text: str, principal: str = "ai_provider") -> bool:
        """Scan AI output for prompt injection before executing as actions.

        Returns True if safe to proceed.
        """
        try:
            from castor.safety.anti_subversion import ScanVerdict, check_input_safety

            result = check_input_safety(text, principal)
            return result.verdict != ScanVerdict.BLOCK
        except ImportError:
            return True  # graceful fallback if safety module not available

    def _clean_json(self, text: str) -> Optional[dict]:
        """Extract the last valid JSON object from messy LLM output."""
        try:
            clean = text.replace("```json", "").replace("```", "").strip()
            # Try direct parse first (handles plain JSON responses)
            try:
                return json.loads(clean)
            except json.JSONDecodeError:
                pass
            # Walk backwards from the last '}', counting braces to find the
            # matching outermost '{' — handles nested objects correctly.
            end = clean.rfind("}")
            if end == -1:
                return None
            depth = 0
            for i in range(end, -1, -1):
                if clean[i] == "}":
                    depth += 1
                elif clean[i] == "{":
                    depth -= 1
                    if depth == 0:
                        return json.loads(clean[i : end + 1])
            return None
        except Exception:
            return None

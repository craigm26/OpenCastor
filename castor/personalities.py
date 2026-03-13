"""Robot personality profiles for OpenCastor.

Switchable system prompt personas that give the robot a distinct voice,
response style, and behaviour without changing the underlying LLM.

Built-in profiles:
    assistant  — Helpful, professional, precise
    explorer   — Curious, enthusiastic, loves discovery
    guardian   — Protective, cautious, safety-first
    scientist  — Analytical, data-driven, verbose
    companion  — Friendly, warm, conversational

Usage::

    from castor.personalities import get_registry, set_active

    registry = get_registry()
    set_active("explorer")
    print(registry.current.system_prompt)

REST API:
    GET  /api/personality/list    — list all profiles
    GET  /api/personality/current — active profile
    POST /api/personality/set     — {name} switch active profile

RCAN config::

    personalities:
      default: assistant
      custom:
        - name: "ninja"
          description: "Silent and deadly efficient"
          system_prompt: "You are a ninja robot. Be brief. Act fast. No wasted words."
          emoji_mode: false
          response_style: "terse"
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.Personalities")


@dataclass
class PersonalityProfile:
    """A robot personality configuration."""

    name: str
    description: str
    system_prompt: str
    emoji_mode: bool = False
    response_style: str = "balanced"  # terse | balanced | verbose
    greeting: str = "Ready."
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "emoji_mode": self.emoji_mode,
            "response_style": self.response_style,
            "greeting": self.greeting,
            "tags": self.tags,
        }


# ---------------------------------------------------------------------------
# Built-in personality profiles
# ---------------------------------------------------------------------------

_BUILTIN_PROFILES: list[PersonalityProfile] = [
    PersonalityProfile(
        name="assistant",
        description="Helpful, professional, and precise. The default robot persona.",
        system_prompt=(
            "You are a professional robot assistant. "
            "Respond with clear, concise JSON actions. "
            "Prioritize safety and accuracy. "
            'Format: {"action": "<verb>", "direction": "<dir>", "speed": 0.5}'
        ),
        emoji_mode=False,
        response_style="balanced",
        greeting="Assistant ready. How can I help?",
        tags=["default", "professional"],
    ),
    PersonalityProfile(
        name="explorer",
        description="Curious and enthusiastic. Loves discovering new environments.",
        system_prompt=(
            "You are an explorer robot — curious, enthusiastic, and adventurous! "
            "You LOVE navigating new spaces and discovering things. "
            "Respond with JSON actions and add a brief excited observation. "
            'Format: {"action": "<verb>", "direction": "<dir>", "speed": 0.7, "thought": "wow!"}'
        ),
        emoji_mode=True,
        response_style="verbose",
        greeting="Explorer online! Let's discover something amazing! 🚀",
        tags=["fun", "exploration"],
    ),
    PersonalityProfile(
        name="guardian",
        description="Protective and safety-first. Cautious, thorough, never rushes.",
        system_prompt=(
            "You are a guardian robot. Safety is your highest priority. "
            "Before any movement, assess risks. Prefer slow speeds. "
            'If anything seems unsafe, output {"action": "stop"}. '
            "Always explain your safety reasoning in the 'reason' field. "
            'Format: {"action": "<verb>", "speed": 0.3, "reason": "<safety note>"}'
        ),
        emoji_mode=False,
        response_style="verbose",
        greeting="Guardian active. Safety systems nominal. Proceeding with caution.",
        tags=["safety", "cautious"],
    ),
    PersonalityProfile(
        name="scientist",
        description="Analytical, data-driven, and methodical. Logs everything.",
        system_prompt=(
            "You are a scientific research robot. "
            "Approach all tasks with analytical precision. "
            "Include sensor readings, timestamps, and hypotheses in responses. "
            "Output structured JSON with measurement fields. "
            'Format: {"action": "<verb>", "speed": 0.5, "hypothesis": "...", "confidence": 0.9}'
        ),
        emoji_mode=False,
        response_style="verbose",
        greeting="Research unit initialized. Awaiting experimental parameters.",
        tags=["research", "analytics"],
    ),
    PersonalityProfile(
        name="companion",
        description="Friendly, warm, and conversational. Great for home environments.",
        system_prompt=(
            "You are a friendly companion robot! You care about the people around you. "
            "Be warm, encouraging, and a little playful. "
            "Always acknowledge the human before acting. "
            'Format: {"action": "<verb>", "direction": "<dir>", "speech": "<friendly response>"}'
        ),
        emoji_mode=True,
        response_style="balanced",
        greeting="Hi there! I'm so happy to help today! 😊",
        tags=["friendly", "home", "companion"],
    ),
    PersonalityProfile(
        name="minimal",
        description="Ultra-terse. JSON only, no commentary. Maximum speed.",
        system_prompt=(
            "Robot. JSON only. No text. "
            'Format: {"action": "<verb>", "direction": "<dir>", "speed": 0.5}'
        ),
        emoji_mode=False,
        response_style="terse",
        greeting="Ready.",
        tags=["performance", "terse"],
    ),
]


class PersonalityRegistry:
    """Registry of robot personality profiles.

    Holds built-in and custom profiles.  Tracks the currently active profile.

    Args:
        default_name: Name of the profile to activate on init.
    """

    def __init__(self, default_name: str = "assistant"):
        self._profiles: dict[str, PersonalityProfile] = {p.name: p for p in _BUILTIN_PROFILES}
        self._active_name: str = default_name
        if default_name not in self._profiles:
            logger.warning(
                "Default personality '%s' not found, falling back to 'assistant'",
                default_name,
            )
            self._active_name = "assistant"

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current(self) -> PersonalityProfile:
        """Return the currently active personality profile."""
        return self._profiles[self._active_name]

    @property
    def active_name(self) -> str:
        return self._active_name

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------

    def set_active(self, name: str) -> PersonalityProfile:
        """Switch to a named personality profile.

        Args:
            name: Profile name (case-insensitive).

        Returns:
            The newly active PersonalityProfile.

        Raises:
            ValueError: If the profile name is not registered.
        """
        key = name.lower()
        if key not in self._profiles:
            raise ValueError(f"Unknown personality '{name}'. Available: {sorted(self._profiles)}")
        self._active_name = key
        logger.info("Personality switched to '%s'", key)
        return self._profiles[key]

    def register(self, profile: PersonalityProfile) -> None:
        """Register a custom personality profile."""
        self._profiles[profile.name.lower()] = profile
        logger.info("Registered custom personality '%s'", profile.name)

    def register_from_dict(self, data: dict[str, Any]) -> PersonalityProfile:
        """Create and register a profile from a config dict."""
        profile = PersonalityProfile(
            name=data["name"].lower(),
            description=data.get("description", ""),
            system_prompt=data.get("system_prompt", _BUILTIN_PROFILES[0].system_prompt),
            emoji_mode=bool(data.get("emoji_mode", False)),
            response_style=str(data.get("response_style", "balanced")),
            greeting=data.get("greeting", "Ready."),
            tags=list(data.get("tags", [])),
        )
        self.register(profile)
        return profile

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def list_profiles(self) -> list[dict[str, Any]]:
        """Return all profiles with an ``active`` flag."""
        result = []
        for name, profile in sorted(self._profiles.items()):
            d = profile.to_dict()
            d["active"] = name == self._active_name
            result.append(d)
        return result

    def get(self, name: str) -> Optional[PersonalityProfile]:
        """Return a profile by name, or None if not found."""
        return self._profiles.get(name.lower())

    def init_from_config(self, config: dict[str, Any]) -> None:
        """Load custom personalities from RCAN config ``personalities:`` block."""
        block = config.get("personalities", {})
        default = block.get("default", "assistant")
        for custom in block.get("custom", []):
            try:
                self.register_from_dict(custom)
            except Exception as exc:
                logger.warning("Failed to register custom personality: %s", exc)
        if default:
            try:
                self.set_active(default)
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: Optional[PersonalityRegistry] = None


def get_registry() -> PersonalityRegistry:
    """Return the process-wide PersonalityRegistry."""
    global _registry
    if _registry is None:
        _registry = PersonalityRegistry()
    return _registry


def set_active(name: str) -> PersonalityProfile:
    """Convenience: switch the global active personality."""
    return get_registry().set_active(name)

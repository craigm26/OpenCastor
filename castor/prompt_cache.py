"""
castor/prompt_cache.py — Prompt cache management for OpenCastor.

Implements Claude Code's cache-first design:
  1. Static content FIRST (robot identity, safety rules, capabilities)
  2. Semi-static SECOND (RCAN config, ALMA insights — cached per session)
  3. Dynamic sensor state passed as <castor-state> blocks in USER messages
     NOT in the system prompt — keeps cache prefix stable every tick.

Key principle: The system prompt must be IDENTICAL across ticks for cache hits.
Sensor readings, timestamps, and per-tick state belong in user messages.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CacheStats:
    """Tracks Anthropic prompt cache hit/miss statistics."""

    total_calls: int = 0
    cache_hits: int = 0          # calls where cache_read_input_tokens > 0
    cache_misses: int = 0
    total_tokens_saved: int = 0  # sum of cache_read_input_tokens
    total_tokens_spent: int = 0  # sum of cache_creation_input_tokens

    @property
    def hit_rate(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.cache_hits / self.total_calls

    def record(self, usage) -> None:
        """Record usage from an Anthropic API response."""
        self.total_calls += 1
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        created = getattr(usage, "cache_creation_input_tokens", 0) or 0
        if read > 0:
            self.cache_hits += 1
            self.total_tokens_saved += read
        else:
            self.cache_misses += 1
        self.total_tokens_spent += created

    def alert_if_low(self, threshold: float = 0.5, logger=None) -> bool:
        """Return True (and log warning) if hit rate below threshold after warmup (10+ calls)."""
        if self.total_calls < 10:
            return False
        if self.hit_rate < threshold:
            msg = (
                f"CACHE ALERT: hit rate {self.hit_rate:.1%} below threshold {threshold:.0%} "
                f"({self.cache_hits}/{self.total_calls} hits). "
                "Check that system prompt is not changing between ticks."
            )
            if logger:
                logger.warning(msg)
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "hit_rate": round(self.hit_rate, 3),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "total_calls": self.total_calls,
            "tokens_saved": self.total_tokens_saved,
            "tokens_spent": self.total_tokens_spent,
        }


def build_cached_system_prompt(base_prompt: str, rcan_config: dict | None = None) -> list[dict]:
    """
    Build a system prompt as a list of content blocks with cache_control breakpoints.

    Structure (static first, per Claude Code's lessons):
      Block 0: Base system prompt (robot identity + safety rules) — CACHE HERE
      Block 1: RCAN config summary (if provided) — CACHE HERE (changes per robot, not per tick)

    Returns a list ready to pass as `system=` to anthropic.messages.create().

    Example:
        system = build_cached_system_prompt("You are a robot...", rcan_config)
        response = client.messages.create(model=..., system=system, ...)
    """
    blocks = []

    # Block 0: Base system prompt — most static, cache first
    base_text = base_prompt or "You are an AI-powered robot."
    blocks.append({
        "type": "text",
        "text": base_text,
        "cache_control": {"type": "ephemeral"},  # Cache this breakpoint
    })

    # Block 1: RCAN config summary (robot-specific but session-static)
    if rcan_config:
        rcan_summary = _format_rcan_summary(rcan_config)
        blocks.append({
            "type": "text",
            "text": f"<robot-config>\n{rcan_summary}\n</robot-config>",
            "cache_control": {"type": "ephemeral"},  # Cache up to here too
        })

    return blocks


def build_sensor_reminder(sensor_data: dict) -> str:
    """
    Format sensor state as a <castor-state> block for injection into USER messages.

    Per Claude Code's lesson: instead of modifying the system prompt with
    current sensor readings (which busts the cache), inject dynamic state
    into the next user message. The system prompt stays identical every tick.

    Usage in tiered_brain.py:
        reminder = build_sensor_reminder(sensor_data)
        instruction = f"{reminder}\\n\\n{original_instruction}"
    """
    if not sensor_data:
        return ""

    lines = ["<castor-state>"]
    if "front_distance_m" in sensor_data:
        lines.append(f"  front_distance: {sensor_data['front_distance_m']:.2f}m")
    if "battery_pct" in sensor_data:
        lines.append(f"  battery: {sensor_data['battery_pct']:.0f}%")
    if "speed_ms" in sensor_data:
        lines.append(f"  speed: {sensor_data['speed_ms']:.2f}m/s")
    if "heading_deg" in sensor_data:
        lines.append(f"  heading: {sensor_data['heading_deg']:.1f}°")
    if "obstacles" in sensor_data:
        obs = sensor_data["obstacles"]
        if obs:
            lines.append(f"  obstacles: {', '.join(str(o) for o in obs[:5])}")
    # Any other keys not explicitly handled
    handled = {"front_distance_m", "battery_pct", "speed_ms", "heading_deg", "obstacles"}
    for k, v in sensor_data.items():
        if k not in handled:
            lines.append(f"  {k}: {v}")
    lines.append("</castor-state>")
    return "\n".join(lines)


def _format_rcan_summary(rcan_config: dict) -> str:
    """Summarize RCAN config fields relevant to the AI brain."""
    important_keys = ["robot_name", "description", "physics", "safety", "provider", "model"]
    lines = []
    for key in important_keys:
        if key in rcan_config:
            lines.append(f"{key}: {rcan_config[key]}")
    return "\n".join(lines) if lines else str(rcan_config)[:500]

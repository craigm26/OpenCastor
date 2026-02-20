"""Runtime statistics tracker for OpenCastor.

Tracks per-session metrics and writes them to two places:
  ~/.opencastor/runtime_stats.json   — full structured data (agents, TUI)
  /tmp/opencastor_status_bar.txt     — compact one-liner (tmux status-right)

Usage (from providers / main loop):
    from castor.runtime_stats import record_api_call, record_tick

    # After every LLM call:
    record_api_call(tokens_in=312, tokens_out=48, model="claude-sonnet-4-6")

    # After every robot tick:
    record_tick(tick=42, action="move_forward")
"""

import json
import os
import threading
import time

# ── Paths ──────────────────────────────────────────────────────────────────
_STATS_PATH = os.path.expanduser("~/.opencastor/runtime_stats.json")
_STATUS_BAR_PATH = "/tmp/opencastor_status_bar.txt"

# ── In-memory state ────────────────────────────────────────────────────────
_lock = threading.Lock()
_stats: dict = {
    "tokens_in": 0,
    "tokens_out": 0,
    "tokens_cached": 0,
    "api_calls": 0,
    "bytes_in": 0,
    "bytes_out": 0,
    "tick": 0,
    "last_action": "—",
    "last_model": "—",
    "session_start": time.time(),
    "updated_at": time.time(),
}


# ── Public API ─────────────────────────────────────────────────────────────


def record_api_call(
    tokens_in: int = 0,
    tokens_out: int = 0,
    tokens_cached: int = 0,
    bytes_in: int = 0,
    bytes_out: int = 0,
    model: str = "",
) -> None:
    """Record one provider LLM call."""
    with _lock:
        _stats["tokens_in"] += tokens_in
        _stats["tokens_out"] += tokens_out
        _stats["tokens_cached"] += tokens_cached
        _stats["bytes_in"] += bytes_in
        _stats["bytes_out"] += bytes_out
        _stats["api_calls"] += 1
        if model:
            _stats["last_model"] = model
        _stats["updated_at"] = time.time()
    _flush()


def record_tick(tick: int, action: str = "") -> None:
    """Record a robot tick + current action type."""
    with _lock:
        _stats["tick"] = tick
        if action:
            _stats["last_action"] = action
        _stats["updated_at"] = time.time()
    _flush()


def get_stats() -> dict:
    """Return a snapshot of current stats."""
    with _lock:
        return dict(_stats)


def get_status_bar_string() -> str:
    """Return the compact one-liner for TUI / status bar display."""
    try:
        with open(_STATUS_BAR_PATH) as f:
            return f.read().strip()
    except Exception:
        return " ⏱ 0s │ no data"


def reset() -> None:
    """Reset all counters (call at session start)."""
    with _lock:
        _stats.update(
            {
                "tokens_in": 0,
                "tokens_out": 0,
                "tokens_cached": 0,
                "api_calls": 0,
                "bytes_in": 0,
                "bytes_out": 0,
                "tick": 0,
                "last_action": "—",
                "last_model": "—",
                "session_start": time.time(),
                "updated_at": time.time(),
            }
        )
    _flush()


# ── Formatting helpers ─────────────────────────────────────────────────────


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _fmt_bytes(n: int) -> str:
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f}MB"
    if n >= 1_024:
        return f"{n / 1_024:.1f}KB"
    return f"{n}B"


def _fmt_uptime(secs: float) -> str:
    s = int(secs)
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60}m"
    if s >= 60:
        return f"{s // 60}m{s % 60}s"
    return f"{s}s"


def _short_model(name: str) -> str:
    """Shorten a model name for display: 'anthropic/claude-sonnet-4-6' → 'claude-sonnet-4-6'."""
    if "/" in name:
        name = name.split("/")[-1]
    # Further shorten common names
    name = name.replace("claude-", "").replace("-instruct", "").replace("-preview", "")
    return name[:22]


# ── File flush ─────────────────────────────────────────────────────────────


def _flush() -> None:
    """Write stats to JSON + status bar text file. Never raises."""
    try:
        os.makedirs(os.path.dirname(_STATS_PATH), exist_ok=True)
        with open(_STATS_PATH, "w") as f:
            json.dump(_stats, f)
    except Exception:
        pass

    try:
        uptime = time.time() - _stats["session_start"]
        tok_in = _stats["tokens_in"]
        tok_out = _stats["tokens_out"]
        tok_cached = _stats["tokens_cached"]
        calls = _stats["api_calls"]
        data = _stats["bytes_in"] + _stats["bytes_out"]
        model = _short_model(_stats["last_model"])
        action = _stats["last_action"][:18]
        tick = _stats["tick"]

        parts = [
            f"⏱ {_fmt_uptime(uptime)}",
            f"🧠 {model}",
            f"↓{_fmt_tokens(tok_in)} ↑{_fmt_tokens(tok_out)}",
        ]
        if tok_cached:
            parts.append(f"💾 {_fmt_tokens(tok_cached)} cached")
        parts += [
            f"🔁 {calls} calls",
            f"↕ {_fmt_bytes(data)}",
            f"t{tick}",
            action,
        ]

        bar = "  │  ".join(parts)

        with open(_STATUS_BAR_PATH, "w") as f:
            f.write(f" {bar} ")
    except Exception:
        pass

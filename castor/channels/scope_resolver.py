"""
RCAN scope resolver for channel-boundary access control.

Resolves the (scope, loa) tuple for an inbound message sender based on their
identity and the channel configuration.  Fail-safe: unknown/missing scope
always defaults to "discover" (most restrictive) — never elevated.

Usage::

    from castor.channels.scope_resolver import resolve_sender_scope

    scope, loa = resolve_sender_scope(sender_id, channel_config)
    # e.g. ("chat", 1) for the owner, ("discover", 0) for unknown
"""

from __future__ import annotations

import contextvars
import logging
import re

logger = logging.getLogger("OpenCastor.Channels.ScopeResolver")

# ---------------------------------------------------------------------------
# Scope hierarchy (higher index = more privileged)
# ---------------------------------------------------------------------------
SCOPE_HIERARCHY: list[str] = ["discover", "status", "chat", "admin"]

# ---------------------------------------------------------------------------
# Default scope for each channel type when sender is unknown
# ---------------------------------------------------------------------------
CHANNEL_SCOPE_MAP: dict[str, str] = {
    "whatsapp": "discover",
    "telegram": "discover",
    "slack": "discover",
    "signal": "discover",
    "discord": "discover",
    "matrix": "discover",
    "mqtt": "status",
    "homeassistant": "status",
    "teams": "discover",
}

# ---------------------------------------------------------------------------
# Context variables — carry scope alongside async/threaded call chains
# without changing callback signatures.
# ---------------------------------------------------------------------------
_current_sender_scope: contextvars.ContextVar[str] = contextvars.ContextVar(
    "rcan_sender_scope", default="discover"
)
_current_sender_loa: contextvars.ContextVar[int] = contextvars.ContextVar(
    "rcan_sender_loa", default=0
)

# ---------------------------------------------------------------------------
# Peer RRN pattern — robot-to-robot identifiers
# ---------------------------------------------------------------------------
_PEER_RRN_PATTERN = re.compile(r"^rrn:[a-zA-Z0-9_\-]+:[a-zA-Z0-9_\-]+$")


def resolve_sender_scope(sender_id: str, config: dict) -> tuple[str, int]:
    """Resolve the RCAN (scope, loa) for an inbound channel sender.

    Scope assignment rules (fail-safe — unknown defaults to "discover"):

    1. Owner JID/ID (matches ``owner_id`` or ``admin_ids`` in config)
       → ``"chat"``, loa=1
    2. Allowlisted sender (in ``allow_from``)
       → ``"chat"``, loa=0
    3. Robot peer (sender matches RRN pattern ``rrn:<ns>:<id>``)
       → scope from ``rcan_protocol.peers[sender_id].scope`` or ``"status"``, loa=0
    4. Pairing / unknown sender
       → ``"discover"``, loa=0

    Args:
        sender_id: Normalized sender identifier (phone number, chat ID, etc.)
        config:    Channel configuration dict.

    Returns:
        Tuple ``(scope: str, loa: int)`` where scope is one of
        ``"discover"``, ``"status"``, ``"chat"`` and loa is 0 or 1.
    """
    if not sender_id:
        return ("discover", 0)

    try:
        return _resolve(sender_id, config)
    except Exception as exc:
        # Fail-safe: any resolution error → most restrictive scope
        logger.warning("scope_resolver: unexpected error for sender %r: %s", sender_id, exc)
        return ("discover", 0)


def _resolve(sender_id: str, config: dict) -> tuple[str, int]:
    """Internal resolution logic (not fail-safe — wrapped by resolve_sender_scope)."""
    norm = _normalize_id(sender_id)

    # ── 1. Owner / admin check ────────────────────────────────────────────
    owner_id: str = str(config.get("owner_id") or config.get("owner_number") or "")
    if owner_id and _ids_match(sender_id, norm, owner_id):
        return ("chat", 1)

    admin_ids: list = list(config.get("admin_ids") or [])
    for admin in admin_ids:
        if _ids_match(sender_id, norm, str(admin)):
            return ("chat", 1)

    # ── 2. Peer robot RRN ─────────────────────────────────────────────────
    if _PEER_RRN_PATTERN.match(sender_id):
        peer_scope = _get_peer_scope(sender_id, config)
        return (peer_scope, 0)

    # ── 3. Allowlist ──────────────────────────────────────────────────────
    allow_from: list = list(config.get("allow_from") or [])
    for allowed in allow_from:
        if _ids_match(sender_id, norm, str(allowed)):
            return ("chat", 0)

    # ── 4. Unknown / pairing → most restrictive ───────────────────────────
    return ("discover", 0)


def _normalize_id(s: str) -> str:
    """Strip non-digit characters for loose phone-number comparison."""
    return re.sub(r"\D", "", s) if s else ""


def _ids_match(sender_id: str, sender_norm: str, target: str) -> bool:
    """Return True if sender_id matches target by exact string or digit-stripped comparison."""
    target_norm = _normalize_id(target)
    # Exact string match (covers channel IDs like "C01234567")
    if sender_id == target:
        return True
    # Digit-stripped match (covers phone number variants: +1916… vs 1916…)
    if sender_norm and target_norm and sender_norm == target_norm:
        return True
    return False


def _get_peer_scope(sender_id: str, config: dict) -> str:
    """Return the configured scope for a peer robot RRN, defaulting to "status"."""
    rcan_protocol: dict = dict(config.get("rcan_protocol") or {})
    peers: dict = dict(rcan_protocol.get("peers") or {})
    peer_cfg = peers.get(sender_id)
    if isinstance(peer_cfg, dict):
        return str(peer_cfg.get("scope", "status"))
    return "status"


def scope_index(scope: str) -> int:
    """Return the privilege index of a scope (lower = more restrictive)."""
    try:
        return SCOPE_HIERARCHY.index(scope)
    except ValueError:
        return -1  # unknown scope treated as less than "discover"


def clamp_scope(scope: str, max_scope: str) -> str:
    """Return *scope* clamped to at most *max_scope* privilege."""
    if scope_index(scope) > scope_index(max_scope):
        return max_scope
    return scope

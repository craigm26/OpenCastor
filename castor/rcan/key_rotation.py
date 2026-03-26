"""
castor.rcan.key_rotation — Key rotation stub for RCAN v1.5 (GAP-09).

Prepares OpenCastor for future key rotation without implementing the full
cryptographic key rotation workflow (deferred to v2026.4.x).

Current behaviour:
    - Generates a stable key_id from RRN + config timestamp hash
    - Stores the current key_id in the RCAN config
    - Validates that incoming message key_ids match known/accepted keys
    - All actual Ed25519 key management remains unchanged from v1.4

v2026.4.x TODO:
    - Full JWKS endpoint (/.well-known/rcan-keys.json)
    - Key expiry and rotation ceremony
    - KEY_ROTATION broadcast to peers
    - Revocation certificates

Spec: RCAN §8.6 — Key Lifecycle and Rotation (GAP-09)
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

# Placeholder sentinel for "any key accepted" (used before first rotation)
KEY_ID_WILDCARD = "*"


def derive_key_id(rrn: str, config_created_at: Optional[str] = None) -> str:
    """Derive a stable key_id from the robot's RRN and optional config timestamp.

    The key_id is a short hash that uniquely identifies the current signing key.
    In the full implementation (v2026.4.x) this will be derived from the public
    key material itself (per JWKS kid spec).

    Args:
        rrn:               Robot Registration Number (e.g. "RRN-00000001").
        config_created_at: ISO timestamp string from config metadata.

    Returns:
        8-character hex key_id (deterministic for same inputs).
    """
    # TODO (v2026.4.x): derive from actual Ed25519 public key bytes
    # SAFETY: do not auto-implement — requires access to key material and JWKS spec
    raw = f"{rrn}:{config_created_at or 'default'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def get_current_key_id(config: dict[str, Any]) -> str:
    """Return the current key_id from config, or derive and store it.

    Reads ``security.key_id`` from the config dict. If not present,
    derives it from the RRN + metadata.created_at and writes it back.

    Args:
        config: RCAN config dict (may be mutated to add security.key_id).

    Returns:
        Current key_id string.
    """
    security = config.setdefault("security", {})
    existing = security.get("key_id")
    if existing:
        return existing

    rrn = config.get("rrn") or config.get("metadata", {}).get("rrn") or "RRN-unknown"
    created_at = config.get("metadata", {}).get("created_at")
    key_id = derive_key_id(rrn, created_at)
    security["key_id"] = key_id
    log.debug("key_rotation: derived key_id=%s for rrn=%s", key_id, rrn)
    return key_id


def get_accepted_key_ids(config: dict[str, Any]) -> list[str]:
    """Return the list of currently accepted key_ids for incoming messages.

    In normal operation this is just the current key_id. During a rotation
    window it includes both the old and new key_id.

    Args:
        config: RCAN config dict.

    Returns:
        List of accepted key_id strings.  Includes ``KEY_ID_WILDCARD`` if
        wildcard acceptance is enabled (not recommended for production).
    """
    security = config.get("security", {})
    current = get_current_key_id(config)
    accepted: list[str] = [current]

    # Rotation window: also accept the previous key_id if still in window.
    # The rotation_window_s value (default 300 s) defines how long the old key
    # is accepted after a rotation.  If rotated_at is absent we stay permissive
    # (backward-compatible with configs written before rotate_key() existed).
    previous = security.get("previous_key_id")
    if previous and previous not in accepted:
        rotated_at = security.get("rotated_at")
        rotation_window_s = int(security.get("rotation_window_s", 300))
        if rotated_at is None or (int(time.time()) - int(rotated_at) <= rotation_window_s):
            log.debug("key_rotation: also accepting previous key_id=%s (rotation window)", previous)
            accepted.append(previous)
        else:
            log.debug(
                "key_rotation: previous key_id=%s rotation window expired (%ds ago)",
                previous,
                int(time.time()) - int(rotated_at),
            )

    return accepted


def validate_incoming_key_id(
    incoming_key_id: Optional[str],
    config: dict[str, Any],
) -> bool:
    """Validate that an incoming message's key_id is in the accepted set.

    Args:
        incoming_key_id: key_id from the incoming RCAN message, or None if absent.
        config:          RCAN config dict.

    Returns:
        True if the key_id is accepted.
        False if the key_id is unknown (caller should log a warning and decide
        whether to reject — strict mode) or accept (permissive mode).
    """
    if incoming_key_id is None:
        # Pre-v1.5 message with no key_id — accept in permissive mode
        # TODO (v2026.4.x): strict mode should reject messages without key_id
        # SAFETY: do not auto-implement — changes message acceptance policy; requires config flag
        log.debug("key_rotation: incoming message has no key_id — accepted (permissive mode)")
        return True

    accepted = get_accepted_key_ids(config)

    if KEY_ID_WILDCARD in accepted:
        return True

    if incoming_key_id in accepted:
        return True

    log.warning(
        "key_rotation: unknown key_id=%r — not in accepted set %s. "
        "This may indicate a compromised key or misconfiguration. "
        "Accepting in permissive mode (TODO v2026.4.x: enforce strict mode).",
        incoming_key_id,
        accepted,
    )
    # TODO (v2026.4.x): return False here to enforce strict key validation
    # SAFETY: do not auto-implement — rejection of unknown keys is a security policy change
    return True


def stamp_outgoing_message(
    message_dict: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Add key_id to an outgoing RCAN message dict.

    Args:
        message_dict: Message dict to stamp (mutated in-place).
        config:       RCAN config dict.

    Returns:
        The message_dict with key_id added.
    """
    key_id = get_current_key_id(config)
    message_dict["key_id"] = key_id
    return message_dict


def rotate_key(config: dict[str, Any], new_key_id: str) -> dict[str, Any]:
    """Perform a key rotation — update config to use new_key_id.

    The old key_id is moved to ``security.previous_key_id`` to allow
    in-flight messages signed with the old key to be validated during
    the rotation window.

    Args:
        config:     RCAN config dict (mutated in-place).
        new_key_id: The new key_id to activate.

    Returns:
        The updated config dict.

    Note:
        This stub only rotates the key_id identifier. Full cryptographic
        key rotation (generating new Ed25519 keypair, updating JWKS,
        broadcasting KEY_ROTATION message) is deferred to v2026.4.x.
    """
    # TODO (v2026.4.x): full key rotation ceremony
    # SAFETY: do not auto-implement — requires JWKS endpoint, Ed25519 keypair gen, and peer broadcast
    security = config.setdefault("security", {})
    old_key_id = security.get("key_id")
    if old_key_id:
        security["previous_key_id"] = old_key_id
        log.info(
            "key_rotation: rotating key_id %s → %s (previous_key_id retained in window)",
            old_key_id,
            new_key_id,
        )
    security["key_id"] = new_key_id
    security["rotated_at"] = int(time.time())
    return config

"""Tests for castor.rcan.key_rotation (GAP-09 / RCAN v1.5 key rotation stub)."""

from __future__ import annotations

import time

from castor.rcan.key_rotation import (
    KEY_ID_WILDCARD,
    derive_key_id,
    get_accepted_key_ids,
    get_current_key_id,
    rotate_key,
    stamp_outgoing_message,
    validate_incoming_key_id,
)

# ── derive_key_id ─────────────────────────────────────────────────────────────


def test_derive_key_id_is_8_chars():
    kid = derive_key_id("RRN-00000001")
    assert len(kid) == 8


def test_derive_key_id_deterministic():
    assert derive_key_id("RRN-00000001") == derive_key_id("RRN-00000001")


def test_derive_key_id_differs_by_rrn():
    assert derive_key_id("RRN-00000001") != derive_key_id("RRN-00000002")


def test_derive_key_id_differs_by_created_at():
    a = derive_key_id("RRN-00000001", "2024-01-01T00:00:00Z")
    b = derive_key_id("RRN-00000001", "2025-01-01T00:00:00Z")
    assert a != b


def test_derive_key_id_hex():
    kid = derive_key_id("RRN-99999999", "2024-01-01")
    int(kid, 16)  # should not raise


# ── get_current_key_id ────────────────────────────────────────────────────────


def test_get_current_key_id_returns_existing():
    config = {"security": {"key_id": "aabbccdd"}}
    assert get_current_key_id(config) == "aabbccdd"


def test_get_current_key_id_derives_and_stores():
    config = {"rrn": "RRN-00000042"}
    kid = get_current_key_id(config)
    assert len(kid) == 8
    assert config["security"]["key_id"] == kid


def test_get_current_key_id_uses_metadata_rrn():
    config = {"metadata": {"rrn": "RRN-00000007", "created_at": "2024-06-01"}}
    kid = get_current_key_id(config)
    expected = derive_key_id("RRN-00000007", "2024-06-01")
    assert kid == expected


def test_get_current_key_id_falls_back_to_unknown_rrn():
    config: dict = {}
    kid = get_current_key_id(config)
    assert len(kid) == 8
    assert config["security"]["key_id"] == kid


# ── get_accepted_key_ids ──────────────────────────────────────────────────────


def test_accepted_key_ids_contains_current():
    config = {"security": {"key_id": "aabbccdd"}}
    assert "aabbccdd" in get_accepted_key_ids(config)


def test_accepted_key_ids_no_previous():
    config = {"security": {"key_id": "aabbccdd"}}
    assert get_accepted_key_ids(config) == ["aabbccdd"]


def test_accepted_key_ids_includes_previous_when_in_window():
    """Previous key_id is accepted while rotation window is still open."""
    now = int(time.time())
    config = {
        "security": {
            "key_id": "newkeyid",
            "previous_key_id": "oldkeyid",
            "rotated_at": now - 60,  # rotated 60 s ago
            "rotation_window_s": 300,  # 5-minute window
        }
    }
    accepted = get_accepted_key_ids(config)
    assert "newkeyid" in accepted
    assert "oldkeyid" in accepted


def test_accepted_key_ids_excludes_previous_after_window_expires():
    """Previous key_id is NOT accepted after rotation window has closed."""
    now = int(time.time())
    config = {
        "security": {
            "key_id": "newkeyid",
            "previous_key_id": "oldkeyid",
            "rotated_at": now - 400,  # rotated 400 s ago
            "rotation_window_s": 300,  # 5-minute window → expired
        }
    }
    accepted = get_accepted_key_ids(config)
    assert "newkeyid" in accepted
    assert "oldkeyid" not in accepted


def test_accepted_key_ids_permissive_when_no_rotated_at():
    """If rotated_at is absent, previous key is still accepted (backward compat)."""
    config = {
        "security": {
            "key_id": "newkeyid",
            "previous_key_id": "oldkeyid",
            # no rotated_at
        }
    }
    accepted = get_accepted_key_ids(config)
    assert "oldkeyid" in accepted


def test_accepted_key_ids_custom_window():
    """rotation_window_s config key overrides the default 300-second window."""
    now = int(time.time())
    config = {
        "security": {
            "key_id": "newkeyid",
            "previous_key_id": "oldkeyid",
            "rotated_at": now - 50,
            "rotation_window_s": 30,  # 30 s window → already expired
        }
    }
    accepted = get_accepted_key_ids(config)
    assert "oldkeyid" not in accepted


# ── validate_incoming_key_id ──────────────────────────────────────────────────


def test_validate_none_key_id_permissive():
    config = {"security": {"key_id": "aabbccdd"}}
    assert validate_incoming_key_id(None, config) is True


def test_validate_known_key_id():
    config = {"security": {"key_id": "aabbccdd"}}
    assert validate_incoming_key_id("aabbccdd", config) is True


def test_validate_unknown_key_id_permissive():
    """Unknown key_id returns True in current permissive mode."""
    config = {"security": {"key_id": "aabbccdd"}}
    assert validate_incoming_key_id("unknown1", config) is True


def test_validate_wildcard_accepts_anything():
    config = {"security": {"key_id": KEY_ID_WILDCARD}}
    assert validate_incoming_key_id("whatever", config) is True


def test_validate_previous_key_id_in_window():
    now = int(time.time())
    config = {
        "security": {
            "key_id": "newkeyid",
            "previous_key_id": "oldkeyid",
            "rotated_at": now - 10,
            "rotation_window_s": 300,
        }
    }
    assert validate_incoming_key_id("oldkeyid", config) is True


def test_validate_previous_key_id_after_window():
    """Previous key_id is not in accepted set after window expires,
    but current permissive mode still returns True (not yet strict)."""
    now = int(time.time())
    config = {
        "security": {
            "key_id": "newkeyid",
            "previous_key_id": "oldkeyid",
            "rotated_at": now - 400,
            "rotation_window_s": 300,
        }
    }
    # Permissive mode: still True even for expired previous keys
    assert validate_incoming_key_id("oldkeyid", config) is True


# ── stamp_outgoing_message ────────────────────────────────────────────────────


def test_stamp_outgoing_message_adds_key_id():
    config = {"security": {"key_id": "aabbccdd"}}
    msg: dict = {"action": "ping"}
    result = stamp_outgoing_message(msg, config)
    assert result["key_id"] == "aabbccdd"
    assert result is msg  # mutated in-place


def test_stamp_outgoing_message_derives_key_id_if_absent():
    config = {"rrn": "RRN-12345678"}
    msg: dict = {}
    stamp_outgoing_message(msg, config)
    assert "key_id" in msg
    assert len(msg["key_id"]) == 8


# ── rotate_key ────────────────────────────────────────────────────────────────


def test_rotate_key_updates_key_id():
    config: dict = {"security": {"key_id": "oldkeyid"}}
    rotate_key(config, "newkeyid")
    assert config["security"]["key_id"] == "newkeyid"


def test_rotate_key_saves_previous_key_id():
    config: dict = {"security": {"key_id": "oldkeyid"}}
    rotate_key(config, "newkeyid")
    assert config["security"]["previous_key_id"] == "oldkeyid"


def test_rotate_key_sets_rotated_at():
    config: dict = {"security": {"key_id": "oldkeyid"}}
    before = int(time.time())
    rotate_key(config, "newkeyid")
    after = int(time.time())
    rotated_at = config["security"]["rotated_at"]
    assert before <= rotated_at <= after


def test_rotate_key_no_previous_when_empty():
    """First-time rotation — no previous_key_id if key was absent."""
    config: dict = {}
    rotate_key(config, "firstkeyid")
    assert config["security"]["key_id"] == "firstkeyid"
    assert "previous_key_id" not in config["security"]


def test_rotate_key_returns_config():
    config: dict = {}
    result = rotate_key(config, "keyid123")
    assert result is config


def test_rotate_key_then_window_check():
    """Full round-trip: rotate, then verify window behaviour."""
    config: dict = {"security": {"key_id": "key_v1"}}
    rotate_key(config, "key_v2")

    # Immediately after rotation: previous key still accepted
    assert "key_v1" in get_accepted_key_ids(config)

    # Simulate time passing past window
    config["security"]["rotated_at"] = int(time.time()) - 400
    config["security"]["rotation_window_s"] = 300
    assert "key_v1" not in get_accepted_key_ids(config)
    assert "key_v2" in get_accepted_key_ids(config)

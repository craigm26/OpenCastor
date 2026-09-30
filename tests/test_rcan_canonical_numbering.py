"""RCAN message numbering follows the canonical table in spec §3.2 (3.6.0).

Covers the decode rules that keep older OpenCastor peers interoperable (they
sent pre-3.6.0 integers but always included ``type_name``), the rejection of
unknown names, and the /api/rcan/message auth gate, which must use the same
resolution as the router: before 3.6.0 DISCOVER was 1, and 1 is COMMAND in
§3.2, so a raw-integer check would let a COMMAND through unauthenticated.
"""

from __future__ import annotations

import pytest

from castor.rcan.message import (
    MessageType,
    Priority,
    RCANMessage,
    resolve_message_type,
    resolve_priority,
)

# The canonical table (spec §3.2) and the rcan-py / rcan-ts enums.
CANONICAL = {
    "COMMAND": 1, "RESPONSE": 2, "STATUS": 3, "HEARTBEAT": 4, "CONFIG": 5,
    "SAFETY": 6, "AUTH": 7, "ERROR": 8, "DISCOVER": 9, "PENDING_AUTH": 10,
    "INVOKE": 11, "INVOKE_RESULT": 12, "INVOKE_CANCEL": 13,
    "REGISTRY_REGISTER": 14, "REGISTRY_RESOLVE": 15, "TRANSPARENCY": 16,
    "COMMAND_ACK": 17, "COMMAND_NACK": 18, "ROBOT_REVOCATION": 19,
    "CONSENT_REQUEST": 20, "CONSENT_GRANT": 21, "CONSENT_DENY": 22,
    "FLEET_COMMAND": 23, "SUBSCRIBE": 24, "UNSUBSCRIBE": 25, "FAULT_REPORT": 26,
    "KEY_ROTATION": 27, "COMMAND_COMMIT": 28, "SENSOR_DATA": 29,
    "TRAINING_CONSENT_REQUEST": 30, "TRAINING_CONSENT_GRANT": 31,
    "TRAINING_CONSENT_DENY": 32, "CONTRIBUTE_REQUEST": 33, "CONTRIBUTE_RESULT": 34,
    "CONTRIBUTE_CANCEL": 35, "TRAINING_DATA": 36, "COMPETITION_ENTER": 37,
    "COMPETITION_SCORE": 38, "SEASON_STANDING": 39, "PERSONAL_RESEARCH_RESULT": 40,
    "AUTHORITY_ACCESS": 41, "AUTHORITY_RESPONSE": 42, "FIRMWARE_ATTESTATION": 43,
    "SBOM_UPDATE": 44, "AUTHORIZE": 45,
}


def test_enum_matches_canonical_table_exactly():
    assert {m.name: m.value for m in MessageType} == CANONICAL


def test_rcan_py_enum_agrees_where_installed():
    rcan = pytest.importorskip("rcan.message")
    sdk = {m.name: m.value for m in rcan.MessageType}
    for name, value in sdk.items():
        assert CANONICAL.get(name) == value, name


def test_priority_matches_spec_3_4():
    assert [(p.name, p.value) for p in Priority] == [
        ("LOW", 1), ("NORMAL", 2), ("HIGH", 3), ("SAFETY", 4)
    ]


# Pre-3.6.0 OpenCastor numbering, as an older peer would send it.
LEGACY = {"DISCOVER": 1, "STATUS": 2, "COMMAND": 3, "SAFETY": 6, "ACK": 7,
          "ERROR": 8, "AUTHORIZE": 9, "INVOKE_CANCEL": 15,
          "REGISTRY_REGISTER": 13, "REGISTRY_RESOLVE": 14}


@pytest.mark.parametrize("name,legacy_int", sorted(LEGACY.items()))
def test_older_peer_is_understood_by_name(name, legacy_int):
    """An older peer sends its old integer plus type_name; the name wins."""
    wire = {"type": legacy_int, "type_name": name, "source": "a", "target": "b"}
    assert resolve_message_type(wire) is MessageType[name]


def test_older_peer_priority_is_understood_by_name():
    # Pre-3.6.0 SAFETY was 3 (HIGH in §3.4); the name decides.
    assert resolve_priority({"priority": 3, "priority_name": "SAFETY"}) is Priority.SAFETY
    assert resolve_priority({"priority": 0, "priority_name": "LOW"}) is Priority.LOW


def test_bare_integer_is_canonical():
    assert resolve_message_type({"type": 1}) is MessageType.COMMAND
    assert resolve_message_type({"msg_type": 9}) is MessageType.DISCOVER
    assert resolve_priority({"priority": 4}) is Priority.SAFETY
    assert resolve_priority({}) is Priority.NORMAL


@pytest.mark.parametrize("name", ["STREAM", "EVENT", "HANDOFF", "NOT_A_TYPE"])
def test_unknown_name_is_rejected_not_guessed_from_the_integer(name):
    """Removed legacy names must not fall back to their old integer."""
    with pytest.raises(ValueError):
        resolve_message_type({"type": 4, "type_name": name})


@pytest.mark.parametrize("bad", [None, "", 0, 99, True, 3.0])
def test_missing_or_unknown_type_is_rejected(bad):
    with pytest.raises(ValueError):
        resolve_message_type({"type": bad} if bad is not None else {})


def test_round_trip_emits_canonical_int_and_name():
    msg = RCANMessage.authorize(
        source="rcan://op/u1", target="rcan://robot/main",
        ref_message_id="abc", principal="u1", decision="approve",
    )
    d = msg.to_dict()
    assert d["type"] == 45 and d["type_name"] == "AUTHORIZE"
    assert RCANMessage.from_dict(d).type is MessageType.AUTHORIZE


def test_ack_is_a_response():
    ack = RCANMessage.ack(source="a", target="b", reply_to="x")
    assert ack.to_dict()["type"] == 2
    assert ack.to_dict()["type_name"] == "RESPONSE"


def test_from_dict_rejects_unknown_type_name():
    with pytest.raises(ValueError):
        RCANMessage.from_dict({"type": 3, "type_name": "STREAM", "source": "a", "target": "b"})


# ── /api/rcan/message auth gate ───────────────────────────────────────────


@pytest.fixture()
def api_client(monkeypatch):
    from fastapi.testclient import TestClient

    from castor.api import app, state

    state.config = {}
    state.rcan_router = None
    return TestClient(app, raise_server_exceptions=False)


def _post(client, body):
    return client.post("/api/rcan/message", json=body)


def test_canonical_discover_is_public(api_client):
    r = _post(api_client, {"type": 9, "source": "rcan://t/c"})
    assert r.status_code == 200
    assert "capabilities" in r.json()


def test_older_peer_discover_is_public(api_client):
    r = _post(api_client, {"type": 1, "type_name": "DISCOVER", "source": "rcan://t/c"})
    assert r.status_code == 200
    assert "capabilities" in r.json()


@pytest.mark.parametrize("body", [
    {"type": 1, "source": "rcan://t/c"},                          # COMMAND, bare
    {"msg_type": 1, "source": "rcan://t/c"},                      # COMMAND, msg_type key
    {"type": 1, "type_name": "COMMAND", "source": "rcan://t/c"},
    {"type": 9, "type_name": "COMMAND", "source": "rcan://t/c"},  # name wins over 9
    {"type": 4, "type_name": "STREAM", "source": "rcan://t/c"},   # unknown name
    {"source": "rcan://t/c"},                                     # no type
])
def test_everything_but_discover_needs_auth(api_client, body):
    assert _post(api_client, body).status_code == 401

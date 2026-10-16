"""Tests for RCANMessage envelope."""

import hashlib
import time

import pytest

from castor.rcan.message import (
    LEGACY_OPENCASTOR_NUMBERS,
    RCAN_SPEC_VERSION,
    DelegationHop,
    MediaChunk,
    MessageType,
    Priority,
    RCANMessage,
)


class TestMessageCreation:
    """Creating messages via factory methods."""

    def test_command_message(self):
        msg = RCANMessage.command(
            source="rcan://opencastor.rover.abc/nav",
            target="rcan://opencastor.arm.def/teleop",
            payload={"type": "move", "linear": 0.5},
        )
        assert msg.type == MessageType.COMMAND
        assert msg.priority == Priority.NORMAL
        assert msg.payload["type"] == "move"
        assert "control" in msg.scope
        assert msg.id  # UUID should be set

    def test_status_message(self):
        msg = RCANMessage.status(
            source="rcan://opencastor.rover.abc",
            target="rcan://*.*.*/status",
            payload={"battery": 85, "mode": "active"},
        )
        assert msg.type == MessageType.STATUS
        assert msg.payload["battery"] == 85
        assert "status" in msg.scope

    def test_ack_message(self):
        original = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://d.e.f",
            payload={"type": "stop"},
        )
        ack = RCANMessage.ack(
            source="rcan://d.e.f",
            target="rcan://a.b.c",
            reply_to=original.id,
        )
        assert ack.type == MessageType.ACK
        assert ack.reply_to == original.id

    def test_error_message(self):
        msg = RCANMessage.error(
            source="rcan://a.b.c",
            target="rcan://d.e.f",
            code="UNAUTHORIZED",
            detail="Missing control scope",
        )
        assert msg.type == MessageType.ERROR
        assert msg.payload["code"] == "UNAUTHORIZED"
        assert msg.payload["detail"] == "Missing control scope"

    def test_safety_priority(self):
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://d.e.f",
            payload={"type": "stop"},
            priority=Priority.SAFETY,
        )
        assert msg.is_safety
        assert msg.priority == Priority.SAFETY


class TestMessageSerialization:
    """Round-trip serialization."""

    def test_to_dict(self):
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://d.e.f",
            payload={"type": "move"},
        )
        d = msg.to_dict()
        assert d["type"] == MessageType.COMMAND
        assert d["type_name"] == "COMMAND"
        assert d["priority_name"] == "NORMAL"
        assert d["source"] == "rcan://a.b.c"

    def test_from_dict_with_ints(self):
        d = {
            "id": "test-id",
            "type": 1,  # COMMAND (RCAN §3.2)
            "priority": 1,
            "source": "rcan://a.b.c",
            "target": "rcan://d.e.f",
            "payload": {"x": 1},
            "timestamp": 1000.0,
            "ttl": 0,
            "reply_to": None,
            "scope": ["control"],
            "version": "1.0.0",
        }
        msg = RCANMessage.from_dict(d)
        assert msg.type == MessageType.COMMAND
        assert msg.priority == Priority.NORMAL
        assert msg.id == "test-id"

    def test_from_dict_with_string_names(self):
        d = {
            "id": "test-id-2",
            "type": "COMMAND",
            "priority": "HIGH",
            "source": "rcan://a.b.c",
            "target": "rcan://d.e.f",
            "payload": {},
            "timestamp": 1000.0,
            "ttl": 0,
            "scope": [],
            "version": "1.0.0",
        }
        msg = RCANMessage.from_dict(d)
        assert msg.type == MessageType.COMMAND
        assert msg.priority == Priority.HIGH

    def test_roundtrip(self):
        original = RCANMessage.command(
            source="rcan://opencastor.rover.abc",
            target="rcan://opencastor.arm.def/teleop",
            payload={"type": "move", "linear": 0.5, "angular": -0.2},
            priority=Priority.HIGH,
            scope=["control", "status"],
        )
        d = original.to_dict()
        restored = RCANMessage.from_dict(d)
        assert restored.type == original.type
        assert restored.source == original.source
        assert restored.target == original.target
        assert restored.payload == original.payload
        assert restored.priority == original.priority
        assert restored.scope == original.scope


class TestMessageTTL:
    """TTL expiration logic."""

    def test_no_ttl_never_expires(self):
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://d.e.f",
            payload={},
        )
        assert not msg.is_expired()

    def test_ttl_expired(self):
        msg = RCANMessage(
            type=MessageType.COMMAND,
            source="rcan://a.b.c",
            target="rcan://d.e.f",
            timestamp=time.time() - 100,
            ttl=10,
        )
        assert msg.is_expired()

    def test_ttl_not_expired(self):
        msg = RCANMessage(
            type=MessageType.COMMAND,
            source="rcan://a.b.c",
            target="rcan://d.e.f",
            timestamp=time.time(),
            ttl=3600,
        )
        assert not msg.is_expired()


class TestMessageTypes:
    """Enum coverage."""

    def test_spec_numbering(self):
        # RCAN spec §3.2 (rcan-spec spec/sections/MESSAGE_TYPE_NUMBERING.md)
        assert MessageType.COMMAND == 1
        assert MessageType.STATUS == 3
        assert MessageType.SAFETY == 6
        assert MessageType.ERROR == 8
        assert MessageType.DISCOVER == 9
        assert MessageType.PENDING_AUTH == 10
        assert MessageType.INVOKE == 11
        assert MessageType.INVOKE_RESULT == 12
        assert MessageType.INVOKE_CANCEL == 13
        assert MessageType.REGISTRY_REGISTER == 14
        assert MessageType.REGISTRY_RESOLVE == 15
        assert MessageType.TRANSPARENCY == 16
        assert MessageType.COMMAND_NACK == 18
        assert MessageType.AUTHORIZE == 45

    def test_matches_rcan_py(self):
        """Every spec type has the same number as rcan-py's MessageType."""
        from rcan.message import MessageType as SpecMessageType

        for name, spec_member in SpecMessageType.__members__.items():
            assert name in MessageType.__members__, f"missing spec type {name}"
            assert int(MessageType[name]) == int(spec_member), name

    def test_opencastor_extensions_do_not_collide(self):
        from rcan.message import MessageType as SpecMessageType

        spec_values = {int(m) for m in SpecMessageType}
        assert MessageType.ACK is MessageType.COMMAND_ACK
        for name in (
            "STREAM",
            "EVENT",
            "HANDOFF",
            "REGISTRY_REGISTER_RESULT",
            "REGISTRY_RESOLVE_RESULT",
        ):
            value = int(MessageType[name])
            assert value >= 100, name  # vendor range, spec §3.2
            assert value not in spec_values, name

    def test_ack_serialises_as_command_ack(self):
        d = RCANMessage.ack("rcan://a.b.c", "rcan://d.e.f", "orig").to_dict()
        assert d["type"] == 17
        assert d["type_name"] == "COMMAND_ACK"


class TestLegacyNumberingDecode:
    """Messages from OpenCastor < 3.7 carry type_name; the name wins."""

    @staticmethod
    def _wire(type_value, type_name=None):
        d = {"type": type_value, "source": "rcan://a.b.c", "target": "rcan://d.e.f"}
        if type_name is not None:
            d["type_name"] = type_name
        return d

    @pytest.mark.parametrize(
        "name",
        sorted(LEGACY_OPENCASTOR_NUMBERS),
    )
    def test_legacy_number_with_name_decodes_by_name(self, name, caplog):
        old = LEGACY_OPENCASTOR_NUMBERS[name]
        msg = RCANMessage.from_dict(self._wire(old, name))
        assert msg.type == MessageType[name]
        assert "pre-3.7 OpenCastor numbering" in caplog.text

    def test_bare_number_is_always_spec(self):
        # 3 was COMMAND before 3.7; without type_name it is read as the spec says.
        assert RCANMessage.from_dict(self._wire(3)).type == MessageType.STATUS
        assert RCANMessage.from_dict(self._wire(9)).type == MessageType.DISCOVER

    def test_consistent_number_and_name_no_warning(self, caplog):
        msg = RCANMessage.from_dict(self._wire(1, "COMMAND"))
        assert msg.type == MessageType.COMMAND
        assert "pre-3.7" not in caplog.text

    def test_unknown_type_name_ignored(self):
        assert RCANMessage.from_dict(self._wire(1, "NOT_A_TYPE")).type == MessageType.COMMAND

    def test_roundtrip_new_numbers(self):
        for member in (MessageType.COMMAND, MessageType.DISCOVER, MessageType.AUTHORIZE):
            m = RCANMessage(type=member, source="rcan://a", target="rcan://b")
            assert RCANMessage.from_dict(m.to_dict()).type is member

    def test_all_priorities(self):
        assert len(Priority) == 4
        assert Priority.LOW < Priority.NORMAL < Priority.HIGH < Priority.SAFETY


class TestRCANv12Messages:
    """RCAN v1.2 — AUTHORIZE and PENDING_AUTH factory methods."""

    def test_authorize_approve(self):
        msg = RCANMessage.authorize(
            source="rcan://operator/user1",
            target="rcan://robot/main",
            ref_message_id="abc-123",
            principal="user1",
            decision="approve",
        )
        assert msg.type == MessageType.AUTHORIZE
        assert msg.payload["decision"] == "approve"
        assert msg.payload["principal"] == "user1"
        assert msg.payload["ref_message_id"] == "abc-123"
        assert msg.priority == Priority.HIGH
        assert "hitl" in msg.scope

    def test_authorize_deny(self):
        msg = RCANMessage.authorize(
            source="rcan://operator/user1",
            target="rcan://robot/main",
            ref_message_id="abc-123",
            principal="user1",
            decision="deny",
        )
        assert msg.payload["decision"] == "deny"

    def test_authorize_invalid_decision_raises(self):
        with pytest.raises(ValueError, match="approve.*deny"):
            RCANMessage.authorize(
                source="rcan://operator/user1",
                target="rcan://robot/main",
                ref_message_id="abc-123",
                principal="user1",
                decision="maybe",
            )

    def test_pending_auth(self):
        msg = RCANMessage.pending_auth(
            source="rcan://robot/main",
            target="rcan://operator/user1",
            pending_id="pending-456",
            action_type="motor_command",
            description="Move forward 1m",
            timeout_remaining_ms=30000,
        )
        assert msg.type == MessageType.PENDING_AUTH
        assert msg.payload["pending_id"] == "pending-456"
        assert msg.payload["action_type"] == "motor_command"
        assert msg.payload["timeout_remaining_ms"] == 30000
        assert msg.priority == Priority.HIGH
        assert "hitl" in msg.scope

    def test_authorize_roundtrip(self):
        msg = RCANMessage.authorize(
            source="rcan://operator/user1",
            target="rcan://robot/main",
            ref_message_id="abc-123",
            principal="user1",
            decision="approve",
        )
        d = msg.to_dict()
        assert d["type_name"] == "AUTHORIZE"
        restored = RCANMessage.from_dict(d)
        assert restored.type == MessageType.AUTHORIZE


class TestRCANv30EnvelopeFields:
    """RCAN v3.0 — EU AI Act compliance (§23-§27), fria_ref, mandatory pqc-hybrid-v1."""

    def test_spec_version_is_2_2(self):
        assert RCAN_SPEC_VERSION == "3.0"

    def test_rcan_message_has_new_fields_with_defaults(self):
        msg = RCANMessage(
            type=MessageType.STATUS,
            source="rcan://a.b.c",
            target="rcan://d.e.f",
        )
        assert msg.firmware_hash == ""
        assert msg.attestation_ref == ""
        assert msg.pq_sig == ""
        assert msg.pq_alg == "ml-dsa-65"
        assert msg.delegation_chain == []
        assert msg.media_chunks == []

    def test_delegation_hop_instantiation(self):
        hop = DelegationHop(
            robot_rrn="rrn://opencastor.rover.abc",
            scope="control",
            issued_at="2026-01-01T00:00:00Z",
            expires_at="2026-01-02T00:00:00Z",
        )
        assert hop.robot_rrn == "rrn://opencastor.rover.abc"
        assert hop.scope == "control"
        assert hop.sig == ""

    def test_delegation_hop_with_sig(self):
        hop = DelegationHop(
            robot_rrn="rrn://opencastor.rover.abc",
            scope="control",
            issued_at="2026-01-01T00:00:00Z",
            expires_at="2026-01-02T00:00:00Z",
            sig="abc123",
        )
        assert hop.sig == "abc123"

    def test_media_chunk_verify_hash_passes(self):
        data = "hello world"
        correct_hash = "sha256:" + hashlib.sha256(data.encode()).hexdigest()
        chunk = MediaChunk(
            chunk_id="chunk-1",
            mime_type="text/plain",
            size_bytes=len(data),
            hash_sha256=correct_hash,
            data=data,
        )
        chunk.verify_hash()  # should not raise

    def test_media_chunk_verify_hash_raises_on_mismatch(self):
        chunk = MediaChunk(
            chunk_id="chunk-2",
            mime_type="text/plain",
            size_bytes=5,
            hash_sha256="sha256:wronghash",
            data="hello",
        )
        with pytest.raises(ValueError, match="hash mismatch"):
            chunk.verify_hash()

    def test_delegation_chain_max_depth_3_raises(self):
        hops = [
            {
                "robot_rrn": f"rrn://r{i}",
                "scope": "control",
                "issued_at": "2026-01-01T00:00:00Z",
                "expires_at": "2026-01-02T00:00:00Z",
            }
            for i in range(4)
        ]
        d = {
            "type": MessageType.COMMAND,
            "source": "rcan://a.b.c",
            "target": "rcan://d.e.f",
            "delegation_chain": hops,
        }
        with pytest.raises(ValueError, match="delegation chain max depth is 3"):
            RCANMessage.from_dict(d)

    def test_delegation_chain_exactly_3_is_ok(self):
        hops = [
            {
                "robot_rrn": f"rrn://r{i}",
                "scope": "control",
                "issued_at": "2026-01-01T00:00:00Z",
                "expires_at": "2026-01-02T00:00:00Z",
            }
            for i in range(3)
        ]
        d = {
            "type": MessageType.COMMAND,
            "source": "rcan://a.b.c",
            "target": "rcan://d.e.f",
            "delegation_chain": hops,
        }
        msg = RCANMessage.from_dict(d)
        assert len(msg.delegation_chain) == 3

    def test_new_fields_in_to_dict(self):
        msg = RCANMessage(
            type=MessageType.STATUS,
            source="rcan://a.b.c",
            target="rcan://d.e.f",
            firmware_hash="sha256:abc",
            attestation_ref="rrf://rrn/attestation/latest",
            pq_sig="sig123",
            pq_alg="ml-dsa-65",
        )
        d = msg.to_dict()
        assert d["firmware_hash"] == "sha256:abc"
        assert d["attestation_ref"] == "rrf://rrn/attestation/latest"
        assert d["pq_sig"] == "sig123"
        assert d["pq_alg"] == "ml-dsa-65"
        assert d["delegation_chain"] == []
        assert d["media_chunks"] == []

    def test_new_fields_roundtrip_from_dict(self):
        d = {
            "type": MessageType.STATUS,
            "source": "rcan://a.b.c",
            "target": "rcan://d.e.f",
            "firmware_hash": "sha256:abc",
            "attestation_ref": "rrf://rrn/attestation/latest",
            "pq_sig": "sig456",
            "pq_alg": "ml-dsa-65",
        }
        msg = RCANMessage.from_dict(d)
        assert msg.firmware_hash == "sha256:abc"
        assert msg.attestation_ref == "rrf://rrn/attestation/latest"
        assert msg.pq_sig == "sig456"
        assert msg.pq_alg == "ml-dsa-65"

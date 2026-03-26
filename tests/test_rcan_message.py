"""Tests for RCANMessage envelope."""

import time

import pytest

from castor.rcan.message import MessageType, Priority, RCANMessage


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
            "type": 3,
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

    def test_all_message_types(self):
        # RCAN v1.2 adds AUTHORIZE (9) and PENDING_AUTH (10)
        # RCAN v1.3 §19 adds INVOKE (11), INVOKE_RESULT (12), INVOKE_CANCEL (15)
        # RCAN v1.3 §21 adds REGISTRY_REGISTER (13), REGISTRY_RESOLVE (14)
        # RCAN v1.3 §21 adds REGISTRY_REGISTER_RESULT (16), REGISTRY_RESOLVE_RESULT (17)
        assert len(MessageType) == 44  # RCAN v2.1: types 1-44 (41-44 added in v2.1)
        assert MessageType.DISCOVER == 1
        assert MessageType.ERROR == 8
        assert MessageType.AUTHORIZE == 9
        assert MessageType.PENDING_AUTH == 10
        assert MessageType.INVOKE == 11
        assert MessageType.INVOKE_RESULT == 12
        assert MessageType.REGISTRY_REGISTER == 13
        assert MessageType.REGISTRY_RESOLVE == 14
        assert MessageType.INVOKE_CANCEL == 15
        assert MessageType.REGISTRY_REGISTER_RESULT == 16
        assert MessageType.REGISTRY_RESOLVE_RESULT == 17

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

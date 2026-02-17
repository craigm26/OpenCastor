"""Tests for RCAN Message Router."""

import pytest

from castor.rcan.capabilities import CapabilityRegistry
from castor.rcan.message import MessageType, RCANMessage
from castor.rcan.rbac import RCANPrincipal, RCANRole
from castor.rcan.router import MessageRouter
from castor.rcan.ruri import RURI


@pytest.fixture
def ruri():
    return RURI("opencastor", "rover", "abc12345")


@pytest.fixture
def caps():
    config = {
        "agent": {"provider": "anthropic", "model": "claude-opus-4-6"},
        "physics": {"type": "differential_drive", "dof": 2},
        "drivers": [{"protocol": "pca9685_i2c"}],
    }
    return CapabilityRegistry(config)


@pytest.fixture
def router(ruri, caps):
    r = MessageRouter(ruri, caps)
    # Register a simple status handler
    r.register_handler("status", lambda msg, p: {"uptime": 42.0, "mode": "active"})
    r.register_handler("nav", lambda msg, p: {"accepted": True})
    r.register_handler("teleop", lambda msg, p: msg.payload)
    r.register_handler("chat", lambda msg, p: {"reply": "Hello!"})
    return r


class TestRouterBasic:
    """Basic routing."""

    def test_route_status(self, router):
        msg = RCANMessage.command(
            source="rcan://client.app.xyz",
            target="rcan://opencastor.rover.abc12345/status",
            payload={},
        )
        resp = router.route(msg)
        assert resp.type == MessageType.ACK
        assert resp.payload["uptime"] == 42.0
        assert resp.reply_to == msg.id

    def test_route_nav(self, router):
        msg = RCANMessage.command(
            source="rcan://client.app.xyz",
            target="rcan://opencastor.rover.abc12345/nav",
            payload={"type": "move", "linear": 0.5},
        )
        principal = RCANPrincipal(name="user1", role=RCANRole.USER)
        resp = router.route(msg, principal)
        assert resp.type == MessageType.ACK
        assert resp.payload["accepted"]

    def test_messages_routed_counter(self, router):
        assert router.messages_routed == 0
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/status",
            payload={},
        )
        router.route(msg)
        assert router.messages_routed == 1
        router.route(msg)
        assert router.messages_routed == 2

    def test_default_capability_is_status(self, router):
        """Target without capability path defaults to 'status'."""
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345",
            payload={},
        )
        resp = router.route(msg)
        assert resp.type == MessageType.ACK


class TestRouterValidation:
    """Validation and error cases."""

    def test_invalid_target_ruri(self, router):
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="http://not-rcan",
            payload={},
        )
        resp = router.route(msg)
        assert resp.type == MessageType.ERROR
        assert resp.payload["code"] == "INVALID_TARGET"

    def test_target_not_matching(self, router):
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://other.bot.xyz/status",
            payload={},
        )
        resp = router.route(msg)
        assert resp.type == MessageType.ERROR
        assert resp.payload["code"] == "NOT_FOR_ME"

    def test_wildcard_target_matches(self, router):
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://*.*.*/status",
            payload={},
        )
        resp = router.route(msg)
        assert resp.type == MessageType.ACK

    def test_expired_message(self, router):
        msg = RCANMessage(
            type=MessageType.COMMAND,
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/status",
            payload={},
            timestamp=1.0,  # Way in the past
            ttl=1,
        )
        resp = router.route(msg)
        assert resp.type == MessageType.ERROR
        assert resp.payload["code"] == "EXPIRED"

    def test_capability_not_found(self, router):
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/nonexistent",
            payload={},
        )
        resp = router.route(msg)
        assert resp.type == MessageType.ERROR
        assert resp.payload["code"] == "CAPABILITY_NOT_FOUND"

    def test_no_handler_registered(self, ruri, caps):
        router = MessageRouter(ruri, caps)  # No handlers registered
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/status",
            payload={},
        )
        resp = router.route(msg)
        assert resp.type == MessageType.ERROR
        assert resp.payload["code"] == "NO_HANDLER"


class TestRouterAuthorization:
    """RBAC scope enforcement."""

    def test_guest_can_read_status(self, router):
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/status",
            payload={},
        )
        guest = RCANPrincipal(name="guest1", role=RCANRole.GUEST)
        resp = router.route(msg, guest)
        assert resp.type == MessageType.ACK

    def test_guest_cannot_control(self, router):
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/nav",
            payload={"type": "move"},
        )
        guest = RCANPrincipal(name="guest1", role=RCANRole.GUEST)
        resp = router.route(msg, guest)
        assert resp.type == MessageType.ERROR
        assert resp.payload["code"] == "UNAUTHORIZED"

    def test_user_can_control(self, router):
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/teleop",
            payload={"type": "move", "linear": 0.3},
        )
        user = RCANPrincipal(name="user1", role=RCANRole.USER)
        resp = router.route(msg, user)
        assert resp.type == MessageType.ACK

    def test_no_principal_allows_all(self, router):
        """When no principal is provided (e.g. local calls), allow through."""
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/nav",
            payload={},
        )
        resp = router.route(msg)  # No principal
        assert resp.type == MessageType.ACK


class TestRouterHandlerErrors:
    """Handler exception handling."""

    def test_handler_exception_returns_error(self, ruri, caps):
        router = MessageRouter(ruri, caps)

        def bad_handler(msg, p):
            raise ValueError("something went wrong")

        router.register_handler("status", bad_handler)
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/status",
            payload={},
        )
        resp = router.route(msg)
        assert resp.type == MessageType.ERROR
        assert resp.payload["code"] == "HANDLER_ERROR"
        assert "something went wrong" in resp.payload["detail"]

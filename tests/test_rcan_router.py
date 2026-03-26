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


@pytest.fixture
def admin_principal():
    """A fully-privileged principal for tests that exercise routing, not auth."""
    return RCANPrincipal(name="test-admin", role=RCANRole.CREATOR)


class TestRouterBasic:
    """Basic routing."""

    def test_route_status(self, router, admin_principal):
        msg = RCANMessage.command(
            source="rcan://client.app.xyz",
            target="rcan://opencastor.rover.abc12345/status",
            payload={},
        )
        resp = router.route(msg, admin_principal)
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

    def test_messages_routed_counter(self, router, admin_principal):
        assert router.messages_routed == 0
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/status",
            payload={},
        )
        router.route(msg, admin_principal)
        assert router.messages_routed == 1
        router.route(msg, admin_principal)
        assert router.messages_routed == 2

    def test_default_capability_is_status(self, router, admin_principal):
        """Target without capability path defaults to 'status'."""
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345",
            payload={},
        )
        resp = router.route(msg, admin_principal)
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

    def test_wildcard_target_matches(self, router, admin_principal):
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://*.*.*/status",
            payload={},
        )
        resp = router.route(msg, admin_principal)
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

    def test_capability_not_found(self, router, admin_principal):
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/nonexistent",
            payload={},
        )
        resp = router.route(msg, admin_principal)
        assert resp.type == MessageType.ERROR
        assert resp.payload["code"] == "CAPABILITY_NOT_FOUND"

    def test_no_handler_registered(self, ruri, caps, admin_principal):
        router = MessageRouter(ruri, caps)  # No handlers registered
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/status",
            payload={},
        )
        resp = router.route(msg, admin_principal)
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

    def test_no_principal_denied(self, router):
        """None principal is treated as unauthorized (security fix #703)."""
        msg = RCANMessage.command(
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/nav",
            payload={},
        )
        resp = router.route(msg)  # No principal → should be denied
        assert resp.type == MessageType.ERROR
        assert "UNAUTHORIZED" in (resp.payload or {}).get("code", "")


class TestInvokeFamily:
    """INVOKE / INVOKE_CANCEL / INVOKE_RESULT routing."""

    @pytest.fixture
    def registry(self):
        from castor.rcan.invoke import SkillRegistry

        reg = SkillRegistry()
        reg.register_fn("echo", lambda params: {"echo": params.get("msg", "")})
        return reg

    @pytest.fixture
    def invoke_router(self, ruri, caps, registry):
        r = MessageRouter(ruri, caps, skill_registry=registry)
        r.register_handler("status", lambda msg, p: {"uptime": 0.0})
        return r

    def _invoke_msg(self, ruri_str: str, skill: str, params: dict | None = None):
        return RCANMessage(
            type=MessageType.INVOKE,
            source="rcan://client.app.xyz",
            target=ruri_str,
            payload={"skill": skill, "params": params or {}},
        )

    def test_invoke_routes_to_skill(self, invoke_router):
        msg = self._invoke_msg("rcan://opencastor.rover.abc12345/status", "echo", {"msg": "hi"})
        resp = invoke_router.route(msg)
        assert resp.type == MessageType.ACK
        assert resp.payload["status"] == "success"
        assert resp.payload["result"]["echo"] == "hi"

    def test_invoke_unknown_skill_returns_not_found(self, invoke_router):
        msg = self._invoke_msg("rcan://opencastor.rover.abc12345/status", "no_such_skill")
        resp = invoke_router.route(msg)
        assert resp.type == MessageType.ACK
        assert resp.payload["status"] == "not_found"

    def test_invoke_no_registry_returns_error(self, router):
        """Router without skill registry returns NO_SKILL_REGISTRY error."""
        msg = RCANMessage(
            type=MessageType.INVOKE,
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/status",
            payload={"skill": "echo"},
        )
        resp = router.route(msg)
        assert resp.type == MessageType.ERROR
        assert resp.payload["code"] == "NO_SKILL_REGISTRY"

    def test_invoke_cancel_found(self, invoke_router, registry):
        """INVOKE_CANCEL with a known msg_id signals the cancel event."""
        import threading

        registry._cancel_events["fake-id-123"] = threading.Event()
        msg = RCANMessage(
            type=MessageType.INVOKE_CANCEL,
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/status",
            payload={"msg_id": "fake-id-123", "reason": "user abort"},
        )
        resp = invoke_router.route(msg)
        assert resp.type == MessageType.ACK
        assert resp.payload["cancelled"] is True

    def test_invoke_cancel_not_found(self, invoke_router):
        """INVOKE_CANCEL for unknown msg_id returns ACK with cancelled=False."""
        msg = RCANMessage(
            type=MessageType.INVOKE_CANCEL,
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/status",
            payload={"msg_id": "no-such-id"},
        )
        resp = invoke_router.route(msg)
        assert resp.type == MessageType.ACK
        assert resp.payload["cancelled"] is False

    def test_invoke_cancel_missing_msg_id(self, invoke_router):
        msg = RCANMessage(
            type=MessageType.INVOKE_CANCEL,
            source="rcan://a.b.c",
            target="rcan://opencastor.rover.abc12345/status",
            payload={},
        )
        resp = invoke_router.route(msg)
        assert resp.type == MessageType.ERROR
        assert resp.payload["code"] == "MISSING_MSG_ID"

    def test_invoke_result_type_exists(self):
        """MessageType.INVOKE_RESULT is defined."""
        assert MessageType.INVOKE_RESULT == 12

    def test_messages_routed_increments_for_invoke(self, invoke_router):
        before = invoke_router.messages_routed
        msg = self._invoke_msg("rcan://opencastor.rover.abc12345/status", "echo")
        invoke_router.route(msg)
        assert invoke_router.messages_routed == before + 1


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
        principal = RCANPrincipal(name="test-admin", role=RCANRole.CREATOR)
        resp = router.route(msg, principal)
        assert resp.type == MessageType.ERROR
        assert resp.payload["code"] == "HANDLER_ERROR"
        assert "something went wrong" in resp.payload["detail"]

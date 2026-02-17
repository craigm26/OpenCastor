"""Tests for work authorization module."""

from __future__ import annotations

import time

import pytest

from castor.safety.authorization import (
    AUTHORIZED_ROLES,
    DestructiveActionDetector,
    WorkAuthority,
    WorkOrder,
)


@pytest.fixture()
def roles() -> dict[str, str]:
    return {
        "creator_alice": "CREATOR",
        "owner_bob": "OWNER",
        "operator_carol": "OPERATOR",
        "viewer_dave": "VIEWER",
    }


@pytest.fixture()
def authority(roles: dict[str, str]) -> WorkAuthority:
    return WorkAuthority(role_resolver=roles, ttl=3600.0)


# --- WorkOrder dataclass ---


class TestWorkOrder:
    def test_new_order_not_approved(self) -> None:
        wo = WorkOrder(
            order_id="test-1",
            action_type="cut",
            target="/dev/gpio/pin7",
            requested_by="someone",
        )
        assert not wo.is_approved
        assert not wo.is_expired
        assert not wo.is_valid

    def test_approved_order_is_valid(self) -> None:
        wo = WorkOrder(
            order_id="test-2",
            action_type="cut",
            target="/dev/gpio/pin7",
            requested_by="someone",
            authorized_by="creator",
            authorized_at=time.time(),
            expires_at=time.time() + 3600,
        )
        assert wo.is_approved
        assert wo.is_valid

    def test_expired_order_not_valid(self) -> None:
        wo = WorkOrder(
            order_id="test-3",
            action_type="cut",
            target="/dev/gpio/pin7",
            requested_by="someone",
            authorized_by="creator",
            authorized_at=time.time() - 7200,
            expires_at=time.time() - 1,
        )
        assert wo.is_expired
        assert not wo.is_valid

    def test_executed_order_not_valid(self) -> None:
        wo = WorkOrder(
            order_id="test-4",
            action_type="cut",
            target="/dev/gpio/pin7",
            requested_by="someone",
            authorized_by="creator",
            authorized_at=time.time(),
            expires_at=time.time() + 3600,
            executed=True,
        )
        assert not wo.is_valid

    def test_revoked_order_not_valid(self) -> None:
        wo = WorkOrder(
            order_id="test-5",
            action_type="cut",
            target="/dev/gpio/pin7",
            requested_by="someone",
            authorized_by="creator",
            authorized_at=time.time(),
            expires_at=time.time() + 3600,
            revoked=True,
        )
        assert not wo.is_valid
        assert not wo.is_approved


# --- WorkAuthority: create, approve, execute ---


class TestWorkAuthorityBasic:
    def test_request_and_approve(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("cut", "/dev/gpio/pin7", "operator_carol")
        assert wo.order_id
        assert not wo.is_approved

        # CREATOR approves
        assert authority.approve(wo.order_id, "creator_alice")
        assert wo.is_approved
        assert wo.is_valid

    def test_approve_and_execute(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("weld", "joint_a", "operator_carol")
        authority.approve(wo.order_id, "creator_alice")
        assert authority.mark_executed(wo.order_id)
        assert wo.executed
        assert not wo.is_valid

    def test_check_authorization(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("drill", "wall_b", "operator_carol")
        assert authority.check_authorization("drill", "wall_b") is None  # not approved yet
        authority.approve(wo.order_id, "creator_alice")
        found = authority.check_authorization("drill", "wall_b")
        assert found is not None
        assert found.order_id == wo.order_id

    def test_check_authorization_wrong_target(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("drill", "wall_b", "operator_carol")
        authority.approve(wo.order_id, "creator_alice")
        assert authority.check_authorization("drill", "wall_c") is None

    def test_list_pending_and_active(self, authority: WorkAuthority) -> None:
        wo1 = authority.request_authorization("cut", "a", "operator_carol")
        wo2 = authority.request_authorization("burn", "b", "operator_carol")
        assert len(authority.list_pending()) == 2
        assert len(authority.list_active()) == 0

        authority.approve(wo1.order_id, "creator_alice")
        assert len(authority.list_pending()) == 1
        assert len(authority.list_active()) == 1


# --- Expiration ---


class TestExpiration:
    def test_expired_order_not_in_active(self) -> None:
        auth = WorkAuthority(
            role_resolver={"c": "CREATOR", "o": "OPERATOR"}, ttl=0.01
        )
        wo = auth.request_authorization("cut", "x", "o")
        auth.approve(wo.order_id, "c")
        time.sleep(0.02)
        assert len(auth.list_active()) == 0
        assert authority_check_gone(auth, "cut", "x")

    def test_expired_order_cannot_execute(self) -> None:
        auth = WorkAuthority(
            role_resolver={"c": "CREATOR", "o": "OPERATOR"}, ttl=0.01
        )
        wo = auth.request_authorization("cut", "x", "o")
        auth.approve(wo.order_id, "c")
        time.sleep(0.02)
        assert not auth.mark_executed(wo.order_id)


def authority_check_gone(auth: WorkAuthority, action: str, target: str) -> bool:
    return auth.check_authorization(action, target) is None


# --- Role-gated approval ---


class TestRoleGating:
    def test_operator_cannot_approve(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("cut", "x", "viewer_dave")
        assert not authority.approve(wo.order_id, "operator_carol")
        assert not wo.is_approved

    def test_viewer_cannot_approve(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("cut", "x", "operator_carol")
        assert not authority.approve(wo.order_id, "viewer_dave")

    def test_owner_can_approve_owner_required(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization(
            "grind", "x", "operator_carol", required_role="OWNER"
        )
        assert authority.approve(wo.order_id, "owner_bob")
        assert wo.is_valid

    def test_owner_cannot_approve_creator_required(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization(
            "demolish", "building", "operator_carol", required_role="CREATOR"
        )
        assert not authority.approve(wo.order_id, "owner_bob")

    def test_creator_can_approve_owner_required(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization(
            "grind", "x", "operator_carol", required_role="OWNER"
        )
        assert authority.approve(wo.order_id, "creator_alice")

    def test_self_approval_denied(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("cut", "x", "creator_alice")
        assert not authority.approve(wo.order_id, "creator_alice")

    def test_invalid_required_role_rejected(self, authority: WorkAuthority) -> None:
        with pytest.raises(ValueError, match="required_role"):
            authority.request_authorization("cut", "x", "a", required_role="OPERATOR")


# --- Revocation ---


class TestRevocation:
    def test_revoke_order(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("cut", "x", "operator_carol")
        authority.approve(wo.order_id, "creator_alice")
        assert wo.is_valid
        assert authority.revoke(wo.order_id, "creator_alice")
        assert not wo.is_valid
        assert wo.revoked

    def test_operator_cannot_revoke(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("cut", "x", "operator_carol")
        authority.approve(wo.order_id, "creator_alice")
        assert not authority.revoke(wo.order_id, "operator_carol")
        assert wo.is_valid

    def test_revoke_nonexistent(self, authority: WorkAuthority) -> None:
        assert not authority.revoke("no-such-id", "creator_alice")


# --- Destructive action detection ---


class TestDestructiveDetection:
    def test_gpio_path(self) -> None:
        d = DestructiveActionDetector()
        assert d.is_destructive_path("/dev/gpio/pin7")
        assert d.is_destructive_path("/dev/gpio/heating_element")
        assert not d.is_destructive_path("/home/user/file.txt")

    def test_motor_command(self) -> None:
        d = DestructiveActionDetector()
        assert d.is_destructive_command("motor_1 speed: 95")
        assert not d.is_destructive_command("motor_1 speed: 30")

    def test_custom_patterns(self) -> None:
        d = DestructiveActionDetector(extra_patterns=[r"laser_on"])
        assert d.classify("laser_on beam_a")
        assert not d.classify("laser_off beam_a")

    def test_authority_requires_auth(self, authority: WorkAuthority) -> None:
        assert authority.requires_authorization("/dev/gpio/pin7")
        assert not authority.requires_authorization("/tmp/safe.txt")


# --- Audit log ---


class TestAuditLog:
    def test_audit_entries(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("cut", "x", "operator_carol")
        authority.approve(wo.order_id, "creator_alice")
        log = authority.get_audit_log()
        events = [e["event"] for e in log]
        assert "requested" in events
        assert "approved" in events

    def test_denied_approval_logged(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("cut", "x", "operator_carol")
        authority.approve(wo.order_id, "operator_carol")  # denied
        log = authority.get_audit_log()
        events = [e["event"] for e in log]
        assert "approve_denied" in events


# --- Security iteration checks ---


class TestSecurityHardening:
    """Verify no privilege escalation or stale order abuse."""

    def test_double_approve_rejected(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("cut", "x", "operator_carol")
        assert authority.approve(wo.order_id, "creator_alice")
        assert not authority.approve(wo.order_id, "owner_bob")

    def test_approve_revoked_order_rejected(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("cut", "x", "operator_carol")
        authority.revoke(wo.order_id, "creator_alice")
        assert not authority.approve(wo.order_id, "creator_alice")

    def test_execute_unapproved_fails(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("cut", "x", "operator_carol")
        assert not authority.mark_executed(wo.order_id)

    def test_execute_twice_fails(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("cut", "x", "operator_carol")
        authority.approve(wo.order_id, "creator_alice")
        assert authority.mark_executed(wo.order_id)
        assert not authority.mark_executed(wo.order_id)

    def test_unknown_principal_cannot_approve(self, authority: WorkAuthority) -> None:
        wo = authority.request_authorization("cut", "x", "operator_carol")
        assert not authority.approve(wo.order_id, "unknown_eve")

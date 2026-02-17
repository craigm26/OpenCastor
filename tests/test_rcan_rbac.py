"""Tests for RCAN RBAC (Role-Based Access Control)."""

from castor.fs.permissions import Cap
from castor.rcan.rbac import (
    RCANPrincipal,
    RCANRole,
    Scope,
)


class TestRCANRole:
    """Role hierarchy."""

    def test_role_ordering(self):
        assert RCANRole.GUEST < RCANRole.USER < RCANRole.OPERATOR
        assert RCANRole.OPERATOR < RCANRole.ADMIN < RCANRole.CREATOR

    def test_role_values(self):
        assert RCANRole.GUEST == 1
        assert RCANRole.CREATOR == 5

    def test_five_tiers(self):
        assert len(RCANRole) == 5


class TestScope:
    """Scope flag operations."""

    def test_scope_for_guest(self):
        s = Scope.for_role(RCANRole.GUEST)
        assert s & Scope.STATUS
        assert not (s & Scope.CONTROL)

    def test_scope_for_user(self):
        s = Scope.for_role(RCANRole.USER)
        assert s & Scope.STATUS
        assert s & Scope.CONTROL
        assert not (s & Scope.CONFIG)

    def test_scope_for_operator(self):
        s = Scope.for_role(RCANRole.OPERATOR)
        assert s & Scope.STATUS
        assert s & Scope.CONTROL
        assert s & Scope.CONFIG
        assert not (s & Scope.TRAINING)

    def test_scope_for_admin(self):
        s = Scope.for_role(RCANRole.ADMIN)
        assert s & Scope.TRAINING
        assert not (s & Scope.ADMIN)

    def test_scope_for_creator(self):
        s = Scope.for_role(RCANRole.CREATOR)
        assert s & Scope.ADMIN
        assert s & Scope.TRAINING
        assert s & Scope.CONFIG
        assert s & Scope.CONTROL
        assert s & Scope.STATUS

    def test_from_strings(self):
        s = Scope.from_strings(["status", "control"])
        assert s & Scope.STATUS
        assert s & Scope.CONTROL
        assert not (s & Scope.CONFIG)

    def test_from_strings_case_insensitive(self):
        s = Scope.from_strings(["STATUS", "Control"])
        assert s & Scope.STATUS
        assert s & Scope.CONTROL

    def test_to_strings(self):
        s = Scope.STATUS | Scope.CONTROL
        names = s.to_strings()
        assert "status" in names
        assert "control" in names
        assert "config" not in names

    def test_roundtrip(self):
        original = Scope.STATUS | Scope.CONFIG | Scope.TRAINING
        names = original.to_strings()
        restored = Scope.from_strings(names)
        assert restored == original


class TestRCANPrincipal:
    """Principal creation and operations."""

    def test_default_scopes_from_role(self):
        p = RCANPrincipal(name="test", role=RCANRole.USER)
        assert p.has_scope(Scope.STATUS)
        assert p.has_scope(Scope.CONTROL)
        assert not p.has_scope(Scope.CONFIG)

    def test_explicit_scopes_override(self):
        p = RCANPrincipal(name="test", role=RCANRole.USER, scopes=Scope.STATUS)
        assert p.has_scope(Scope.STATUS)
        assert not p.has_scope(Scope.CONTROL)

    def test_from_legacy_root(self):
        p = RCANPrincipal.from_legacy("root")
        assert p.role == RCANRole.CREATOR
        assert p.has_scope(Scope.ADMIN)

    def test_from_legacy_brain(self):
        p = RCANPrincipal.from_legacy("brain")
        assert p.role == RCANRole.ADMIN
        assert p.has_scope(Scope.TRAINING)

    def test_from_legacy_api(self):
        p = RCANPrincipal.from_legacy("api")
        assert p.role == RCANRole.OPERATOR
        assert p.has_scope(Scope.CONFIG)

    def test_from_legacy_channel(self):
        p = RCANPrincipal.from_legacy("channel")
        assert p.role == RCANRole.USER
        assert p.has_scope(Scope.CONTROL)

    def test_from_legacy_driver(self):
        p = RCANPrincipal.from_legacy("driver")
        assert p.role == RCANRole.GUEST
        assert p.has_scope(Scope.STATUS)
        assert not p.has_scope(Scope.CONTROL)

    def test_from_legacy_unknown_defaults_to_guest(self):
        p = RCANPrincipal.from_legacy("unknown_user")
        assert p.role == RCANRole.GUEST

    def test_to_caps_control(self):
        p = RCANPrincipal(name="test", role=RCANRole.USER)
        caps = p.to_caps()
        assert caps & Cap.MOTOR_WRITE
        assert caps & Cap.DEVICE_ACCESS
        assert caps & Cap.ESTOP
        assert not (caps & Cap.SAFETY_OVERRIDE)

    def test_to_caps_admin(self):
        p = RCANPrincipal(name="test", role=RCANRole.CREATOR)
        caps = p.to_caps()
        assert caps & Cap.SAFETY_OVERRIDE
        assert caps & Cap.MEMORY_WRITE
        assert caps & Cap.CONFIG_WRITE

    def test_rate_limit(self):
        assert RCANPrincipal(name="g", role=RCANRole.GUEST).rate_limit == 10
        assert RCANPrincipal(name="u", role=RCANRole.USER).rate_limit == 100
        assert RCANPrincipal(name="c", role=RCANRole.CREATOR).rate_limit == 0

    def test_session_timeout(self):
        assert RCANPrincipal(name="g", role=RCANRole.GUEST).session_timeout == 300
        assert RCANPrincipal(name="c", role=RCANRole.CREATOR).session_timeout == 0

    def test_to_dict(self):
        p = RCANPrincipal(name="operator1", role=RCANRole.OPERATOR)
        d = p.to_dict()
        assert d["name"] == "operator1"
        assert d["role"] == "OPERATOR"
        assert d["role_level"] == 3
        assert "status" in d["scopes"]
        assert "control" in d["scopes"]
        assert "config" in d["scopes"]

    def test_fleet_default_empty(self):
        p = RCANPrincipal(name="test", role=RCANRole.USER)
        assert p.fleet == []

    def test_fleet_custom(self):
        p = RCANPrincipal(name="test", role=RCANRole.USER, fleet=["rcan://opencastor.*.*/nav"])
        assert len(p.fleet) == 1

"""Tests for RCAN JWT Authentication."""

import time

import pytest

from castor.rcan.rbac import RCANRole, Scope

# Skip all tests if PyJWT is not installed
jwt = pytest.importorskip("jwt", reason="PyJWT not installed")

from castor.rcan.jwt_auth import RCANTokenManager


@pytest.fixture
def manager():
    return RCANTokenManager(
        secret="test-secret-key-for-unit-tests",
        issuer="rcan://opencastor.testbot.abc12345",
    )


class TestTokenManager:
    """Token manager lifecycle."""

    def test_enabled_with_secret(self, manager):
        assert manager.enabled

    def test_disabled_without_secret(self):
        m = RCANTokenManager(secret="")
        assert not m.enabled

    def test_default_issuer(self):
        m = RCANTokenManager(secret="x")
        assert "opencastor" in m.issuer


class TestIssueToken:
    """Token issuance."""

    def test_issue_guest_token(self, manager):
        token = manager.issue("guest1", role=RCANRole.GUEST)
        assert isinstance(token, str)
        assert len(token) > 20

    def test_issue_operator_token(self, manager):
        token = manager.issue("op1", role=RCANRole.OPERATOR)
        claims = manager.decode_claims(token)
        assert claims["sub"] == "op1"
        assert claims["role"] == "OPERATOR"
        assert "status" in claims["scope"]
        assert "control" in claims["scope"]
        assert "config" in claims["scope"]

    def test_custom_scopes(self, manager):
        token = manager.issue("user1", role=RCANRole.USER,
                              scopes=["status"])
        claims = manager.decode_claims(token)
        assert claims["scope"] == ["status"]

    def test_custom_ttl(self, manager):
        token = manager.issue("user1", role=RCANRole.USER, ttl_seconds=60)
        claims = manager.decode_claims(token)
        assert claims["exp"] - claims["iat"] == 60

    def test_fleet_claim(self, manager):
        fleet = ["rcan://opencastor.rover.*/nav"]
        token = manager.issue("fleet_user", role=RCANRole.USER, fleet=fleet)
        claims = manager.decode_claims(token)
        assert claims["fleet"] == fleet

    def test_issuer_claim(self, manager):
        token = manager.issue("test", role=RCANRole.GUEST)
        claims = manager.decode_claims(token)
        assert claims["iss"] == "rcan://opencastor.testbot.abc12345"


class TestVerifyToken:
    """Token verification."""

    def test_verify_valid_token(self, manager):
        token = manager.issue("op1", role=RCANRole.OPERATOR)
        principal = manager.verify(token)
        assert principal.name == "op1"
        assert principal.role == RCANRole.OPERATOR
        assert principal.has_scope(Scope.STATUS)
        assert principal.has_scope(Scope.CONTROL)
        assert principal.has_scope(Scope.CONFIG)

    def test_verify_preserves_fleet(self, manager):
        fleet = ["rcan://acme.*.*/nav"]
        token = manager.issue("u1", role=RCANRole.USER, fleet=fleet)
        principal = manager.verify(token)
        assert principal.fleet == fleet

    def test_verify_expired_token(self, manager):
        token = manager.issue("exp_user", role=RCANRole.GUEST, ttl_seconds=0)
        # Token is already expired (ttl=0 means exp == iat)
        time.sleep(1)
        with pytest.raises(jwt.ExpiredSignatureError):
            manager.verify(token)

    def test_verify_wrong_secret(self, manager):
        token = manager.issue("user1", role=RCANRole.GUEST)
        bad_manager = RCANTokenManager(secret="wrong-secret")
        with pytest.raises(jwt.InvalidSignatureError):
            bad_manager.verify(token)

    def test_verify_malformed_token(self, manager):
        with pytest.raises(jwt.DecodeError):
            manager.verify("not.a.valid.token")


class TestNoSecret:
    """Error handling when secret is not configured."""

    def test_issue_without_secret(self):
        m = RCANTokenManager(secret="")
        with pytest.raises(RuntimeError, match="not configured"):
            m.issue("test", role=RCANRole.GUEST)

    def test_verify_without_secret(self):
        m = RCANTokenManager(secret="")
        with pytest.raises(RuntimeError, match="not configured"):
            m.verify("sometoken")

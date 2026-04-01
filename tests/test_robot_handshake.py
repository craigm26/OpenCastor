"""Tests for castor.auth.robot_handshake — PQC registration token round-trip."""

import pytest
from dilithium_py.ml_dsa import ML_DSA_65

from castor.auth.jwt_pqc import JWTError
from castor.auth.robot_handshake import issue_registration_token, verify_registration_token

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeKeypair:
    """Minimal stand-in for RobotKeyPair — holds real ML-DSA-65 key material."""

    def __init__(self, ml_dsa_public: bytes, ml_dsa_private: bytes) -> None:
        self.ml_dsa_public = ml_dsa_public
        self.ml_dsa_private = ml_dsa_private
        self.profile = "pqc-v1"


@pytest.fixture(scope="module")
def keypair() -> _FakeKeypair:
    """Generate a real ML-DSA-65 keypair once for the whole module."""
    pub, priv = ML_DSA_65.keygen()
    return _FakeKeypair(ml_dsa_public=pub, ml_dsa_private=priv)


@pytest.fixture(scope="module")
def other_keypair() -> _FakeKeypair:
    """A second, unrelated keypair (wrong-key tests)."""
    pub, priv = ML_DSA_65.keygen()
    return _FakeKeypair(ml_dsa_public=pub, ml_dsa_private=priv)


_RRN = "rrn://org/robot/opencastor/test-001"
_GATEWAY = "https://robot.local:8000"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_round_trip(keypair):
    """issue → verify round-trip returns the original claims."""
    token = issue_registration_token(keypair, _RRN, _GATEWAY)
    claims = verify_registration_token(token, keypair.ml_dsa_public)

    assert claims["sub"] == _RRN
    assert claims["iss"] == "opencastor-robot"
    assert claims["aud"] == _GATEWAY
    assert len(claims["rnonce"]) == 16  # 8 bytes → 16 hex chars


def test_wrong_rrn_rejected(keypair):
    """verify_registration_token raises JWTError when expected_rrn differs."""
    token = issue_registration_token(keypair, _RRN, _GATEWAY)
    with pytest.raises(JWTError, match="RRN mismatch"):
        verify_registration_token(
            token, keypair.ml_dsa_public, expected_rrn="rrn://org/robot/other/id"
        )


def test_wrong_aud_rejected(keypair):
    """verify_registration_token raises JWTError when expected_aud differs."""
    token = issue_registration_token(keypair, _RRN, _GATEWAY)
    with pytest.raises(JWTError, match="audience mismatch"):
        verify_registration_token(
            token, keypair.ml_dsa_public, expected_aud="https://other-gateway.example"
        )


def test_expired_token_rejected(keypair):
    """A token issued with expires_in=-1 is immediately expired."""
    token = issue_registration_token(keypair, _RRN, _GATEWAY, expires_in=-1)
    with pytest.raises(JWTError, match="expired"):
        verify_registration_token(token, keypair.ml_dsa_public)


def test_nonce_unique(keypair):
    """Each issued token carries a distinct rnonce (replay-guard uniqueness)."""
    tokens = [issue_registration_token(keypair, _RRN, _GATEWAY) for _ in range(10)]
    nonces = set()
    for tok in tokens:
        claims = verify_registration_token(tok, keypair.ml_dsa_public)
        nonces.add(claims["rnonce"])
    assert len(nonces) == 10, "rnonce must be unique per issuance"

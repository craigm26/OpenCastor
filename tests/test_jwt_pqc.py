"""Tests for castor.auth.jwt_pqc — ML-DSA-65 JWT for robot identity handshake.

These tests exercise the two public functions:
    issue_pqc_jwt(payload, ml_dsa_private, *, expires_in=3600) -> str
    verify_pqc_jwt(token, ml_dsa_public) -> dict

TDD: tests written before implementation.
"""

from __future__ import annotations

import time

import pytest

from castor.auth.jwt_pqc import JWTError, issue_pqc_jwt, verify_pqc_jwt
from castor.crypto.pqc import generate_robot_keypair_v1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def robot_keypair():
    """Generate one ML-DSA-65 keypair for all tests in this module."""
    return generate_robot_keypair_v1()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_issue_and_verify_round_trip(robot_keypair):
    """Issuing a token and verifying it with the correct key returns the payload."""
    payload = {"sub": "rrn://craigm26/robot/opencastor-rpi5-hailo/bob-001", "role": "owner"}
    token = issue_pqc_jwt(payload, robot_keypair.ml_dsa_private)
    result = verify_pqc_jwt(token, robot_keypair.ml_dsa_public)
    assert result["sub"] == payload["sub"]
    assert result["role"] == payload["role"]


# ---------------------------------------------------------------------------
# Standard claims
# ---------------------------------------------------------------------------


def test_token_contains_iat_and_exp(robot_keypair):
    """Issued token carries iat (issued-at) and exp (expiry) claims."""
    payload = {"sub": "RRN-000000000001"}
    before = int(time.time())
    token = issue_pqc_jwt(payload, robot_keypair.ml_dsa_private, expires_in=3600)
    after = int(time.time())
    result = verify_pqc_jwt(token, robot_keypair.ml_dsa_public)
    assert before <= result["iat"] <= after
    assert result["exp"] == result["iat"] + 3600


def test_token_contains_sub_from_payload(robot_keypair):
    """The sub claim in the verified payload matches what was issued."""
    rrn = "rrn://acme/robot/arm/unit-42"
    token = issue_pqc_jwt({"sub": rrn}, robot_keypair.ml_dsa_private)
    result = verify_pqc_jwt(token, robot_keypair.ml_dsa_public)
    assert result["sub"] == rrn


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_expired_token_raises_jwt_error(robot_keypair):
    """A token whose exp is in the past raises JWTError on verify."""
    payload = {"sub": "RRN-000000000001"}
    token = issue_pqc_jwt(payload, robot_keypair.ml_dsa_private, expires_in=-1)
    with pytest.raises(JWTError, match="expired"):
        verify_pqc_jwt(token, robot_keypair.ml_dsa_public)


# ---------------------------------------------------------------------------
# Tamper resistance
# ---------------------------------------------------------------------------


def test_tampered_payload_raises_jwt_error(robot_keypair):
    """Modifying the payload section of the token raises JWTError."""
    import base64
    import json

    payload = {"sub": "RRN-000000000001"}
    token = issue_pqc_jwt(payload, robot_keypair.ml_dsa_private)
    header_b64, payload_b64, sig_b64 = token.split(".")

    # Tamper: change sub in payload
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    original = json.loads(base64.urlsafe_b64decode(padded))
    original["sub"] = "rrn://evil/robot/hacked/000"
    tampered_payload = base64.urlsafe_b64encode(
        json.dumps(original).encode()
    ).rstrip(b"=").decode()

    tampered_token = f"{header_b64}.{tampered_payload}.{sig_b64}"
    with pytest.raises(JWTError):
        verify_pqc_jwt(tampered_token, robot_keypair.ml_dsa_public)


# ---------------------------------------------------------------------------
# Wrong key
# ---------------------------------------------------------------------------


def test_wrong_key_raises_jwt_error(robot_keypair):
    """Verifying with a different public key raises JWTError."""
    other = generate_robot_keypair_v1()
    payload = {"sub": "RRN-000000000001"}
    token = issue_pqc_jwt(payload, robot_keypair.ml_dsa_private)
    with pytest.raises(JWTError):
        verify_pqc_jwt(token, other.ml_dsa_public)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def test_token_header_alg_and_typ(robot_keypair):
    """The JWT header declares alg=ML-DSA-65 and typ=JWT."""
    import base64
    import json

    token = issue_pqc_jwt({"sub": "RRN-000000000001"}, robot_keypair.ml_dsa_private)
    header_b64 = token.split(".")[0]
    padded = header_b64 + "=" * (-len(header_b64) % 4)
    header = json.loads(base64.urlsafe_b64decode(padded))
    assert header["alg"] == "ML-DSA-65"
    assert header["typ"] == "JWT"

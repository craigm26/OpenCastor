"""PQC robot registration handshake tokens.

Thin wrapper around castor.auth.jwt_pqc that adds the handshake-specific
payload structure and optional claim validation.

Public API
----------
issue_registration_token(keypair, rrn, gateway_url, *, expires_in=300) -> str
    Issue a short-lived PQC-signed JWT for the registration handshake.

verify_registration_token(token, ml_dsa_public, *, expected_rrn=None, expected_aud=None) -> dict
    Verify the token; return the decoded payload dict.
    Raises JWTError on any failure (expired, tampered, wrong key, wrong claims).

Payload structure
-----------------
    {
        "sub":    "<rrn>",
        "iss":    "opencastor-robot",
        "aud":    "<gateway_url>",
        "rnonce": "<8-byte hex token>",   # replay guard — unique per issuance
        "iat":    <int>,
        "exp":    <int>,
    }
"""

from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING, Any, Optional

from castor.auth.jwt_pqc import JWTError, issue_pqc_jwt, verify_pqc_jwt  # noqa: F401

if TYPE_CHECKING:
    from castor.crypto.pqc import RobotKeyPair

logger = logging.getLogger("OpenCastor.Auth.Handshake")

_ISS = "opencastor-robot"


def issue_registration_token(
    keypair: RobotKeyPair,
    rrn: str,
    gateway_url: str,
    *,
    expires_in: int = 300,
) -> str:
    """Issue a PQC-signed registration token for the robot identity handshake.

    The token is signed with ML-DSA-65 and is intended for single-session
    registration only (default TTL: 5 minutes).

    Args:
        keypair:     RobotKeyPair holding ``ml_dsa_private`` bytes.
        rrn:         Robot Registration Number
                     (e.g. ``rrn://org/robot/model/id`` or ``RRN-000000000001``).
        gateway_url: Gateway URL — used as the JWT ``aud`` claim.
        expires_in:  Token lifetime in seconds (default 300).

    Returns:
        Compact JWT string: ``<header>.<payload>.<signature>``.
    """
    payload: dict[str, Any] = {
        "sub": rrn,
        "iss": _ISS,
        "aud": gateway_url,
        "rnonce": secrets.token_hex(8),
    }
    return issue_pqc_jwt(payload, keypair.ml_dsa_private, expires_in=expires_in)


def verify_registration_token(
    token: str,
    ml_dsa_public: bytes,
    *,
    expected_rrn: Optional[str] = None,
    expected_aud: Optional[str] = None,
) -> dict[str, Any]:
    """Verify a registration token and return the decoded payload.

    Args:
        token:         JWT produced by :func:`issue_registration_token`.
        ml_dsa_public: ML-DSA-65 public key bytes (1952 B for ML-DSA-65).
        expected_rrn:  If given, raises :class:`JWTError` when ``sub`` differs.
        expected_aud:  If given, raises :class:`JWTError` when ``aud`` differs.

    Returns:
        Decoded payload dict (includes ``iat``, ``exp``, ``rnonce`` claims).

    Raises:
        JWTError: On any failure — bad format, invalid signature, expired,
                  or mismatched ``sub``/``aud`` claims.
    """
    claims = verify_pqc_jwt(token, ml_dsa_public)

    if expected_rrn is not None and claims.get("sub") != expected_rrn:
        raise JWTError(f"RRN mismatch: expected {expected_rrn!r}, got {claims.get('sub')!r}")

    if expected_aud is not None and claims.get("aud") != expected_aud:
        raise JWTError(f"audience mismatch: expected {expected_aud!r}, got {claims.get('aud')!r}")

    return claims

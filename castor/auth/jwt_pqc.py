"""PQC JWT primitives for robot identity handshake / session establishment.

Use for robot identity handshake / session establishment only.
The ML-DSA-65 signature is ~4.4 KB — acceptable for a once-per-session token
but far too large for per-request auth.  For per-request auth use HS256
session tokens (see castor/rcan/jwt_auth.py or castor/auth_jwt.py).

Public API
----------
issue_pqc_jwt(payload, ml_dsa_private, *, expires_in=3600) -> str
    Issue a JWT signed with ML-DSA-65 for identity handshake.

verify_pqc_jwt(token, ml_dsa_public) -> dict
    Verify the token; return the decoded payload dict.
    Raises JWTError on any failure (expired, tampered, wrong key, bad format).

JWT structure
-------------
    header  = base64url({"alg": "ML-DSA-65", "typ": "JWT"})
    payload = base64url({...claims..., "iat": int, "exp": int})
    sig     = base64url(ML_DSA_65.sign(private, header + "." + payload))
    token   = header + "." + payload + "." + sig
"""

from __future__ import annotations

import json
import logging
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any

logger = logging.getLogger("OpenCastor.Auth.JWTPQC")

_HEADER = (
    urlsafe_b64encode(
        json.dumps({"alg": "ML-DSA-65", "typ": "JWT"}, separators=(",", ":")).encode()
    )
    .rstrip(b"=")
    .decode()
)


class JWTError(Exception):
    """Raised by verify_pqc_jwt on any verification failure."""


def _b64url_encode(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    return urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_pqc_jwt(
    payload: dict[str, Any],
    ml_dsa_private: bytes,
    *,
    expires_in: int = 3600,
) -> str:
    """Issue a PQC-signed JWT for robot identity handshake / session establishment.

    The token is signed with ML-DSA-65 (NIST FIPS 204).  The signature is
    large (~4.4 KB) and is intended for once-per-session issuance only.

    Args:
        payload:        Claims dict.  ``iat`` and ``exp`` are set automatically
                        (caller-supplied values are overwritten).
        ml_dsa_private: ML-DSA-65 private key bytes (4032 B for ML-DSA-65).
        expires_in:     Token lifetime in seconds (default 3600).  Set to a
                        negative value to issue an already-expired token (useful
                        in tests).

    Returns:
        Compact JWT string: ``<header>.<payload>.<signature>``.
    """
    from dilithium_py.ml_dsa import ML_DSA_65

    now = int(time.time())
    claims = dict(payload)
    claims["iat"] = now
    claims["exp"] = now + expires_in

    payload_b64 = _b64url_encode(json.dumps(claims, separators=(",", ":")).encode())

    signing_input = f"{_HEADER}.{payload_b64}".encode()
    signature = ML_DSA_65.sign(ml_dsa_private, signing_input)
    sig_b64 = _b64url_encode(signature)

    return f"{_HEADER}.{payload_b64}.{sig_b64}"


def verify_pqc_jwt(token: str, ml_dsa_public: bytes) -> dict[str, Any]:
    """Verify a PQC JWT and return the decoded payload.

    Args:
        token:         Compact JWT string produced by :func:`issue_pqc_jwt`.
        ml_dsa_public: ML-DSA-65 public key bytes (1952 B for ML-DSA-65).

    Returns:
        Decoded payload dict (includes ``iat`` and ``exp`` claims).

    Raises:
        JWTError: On any failure — bad format, wrong algorithm, invalid
                  signature, wrong key, or expired token.
    """
    from dilithium_py.ml_dsa import ML_DSA_65

    # --- structural check ---
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTError("malformed token: expected 3 dot-separated parts")

    header_b64, payload_b64, sig_b64 = parts

    # --- header check ---
    try:
        header = json.loads(_b64url_decode(header_b64))
    except Exception as exc:
        raise JWTError(f"malformed header: {exc}") from exc

    if header.get("alg") != "ML-DSA-65":
        raise JWTError(f"unsupported algorithm: {header.get('alg')!r}")
    if header.get("typ") != "JWT":
        raise JWTError(f"unexpected typ: {header.get('typ')!r}")

    # --- signature verification ---
    try:
        signature = _b64url_decode(sig_b64)
    except Exception as exc:
        raise JWTError(f"malformed signature encoding: {exc}") from exc

    signing_input = f"{header_b64}.{payload_b64}".encode()
    try:
        valid = ML_DSA_65.verify(ml_dsa_public, signing_input, signature)
    except Exception as exc:
        raise JWTError(f"signature verification error: {exc}") from exc

    if not valid:
        raise JWTError("signature verification failed")

    # --- payload decode ---
    try:
        claims = json.loads(_b64url_decode(payload_b64))
    except Exception as exc:
        raise JWTError(f"malformed payload: {exc}") from exc

    # --- expiry check ---
    exp = claims.get("exp")
    if exp is None:
        raise JWTError("missing exp claim")
    if int(time.time()) > exp:
        raise JWTError("token expired")

    return claims

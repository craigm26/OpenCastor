"""
RCAN JWT Authentication.

Opt-in JWT token management for the RCAN protocol.  Enabled when
``OPENCASTOR_JWT_SECRET`` is set in the environment.

JWT claims follow the RCAN spec::

    sub   -- Principal name (e.g. ``operator1``).
    iss   -- Issuer RURI (the robot that issued the token).
    aud   -- Audience RURI pattern (which robots this token is valid for).
    role  -- RCAN role name (GUEST, USER, LEASEE, OWNER, CREATOR).
    scope -- List of scope strings (status, control, config, training, admin).
    fleet -- Optional list of RURI patterns for fleet-scoped access.
    exp   -- Expiration timestamp (Unix epoch).
    iat   -- Issued-at timestamp.

Requires ``PyJWT>=2.8.0`` (pure Python, ~100KB).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from castor.rcan.rbac import RCANPrincipal, RCANRole, Scope
from castor.secret_provider import get_jwt_secret_provider

logger = logging.getLogger("OpenCastor.RCAN.JWT")

try:
    import jwt

    HAS_JWT = True
except ImportError:
    HAS_JWT = False
    jwt = None


class RCANTokenManager:
    """Issue and verify JWT tokens with RCAN claims.

    Args:
        secret:     HMAC secret for signing (HS256).
        issuer:     RURI of the issuing robot.
        algorithm:  JWT signing algorithm (default: HS256).
    """

    def __init__(
        self,
        secret: Optional[str] = None,
        issuer: Optional[str] = None,
        algorithm: str = "HS256",
    ):
        self._static_secret = secret
        self.issuer = issuer or "rcan://opencastor.unknown.00000000"
        self.algorithm = algorithm

        if not self._static_secret and not get_jwt_secret_provider().get_bundle().active.secret:
            logger.debug("JWT secret not configured -- token operations will fail")

    def _key_candidates(self):
        if self._static_secret:
            return [(os.getenv("OPENCASTOR_JWT_KID", "static"), self._static_secret)]
        bundle = get_jwt_secret_provider().get_bundle()
        candidates = [(bundle.active.kid, bundle.active.secret)]
        if bundle.previous:
            candidates.append((bundle.previous.kid, bundle.previous.secret))
        return candidates

    @property
    def enabled(self) -> bool:
        """True if JWT is configured (secret is set and PyJWT available)."""
        return bool(self._key_candidates()) and HAS_JWT

    def issue(
        self,
        subject: str,
        role: RCANRole = RCANRole.GUEST,
        scopes: Optional[List[str]] = None,
        audience: str = "rcan://*.*.*",
        fleet: Optional[List[str]] = None,
        ttl_seconds: int = 86400,
    ) -> str:
        """Issue a signed JWT token.

        Args:
            subject:      Principal name.
            role:         RCAN role.
            scopes:       Scope names (defaults to role's default scopes).
            audience:     Target RURI pattern.
            fleet:        Optional fleet RURI patterns.
            ttl_seconds:  Token lifetime in seconds (default: 24h).

        Returns:
            Encoded JWT string.

        Raises:
            RuntimeError: If PyJWT is not installed or secret is not set.
        """
        if not HAS_JWT:
            raise RuntimeError("PyJWT is not installed. Install with: pip install PyJWT")
        key_candidates = self._key_candidates()
        if not key_candidates:
            raise RuntimeError("OPENCASTOR_JWT_SECRET is not configured")

        now = time.time()
        if scopes is None:
            scopes = Scope.for_role(role).to_strings()

        claims: Dict[str, Any] = {
            "sub": subject,
            "iss": self.issuer,
            "aud": audience,
            "role": role.name,
            "scope": scopes,
            "fleet": fleet or [],
            "iat": int(now),
            "exp": int(now + ttl_seconds),
        }

        active_kid, active_secret = key_candidates[0]
        token = jwt.encode(
            claims, active_secret, algorithm=self.algorithm, headers={"kid": active_kid}
        )
        logger.info("Issued JWT for %s (role=%s, ttl=%ds)", subject, role.name, ttl_seconds)
        return token

    def verify(self, token: str) -> RCANPrincipal:
        """Verify a JWT token and return the authenticated principal.

        Args:
            token: Encoded JWT string.

        Returns:
            :class:`RCANPrincipal` with role and scopes from the token.

        Raises:
            RuntimeError:  If PyJWT is not installed.
            jwt.ExpiredSignatureError:  If the token is expired.
            jwt.InvalidTokenError:  If the token is invalid.
        """
        if not HAS_JWT:
            raise RuntimeError("PyJWT is not installed")
        key_candidates = self._key_candidates()
        if not key_candidates:
            raise RuntimeError("OPENCASTOR_JWT_SECRET is not configured")

        header_kid = None
        try:
            header_kid = jwt.get_unverified_header(token).get("kid")
        except Exception:
            header_kid = None
        if header_kid:
            key_candidates = [k for k in key_candidates if k[0] == header_kid] + [
                k for k in key_candidates if k[0] != header_kid
            ]

        claims = None
        last_exc = None
        for kid, key_secret in key_candidates:
            try:
                claims = jwt.decode(
                    token,
                    key_secret,
                    algorithms=[self.algorithm],
                    options={"verify_aud": False},
                )
                claims.setdefault("kid", kid)
                break
            except Exception as exc:
                last_exc = exc
        if claims is None and last_exc is not None:
            raise last_exc

        role = RCANRole[claims.get("role", "GUEST")]
        scopes = Scope.from_strings(claims.get("scope", []))
        fleet = claims.get("fleet", [])

        principal = RCANPrincipal(
            name=claims["sub"],
            role=role,
            scopes=scopes,
            fleet=fleet,
        )

        logger.debug("Verified JWT for %s (role=%s)", principal.name, role.name)
        return principal

    def decode_claims(self, token: str) -> Dict[str, Any]:
        """Decode a JWT token without verification (for inspection only)."""
        if not HAS_JWT:
            raise RuntimeError("PyJWT is not installed")
        key_candidates = self._key_candidates()
        if not key_candidates:
            raise RuntimeError("OPENCASTOR_JWT_SECRET is not configured")
        return jwt.decode(
            token,
            key_candidates[0][1],
            algorithms=[self.algorithm],
            options={"verify_exp": False, "verify_aud": False},
        )

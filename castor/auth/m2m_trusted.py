"""
castor/auth/m2m_trusted — RCAN v2.1 M2M_TRUSTED session authentication.

Validates incoming messages authenticated with role level 6 (M2M_TRUSTED).
M2M_TRUSTED tokens are issued exclusively by the Robot Registry Foundation (RRF)
and authorize fleet orchestrators to command multiple robots.

Spec: §2.9 M2M_TRUSTED
RRF: https://api.rrf.rcan.dev/.well-known/rrf-root-pubkey.pem
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("OpenCastor.Auth.M2MTrusted")

# ---------------------------------------------------------------------------
# Active session tracking
# ---------------------------------------------------------------------------

@dataclass
class M2MTrustedSession:
    """Tracks an active M2M_TRUSTED orchestrator session."""
    orchestrator_id: str            # JWT sub claim
    fleet_rrns: list[str]           # Authorized robot RRNs from JWT
    exp: int                        # JWT expiry (Unix timestamp)
    token_hash: str                 # SHA-256 of raw JWT (for revocation matching)
    started_at: float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        return time.time() > self.exp

    @property
    def age_seconds(self) -> float:
        return time.time() - self.started_at


# In-memory session store (process-local)
_active_sessions: dict[str, M2MTrustedSession] = {}  # orchestrator_id → session


def get_active_sessions() -> dict[str, M2MTrustedSession]:
    return _active_sessions


def has_active_m2m_trusted_sessions() -> bool:
    # Purge expired first
    expired = [k for k, v in _active_sessions.items() if v.is_expired]
    for k in expired:
        del _active_sessions[k]
    return bool(_active_sessions)


# ---------------------------------------------------------------------------
# Token validation
# ---------------------------------------------------------------------------

class M2MTrustedAuthError(Exception):
    def __init__(self, message: str, code: str = "M2M_AUTH_ERROR"):
        super().__init__(message)
        self.code = code


def _token_hash(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


def validate_m2m_trusted_message(
    token: str,
    target_rrn: str,
    revocation_cache: Optional["RevocationCache"] = None,
) -> M2MTrustedSession:
    """Validate an M2M_TRUSTED JWT for a message targeting this robot.

    Args:
        token: Raw JWT string from message envelope.
        target_rrn: This robot's RRN (must be in token's fleet_rrns).
        revocation_cache: Optional revocation cache; if None, skips revocation check.

    Returns:
        M2MTrustedSession if valid.
    Raises:
        M2MTrustedAuthError on any validation failure.
    """
    import hashlib

    # Decode claims (no signature verification — that requires RRF pubkey fetch)
    try:
        import base64, json as _json
        parts = token.split('.')
        if len(parts) < 2:
            raise M2MTrustedAuthError("Invalid JWT structure", "M2M_INVALID_TOKEN")
        b64 = parts[1] + '=' * (4 - len(parts[1]) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(b64))
    except M2MTrustedAuthError:
        raise
    except Exception as e:
        raise M2MTrustedAuthError(f"JWT decode failed: {e}", "M2M_INVALID_TOKEN")

    # Validate issuer
    iss = payload.get("iss", "")
    if iss != "rrf.rcan.dev":
        raise M2MTrustedAuthError(
            f"M2M_TRUSTED issuer must be 'rrf.rcan.dev', got '{iss}'",
            "M2M_INVALID_ISSUER",
        )

    # Validate expiry
    exp = int(payload.get("exp", 0))
    if exp > 0 and time.time() > exp:
        raise M2MTrustedAuthError(
            f"M2M_TRUSTED token expired (sub={payload.get('sub')})",
            "M2M_TOKEN_EXPIRED",
        )

    # Validate scopes
    scopes = payload.get("rcan_scopes", payload.get("scopes", []))
    if "fleet.trusted" not in scopes:
        raise M2MTrustedAuthError(
            "M2M_TRUSTED token missing required 'fleet.trusted' scope",
            "M2M_MISSING_SCOPE",
        )

    # Validate fleet_rrns
    fleet_rrns: list[str] = payload.get("fleet_rrns", [])
    if target_rrn not in fleet_rrns:
        raise M2MTrustedAuthError(
            f"M2M_TRUSTED token does not authorize commanding '{target_rrn}'. "
            f"Authorized fleet: {fleet_rrns}",
            "M2M_NOT_IN_FLEET",
        )

    # Validate rrf_sig present
    if not payload.get("rrf_sig"):
        raise M2MTrustedAuthError(
            "M2M_TRUSTED token missing rrf_sig claim",
            "M2M_MISSING_SIG",
        )

    sub = str(payload.get("sub", ""))

    # Revocation check
    if revocation_cache is not None:
        if revocation_cache.is_revoked(sub):
            raise M2MTrustedAuthError(
                f"M2M_TRUSTED orchestrator '{sub}' is on the RRF revocation list",
                "M2M_REVOKED",
            )

    return M2MTrustedSession(
        orchestrator_id=sub,
        fleet_rrns=fleet_rrns,
        exp=exp,
        token_hash=_token_hash(token),
    )


def register_session(session: M2MTrustedSession) -> None:
    """Register an active M2M_TRUSTED session."""
    _active_sessions[session.orchestrator_id] = session
    logger.info(
        "M2M_TRUSTED session started: orchestrator=%s fleet=%s exp=%s",
        session.orchestrator_id, session.fleet_rrns, session.exp,
    )


def terminate_session(orchestrator_id: str, reason: str = "normal") -> None:
    """Terminate an M2M_TRUSTED session."""
    if orchestrator_id in _active_sessions:
        del _active_sessions[orchestrator_id]
        logger.info("M2M_TRUSTED session terminated: %s reason=%s", orchestrator_id, reason)


# ---------------------------------------------------------------------------
# Revocation cache
# ---------------------------------------------------------------------------

class RevocationCache:
    """Thread-safe in-memory RRF revocation cache.

    Polled by RRFRevocationPoller. Used by validate_m2m_trusted_message().
    """

    def __init__(self):
        self._revoked_orchestrators: set[str] = set()
        self._revoked_jtis: set[str] = set()
        self._fetched_at: float = 0.0
        import threading
        self._lock = threading.Lock()

    def update(self, revoked_orchestrators: list[str], revoked_jtis: list[str]) -> None:
        with self._lock:
            self._revoked_orchestrators = set(revoked_orchestrators)
            self._revoked_jtis = set(revoked_jtis)
            self._fetched_at = time.time()

    def is_revoked(self, orchestrator_id: str, jti: Optional[str] = None) -> bool:
        with self._lock:
            if orchestrator_id in self._revoked_orchestrators:
                return True
            if jti and jti in self._revoked_jtis:
                return True
        return False

    @property
    def age_seconds(self) -> float:
        return time.time() - self._fetched_at if self._fetched_at else float('inf')

    @property
    def is_stale(self) -> bool:
        return self.age_seconds > 55  # spec: ≤ 60 s


# Global revocation cache instance
revocation_cache = RevocationCache()

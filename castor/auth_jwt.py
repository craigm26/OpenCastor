"""
Multi-user JWT authentication for OpenCastor Gateway.

Parses the OPENCASTOR_USERS env var to build a simple user database,
issues JWT access tokens, and provides a FastAPI Depends factory that
accepts either JWT Bearer tokens or the legacy static API token.

Environment variables
---------------------
OPENCASTOR_USERS
    Colon-and-comma-delimited user list::

        admin:pass1:admin,viewer:pass2:viewer,ops:pass3:operator

    Passwords are stored as SHA-256 hex digests; the plaintext value in the
    env var is hashed on first parse and never stored.

JWT_SECRET
    Secret used to sign JWT tokens.  When unset, falls back to the value of
    OPENCASTOR_API_TOKEN.  If neither is set, a random secret is generated
    at startup (tokens do not survive restarts).

Roles
-----
ROLES maps role name -> numeric level for comparison::

    admin    = 3  (full access)
    operator = 2  (cannot reload config)
    viewer   = 1  (read-only; blocked from /api/command and /api/action)
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("OpenCastor.AuthJWT")

# ---------------------------------------------------------------------------
# Role hierarchy
# ---------------------------------------------------------------------------

ROLES: Dict[str, int] = {
    "admin": 3,
    "operator": 2,
    "viewer": 1,
}

# Endpoints restricted per role level
_VIEWER_BLOCKED = {"/api/command", "/api/action"}
_OPERATOR_BLOCKED = {"/api/config/reload"}

# ---------------------------------------------------------------------------
# PyJWT import (optional at module level; raises at runtime if missing)
# ---------------------------------------------------------------------------

try:
    import jwt as _pyjwt

    HAS_JWT = True
except ImportError:
    _pyjwt = None  # type: ignore[assignment]
    HAS_JWT = False

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    """Return lowercase hex SHA-256 digest of *text*."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _get_jwt_secret() -> str:
    """Return the JWT secret, falling back to API token or random value."""
    secret = os.getenv("JWT_SECRET") or os.getenv("OPENCASTOR_JWT_SECRET")
    if secret:
        return secret
    api_token = os.getenv("OPENCASTOR_API_TOKEN")
    if api_token:
        return api_token
    # Generate a random secret once per process and cache it
    if not hasattr(_get_jwt_secret, "_cached"):
        _get_jwt_secret._cached = secrets.token_hex(32)  # type: ignore[attr-defined]
        logger.warning(
            "No JWT_SECRET or OPENCASTOR_API_TOKEN configured — "
            "using a per-process random secret.  Tokens will not survive restarts."
        )
    return _get_jwt_secret._cached  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_users_env() -> Dict[str, Dict[str, str]]:
    """Parse the OPENCASTOR_USERS environment variable.

    Format::  username:password:role[,username2:password2:role2,...]

    Passwords are hashed with SHA-256 before storage.

    Returns:
        dict of {username: {"password_hash": str, "role": str}}
        Empty dict when the env var is absent or empty.
    """
    raw = os.getenv("OPENCASTOR_USERS", "").strip()
    if not raw:
        return {}

    users: Dict[str, Dict[str, str]] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) < 3:
            logger.warning("Skipping malformed OPENCASTOR_USERS entry: %r", entry)
            continue
        username, password, role = parts[0].strip(), parts[1].strip(), parts[2].strip().lower()
        if not username or not password:
            logger.warning("Skipping empty username/password in OPENCASTOR_USERS: %r", entry)
            continue
        if role not in ROLES:
            logger.warning("Unknown role %r for user %r; defaulting to 'viewer'", role, username)
            role = "viewer"
        users[username] = {
            "password_hash": _sha256(password),
            "role": role,
        }
    return users


def create_token(
    username: str,
    role: str,
    secret: Optional[str] = None,
    expires_h: int = 24,
) -> str:
    """Create a signed JWT access token.

    Args:
        username:  Subject claim (username).
        role:      Role string (admin / operator / viewer).
        secret:    HMAC secret.  Uses :func:`_get_jwt_secret` when *None*.
        expires_h: Token lifetime in hours (default: 24).

    Returns:
        Encoded JWT string.

    Raises:
        ImportError: When PyJWT is not installed.
        ValueError:  When role is unknown.
    """
    if not HAS_JWT:
        raise ImportError("PyJWT is required for JWT auth.  Install with: pip install PyJWT>=2.8.0")

    import datetime

    if role not in ROLES:
        raise ValueError(f"Unknown role: {role!r}.  Valid roles: {list(ROLES)}")

    secret = secret or _get_jwt_secret()
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + datetime.timedelta(hours=expires_h),
    }
    return _pyjwt.encode(payload, secret, algorithm="HS256")


def decode_token(token: str, secret: Optional[str] = None) -> Dict[str, Any]:
    """Decode and verify a JWT token.

    Args:
        token:  Encoded JWT string.
        secret: HMAC secret.  Uses :func:`_get_jwt_secret` when *None*.

    Returns:
        Decoded payload dict.

    Raises:
        ImportError:           When PyJWT is not installed.
        jwt.ExpiredSignatureError: When the token has expired.
        jwt.InvalidTokenError: When the token is invalid.
    """
    if not HAS_JWT:
        raise ImportError("PyJWT is required for JWT auth.  Install with: pip install PyJWT>=2.8.0")

    secret = secret or _get_jwt_secret()
    return _pyjwt.decode(token, secret, algorithms=["HS256"])


def authenticate_user(
    username: str,
    password: str,
    users: Optional[Dict[str, Dict[str, str]]] = None,
) -> Optional[Tuple[str, str]]:
    """Verify username / password against the users database.

    Args:
        username: Submitted username.
        password: Plaintext password.
        users:    Users dict from :func:`parse_users_env`.  Parsed from env
                  when *None*.

    Returns:
        ``(username, role)`` tuple on success, or ``None`` on failure.
    """
    if users is None:
        users = parse_users_env()
    record = users.get(username)
    if record is None:
        return None
    if record["password_hash"] != _sha256(password):
        return None
    return username, record["role"]


# ---------------------------------------------------------------------------
# FastAPI Depends factory
# ---------------------------------------------------------------------------


def require_role(min_role: str):
    """Return a FastAPI dependency that enforces a minimum role level.

    Accepts Bearer JWT tokens first; falls back to the static
    OPENCASTOR_API_TOKEN (treated as admin-level).

    Args:
        min_role: Minimum required role name (admin / operator / viewer).

    Returns:
        An async FastAPI dependency function.

    Example::

        @app.post("/api/command", dependencies=[Depends(require_role("operator"))])
        async def command(...): ...
    """
    from fastapi import HTTPException, Request

    async def _dependency(request: Request):
        auth = request.headers.get("Authorization", "")
        query_token = request.query_params.get("token", "")
        if not auth and query_token:
            auth = f"Bearer {query_token}"

        # --- Try JWT Bearer token ---
        if auth.startswith("Bearer "):
            raw_token = auth[7:]
            if HAS_JWT:
                try:
                    payload = decode_token(raw_token)
                    role = payload.get("role", "viewer")
                    level = ROLES.get(role, 0)
                    min_level = ROLES.get(min_role, 0)
                    if level < min_level:
                        raise HTTPException(
                            status_code=403,
                            detail=f"Insufficient role: '{role}' (requires '{min_role}')",
                        )
                    # Attach user info to request state
                    request.state.jwt_username = payload.get("sub", "unknown")
                    request.state.jwt_role = role
                    request.state.auth_type = "jwt"
                    return
                except HTTPException:
                    raise
                except Exception:
                    pass  # Fall through to static token check

            # --- Fall back to static API token (admin-level) ---
            static_token = os.getenv("OPENCASTOR_API_TOKEN")
            if static_token and raw_token == static_token:
                request.state.jwt_username = "api"
                request.state.jwt_role = "admin"
                request.state.auth_type = "static"
                return

        # --- No auth configured: open access ---
        static_token = os.getenv("OPENCASTOR_API_TOKEN")
        if (
            not static_token
            and not os.getenv("JWT_SECRET")
            and not os.getenv("OPENCASTOR_JWT_SECRET")
        ):
            request.state.jwt_username = "anonymous"
            request.state.jwt_role = "viewer"
            request.state.auth_type = "none"
            # Still check role for anonymous
            min_level = ROLES.get(min_role, 0)
            if min_level > ROLES.get("viewer", 0):
                raise HTTPException(
                    status_code=401,
                    detail="Authentication required",
                )
            return

        raise HTTPException(status_code=401, detail="Invalid or missing API token")

    return _dependency

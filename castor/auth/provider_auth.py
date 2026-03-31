"""Provider authentication handlers for gated/closed model APIs.

Supports:
- api_key: Static bearer token (OpenAI-style)
- bearer: Pre-issued JWT/token with optional refresh
- oauth2: Client credentials flow with auto-refresh (Pi, enterprise)
- huggingface: HuggingFace Hub token with model gate check
- mutual_tls: Certificate-based authentication
- oidc: OpenID Connect / workload identity federation

Deprecation notice (RCAN v2.2 / issue #808):
  RS256 (RSA + SHA-256) and ES256 (ECDSA + SHA-256) JWT algorithms are
  deprecated for robot-to-robot authentication.  New deployments MUST use
  pqc-hybrid-v1 (Ed25519 + ML-DSA-65) via castor.crypto.pqc.  RS256/ES256
  remain supported only for legacy LLM provider integrations that the
  operator does not control (e.g. external OIDC issuers).  Do not introduce
  RS256/ES256 for any new inbound RCAN message authentication.
"""

from __future__ import annotations

import abc
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("OpenCastor.Auth")


@dataclass
class AuthCredentials:
    """Resolved credentials ready for use in HTTP requests."""

    headers: dict[str, str] = field(default_factory=dict)
    client_cert: tuple[str, str] | None = None  # (cert_path, key_path)
    ca_bundle: str | None = None
    expires_at: float = 0.0  # Unix timestamp, 0 = no expiry

    @property
    def expired(self) -> bool:
        if self.expires_at == 0.0:
            return False
        return time.time() >= (self.expires_at - 30)  # 30s buffer


class ProviderAuth(abc.ABC):
    """Base class for provider authentication handlers."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._credentials: AuthCredentials | None = None
        self._lock = threading.Lock()

    @abc.abstractmethod
    def authenticate(self) -> AuthCredentials:
        """Perform authentication and return credentials."""
        ...

    def get_credentials(self) -> AuthCredentials:
        """Get valid credentials, refreshing if expired."""
        with self._lock:
            if self._credentials is None or self._credentials.expired:
                self._credentials = self.authenticate()
            return self._credentials

    def invalidate(self) -> None:
        """Force re-authentication on next request."""
        with self._lock:
            self._credentials = None

    @property
    def method(self) -> str:
        return self.config.get("method", "unknown")


class ApiKeyAuth(ProviderAuth):
    """Static API key authentication (OpenAI, Anthropic, Cohere style)."""

    def authenticate(self) -> AuthCredentials:
        key = _resolve_secret(self.config.get("api_key", ""))
        if not key:
            raise ValueError("api_key not configured or empty")
        header_name = self.config.get("header", "Authorization")
        prefix = self.config.get("prefix", "Bearer")
        return AuthCredentials(
            headers={header_name: f"{prefix} {key}".strip()},
        )


class BearerAuth(ProviderAuth):
    """Pre-issued bearer token with optional refresh URL.

    Deprecated for robot identity: if the token is a JWT signed with RS256 or
    ES256, migrate to pqc-hybrid-v1 (castor.crypto.pqc) per issue #808.
    """

    def authenticate(self) -> AuthCredentials:
        token = _resolve_secret(self.config.get("token", ""))
        if not token:
            raise ValueError("bearer token not configured or empty")

        expires_at = 0.0
        refresh_url = self.config.get("refresh_url")
        if refresh_url:
            # If refresh URL is configured, assume token expires in 1 hour
            expires_at = time.time() + 3600

        return AuthCredentials(
            headers={"Authorization": f"Bearer {token}"},
            expires_at=expires_at,
        )


class OAuth2Auth(ProviderAuth):
    """OAuth2 client credentials flow with auto-refresh.

    Designed for gated APIs like Physical Intelligence (π), enterprise models.
    """

    def authenticate(self) -> AuthCredentials:
        import httpx

        client_id = _resolve_secret(self.config.get("client_id", ""))
        client_secret = _resolve_secret(self.config.get("client_secret", ""))
        token_url = self.config.get("token_url", "")
        scopes = self.config.get("scopes", [])

        if not all([client_id, client_secret, token_url]):
            raise ValueError("oauth2 requires client_id, client_secret, and token_url")

        data: dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if scopes:
            data["scope"] = " ".join(scopes)

        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(token_url, data=data)
                resp.raise_for_status()
                token_data = resp.json()
        except Exception as exc:
            log.error("OAuth2 token request failed: %s", exc)
            raise

        access_token = token_data.get("access_token", "")
        expires_in = token_data.get("expires_in", 3600)
        expires_at = time.time() + expires_in

        log.info(
            "OAuth2 token acquired (expires in %ds, scopes=%s)",
            expires_in,
            scopes,
        )

        return AuthCredentials(
            headers={"Authorization": f"Bearer {access_token}"},
            expires_at=expires_at,
        )


class HuggingFaceAuth(ProviderAuth):
    """HuggingFace Hub authentication for gated models.

    Validates that the token has access to the requested model.
    """

    def authenticate(self) -> AuthCredentials:
        token = _resolve_secret(self.config.get("token", "")) or os.environ.get("HF_TOKEN", "")

        if not token:
            raise ValueError("HuggingFace token not configured. Set HF_TOKEN or auth.token")

        # Optionally validate model access
        model_id = self.config.get("validate_model")
        if model_id:
            self._check_model_access(token, model_id)

        return AuthCredentials(
            headers={"Authorization": f"Bearer {token}"},
        )

    def _check_model_access(self, token: str, model_id: str) -> None:
        """Verify token has access to a gated model."""
        import httpx

        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(
                    f"https://huggingface.co/api/models/{model_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code == 403:
                    raise PermissionError(
                        f"HuggingFace token does not have access to gated model: {model_id}. "
                        f"Request access at https://huggingface.co/{model_id}"
                    )
                if resp.status_code == 401:
                    raise ValueError("Invalid HuggingFace token")
                resp.raise_for_status()
        except (PermissionError, ValueError):
            raise
        except Exception as exc:
            log.warning("HF model access check failed (non-fatal): %s", exc)


class MutualTLSAuth(ProviderAuth):
    """Mutual TLS (mTLS) certificate-based authentication.

    Used for enterprise/government deployments where both client
    and server authenticate via X.509 certificates.
    """

    def authenticate(self) -> AuthCredentials:
        client_cert = self.config.get("client_cert", "")
        client_key = self.config.get("client_key", "")
        ca_bundle = self.config.get("ca_bundle")

        if not client_cert or not client_key:
            raise ValueError("mutual_tls requires client_cert and client_key")

        cert_path = Path(client_cert).expanduser()
        key_path = Path(client_key).expanduser()

        if not cert_path.exists():
            raise FileNotFoundError(f"Client certificate not found: {cert_path}")
        if not key_path.exists():
            raise FileNotFoundError(f"Client key not found: {key_path}")

        ca = None
        if ca_bundle:
            ca_path = Path(ca_bundle).expanduser()
            if not ca_path.exists():
                raise FileNotFoundError(f"CA bundle not found: {ca_path}")
            ca = str(ca_path)

        return AuthCredentials(
            client_cert=(str(cert_path), str(key_path)),
            ca_bundle=ca,
        )


class OIDCAuth(ProviderAuth):
    """OpenID Connect / workload identity federation.

    Supports GCP workload identity, Azure managed identity,
    and standard OIDC token exchange.
    """

    def authenticate(self) -> AuthCredentials:
        issuer = self.config.get("issuer", "")
        audience = self.config.get("audience", "")

        # Try GCP workload identity first
        if self.config.get("gcp_workload_identity") or not issuer:
            return self._gcp_workload_identity()

        # Standard OIDC token exchange
        return self._oidc_exchange(issuer, audience)

    def _gcp_workload_identity(self) -> AuthCredentials:
        """Get credentials via GCP Application Default Credentials."""
        try:
            import google.auth
            import google.auth.transport.requests

            creds, _ = google.auth.default()
            creds.refresh(google.auth.transport.requests.Request())
            return AuthCredentials(
                headers={"Authorization": f"Bearer {creds.token}"},
                expires_at=creds.expiry.timestamp() if creds.expiry else 0.0,
            )
        except Exception as exc:
            raise RuntimeError(f"GCP workload identity failed: {exc}") from exc

    def _oidc_exchange(self, issuer: str, audience: str) -> AuthCredentials:
        """Standard OIDC token exchange."""
        import httpx

        # Discover token endpoint
        try:
            with httpx.Client(timeout=10) as client:
                disco = client.get(f"{issuer.rstrip('/')}/.well-known/openid-configuration")
                disco.raise_for_status()
                token_endpoint = disco.json()["token_endpoint"]

                # Exchange
                client_id = _resolve_secret(self.config.get("client_id", ""))
                client_secret = _resolve_secret(self.config.get("client_secret", ""))
                resp = client.post(
                    token_endpoint,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "audience": audience,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                return AuthCredentials(
                    headers={"Authorization": f"Bearer {data['access_token']}"},
                    expires_at=time.time() + data.get("expires_in", 3600),
                )
        except Exception as exc:
            raise RuntimeError(f"OIDC exchange failed: {exc}") from exc


# ── Factory ──────────────────────────────────────────────────────────────

_AUTH_HANDLERS: dict[str, type[ProviderAuth]] = {
    "api_key": ApiKeyAuth,
    "bearer": BearerAuth,
    "oauth2": OAuth2Auth,
    "huggingface": HuggingFaceAuth,
    "mutual_tls": MutualTLSAuth,
    "oidc": OIDCAuth,
}


def create_provider_auth(auth_config: dict[str, Any]) -> ProviderAuth:
    """Create a ProviderAuth instance from config."""
    method = auth_config.get("method", "api_key")
    handler_cls = _AUTH_HANDLERS.get(method)
    if handler_cls is None:
        raise ValueError(f"Unknown auth method: {method}. Supported: {', '.join(_AUTH_HANDLERS)}")
    return handler_cls(auth_config)


# ── Helpers ──────────────────────────────────────────────────────────────


def _resolve_secret(value: str) -> str:
    """Resolve a secret value — supports ${ENV_VAR} syntax and file: prefix."""
    if not value:
        return ""
    if value.startswith("${") and value.endswith("}"):
        env_var = value[2:-1]
        return os.environ.get(env_var, "")
    if value.startswith("file:"):
        path = Path(value[5:]).expanduser()
        if path.exists():
            return path.read_text().strip()
        log.warning("Secret file not found: %s", path)
        return ""
    return value

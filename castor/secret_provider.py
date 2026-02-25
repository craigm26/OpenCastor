"""Secret provider abstraction for JWT signing and verification."""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("OpenCastor.Secrets")


@dataclass
class JWTKeyMaterial:
    kid: str
    secret: str


@dataclass
class JWTSecretBundle:
    active: JWTKeyMaterial
    previous: Optional[JWTKeyMaterial]
    source: str

    @property
    def weak_source(self) -> bool:
        return self.source == "env"


class JWTSecretProvider:
    """Load JWT keys from preferred secure sources with env fallback."""

    def __init__(self) -> None:
        self._cached_bundle: Optional[JWTSecretBundle] = None

    def get_bundle(self) -> JWTSecretBundle:
        if self._cached_bundle is None:
            self._cached_bundle = self._load_bundle()
        return self._cached_bundle

    def invalidate(self) -> None:
        self._cached_bundle = None

    def rotate(
        self, *, new_secret: Optional[str] = None, new_kid: Optional[str] = None
    ) -> JWTSecretBundle:
        current = self.get_bundle()
        active = current.active
        replacement = JWTKeyMaterial(
            kid=new_kid or f"k-{secrets.token_hex(4)}",
            secret=new_secret or secrets.token_hex(32),
        )
        rotated = {
            "active": {"kid": replacement.kid, "secret": replacement.secret},
            "previous": {"kid": active.kid, "secret": active.secret},
        }
        rotation_path = Path(
            os.getenv("OPENCASTOR_JWT_ROTATION_FILE", "~/.opencastor/jwt-keys.json")
        ).expanduser()
        rotation_path.parent.mkdir(parents=True, exist_ok=True)
        rotation_path.write_text(json.dumps(rotated, indent=2), encoding="utf-8")
        os.environ["OPENCASTOR_JWT_SECRETS_FILE"] = str(rotation_path)
        self.invalidate()
        bundle = self.get_bundle()
        logger.info(
            "Rotated JWT signing key. active_kid=%s previous_kid=%s",
            bundle.active.kid,
            bundle.previous.kid if bundle.previous else None,
        )
        return bundle

    def enforce_weak_source_policy(self) -> None:
        bundle = self.get_bundle()
        if not bundle.weak_source:
            return

        env_profile = (
            (
                os.getenv("OPENCASTOR_ENV")
                or os.getenv("OPENCASTOR_PROFILE")
                or os.getenv("OPENCASTOR_SECURITY_PROFILE")
                or ""
            )
            .strip()
            .lower()
        )
        is_prod = env_profile in {"prod", "production", "secure", "hardened"}
        if not is_prod:
            return

        policy = os.getenv("OPENCASTOR_JWT_WEAK_SOURCE_POLICY", "warn").strip().lower()
        message = (
            "JWT secrets loaded from plain environment variables in production profile. "
            "Prefer kernel keyring/systemd credentials/vault-mounted file sources."
        )
        if policy == "error":
            raise RuntimeError(message)
        logger.warning(message)

    def _load_bundle(self) -> JWTSecretBundle:
        for loader in (
            self._from_keyring,
            self._from_systemd_credentials,
            self._from_vault_file,
            self._from_env,
        ):
            bundle = loader()
            if bundle is not None:
                return bundle

        fallback = secrets.token_hex(32)
        logger.warning("No JWT secret configured; using process-local random secret")
        return JWTSecretBundle(
            active=JWTKeyMaterial(kid="ephemeral", secret=fallback),
            previous=None,
            source="ephemeral",
        )

    def _from_keyring(self) -> Optional[JWTSecretBundle]:
        key_id = os.getenv("OPENCASTOR_JWT_SECRET_KEYRING_ID")
        if not key_id:
            return None
        try:
            out = subprocess.check_output(["keyctl", "pipe", key_id], text=True).strip()
            if not out:
                return None
            return JWTSecretBundle(
                active=JWTKeyMaterial(
                    kid=os.getenv("OPENCASTOR_JWT_KID", "keyring-active"),
                    secret=out,
                ),
                previous=None,
                source="keyring",
            )
        except Exception as exc:
            logger.warning("Failed loading JWT secret from keyring id=%s: %s", key_id, exc)
            return None

    def _from_systemd_credentials(self) -> Optional[JWTSecretBundle]:
        credential_name = os.getenv("OPENCASTOR_JWT_SECRET_CREDENTIAL", "opencastor_jwt_secret")
        search_paths = []
        cred_dir = os.getenv("CREDENTIALS_DIRECTORY")
        if cred_dir:
            search_paths.append(Path(cred_dir) / credential_name)
        search_paths.extend(
            [
                Path(f"/run/credentials/{credential_name}"),
                Path(f"/run/secrets/{credential_name}"),
            ]
        )
        for path in search_paths:
            if path.exists():
                secret = path.read_text(encoding="utf-8").strip()
                if secret:
                    return JWTSecretBundle(
                        active=JWTKeyMaterial(
                            kid=os.getenv("OPENCASTOR_JWT_KID", "systemd-active"),
                            secret=secret,
                        ),
                        previous=None,
                        source="systemd",
                    )
        return None

    def _from_vault_file(self) -> Optional[JWTSecretBundle]:
        configured = os.getenv("OPENCASTOR_JWT_SECRETS_FILE") or os.getenv(
            "OPENCASTOR_JWT_SECRET_FILE"
        )
        paths = [configured] if configured else []
        paths.extend(
            [
                os.getenv("OPENCASTOR_JWT_ROTATION_FILE"),
                "/vault/secrets/opencastor-jwt.json",
                "/vault/secrets/opencastor_jwt_secret",
            ]
        )
        for raw in paths:
            if not raw:
                continue
            path = Path(raw).expanduser()
            if not path.exists():
                continue
            raw_text = path.read_text(encoding="utf-8").strip()
            if not raw_text:
                continue
            try:
                payload = json.loads(raw_text)
                active_raw = payload.get("active", {})
                prev_raw = payload.get("previous")
                active = JWTKeyMaterial(
                    kid=str(active_raw.get("kid") or "vault-active"),
                    secret=str(active_raw.get("secret") or ""),
                )
                if not active.secret:
                    continue
                previous = None
                if isinstance(prev_raw, dict) and prev_raw.get("secret"):
                    previous = JWTKeyMaterial(
                        kid=str(prev_raw.get("kid") or "vault-previous"),
                        secret=str(prev_raw.get("secret")),
                    )
                return JWTSecretBundle(active=active, previous=previous, source=f"file:{path}")
            except json.JSONDecodeError:
                return JWTSecretBundle(
                    active=JWTKeyMaterial(
                        kid=os.getenv("OPENCASTOR_JWT_KID", "file-active"), secret=raw_text
                    ),
                    previous=None,
                    source=f"file:{path}",
                )
        return None

    def _from_env(self) -> Optional[JWTSecretBundle]:
        active_secret = (
            os.getenv("JWT_SECRET")
            or os.getenv("OPENCASTOR_JWT_SECRET")
            or os.getenv("OPENCASTOR_API_TOKEN")
        )
        if not active_secret:
            return None
        active = JWTKeyMaterial(
            kid=os.getenv("OPENCASTOR_JWT_KID") or os.getenv("JWT_KID") or "env-active",
            secret=active_secret,
        )
        prev_secret = os.getenv("OPENCASTOR_JWT_PREVIOUS_SECRET")
        previous = None
        if prev_secret:
            previous = JWTKeyMaterial(
                kid=os.getenv("OPENCASTOR_JWT_PREVIOUS_KID", "env-previous"),
                secret=prev_secret,
            )
        return JWTSecretBundle(active=active, previous=previous, source="env")


_provider = JWTSecretProvider()


def get_jwt_secret_provider() -> JWTSecretProvider:
    return _provider

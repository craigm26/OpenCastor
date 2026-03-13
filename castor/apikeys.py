"""API key rotation for OpenCastor (issue #145).

Generate, list, and revoke named API tokens at runtime.  Keys are stored as
SHA-256 hashes in ~/.castor/apikeys.json and work alongside
OPENCASTOR_API_TOKEN.

Usage::

    from castor.apikeys import get_manager

    mgr = get_manager()
    key = mgr.generate(label="ci-bot", role="operator", expires_in_days=30)
    verified = mgr.verify(key)   # returns "operator" or None
    mgr.revoke(key_id)

REST API:
    POST   /api/keys/generate  — {label, role, expires_in_days}
    GET    /api/keys/list       — list all keys (hashes hidden)
    DELETE /api/keys/{key_id}   — revoke a key

CLI:
    castor keys generate --label ci-bot --role operator --expires 30
    castor keys list
    castor keys revoke <key_id>
"""

import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.ApiKeys")

_STORE_PATH = Path(os.getenv("CASTOR_APIKEYS_DB", str(Path.home() / ".castor" / "apikeys.json")))
_VALID_ROLES = {"admin", "operator", "viewer"}


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class ApiKeyManager:
    """Manages runtime-generated API keys.

    Keys are stored as SHA-256 hashes; the raw key is shown only once
    at generation time.
    """

    def __init__(self, store_path: Path = _STORE_PATH):
        self._path = store_path
        self._keys: dict[str, dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path) as f:
                    self._keys = json.load(f)
            except Exception as exc:
                logger.warning("ApiKeyManager: failed to load store: %s", exc)
                self._keys = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w") as f:
                json.dump(self._keys, f, indent=2)
        except Exception as exc:
            logger.warning("ApiKeyManager: failed to save store: %s", exc)

    # ------------------------------------------------------------------
    # Key management
    # ------------------------------------------------------------------

    def generate(
        self,
        label: str = "key",
        role: str = "operator",
        expires_in_days: Optional[int] = None,
    ) -> str:
        """Generate and store a new API key.

        Args:
            label: Human-readable name for the key.
            role: Role granted by the key (admin/operator/viewer).
            expires_in_days: Expiry in days from now (None = no expiry).

        Returns:
            The raw API key string (shown once; store securely).

        Raises:
            ValueError: If role is not valid.
        """
        if role not in _VALID_ROLES:
            raise ValueError(f"Invalid role '{role}'. Valid: {sorted(_VALID_ROLES)}")

        raw = secrets.token_hex(32)
        key_id = secrets.token_hex(8)
        hashed = _hash_key(raw)
        expires_at = time.time() + expires_in_days * 86400 if expires_in_days is not None else None

        self._keys[key_id] = {
            "key_id": key_id,
            "label": label,
            "role": role,
            "hash": hashed,
            "created_at": time.time(),
            "expires_at": expires_at,
        }
        self._save()
        logger.info("ApiKey generated: id=%s label=%s role=%s", key_id, label, role)
        return raw

    def revoke(self, key_id: str) -> bool:
        """Revoke a key by ID.

        Returns:
            True if the key existed and was removed; False if not found.
        """
        if key_id in self._keys:
            del self._keys[key_id]
            self._save()
            logger.info("ApiKey revoked: id=%s", key_id)
            return True
        return False

    def verify(self, raw: str) -> Optional[str]:
        """Verify a raw API key and return its role, or None if invalid/expired."""
        hashed = _hash_key(raw)
        for entry in self._keys.values():
            if entry["hash"] == hashed:
                if entry["expires_at"] and time.time() > entry["expires_at"]:
                    logger.debug("ApiKey expired: id=%s", entry["key_id"])
                    return None
                return entry["role"]
        return None

    def list(self) -> list[dict[str, Any]]:
        """Return all keys (hash excluded) sorted by creation time."""
        now = time.time()
        result = []
        for entry in self._keys.values():
            d = {k: v for k, v in entry.items() if k != "hash"}
            expires_at = entry.get("expires_at")
            d["expired"] = bool(expires_at and now > expires_at)
            d["expires_in_s"] = round(expires_at - now) if expires_at and not d["expired"] else None
            result.append(d)
        result.sort(key=lambda x: x.get("created_at", 0))
        return result

    def get(self, key_id: str) -> Optional[dict[str, Any]]:
        """Return key metadata (no hash) for a key ID."""
        entry = self._keys.get(key_id)
        if entry is None:
            return None
        d = {k: v for k, v in entry.items() if k != "hash"}
        d["expired"] = bool(entry.get("expires_at") and time.time() > entry["expires_at"])
        return d

    def purge_expired(self) -> int:
        """Remove all expired keys. Returns count removed."""
        now = time.time()
        expired_ids = [
            kid
            for kid, entry in self._keys.items()
            if entry.get("expires_at") and now > entry["expires_at"]
        ]
        for kid in expired_ids:
            del self._keys[kid]
        if expired_ids:
            self._save()
            logger.info("Purged %d expired API keys", len(expired_ids))
        return len(expired_ids)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manager: Optional[ApiKeyManager] = None


def get_manager() -> ApiKeyManager:
    """Return the process-wide ApiKeyManager."""
    global _manager
    if _manager is None:
        _manager = ApiKeyManager()
    return _manager

"""castor.rcan3.identity — ML-DSA-65 keypair management.

Stores keys at ``~/.castor/keys/`` by default:
- ``ml_dsa.pub.jwk``  — public key JWK (readable)
- ``ml_dsa.priv.jwk`` — private key JWK (mode 0o600)

Idempotent: call ``load_or_generate_identity()`` any number of times; first
call generates + persists, subsequent calls reload.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rcan.crypto import (
    MlDsaKeyPair,
    decode_public_key_jwk,
    encode_public_key_jwk,
    generate_ml_dsa_keypair,
)

DEFAULT_KEYDIR = Path.home() / ".castor" / "keys"
_PUB_FILE = "ml_dsa.pub.jwk"
_PRIV_FILE = "ml_dsa.priv.jwk"


@dataclass(frozen=True)
class CastorIdentity:
    """The robot's PQ identity — a loaded or newly-generated ML-DSA keypair."""

    keypair: MlDsaKeyPair
    public_key_jwk: dict[str, Any]
    keydir: Path


def load_or_generate_identity(keydir: Path | None = None) -> CastorIdentity:
    """Load a keypair from ``keydir``; generate + persist one if absent."""
    kd = Path(keydir) if keydir is not None else DEFAULT_KEYDIR
    kd.mkdir(parents=True, exist_ok=True)

    pub_path = kd / _PUB_FILE
    priv_path = kd / _PRIV_FILE

    if pub_path.exists() and priv_path.exists():
        try:
            pub = json.loads(pub_path.read_text())
            priv = json.loads(priv_path.read_text())
            kp = _jwk_to_keypair(pub, priv)
            return CastorIdentity(keypair=kp, public_key_jwk=pub, keydir=kd)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise ValueError(
                f"corrupt keypair at {kd} — refusing to regenerate automatically. "
                "Move or delete the existing keys manually if intentional. "
                f"Underlying error: {e}"
            ) from e

    kp = generate_ml_dsa_keypair()
    pub = encode_public_key_jwk(kp)
    priv = _keypair_to_priv_jwk(kp)

    pub_path.write_text(json.dumps(pub, indent=2))
    priv_path.write_text(json.dumps(priv, indent=2))
    os.chmod(priv_path, 0o600)

    return CastorIdentity(keypair=kp, public_key_jwk=pub, keydir=kd)


def _keypair_to_priv_jwk(kp: MlDsaKeyPair) -> dict[str, Any]:
    """Dump private-key bytes alongside pub-key JWK for re-loading."""
    pub = encode_public_key_jwk(kp)
    secret = kp._secret_key  # noqa: SLF001 — accessing private field intentionally
    if secret is None:
        raise ValueError("keypair has no private key — cannot persist")
    return {
        **pub,
        "d": _bytes_to_b64url(secret),
    }


def _jwk_to_keypair(pub: dict[str, Any], priv: dict[str, Any]) -> MlDsaKeyPair:
    """Reconstruct an MlDsaKeyPair from saved JWK blobs."""
    if "d" not in priv:
        raise ValueError("private JWK missing 'd' field")
    pub_kp = decode_public_key_jwk(pub)
    secret_bytes = _b64url_to_bytes(priv["d"])
    return MlDsaKeyPair(
        key_id=pub_kp.key_id,
        public_key_bytes=pub_kp.public_key_bytes,
        _secret_key=secret_bytes,
    )


def _bytes_to_b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_to_bytes(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)

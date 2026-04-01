"""PQC robot identity — two profiles for owned vs external robots.

Profile "pqc-v1" (owned/internal robots — Bob, Alex, operator-controlled):
  - ML-DSA-65 only (NIST FIPS 204, quantum-resistant)
  - Simpler key material; no classical component needed when operator controls both ends

Profile "pqc-hybrid-v1" (external/third-party robot registrations):
  - Ed25519  (NIST SP 800-186, classical baseline)
  - ML-DSA-65 (NIST FIPS 204, quantum-resistant primary)
  - Both algorithms sign every message; verification requires BOTH to pass

Key storage layout (default):
    ~/.opencastor/robot_identity.json   # full keypair (base64url JSON)

Environment overrides:
    OPENCASTOR_ROBOT_IDENTITY_PATH   — keypair file location
    ROBOT_OWNER_MODE                 — "true" (default) → pqc-v1, "false" → pqc-hybrid-v1

Dependencies:
    cryptography>=41  (Ed25519)
    dilithium-py>=1.0 (ML-DSA-65, FIPS 204)
"""

from __future__ import annotations

import json
import logging
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("OpenCastor.Crypto.PQC")

# ---------------------------------------------------------------------------
# Profile constants
# ---------------------------------------------------------------------------

PQC_V1 = "pqc-v1"          # ML-DSA-65 only — owned/internal robots
PQC_HYBRID_V1 = "pqc-hybrid-v1"  # Ed25519 + ML-DSA-65 — external registrations

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RobotKeyPair:
    """Robot keypair.  Ed25519 fields are None for pqc-v1 (owned) robots."""

    ml_dsa_private: bytes
    ml_dsa_public: bytes
    profile: str = PQC_HYBRID_V1
    ed25519_private: Optional[bytes] = None
    ed25519_public: Optional[bytes] = None


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


def generate_robot_keypair_v1() -> RobotKeyPair:
    """Generate a fresh pqc-v1 keypair (ML-DSA-65 only).

    Use for owned/internal robots (Bob, Alex, any operator-controlled robot).
    No Ed25519 component — both ends are operator-controlled so classical
    compatibility is not required.
    """
    from dilithium_py.ml_dsa import ML_DSA_65

    ml_pub, ml_priv = ML_DSA_65.keygen()
    logger.info("pqc-v1 keypair generated (ML-DSA-65 pk=%d B)", len(ml_pub))
    return RobotKeyPair(
        ml_dsa_private=ml_priv,
        ml_dsa_public=ml_pub,
        profile=PQC_V1,
    )


def generate_robot_keypair() -> RobotKeyPair:
    """Generate a fresh pqc-hybrid-v1 keypair (Ed25519 + ML-DSA-65).

    Use for external/third-party robot registrations where classical verifiers
    may also be present.  Returns a RobotKeyPair with all four key components.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )
    from dilithium_py.ml_dsa import ML_DSA_65

    # Ed25519 keypair
    ed_priv_key = Ed25519PrivateKey.generate()
    ed_pub_key = ed_priv_key.public_key()
    ed_priv = ed_priv_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    ed_pub = ed_pub_key.public_bytes(Encoding.Raw, PublicFormat.Raw)

    # ML-DSA-65 keypair
    ml_pub, ml_priv = ML_DSA_65.keygen()

    logger.info(
        "pqc-hybrid-v1 keypair generated (Ed25519 %d B + ML-DSA-65 pk=%d B)",
        len(ed_pub),
        len(ml_pub),
    )
    return RobotKeyPair(
        ml_dsa_private=ml_priv,
        ml_dsa_public=ml_pub,
        profile=PQC_HYBRID_V1,
        ed25519_private=ed_priv,
        ed25519_public=ed_pub,
    )


# ---------------------------------------------------------------------------
# Signing and verification — pqc-v1 (ML-DSA-65 only)
# ---------------------------------------------------------------------------


def sign_robot_message_v1(ml_dsa_private: bytes, message: bytes) -> str:
    """Sign *message* with ML-DSA-65 only (pqc-v1 profile).

    Returns a compact dot-prefixed string:
        "pqc-v1.<base64url ML-DSA-65 signature>"
    """
    from dilithium_py.ml_dsa import ML_DSA_65

    ml_sig = ML_DSA_65.sign(ml_dsa_private, message)
    return f"{PQC_V1}.{urlsafe_b64encode(ml_sig).decode()}"


def verify_robot_message_v1(ml_dsa_public: bytes, message: bytes, sig_str: str) -> bool:
    """Verify a pqc-v1 signature produced by sign_robot_message_v1.

    Returns True only when the ML-DSA-65 signature is valid and the string
    has the expected "pqc-v1.<b64url>" format.  Any failure returns False.
    """
    from dilithium_py.ml_dsa import ML_DSA_65

    try:
        prefix, b64_sig = sig_str.split(".", 1)
        if prefix != PQC_V1:
            return False
        ml_sig = urlsafe_b64decode(b64_sig + "=" * (-len(b64_sig) % 4))
        return bool(ML_DSA_65.verify(ml_dsa_public, message, ml_sig))
    except Exception as exc:  # noqa: BLE001
        logger.debug("verify_robot_message_v1 failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Signing and verification — pqc-hybrid-v1 (Ed25519 + ML-DSA-65)
# ---------------------------------------------------------------------------


def sign_robot_message(keypair: RobotKeyPair, message: bytes) -> str:
    """Sign *message* with both Ed25519 and ML-DSA-65.

    Returns a base64url-encoded JSON envelope:
        {
          "profile": "pqc-hybrid-v1",
          "ed25519":  "<b64url sig>",
          "ml_dsa_65": "<b64url sig>"
        }

    Verification requires both signatures to pass.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from dilithium_py.ml_dsa import ML_DSA_65

    ed_priv_key = Ed25519PrivateKey.from_private_bytes(keypair.ed25519_private)
    ed_sig = ed_priv_key.sign(message)

    ml_sig = ML_DSA_65.sign(keypair.ml_dsa_private, message)

    envelope = {
        "profile": keypair.profile,
        "ed25519": urlsafe_b64encode(ed_sig).decode(),
        "ml_dsa_65": urlsafe_b64encode(ml_sig).decode(),
    }
    return urlsafe_b64encode(json.dumps(envelope, separators=(",", ":")).encode()).decode()


def verify_robot_message(
    ed25519_pub: bytes,
    ml_dsa_pub: bytes,
    message: bytes,
    sig_str: str,
) -> bool:
    """Verify a signature envelope produced by sign_robot_message.

    Returns True only when BOTH Ed25519 AND ML-DSA-65 signatures are valid.
    Any failure (bad signature, missing field, wrong format) returns False.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from dilithium_py.ml_dsa import ML_DSA_65

    try:
        # Decode envelope — pad to multiple of 4 for urlsafe_b64decode
        padded = sig_str + "=" * (-len(sig_str) % 4)
        envelope = json.loads(urlsafe_b64decode(padded))

        ed_sig = urlsafe_b64decode(envelope["ed25519"] + "=" * (-len(envelope["ed25519"]) % 4))
        ml_sig = urlsafe_b64decode(envelope["ml_dsa_65"] + "=" * (-len(envelope["ml_dsa_65"]) % 4))

        # Ed25519 verification
        pub = Ed25519PublicKey.from_public_bytes(ed25519_pub)
        pub.verify(ed_sig, message)

        # ML-DSA-65 verification
        if not ML_DSA_65.verify(ml_dsa_pub, message, ml_sig):
            return False

        return True

    except InvalidSignature:
        return False
    except Exception as exc:  # noqa: BLE001
        logger.debug("verify_robot_message failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Identity record
# ---------------------------------------------------------------------------


def robot_identity_record(keypair: RobotKeyPair) -> dict:
    """Return the public identity dict for this robot.

    Safe to store, publish, or include in registration payloads.
    Never contains private key material.

    For pqc-v1 (owned robots):
        {
          "crypto_profile": "pqc-v1",
          "pqc_public_key": "<b64url ML-DSA-65 public key>"
        }

    For pqc-hybrid-v1 (external robots):
        {
          "crypto_profile":    "pqc-hybrid-v1",
          "pqc_public_key":    "<b64url ML-DSA-65 public key>",
          "ed25519_public_key": "<b64url Ed25519 public key>"
        }
    """
    record: dict = {
        "crypto_profile": keypair.profile,
        "pqc_public_key": urlsafe_b64encode(keypair.ml_dsa_public).decode(),
    }
    if keypair.profile != PQC_V1 and keypair.ed25519_public is not None:
        record["ed25519_public_key"] = urlsafe_b64encode(keypair.ed25519_public).decode()
    return record


# ---------------------------------------------------------------------------
# Keypair persistence
# ---------------------------------------------------------------------------

_DEFAULT_IDENTITY_PATH = Path.home() / ".opencastor" / "robot_identity.json"


def _identity_path() -> Path:
    env = os.environ.get("OPENCASTOR_ROBOT_IDENTITY_PATH", "")
    return Path(env).expanduser() if env else _DEFAULT_IDENTITY_PATH


def _b64(b: bytes) -> str:
    return urlsafe_b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return urlsafe_b64decode(s + "=" * (-len(s) % 4))


def save_robot_keypair(keypair: RobotKeyPair, path: Path | None = None) -> None:
    """Persist the full keypair to disk as JSON (base64url-encoded).

    Ed25519 fields are omitted for pqc-v1 keypairs.
    """
    target = path or _identity_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {
        "profile": keypair.profile,
        "ml_dsa_private": _b64(keypair.ml_dsa_private),
        "ml_dsa_public": _b64(keypair.ml_dsa_public),
    }
    if keypair.ed25519_private is not None:
        data["ed25519_private"] = _b64(keypair.ed25519_private)
    if keypair.ed25519_public is not None:
        data["ed25519_public"] = _b64(keypair.ed25519_public)
    target.write_text(json.dumps(data, indent=2))
    logger.info("Robot identity keypair saved: %s", target)


def load_robot_keypair(path: Path | None = None) -> RobotKeyPair:
    """Load a keypair previously saved by save_robot_keypair."""
    target = path or _identity_path()
    data = json.loads(target.read_text())
    profile = data.get("profile", PQC_HYBRID_V1)
    ed_priv = _unb64(data["ed25519_private"]) if "ed25519_private" in data else None
    ed_pub = _unb64(data["ed25519_public"]) if "ed25519_public" in data else None
    return RobotKeyPair(
        ml_dsa_private=_unb64(data["ml_dsa_private"]),
        ml_dsa_public=_unb64(data["ml_dsa_public"]),
        profile=profile,
        ed25519_private=ed_priv,
        ed25519_public=ed_pub,
    )


def load_or_generate_robot_keypair(
    path: Path | None = None, profile: str = PQC_HYBRID_V1
) -> tuple[RobotKeyPair, bool]:
    """Load existing keypair or generate a new one if the file is absent.

    Args:
        path:    Override keypair file location.
        profile: Which profile to generate if creating a new keypair.
                 Use PQC_V1 for owned/internal robots, PQC_HYBRID_V1 for external.

    Returns:
        (keypair, generated) — generated=True on first creation.
    """
    target = path or _identity_path()
    if target.exists():
        kp = load_robot_keypair(target)
        logger.info("Robot identity loaded from %s", target)
        return kp, False

    kp = generate_robot_keypair_v1() if profile == PQC_V1 else generate_robot_keypair()
    save_robot_keypair(kp, target)
    logger.info(
        "NEW robot identity generated (%s) and saved to %s — "
        "store the private key securely before fleet expansion",
        kp.profile,
        target,
    )
    return kp, True

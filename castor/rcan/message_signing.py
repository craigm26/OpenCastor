"""
RCAN message signing integration for OpenCastor — v2.2 hybrid (Ed25519 + ML-DSA-65).

Wires rcan-py's signing module into the OpenCastor action pipeline.
Signs outbound RCAN messages with Ed25519 (backward-compat) and ML-DSA-65
(NIST FIPS 204, post-quantum) when dilithium-py is installed.

Config (robot.rcan.yaml):
    agent:
      signing:
        enabled: true
        key_path: ~/.opencastor/signing_key.pem     # Ed25519 (auto-generated if missing)
        pq_key_path: ~/.opencastor/pq_signing.key   # ML-DSA-65 (auto-generated if missing)
        key_id: ""                                   # optional; derived from pub key if empty

Environment:
    OPENCASTOR_SIGNING_KEY_PATH    — override Ed25519 key path
    OPENCASTOR_PQ_KEY_PATH         — override ML-DSA-65 key path
    OPENCASTOR_SIGNING_ENABLED     — "true" / "false" to override config
    OPENCASTOR_PQ_SIGNING_DISABLED — set "true" to disable ML-DSA (Ed25519 only)

Q-Day timeline: hybrid 2026–2028, ML-DSA primary 2028+, Ed25519 sunset 2029

Usage:
    from castor.rcan.message_signing import get_signer, sign_action_payload

    signer = get_signer(config)
    if signer:
        signed = signer.sign_action(action_dict)
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MessageSigner:
    """
    Signs outbound RCAN action payloads with Ed25519 + ML-DSA-65 (v2.2 hybrid).

    At boot, loads or generates both an Ed25519 key and an ML-DSA-65 key.
    - `signature` field — Ed25519 (backward-compatible with RCAN v2.1)
    - `pq_sig` field    — ML-DSA-65 (FIPS 204, quantum-resistant, v2.2+)

    Thread-safe singleton pattern; one instance per process.
    """

    def __init__(
        self,
        key_path: Path,
        key_id: str = "",
        pq_key_path: Path | None = None,
        pq_disabled: bool = False,
    ) -> None:
        self._key_path = key_path
        self._pq_key_path = pq_key_path or key_path.parent / "pq_signing.key"
        self._key_pair: Any = None
        self._pq_key_pair: Any = None
        self._key_id = key_id
        self._pq_key_id = ""
        self._lock = threading.Lock()
        self._available = False
        self._pq_available = False
        self._pq_disabled = pq_disabled
        self._load_or_generate()

    def _load_or_generate(self) -> None:
        """Load existing keys or generate new Ed25519 + ML-DSA-65 key pairs."""
        # --- Ed25519 (always required) ---
        try:
            from rcan.signing import KeyPair

            if self._key_path.exists():
                self._key_pair = KeyPair.load(str(self._key_path))
                logger.info("RCAN Ed25519 signing key loaded: %s", self._key_path)
            else:
                self._key_path.parent.mkdir(parents=True, exist_ok=True)
                self._key_pair = KeyPair.generate()
                self._key_pair.save_private(str(self._key_path))
                logger.info("RCAN Ed25519 signing key generated: %s", self._key_path)

            if not self._key_id:
                import hashlib

                pub = self._key_pair.public_pem
                pub_bytes = pub if isinstance(pub, bytes) else pub.encode()
                self._key_id = hashlib.sha256(pub_bytes).hexdigest()[:8]

            self._available = True
        except ImportError:
            logger.debug("rcan[crypto] not installed — Ed25519 signing disabled")
        except Exception as exc:
            logger.warning("RCAN Ed25519 signing setup failed (non-fatal): %s", exc)

        # --- ML-DSA-65 (optional, Q-Day protection) ---
        if not self._available or self._pq_disabled:
            return

        try:
            from rcan.signing import MLDSAKeyPair

            if self._pq_key_path.exists():
                self._pq_key_pair = MLDSAKeyPair.load(str(self._pq_key_path))
                logger.info("RCAN ML-DSA-65 signing key loaded: %s", self._pq_key_path)
            else:
                self._pq_key_path.parent.mkdir(parents=True, exist_ok=True)
                self._pq_key_pair = MLDSAKeyPair.generate()
                self._pq_key_pair.save(str(self._pq_key_path))
                logger.info(
                    "RCAN ML-DSA-65 signing key generated: %s (FIPS 204 — Q-Day protection)",
                    self._pq_key_path,
                )

            self._pq_key_id = self._pq_key_pair.key_id
            self._pq_available = True
            logger.info(
                "RCAN v2.2 hybrid signing active: Ed25519 (kid=%s) + ML-DSA-65 (kid=%s)",
                self._key_id,
                self._pq_key_id,
            )
        except ImportError:
            logger.debug(
                "dilithium-py not installed — ML-DSA-65 signing disabled. "
                "Install with: pip install dilithium-py"
            )
        except Exception as exc:
            logger.warning("RCAN ML-DSA-65 signing setup failed (non-fatal): %s", exc)

    @property
    def available(self) -> bool:
        return self._available

    @property
    def pq_available(self) -> bool:
        return self._pq_available

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def pq_key_id(self) -> str:
        return self._pq_key_id

    @property
    def public_key_pem(self) -> bytes:
        """Return the Ed25519 public key in PEM format for sharing."""
        if self._key_pair and hasattr(self._key_pair, "public_pem"):
            pub = self._key_pair.public_pem
            return pub if isinstance(pub, bytes) else pub.encode()
        return b""

    @property
    def pq_public_key_bytes(self) -> bytes:
        """Return the ML-DSA-65 raw public key bytes (1952 bytes) for sharing."""
        if self._pq_key_pair and hasattr(self._pq_key_pair, "public_key"):
            return bytes(self._pq_key_pair.public_key)
        return b""

    def sign_message(self, message: dict) -> dict:
        """
        Add Ed25519 + ML-DSA-65 signatures to a RCAN message dict (v2.2 hybrid).

        - ``signature`` — Ed25519 (backward-compatible with RCAN v2.1)
        - ``pq_sig``    — ML-DSA-65 (v2.2, FIPS 204; only when dilithium-py installed)

        Returns a new dict; does not modify the input.
        """
        if not self._available or self._key_pair is None:
            return message

        try:
            import base64
            import json

            msg_copy = dict(message)
            # Canonical payload: sorted JSON without signature fields
            payload = {
                k: v for k, v in msg_copy.items()
                if k not in ("signature", "pq_sig")
            }
            payload_bytes = json.dumps(
                payload, sort_keys=True, separators=(",", ":")
            ).encode()

            with self._lock:
                # Ed25519 signature (always)
                ed_sig = self._key_pair.sign_bytes(payload_bytes)
                msg_copy["signature"] = {
                    "alg": "ed25519",
                    "kid": self._key_id,
                    "sig": base64.urlsafe_b64encode(ed_sig).decode().rstrip("="),
                }

                # ML-DSA-65 signature (v2.2 hybrid — when available)
                if self._pq_available and self._pq_key_pair is not None:
                    pq_sig_bytes = self._pq_key_pair.sign_bytes(payload_bytes)
                    msg_copy["pq_sig"] = {
                        "alg": "ml-dsa-65",
                        "kid": self._pq_key_id,
                        "sig": base64.urlsafe_b64encode(pq_sig_bytes).decode().rstrip("="),
                    }

            return msg_copy
        except Exception as exc:
            logger.debug("RCAN hybrid message signing failed (non-fatal): %s", exc)
            return message

    def sign_action(self, action: dict) -> dict:
        """
        Inject a signature block into an action dict for the commitment chain.

        The action dict is signed as-is (type, params, confidence, etc.)
        Returns a new dict with 'rcan_sig' key added.
        """
        if not self._available or self._key_pair is None:
            return action

        try:
            import json

            payload_bytes = json.dumps(action, sort_keys=True, separators=(",", ":")).encode()
            with self._lock:
                sig_hex = self._key_pair.sign_bytes(payload_bytes).hex()

            signed = dict(action)
            signed["rcan_sig"] = {
                "alg": "Ed25519",
                "kid": self._key_id,
                "sig": sig_hex,
            }
            return signed
        except Exception as exc:
            logger.debug("RCAN action signing failed (non-fatal): %s", exc)
            return action

    def verify_action(self, action: dict) -> bool:
        """Verify a signed action dict. Returns True if valid or unsigned."""
        sig_block = action.get("rcan_sig")
        if not sig_block:
            return True  # unsigned is not invalid by default

        if not self._available or self._key_pair is None:
            return True  # can't verify without key — assume ok

        try:
            import json

            payload = {k: v for k, v in action.items() if k != "rcan_sig"}
            payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            sig_bytes = bytes.fromhex(sig_block["sig"])

            with self._lock:
                return self._key_pair.verify(payload_bytes, sig_bytes)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_signer: MessageSigner | None = None
_signer_lock = threading.Lock()


def get_signer(config: dict | None = None) -> MessageSigner | None:
    """
    Return the module-level MessageSigner singleton.

    Instantiated on first call using config. Returns None if signing
    is disabled or rcan[crypto] is not installed.
    """
    global _signer

    with _signer_lock:
        if _signer is not None:
            return _signer if _signer.available else None

        cfg = config or {}
        agent_cfg = cfg.get("agent", {})
        signing_cfg = agent_cfg.get("signing", {})

        # Check enabled flag
        env_enabled = os.environ.get("OPENCASTOR_SIGNING_ENABLED", "")
        if env_enabled.lower() == "false":
            return None
        if env_enabled.lower() != "true" and not signing_cfg.get("enabled", False):
            return None

        # Key paths
        env_key_path = os.environ.get("OPENCASTOR_SIGNING_KEY_PATH", "")
        key_path_str = (
            env_key_path
            or signing_cfg.get("key_path", "")
            or str(Path.home() / ".opencastor" / "signing_key.pem")
        )
        key_path = Path(key_path_str).expanduser()
        key_id = signing_cfg.get("key_id", "")

        env_pq_path = os.environ.get("OPENCASTOR_PQ_KEY_PATH", "")
        pq_key_path_str = (
            env_pq_path
            or signing_cfg.get("pq_key_path", "")
            or str(Path.home() / ".opencastor" / "pq_signing.key")
        )
        pq_key_path = Path(pq_key_path_str).expanduser()
        pq_disabled = os.environ.get("OPENCASTOR_PQ_SIGNING_DISABLED", "").lower() == "true"

        signer = MessageSigner(
            key_path=key_path,
            key_id=key_id,
            pq_key_path=pq_key_path,
            pq_disabled=pq_disabled,
        )
        if signer.available:
            _signer = signer
            return _signer
        return None


def sign_action_payload(action: dict, config: dict | None = None) -> dict:
    """
    Convenience wrapper: sign an action dict if signing is configured.

    Returns the original dict unchanged if signing is disabled.
    """
    signer = get_signer(config)
    if signer is None:
        return action
    return signer.sign_action(action)

"""
RCAN message signing — ML-DSA-65 (NIST FIPS 204).

RCAN v2.2: Ed25519 is fully deprecated. ML-DSA-65 is the ONLY signing algorithm.
Bob (RRN-000000000001) is the first robot running ML-DSA-65 primary.

Key location (auto-generated if missing):
    ~/.opencastor/pq_signing.key   # ML-DSA-65

Config overrides:
    OPENCASTOR_PQ_KEY_PATH   — override ML-DSA-65 key path

Q-Day timeline: ML-DSA-65 PRIMARY NOW (2026). Ed25519 sunset 2027. NIST deadline 2029.
"""

import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("OpenCastor.Signing")


class MessageSigner:
    """
    Signs outbound RCAN messages with ML-DSA-65 (FIPS 204).

    At boot, loads or auto-generates the ML-DSA-65 key pair.
    Thread-safe singleton pattern; one instance per process.
    """

    def __init__(
        self,
        key_path: Path,
        key_id: str = "",
        pq_key_path: Path | None = None,
        pq_disabled: bool = False,
    ) -> None:
        # key_path arg kept for API compat but unused (ML-DSA only now)
        self._key_path = key_path
        self._pq_key_path = pq_key_path or key_path.parent / "pq_signing.key"
        self._pq_key_pair: Any = None
        self._key_id = key_id
        self._pq_key_id = ""
        self._lock = threading.Lock()
        self._available = False
        self._pq_disabled = pq_disabled

    def initialize(self) -> None:
        """Load or generate the ML-DSA-65 signing key pair."""
        with self._lock:
            self._load_or_generate()

    def _load_or_generate(self) -> None:
        if self._pq_disabled:
            logger.warning("ML-DSA-65 signing disabled — robot is NOT Q-Day protected")
            return

        try:
            from rcan.signing import MLDSAKeyPair

            if self._pq_key_path.exists():
                self._pq_key_pair = MLDSAKeyPair.load(str(self._pq_key_path))
                logger.info(
                    "ML-DSA-65 signing key loaded: %s (kid=%s)",
                    self._pq_key_path,
                    self._pq_key_pair.key_id,
                )
            else:
                self._pq_key_path.parent.mkdir(parents=True, exist_ok=True)
                self._pq_key_pair = MLDSAKeyPair.generate()
                self._pq_key_pair.save(str(self._pq_key_path))
                logger.info(
                    "ML-DSA-65 signing key generated: %s (kid=%s, FIPS 204)",
                    self._pq_key_path,
                    self._pq_key_pair.key_id,
                )

            self._pq_key_id = self._pq_key_pair.key_id
            self._available = True
            logger.info("RCAN v2.2 ML-DSA-65 signing active (kid=%s)", self._pq_key_id)

        except ImportError:
            logger.error(
                "CRITICAL: dilithium-py not installed — ML-DSA-65 signing DISABLED. "
                "Robot is NOT Q-Day protected. Install: pip install dilithium-py"
            )
        except Exception as exc:
            logger.error("ML-DSA-65 signing setup failed: %s", exc)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._available

    @property
    def pq_available(self) -> bool:
        return self._available

    @property
    def key_id(self) -> str:
        return self._pq_key_id

    @property
    def pq_key_id(self) -> str:
        return self._pq_key_id

    def public_key_bytes(self) -> bytes | None:
        if self._pq_key_pair and hasattr(self._pq_key_pair, "public_key"):
            return self._pq_key_pair.public_key
        return None

    # ------------------------------------------------------------------
    # Signing
    # ------------------------------------------------------------------

    def sign_message_dict(self, msg_dict: dict) -> dict:
        """
        Add ML-DSA-65 signature to a RCAN message dict (in-place).

        Sets msg_dict["sig"] = { alg: "ml-dsa-65", kid, value }.
        Returns the dict with signature added.
        """
        if not self._available or self._pq_key_pair is None:
            return msg_dict

        import base64
        import json

        try:
            payload = {
                "rcan": msg_dict.get("rcan", ""),
                "msg_id": msg_dict.get("msg_id", ""),
                "timestamp": msg_dict.get("timestamp", 0),
                "cmd": msg_dict.get("cmd", ""),
                "target": msg_dict.get("target", ""),
                "params": msg_dict.get("params", {}),
            }
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            raw_sig = self._pq_key_pair.sign_bytes(canonical)
            msg_dict["sig"] = {
                "alg": "ml-dsa-65",
                "kid": self._pq_key_id,
                "value": base64.urlsafe_b64encode(raw_sig).decode(),
            }
        except Exception as exc:
            logger.debug("ML-DSA-65 message signing failed: %s", exc)

        return msg_dict


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_signer: MessageSigner | None = None
_signer_lock = threading.Lock()


def get_message_signer(config: dict | None = None) -> MessageSigner:
    """Return the process-level MessageSigner (initialized on first call)."""
    global _signer
    if _signer is not None:
        return _signer
    with _signer_lock:
        if _signer is not None:
            return _signer

        signing_cfg = (config or {}).get("agent", {}).get("signing", {})

        env_pq_path = os.environ.get("OPENCASTOR_PQ_KEY_PATH", "")
        pq_path_str = (
            env_pq_path
            or signing_cfg.get("pq_key_path", "")
            or str(Path.home() / ".opencastor" / "pq_signing.key")
        )
        pq_key_path = Path(pq_path_str).expanduser()

        # key_path kept for API compat
        key_path = Path.home() / ".opencastor" / "signing_key.pem"

        pq_disabled = os.environ.get("OPENCASTOR_PQ_SIGNING_DISABLED", "").lower() == "true"

        _signer = MessageSigner(
            key_path=key_path,
            pq_key_path=pq_key_path,
            pq_disabled=pq_disabled,
        )
        _signer.initialize()
        return _signer

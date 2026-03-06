"""
RCAN Commitment Chain — cryptographically sealed action audit trail.

Every robot action executed by OpenCastor can be sealed into a
:class:`rcan.CommitmentRecord` and appended to a persistent HMAC-chained
log. This provides forensic-grade proof of what the robot did, when, at
what confidence, and under which authorization — independently verifiable
by any party with the shared HMAC secret.

Usage:
    chain = CommitmentChain(secret="your-secret", log_path=".opencastor-commitments.jsonl")
    chain.append_action("move_forward", {"distance_m": 1.0}, robot_uri=str(ruri))
    chain.verify()  # True

Config (robot.rcan.yaml):
    agent:
      commitment_chain:
        enabled: true
        secret_env: OPENCASTOR_COMMITMENT_SECRET

Spec: https://rcan.dev/spec#section-16
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("OpenCastor.CommitmentChain")


def _resolve_default_log_path() -> Path:
    """Resolve the default commitment log path using XDG conventions.

    Resolution order:
    1. ``$OPENCASTOR_COMMITMENT_LOG`` env var
    2. ``$XDG_DATA_HOME/opencastor/commitments.jsonl``
    3. ``~/.local/share/opencastor/commitments.jsonl``
    4. ``.opencastor-commitments.jsonl`` (CWD fallback)
    """
    env_path = os.environ.get("OPENCASTOR_COMMITMENT_LOG")
    if env_path:
        return Path(env_path)

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / "opencastor" / "commitments.jsonl"

    local_share = Path.home() / ".local" / "share" / "opencastor" / "commitments.jsonl"
    try:
        # Verify home directory is accessible
        local_share.parent.mkdir(parents=True, exist_ok=True)
        return local_share
    except OSError:
        pass

    return Path(".opencastor-commitments.jsonl")


DEFAULT_LOG_PATH = _resolve_default_log_path()


class CommitmentChain:
    """
    Thread-safe, persistent HMAC-chained commitment record log.

    Wraps ``rcan.audit.AuditChain`` with file persistence and a
    last-hash tracker for cross-process chain continuity.

    Args:
        secret:   HMAC secret (str or bytes). Read from env var if not set.
        log_path: Path to the JSONL commitment log file.
    """

    def __init__(
        self,
        secret: str | bytes | None = None,
        log_path: Path | str = DEFAULT_LOG_PATH,
    ) -> None:
        self._lock = threading.Lock()
        self._log_path = Path(log_path)
        self._secret = self._resolve_secret(secret)
        self._last_hash: str | None = self._load_last_hash()

        try:
            from rcan.audit import AuditChain

            self._chain: AuditChain | None = AuditChain(self._secret)
        except ImportError:
            logger.warning("rcan package not installed — commitment chain disabled")
            self._chain = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._chain is not None and bool(self._secret)

    def append_action(
        self,
        action_type: str,
        params: dict,
        robot_uri: str = "",
        confidence: float | None = None,
        model_identity: str | None = None,
        operator: str | None = None,
        safety_approved: bool = True,
        safety_reason: str = "",
    ) -> Any | None:
        """
        Create, seal, and persist a CommitmentRecord for an action.

        Returns the sealed :class:`rcan.CommitmentRecord`, or None if disabled.
        """
        if not self.enabled:
            return None

        try:
            from rcan import CommitmentRecord

            with self._lock:
                record = CommitmentRecord(
                    action=action_type,
                    params=params,
                    robot_uri=robot_uri,
                    confidence=confidence,
                    model_identity=model_identity,
                    operator=operator,
                    safety_approved=safety_approved,
                    safety_reason=safety_reason,
                    previous_hash=self._last_hash,
                )
                sealed = self._chain.append(record)
                self._last_hash = sealed.content_hash
                self._persist(sealed)
                logger.debug(
                    "CommitmentRecord sealed: action=%s hash=%s",
                    action_type,
                    sealed.content_hash[:12],
                )
                return sealed
        except Exception as exc:
            logger.warning("CommitmentRecord failed (non-fatal): %s", exc)
            return None

    def verify(self) -> bool:
        """Verify the in-memory chain integrity. Returns True if valid."""
        if not self.enabled:
            return True
        try:
            return self._chain.verify_all()
        except Exception:
            return False

    def verify_log(self) -> tuple[bool, int, list[str]]:
        """
        Verify the on-disk JSONL commitment log.

        Returns:
            (valid: bool, count: int, errors: list[str])
        """
        if not self._log_path.exists():
            return True, 0, []

        try:
            from rcan import CommitmentRecord

            errors: list[str] = []
            prev_hash: str | None = None
            count = 0
            with open(self._log_path) as f:
                for i, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue
                    import json

                    data = json.loads(line)
                    record = CommitmentRecord.from_dict(data)

                    if not record.verify(self._secret):
                        errors.append(
                            f"Line {i + 1}: HMAC invalid (record_id={record.record_id[:8]})"
                        )
                    if prev_hash is not None and record.previous_hash != prev_hash:
                        errors.append(
                            f"Line {i + 1}: chain broken (expected prev_hash={prev_hash[:12]})"
                        )
                    prev_hash = record.content_hash
                    count += 1
            return len(errors) == 0, count, errors
        except Exception as exc:
            return False, 0, [f"Parse error: {exc}"]

    def last_n(self, n: int = 10) -> list[dict]:
        """Return the last N records from the log as dicts."""
        if not self._log_path.exists():
            return []
        try:
            import json

            lines = self._log_path.read_text().strip().splitlines()
            return [json.loads(line) for line in lines[-n:] if line.strip()]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _persist(self, record: Any) -> None:
        """Append a sealed record to the JSONL log."""
        try:
            with open(self._log_path, "a") as f:
                f.write(record.to_json() + "\n")
        except Exception as exc:
            logger.warning("Failed to persist CommitmentRecord: %s", exc)

    def _load_last_hash(self) -> str | None:
        """Read the last record's content hash from the log for chain continuity."""
        if not self._log_path.exists():
            return None
        try:
            import json

            with open(self._log_path, "rb") as f:
                # Read last non-empty line efficiently
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return None
                chunk_size = min(4096, size)
                f.seek(max(0, size - chunk_size))
                tail = f.read().decode(errors="replace")
                for line in reversed(tail.splitlines()):
                    line = line.strip()
                    if line:
                        data = json.loads(line)
                        # Recompute content hash from canonical payload
                        from rcan import CommitmentRecord

                        record = CommitmentRecord.from_dict(data)
                        return record.content_hash
        except Exception:
            pass
        return None

    @staticmethod
    def _resolve_secret(secret: str | bytes | None) -> bytes:
        if secret:
            return secret.encode() if isinstance(secret, str) else secret
        env_secret = os.environ.get("OPENCASTOR_COMMITMENT_SECRET", "")
        if env_secret:
            return env_secret.encode()
        # Default: warn but allow operation with a weak default
        logger.warning(
            "OPENCASTOR_COMMITMENT_SECRET not set — using default (not suitable for production)"
        )
        return b"opencastor-default-commitment-secret"


# Module-level singleton
_chain: CommitmentChain | None = None
_chain_lock = threading.Lock()


def get_commitment_chain(
    secret: str | bytes | None = None,
    log_path: Path | str = DEFAULT_LOG_PATH,
) -> CommitmentChain:
    """Return (or create) the module-level CommitmentChain singleton."""
    global _chain
    with _chain_lock:
        if _chain is None:
            _chain = CommitmentChain(secret=secret, log_path=log_path)
    return _chain


def reset_chain() -> None:
    """Reset the singleton (for testing)."""
    global _chain
    with _chain_lock:
        _chain = None

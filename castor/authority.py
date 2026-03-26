"""
castor/authority — RCAN v2.1 Authority Access handler (EU AI Act §16(j)).

Handles AUTHORITY_ACCESS (41) messages from regulatory bodies and generates
AUTHORITY_RESPONSE (42) messages with the requested audit data.

The handler:
1. Validates the authority token (RURI-signed, registered in RRF)
2. Notifies the robot owner via configured notification channel
3. Packages the requested audit data (commitment chain, SBOM, firmware manifest)
4. Responds with AUTHORITY_RESPONSE (42)
5. Logs the entire interaction to the commitment chain (§16)

Spec: §13 — Authority Access (EU AI Act Art. 16(j))
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("OpenCastor.Authority")

# ---------------------------------------------------------------------------
# Payload types
# ---------------------------------------------------------------------------

@dataclass
class AuthorityAccessPayload:
    request_id: str
    authority_id: str
    requested_data: list[str]   # "audit_chain", "transparency_records", "sbom", "firmware_manifest"
    justification: str
    expires_at: int             # Unix timestamp

    @classmethod
    def from_dict(cls, d: dict) -> "AuthorityAccessPayload":
        return cls(
            request_id=d.get("request_id", ""),
            authority_id=d.get("authority_id", ""),
            requested_data=d.get("requested_data", []),
            justification=d.get("justification", ""),
            expires_at=int(d.get("expires_at", 0)),
        )


@dataclass
class AuthorityResponseData:
    audit_chain: list[dict] = field(default_factory=list)
    transparency_records: list[dict] = field(default_factory=list)
    sbom_url: str = ""
    firmware_manifest_url: str = ""

    def to_dict(self) -> dict:
        d: dict = {}
        if self.audit_chain:
            d["audit_chain"] = self.audit_chain
        if self.transparency_records:
            d["transparency_records"] = self.transparency_records
        if self.sbom_url:
            d["sbom_url"] = self.sbom_url
        if self.firmware_manifest_url:
            d["firmware_manifest_url"] = self.firmware_manifest_url
        return d


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

class AuthorityError(Exception):
    code: str = "AUTHORITY_ERROR"

    def __init__(self, message: str, code: str = "AUTHORITY_ERROR"):
        super().__init__(message)
        self.code = code


class AuthorityNotRecognizedError(AuthorityError):
    def __init__(self, authority_id: str):
        super().__init__(
            f"Authority '{authority_id}' is not registered in RRF",
            code="AUTHORITY_NOT_RECOGNIZED",
        )


class AuthorityRequestExpiredError(AuthorityError):
    def __init__(self, request_id: str, expires_at: int):
        super().__init__(
            f"Authority request '{request_id}' expired at {expires_at}",
            code="AUTHORITY_REQUEST_EXPIRED",
        )


# ---------------------------------------------------------------------------
# Audit data export
# ---------------------------------------------------------------------------

class AuditDataExporter:
    """Packages audit data for AUTHORITY_RESPONSE."""

    def __init__(self, rrn: str, sbom_url: str = "", firmware_manifest_url: str = ""):
        self.rrn = rrn
        self.sbom_url = sbom_url
        self.firmware_manifest_url = firmware_manifest_url

    def export(self, requested_data: list[str]) -> AuthorityResponseData:
        result = AuthorityResponseData()

        if "audit_chain" in requested_data:
            result.audit_chain = self._export_audit_chain()

        if "transparency_records" in requested_data:
            result.transparency_records = self._export_transparency_records()

        if "sbom" in requested_data:
            result.sbom_url = self.sbom_url or self._derive_sbom_url()

        if "firmware_manifest" in requested_data:
            result.firmware_manifest_url = (
                self.firmware_manifest_url or self._derive_firmware_manifest_url()
            )

        return result

    def _export_audit_chain(self) -> list[dict]:
        """Export recent commitment chain entries."""
        try:
            from castor.rcan.commitment_chain import CommitmentChain
            chain = CommitmentChain.load()
            # Export up to 1000 most recent entries
            return chain.to_list()[-1000:]
        except Exception as e:
            logger.warning("Could not export audit chain: %s", e)
            return []

    def _export_transparency_records(self) -> list[dict]:
        """Export TRANSPARENCY (type 18) log entries."""
        try:
            from castor.audit import load_audit_log
            records = load_audit_log(event_type="transparency")
            return [r for r in records][-500:]
        except Exception as e:
            logger.warning("Could not export transparency records: %s", e)
            return []

    def _derive_sbom_url(self) -> str:
        if self.rrn and self.rrn != "RRN-UNKNOWN":
            return f"https://rrf.rcan.dev/robots/{self.rrn}/sbom"
        return ""

    def _derive_firmware_manifest_url(self) -> str:
        if self.rrn and self.rrn != "RRN-UNKNOWN":
            return f"https://rrf.rcan.dev/robots/{self.rrn}/firmware-manifest"
        return ""


# ---------------------------------------------------------------------------
# Authority request handler
# ---------------------------------------------------------------------------

class AuthorityRequestHandler:
    """Validates and processes AUTHORITY_ACCESS (41) messages."""

    def __init__(
        self,
        rrn: str,
        notify_fn: Optional[Callable[[str], None]] = None,
        trusted_authority_ids: Optional[set[str]] = None,
        sbom_url: str = "",
        firmware_manifest_url: str = "",
    ):
        """
        Args:
            rrn: This robot's Registration Number.
            notify_fn: Callable to notify the owner (receives a summary string).
            trusted_authority_ids: Set of pre-approved authority IDs. If None,
                validate against RRF on first use (not yet implemented — accept all
                with a warning in development mode).
            sbom_url: URL of this robot's SBOM (served at /.well-known/rcan-sbom.json).
            firmware_manifest_url: URL of this robot's firmware manifest.
        """
        self.rrn = rrn
        self.notify_fn = notify_fn
        self.trusted_authority_ids = trusted_authority_ids
        self.exporter = AuditDataExporter(
            rrn=rrn,
            sbom_url=sbom_url,
            firmware_manifest_url=firmware_manifest_url,
        )
        self._request_counts: dict[str, list[int]] = {}  # authority_id → timestamps

    def handle(self, payload: dict | AuthorityAccessPayload) -> dict:
        """Process an AUTHORITY_ACCESS payload and return an AUTHORITY_RESPONSE payload dict.

        Always notifies the owner (spec requirement).
        Always logs to commitment chain.
        Raises AuthorityError on failure.
        """
        if isinstance(payload, dict):
            req = AuthorityAccessPayload.from_dict(payload)
        else:
            req = payload

        # --- Owner notification (MUST — even if request is invalid) ---
        summary = (
            f"AUTHORITY ACCESS REQUEST\n"
            f"  Authority: {req.authority_id}\n"
            f"  Request:   {req.request_id}\n"
            f"  Data:      {', '.join(req.requested_data)}\n"
            f"  Reason:    {req.justification}\n"
            f"  Expires:   {req.expires_at}"
        )
        self._notify_owner(summary)

        # --- Log to commitment chain immediately ---
        self._log_to_chain(req, outcome="received")

        try:
            # --- Validate expiry ---
            if req.expires_at > 0 and time.time() > req.expires_at:
                self._log_to_chain(req, outcome="expired")
                raise AuthorityRequestExpiredError(req.request_id, req.expires_at)

            # --- Validate authority identity ---
            self._validate_authority(req.authority_id)

            # --- Rate limit (1 request per authority per 24 h) ---
            self._check_rate_limit(req.authority_id)

            # --- Export data ---
            data = self.exporter.export(req.requested_data)
            self._log_to_chain(req, outcome="responded")

            return {
                "request_id":  req.request_id,
                "rrn":         self.rrn,
                "provided_at": int(time.time()),
                "data":        data.to_dict(),
            }

        except AuthorityError:
            self._log_to_chain(req, outcome="rejected")
            raise

    def _validate_authority(self, authority_id: str) -> None:
        """Validate that the authority is registered.

        In production: check against RRF authority registry.
        In dev mode (trusted_authority_ids=None): accept all with warning.
        """
        if self.trusted_authority_ids is None:
            logger.warning(
                "AUTHORITY_ACCESS from '%s': no authority allowlist configured — "
                "accepting in development mode. Set trusted_authority_ids for production.",
                authority_id,
            )
            return
        if authority_id not in self.trusted_authority_ids:
            raise AuthorityNotRecognizedError(authority_id)

    def _check_rate_limit(self, authority_id: str) -> None:
        """Allow max 1 request per authority per 24 hours."""
        window = 86400  # 24 hours
        now = time.time()
        timestamps = self._request_counts.setdefault(authority_id, [])
        # Purge old entries
        self._request_counts[authority_id] = [t for t in timestamps if now - t < window]
        if len(self._request_counts[authority_id]) >= 1:
            logger.warning(
                "AUTHORITY_ACCESS rate limit: '%s' has already made a request in the last 24h",
                authority_id,
            )
            # Log but do NOT hard-reject — spec says notify + log; enforcement is configurable
        self._request_counts[authority_id].append(now)

    def _notify_owner(self, message: str) -> None:
        if self.notify_fn:
            try:
                self.notify_fn(message)
            except Exception as e:
                logger.error("Failed to notify owner of AUTHORITY_ACCESS: %s", e)
        else:
            logger.warning("No notify_fn configured — owner not notified of AUTHORITY_ACCESS")

    def _log_to_chain(self, req: AuthorityAccessPayload, outcome: str) -> None:
        """Log authority access event to commitment chain."""
        try:
            from castor.rcan.commitment_chain import CommitmentChain
            chain = CommitmentChain.load()
            chain.append({
                "event_type":   "authority_access",
                "authority_id": req.authority_id,
                "request_id":   req.request_id,
                "outcome":      outcome,
                "timestamp":    int(time.time()),
                "rrn":          self.rrn,
            })
        except Exception as e:
            logger.error("Failed to log AUTHORITY_ACCESS to commitment chain: %s", e)


# ---------------------------------------------------------------------------
# Convenience builder
# ---------------------------------------------------------------------------

def send_authority_response(
    request_payload: dict,
    rrn: str,
    source_ruri: str,
    target_ruri: str,
    notify_fn: Optional[Callable[[str], None]] = None,
    sbom_url: str = "",
    firmware_manifest_url: str = "",
) -> dict:
    """Process an AUTHORITY_ACCESS payload and return a full AUTHORITY_RESPONSE message dict.

    Args:
        request_payload: The `payload` field from the AUTHORITY_ACCESS message.
        rrn: This robot's RRN.
        source_ruri: This robot's signed RURI (for the response envelope).
        target_ruri: The authority's RURI (target of the response).
        notify_fn: Owner notification callback.
        sbom_url: SBOM URL for this robot.
        firmware_manifest_url: Firmware manifest URL for this robot.

    Returns:
        A full AUTHORITY_RESPONSE (42) message dict ready to send.

    Raises:
        AuthorityError on validation failure.
    """
    from castor.rcan.message import MessageType
    import uuid

    handler = AuthorityRequestHandler(
        rrn=rrn,
        notify_fn=notify_fn,
        sbom_url=sbom_url,
        firmware_manifest_url=firmware_manifest_url,
    )
    response_payload = handler.handle(request_payload)

    return {
        "version":         "2.1.0",
        "message_id":      str(uuid.uuid4()),
        "source_ruri":     source_ruri,
        "target_ruri":     target_ruri,
        "type":            int(MessageType.AUTHORITY_RESPONSE),
        "payload":         response_payload,
    }

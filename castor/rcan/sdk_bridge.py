"""
RCAN SDK Bridge — OpenCastor ↔ rcan-py interoperability.

This module bridges OpenCastor's internal RCAN implementation with the
official ``rcan`` Python SDK (rcan-py), ensuring full RCAN v2.2 spec
compliance at the protocol boundary.

Key responsibilities:
  - Convert OpenCastor RURI ↔ spec-compliant rcan.RobotURI
  - Parse inbound spec-format messages (rcan.RCANMessage) into OpenCastor
    RCANMessage envelopes understood by the router
  - Export OpenCastor audit entries as rcan.CommitmentRecord objects
  - Bridge OpenCastor ConfidenceGate ↔ rcan.ConfidenceGate semantics
  - Bridge OpenCastor HiTLGate ↔ rcan.HiTLGate semantics

The bridge is designed to be fully backward-compatible: existing OpenCastor
deployments using the legacy RURI format continue to work unchanged. The
bridge adds spec-format support as a superset.

Spec: https://rcan.dev/spec
SDK:  https://github.com/continuonai/rcan-py
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("OpenCastor.RCAN.Bridge")

# Default registry used when converting legacy RURI → spec RobotURI
DEFAULT_REGISTRY = "registry.rcan.dev"


# ---------------------------------------------------------------------------
# URI conversion
# ---------------------------------------------------------------------------


def ruri_to_robot_uri(ruri: Any, config: dict | None = None) -> Any:
    """
    Convert an OpenCastor :class:`~castor.rcan.ruri.RURI` to a spec-compliant
    :class:`rcan.RobotURI`.

    The legacy RURI format is ``rcan://manufacturer.model.instance/capability``.
    The spec format is  ``rcan://registry/manufacturer/model/version/device-id``.

    Args:
        ruri:    OpenCastor RURI object or string.
        config:  RCAN YAML config dict (used to extract version from metadata).

    Returns:
        ``rcan.RobotURI`` instance.

    Raises:
        ImportError: If the ``rcan`` package is not installed.
    """
    from rcan import RobotURI

    # Handle string input
    if isinstance(ruri, str):
        # Try spec format first
        try:
            return RobotURI.parse(ruri)
        except Exception:
            pass
        # Fall back to parsing as legacy RURI
        from castor.rcan.ruri import RURI as LegacyRURI

        ruri = LegacyRURI.parse(ruri)

    meta = (config or {}).get("metadata", {})
    version = meta.get("version") or meta.get("firmware_version") or "v1"
    registry = meta.get("registry") or DEFAULT_REGISTRY

    return RobotURI.build(
        manufacturer=ruri.manufacturer,
        model=ruri.model,
        version=version,
        device_id=ruri.instance,
        registry=registry,
    )


def robot_uri_to_ruri(robot_uri: Any) -> Any:
    """
    Convert a spec-compliant :class:`rcan.RobotURI` to an OpenCastor
    :class:`~castor.rcan.ruri.RURI`.

    Args:
        robot_uri: ``rcan.RobotURI`` instance or URI string.

    Returns:
        OpenCastor ``RURI`` instance.
    """
    from rcan import RobotURI

    from castor.rcan.ruri import RURI as LegacyRURI

    if isinstance(robot_uri, str):
        robot_uri = RobotURI.parse(robot_uri)

    return LegacyRURI(
        manufacturer=robot_uri.manufacturer,
        model=robot_uri.model,
        instance=robot_uri.device_id,
        capability=None,
    )


# ---------------------------------------------------------------------------
# Message conversion
# ---------------------------------------------------------------------------


def spec_message_to_opencastor(spec_msg: Any) -> Any:
    """
    Convert a spec-compliant :class:`rcan.RCANMessage` into an OpenCastor
    :class:`~castor.rcan.message.RCANMessage` envelope for routing.

    This allows external clients using the rcan-py SDK to send RCAN v2.2
    messages to OpenCastor's ``/rcan`` endpoint.

    Args:
        spec_msg: ``rcan.RCANMessage`` instance.

    Returns:
        OpenCastor ``RCANMessage`` with COMMAND type.
    """
    from castor.rcan.message import MessageType, Priority
    from castor.rcan.message import RCANMessage as OCMessage

    return OCMessage(
        type=MessageType.COMMAND,
        source=spec_msg.sender or "external",
        target=str(spec_msg.target),
        payload={
            "cmd": spec_msg.cmd,
            "params": spec_msg.params,
            "confidence": spec_msg.confidence,
            "model_identity": None,
            "rcan_version": spec_msg.rcan,
            "scope": spec_msg.scope,
        },
        priority=Priority.NORMAL,
        id=spec_msg.msg_id,
        timestamp=spec_msg.timestamp,
    )


def parse_inbound(body: dict) -> Any:
    """
    Detect and parse an inbound RCAN message body.

    Accepts both formats:
      - Spec v2.2 format: ``{"rcan": "2.2", "cmd": ..., "target": "rcan://...", ...}``
      - OpenCastor internal format: ``{"msg_type": 3, "source": ..., ...}``

    Returns the appropriate message object:
      - ``rcan.RCANMessage`` for spec format (bridge with ``spec_message_to_opencastor``)
      - OpenCastor ``RCANMessage`` for internal format

    Raises:
        ValueError: If the body cannot be parsed in either format.
    """
    # Detect spec format: has "rcan" version field and "cmd"
    if "rcan" in body and "cmd" in body and "target" in body:
        try:
            from rcan import RCANMessage as SpecMsg

            spec = SpecMsg.from_dict(body)
            logger.debug("Parsed inbound message as RCAN v2.x spec format: cmd=%s", spec.cmd)
            return spec
        except Exception as e:
            logger.warning("Failed to parse as spec message: %s", e)

    # Fall back to OpenCastor internal format
    from castor.rcan.message import RCANMessage as OCMessage

    return OCMessage.from_dict(body)


# ---------------------------------------------------------------------------
# Audit / CommitmentRecord
# ---------------------------------------------------------------------------


def action_to_commitment_record(
    action_type: str,
    params: dict,
    robot_uri_str: str,
    confidence: float | None = None,
    model_identity: str | None = None,
    operator: str | None = None,
    safety_approved: bool = True,
    safety_reason: str = "",
    previous_hash: str | None = None,
) -> Any:
    """
    Create a :class:`rcan.CommitmentRecord` from an OpenCastor action execution.

    The record is *not* sealed here — call ``.seal(secret)`` with your HMAC
    secret to produce a sealed commitment. The seal is a shared-secret HMAC:
    anyone holding that secret can recompute it, so it detects a change made
    by someone without the key and proves nothing to a party that has it.

    Args:
        action_type:    Command name (e.g. ``"move_forward"``).
        params:         Command parameters dict.
        robot_uri_str:  Robot URI string (any format — normalized internally).
        confidence:     AI inference confidence if applicable.
        model_identity: Model name/version that drove the decision.
        operator:       Operator identity.
        safety_approved: Whether the safety gate passed.
        safety_reason:  Reason if blocked.
        previous_hash:  Hash of the preceding record (for chain linking).

    Returns:
        Unsealed :class:`rcan.CommitmentRecord`.
    """
    from rcan import CommitmentRecord

    return CommitmentRecord(
        action=action_type,
        params=params,
        robot_uri=robot_uri_str,
        confidence=confidence,
        model_identity=model_identity,
        operator=operator,
        safety_approved=safety_approved,
        safety_reason=safety_reason,
        previous_hash=previous_hash,
        timestamp=time.time(),
    )


def audit_entry_to_commitment_record(entry: dict) -> Any:
    """
    Convert an OpenCastor audit log entry dict to a :class:`rcan.CommitmentRecord`.

    Useful for re-exporting audit log entries in spec-compliant format.
    """
    from rcan import CommitmentRecord

    payload = entry.get("action", {}) or entry.get("payload", {}) or {}
    return CommitmentRecord(
        action=entry.get("event", "unknown"),
        params=payload,
        robot_uri=entry.get("source", ""),
        timestamp=entry.get("ts", time.time())
        if isinstance(entry.get("ts"), float)
        else time.time(),
        safety_approved=entry.get("safety_approved", True),
        safety_reason=entry.get("safety_reason", ""),
    )


# ---------------------------------------------------------------------------
# Gate bridging
# ---------------------------------------------------------------------------


def opencastor_gate_to_rcan(oc_gate: Any) -> Any:
    """
    Convert an OpenCastor :class:`~castor.confidence_gate.ConfidenceGate`
    to a :class:`rcan.ConfidenceGate`.

    Args:
        oc_gate: OpenCastor ConfidenceGate dataclass.

    Returns:
        ``rcan.ConfidenceGate`` with equivalent threshold.
    """
    from rcan import ConfidenceGate

    return ConfidenceGate(
        threshold=getattr(oc_gate, "min_confidence", 0.7),
        action_type=getattr(oc_gate, "scope", None),
        raise_on_block=False,
    )


def rcan_gate_to_opencastor(rcan_gate: Any, scope: str = "default", on_fail: str = "block") -> Any:
    """
    Convert a :class:`rcan.ConfidenceGate` to an OpenCastor
    :class:`~castor.confidence_gate.ConfidenceGate`.

    Args:
        rcan_gate: ``rcan.ConfidenceGate`` instance.
        scope:     OpenCastor gate scope name.
        on_fail:   OpenCastor failure mode (``"block"``, ``"escalate"``, ``"allow"``).

    Returns:
        OpenCastor ``ConfidenceGate`` dataclass.
    """
    from castor.confidence_gate import ConfidenceGate as OCGate

    return OCGate(
        scope=scope,
        min_confidence=rcan_gate.threshold,
        on_fail=on_fail,
    )


# ---------------------------------------------------------------------------
# Spec compliance check
# ---------------------------------------------------------------------------


def check_compliance(config: dict) -> list[str]:
    """
    Check a robot RCAN YAML config for RCAN v1.2 spec compliance.

    Returns a list of compliance issues (empty = fully compliant).
    """
    issues: list[str] = []

    # L1: addressing
    meta = config.get("metadata", {})
    if not meta.get("manufacturer"):
        issues.append("L1: metadata.manufacturer is required (§2)")
    if not meta.get("model"):
        issues.append("L1: metadata.model is required (§2)")

    # L2: authentication
    rcan_proto = config.get("rcan_protocol", {})
    if not rcan_proto.get("jwt_auth", {}).get("enabled"):
        issues.append("L2: rcan_protocol.jwt_auth.enabled recommended (§8)")

    # L2: confidence gates
    agent = config.get("agent", {})
    if not agent.get("confidence_gates"):
        issues.append("L2: agent.confidence_gates not configured (§16)")

    # L3: HiTL gates
    if not agent.get("hitl_gates"):
        issues.append("L3: agent.hitl_gates not configured (§16)")

    # L1: RCAN version declared
    if not rcan_proto.get("version") and not config.get("rcan_version"):
        issues.append("L1: rcan_version should be declared in config (§1)")

    return issues

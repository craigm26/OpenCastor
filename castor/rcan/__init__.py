"""
OpenCastor RCAN Protocol Implementation.

Provides RURI addressing, RCANMessage envelopes, RBAC roles,
JWT authentication, mDNS discovery, capability routing, and
message dispatching per the RCAN protocol specification.

The ``rcan_protocol`` section is required in every ``.rcan.yaml``
config for safety invariants, RURI identity, RBAC, and capability
declaration.  Individual features like mDNS and JWT remain opt-in
within that section.
"""

from castor.rcan import telemetry_fields
from castor.rcan.capabilities import Capability, CapabilityRegistry
from castor.rcan.invoke import InvokeCancelRequest, InvokeRequest, InvokeResult, SkillRegistry
from castor.rcan.message import MessageType, Priority, RCANMessage
from castor.rcan.rbac import CapabilityBroker, CapabilityLease, RCANPrincipal, RCANRole, Scope
from castor.rcan.router import MessageRouter
from castor.rcan.ruri import RURI
from castor.rcan.sdk_bridge import (
    action_to_commitment_record,
    audit_entry_to_commitment_record,
    check_compliance,
    opencastor_gate_to_rcan,
    parse_inbound,
    rcan_gate_to_opencastor,
    robot_uri_to_ruri,
    ruri_to_robot_uri,
    spec_message_to_opencastor,
)

__all__ = [
    # Core
    "RURI",
    "RCANMessage",
    "MessageType",
    "Priority",
    # Auth / RBAC
    "RCANRole",
    "Scope",
    "RCANPrincipal",
    "CapabilityLease",
    "CapabilityBroker",
    # Capabilities
    "Capability",
    "CapabilityRegistry",
    # Routing
    "MessageRouter",
    # §19 INVOKE
    "InvokeRequest",
    "InvokeResult",
    "InvokeCancelRequest",
    "SkillRegistry",
    # §20 Telemetry fields
    "telemetry_fields",
    # SDK Bridge (rcan-py interoperability)
    "ruri_to_robot_uri",
    "robot_uri_to_ruri",
    "spec_message_to_opencastor",
    "parse_inbound",
    "action_to_commitment_record",
    "audit_entry_to_commitment_record",
    "opencastor_gate_to_rcan",
    "rcan_gate_to_opencastor",
    "check_compliance",
]

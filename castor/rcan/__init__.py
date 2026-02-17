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

from castor.rcan.capabilities import Capability, CapabilityRegistry
from castor.rcan.message import MessageType, Priority, RCANMessage
from castor.rcan.rbac import RCANPrincipal, RCANRole, Scope
from castor.rcan.router import MessageRouter
from castor.rcan.ruri import RURI

__all__ = [
    "RURI",
    "RCANMessage",
    "MessageType",
    "Priority",
    "RCANRole",
    "Scope",
    "RCANPrincipal",
    "Capability",
    "CapabilityRegistry",
    "MessageRouter",
]

"""
RCAN Capability Registry.

Auto-detects robot capabilities from the RCAN config and provides
a runtime registry for capability discovery and routing.

Standard capabilities::

    status  -- Always present.  Telemetry / health.
    nav     -- Navigation / autonomous movement (has motors).
    teleop  -- Teleoperation (has motors).
    vision  -- Camera / visual perception (has camera).
    chat    -- Conversational AI (has agent).
    arm     -- Manipulator control (serial/parallel kinematics).
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger("OpenCastor.RCAN.Capabilities")


class Capability(str, Enum):
    """Standard RCAN capabilities."""

    STATUS = "status"
    NAV = "nav"
    TELEOP = "teleop"
    VISION = "vision"
    CHAT = "chat"
    ARM = "arm"


class CapabilityRegistry:
    """Runtime registry of robot capabilities.

    Auto-detects capabilities from the RCAN config, or they can be
    explicitly set via ``rcan_protocol.capabilities``.

    Args:
        config: RCAN configuration dict.
    """

    def __init__(self, config: Optional[Dict] = None):
        self._capabilities: Dict[str, Dict] = {}
        if config is not None:
            self._auto_detect(config)

    def _auto_detect(self, config: Dict):
        """Detect capabilities from config sections."""
        # Status is always available
        self.register(Capability.STATUS, description="Runtime telemetry and health")

        # Check for explicit capabilities in rcan_protocol (required section)
        rcan_proto = config.get("rcan_protocol") or {}
        explicit = rcan_proto.get("capabilities", [])
        if explicit:
            for cap_name in explicit:
                try:
                    cap = Capability(cap_name)
                    self.register(cap, description=f"Configured: {cap_name}")
                except ValueError:
                    # Custom capability name
                    self._capabilities[cap_name] = {
                        "name": cap_name,
                        "description": f"Custom: {cap_name}",
                        "auto_detected": False,
                    }

        # Auto-detect from config structure
        # Has motors -> nav + teleop
        drivers = config.get("drivers", [])
        if drivers:
            physics_type = config.get("physics", {}).get("type", "")
            if physics_type in ("serial_manipulator", "parallel_manipulator"):
                self.register(Capability.ARM, description="Manipulator control")
            else:
                self.register(Capability.NAV, description="Autonomous navigation")
                self.register(Capability.TELEOP, description="Remote teleoperation")

        # Has camera -> vision
        if config.get("camera") or any(
            d.get("protocol", "").startswith("camera") for d in drivers
        ):
            self.register(Capability.VISION, description="Visual perception")

        # Has agent -> chat
        if config.get("agent"):
            self.register(Capability.CHAT, description="Conversational AI")
            # If agent + camera, also vision
            if config.get("camera") is not None:
                self.register(Capability.VISION, description="Visual perception")

    def register(self, capability: Capability, description: str = ""):
        """Register a capability."""
        self._capabilities[capability.value] = {
            "name": capability.value,
            "description": description,
            "auto_detected": True,
        }

    def has(self, capability: str) -> bool:
        """Check if a capability is registered."""
        return capability in self._capabilities

    @property
    def names(self) -> List[str]:
        """Return sorted list of capability names."""
        return sorted(self._capabilities.keys())

    def to_dict(self) -> Dict[str, Dict]:
        """Return capability registry as a dict."""
        return dict(self._capabilities)

    def __len__(self) -> int:
        return len(self._capabilities)

    def __contains__(self, item: str) -> bool:
        return self.has(item)

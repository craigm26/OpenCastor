"""
OpenCastor Virtual Filesystem -- Proc (Runtime Introspection).

Like Linux's ``/proc``, this module populates read-only nodes that
expose runtime state:

    /proc/uptime          Seconds since boot.
    /proc/status          Current mode: ``active``, ``idle``, ``estop``.
    /proc/version         OpenCastor version string.
    /proc/loop/iteration  Current perception-action loop iteration.
    /proc/loop/latency_ms Last loop latency in milliseconds.
    /proc/loop/budget_ms  Configured latency budget.
    /proc/brain/provider  Active provider name.
    /proc/brain/model     Active model name.
    /proc/brain/thoughts  Total thoughts produced.
    /proc/hw/driver       Active driver type (or ``none``).
    /proc/hw/camera       Camera status (``online`` / ``offline``).
    /proc/hw/speaker      Speaker status.
"""

import logging
import time
from typing import Dict, Optional

from castor.fs.namespace import Namespace

logger = logging.getLogger("OpenCastor.FS.Proc")


class ProcFS:
    """Manages the ``/proc`` subtree with runtime telemetry.

    Call :meth:`bootstrap` once at startup, then use the update methods
    as the runtime progresses.  All ``/proc`` nodes are read-only at
    the permission level (enforced by the default ACLs).

    Args:
        ns:  The underlying namespace.
    """

    def __init__(self, ns: Namespace):
        self.ns = ns
        self._boot_time = time.time()
        self._iteration = 0

    def bootstrap(self, config: Optional[Dict] = None):
        """Create the /proc tree and populate initial values."""
        self.ns.mkdir("/proc")
        self.ns.mkdir("/proc/loop")
        self.ns.mkdir("/proc/brain")
        self.ns.mkdir("/proc/hw")

        self.ns.write("/proc/uptime", 0.0)
        self.ns.write("/proc/status", "booting")

        # Import version at runtime to avoid circular imports
        try:
            from castor import __version__
            self.ns.write("/proc/version", __version__)
        except ImportError:
            self.ns.write("/proc/version", "unknown")

        # Loop telemetry
        self.ns.write("/proc/loop/iteration", 0)
        self.ns.write("/proc/loop/latency_ms", 0.0)
        budget = 3000
        if config:
            budget = config.get("agent", {}).get("latency_budget_ms", 3000)
        self.ns.write("/proc/loop/budget_ms", budget)

        # Brain state
        provider = "none"
        model = "none"
        if config:
            provider = config.get("agent", {}).get("provider", "none")
            model = config.get("agent", {}).get("model", "none")
        self.ns.write("/proc/brain/provider", provider)
        self.ns.write("/proc/brain/model", model)
        self.ns.write("/proc/brain/thoughts", 0)
        self.ns.write("/proc/brain/last_thought", None)

        # Hardware state
        self.ns.write("/proc/hw/driver", "none")
        self.ns.write("/proc/hw/camera", "offline")
        self.ns.write("/proc/hw/speaker", "offline")

        # RCAN protocol state
        self.ns.mkdir("/proc/rcan")
        self.ns.write("/proc/ruri", None)
        self.ns.write("/proc/rcan/version", "1.0.0")
        self.ns.write("/proc/rcan/peers", [])
        self.ns.write("/proc/rcan/capabilities", [])
        self.ns.write("/proc/rcan/messages_routed", 0)

        logger.info("/proc filesystem bootstrapped")

    # ------------------------------------------------------------------
    # Update methods (called by the runtime)
    # ------------------------------------------------------------------
    def update_uptime(self):
        """Refresh /proc/uptime."""
        self.ns.write("/proc/uptime", round(time.time() - self._boot_time, 1))

    def update_status(self, status: str):
        """Set /proc/status (``booting``, ``active``, ``idle``, ``estop``, ``shutdown``)."""
        self.ns.write("/proc/status", status)

    def record_loop_iteration(self, latency_ms: float):
        """Record a completed perception-action loop iteration."""
        self._iteration += 1
        self.ns.write("/proc/loop/iteration", self._iteration)
        self.ns.write("/proc/loop/latency_ms", round(latency_ms, 2))
        self.update_uptime()

    def record_thought(self, raw_text: str, action: Optional[Dict] = None):
        """Record that the brain produced a thought."""
        count = (self.ns.read("/proc/brain/thoughts") or 0) + 1
        self.ns.write("/proc/brain/thoughts", count)
        self.ns.write("/proc/brain/last_thought", {
            "raw_text": raw_text[:200],
            "action": action,
            "t": time.time(),
        })

    def set_driver(self, driver_type: str):
        """Set /proc/hw/driver to the active driver name."""
        self.ns.write("/proc/hw/driver", driver_type)

    def set_camera(self, status: str):
        """Set /proc/hw/camera (``online`` or ``offline``)."""
        self.ns.write("/proc/hw/camera", status)

    def set_speaker(self, status: str):
        """Set /proc/hw/speaker (``online`` or ``offline``)."""
        self.ns.write("/proc/hw/speaker", status)

    def set_ruri(self, ruri: str):
        """Set /proc/ruri to the robot's RCAN URI."""
        self.ns.write("/proc/ruri", ruri)

    def set_capabilities(self, capabilities: list):
        """Set /proc/rcan/capabilities."""
        self.ns.write("/proc/rcan/capabilities", capabilities)

    def set_messages_routed(self, count: int):
        """Set /proc/rcan/messages_routed."""
        self.ns.write("/proc/rcan/messages_routed", count)

    # ------------------------------------------------------------------
    # Read helpers (convenience)
    # ------------------------------------------------------------------
    def snapshot(self) -> Dict:
        """Return a complete snapshot of /proc as a nested dict."""
        return {
            "uptime": self.ns.read("/proc/uptime"),
            "status": self.ns.read("/proc/status"),
            "version": self.ns.read("/proc/version"),
            "ruri": self.ns.read("/proc/ruri"),
            "rcan": {
                "version": self.ns.read("/proc/rcan/version"),
                "capabilities": self.ns.read("/proc/rcan/capabilities"),
                "messages_routed": self.ns.read("/proc/rcan/messages_routed"),
            },
            "loop": {
                "iteration": self.ns.read("/proc/loop/iteration"),
                "latency_ms": self.ns.read("/proc/loop/latency_ms"),
                "budget_ms": self.ns.read("/proc/loop/budget_ms"),
            },
            "brain": {
                "provider": self.ns.read("/proc/brain/provider"),
                "model": self.ns.read("/proc/brain/model"),
                "thoughts": self.ns.read("/proc/brain/thoughts"),
                "last_thought": self.ns.read("/proc/brain/last_thought"),
            },
            "hw": {
                "driver": self.ns.read("/proc/hw/driver"),
                "camera": self.ns.read("/proc/hw/camera"),
                "speaker": self.ns.read("/proc/hw/speaker"),
            },
        }

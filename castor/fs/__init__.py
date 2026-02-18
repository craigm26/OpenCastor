"""
OpenCastor Virtual Filesystem -- ``castor.fs``

A Unix-inspired virtual filesystem that provides:

- **Hierarchical namespace** -- Everything is a path.
- **Permission model** -- rwx per principal (brain, channel, api, driver).
- **Capabilities** -- Fine-grained safety gates (CAP_MOTOR_WRITE, CAP_ESTOP, ...).
- **Safety enforcement** -- Rate limiting, value clamping, audit logging, lockout.
- **Memory** -- Episodic, semantic, and procedural memory stores.
- **Context window** -- Sliding context for multi-turn reasoning.
- **Compound pipelines** -- Unix-pipe-style operation chaining.
- **Proc introspection** -- Real-time runtime telemetry at ``/proc``.

Quick start::

    from castor.fs import CastorFS

    fs = CastorFS()
    fs.boot(config)

    # Read/write with permission enforcement
    fs.write("/var/memory/semantic/facts", {"door": "locked"}, principal="brain")
    fs.read("/proc/uptime", principal="api")

    # Memory operations
    fs.memory.record_episode("saw obstacle", action={"type": "stop"})
    fs.memory.learn_fact("hallway.blocked", True)

    # Context window
    fs.context.push("user", "turn left")
    prompt_ctx = fs.context.build_prompt_context()

    # Compound pipeline
    from castor.fs.context import Pipeline
    result = (Pipeline("observe", fs.ns)
              .read("/dev/camera")
              .transform(process_frame)
              .write("/proc/brain/last_thought")
              .run())

    # Emergency stop
    fs.estop(principal="api")

Filesystem layout::

    /proc/          Runtime introspection (read-only)
    /dev/           Device nodes (motor, camera, speaker)
    /etc/           Configuration & safety policies
    /var/log/       Audit logs (actions, safety events, access)
    /var/memory/    Persistent memory (episodic, semantic, procedural)
    /tmp/           Working memory (context window, scratch)
    /mnt/           Mounted subsystems (channels, providers)
"""

import logging
from typing import Any, Dict, List, Optional

from castor.fs.context import ContextWindow, Pipeline, PipelineStage
from castor.fs.memory import MemoryStore
from castor.fs.namespace import Namespace
from castor.fs.permissions import ACL, Cap, PermissionTable
from castor.fs.proc import ProcFS
from castor.fs.safety import SafetyLayer

logger = logging.getLogger("OpenCastor.FS")

__all__ = [
    "CastorFS",
    "Namespace",
    "PermissionTable",
    "ACL",
    "Cap",
    "SafetyLayer",
    "MemoryStore",
    "ContextWindow",
    "Pipeline",
    "PipelineStage",
    "ProcFS",
]


class CastorFS:
    """Unified facade for the OpenCastor virtual filesystem.

    Wires together the namespace, permission table, safety layer,
    memory store, context window, and proc introspection into a
    single coherent API.

    Usage::

        fs = CastorFS(persist_dir="/var/lib/opencastor/memory")
        fs.boot(config)

        # Permission-enforced read/write
        fs.write("/dev/motor", {"type": "move", "linear": 0.5},
                 principal="brain")

        # Memory
        fs.memory.learn_fact("room.temperature", 22.5)

        # Context
        fs.context.push("user", "go forward")

        # Proc
        fs.proc.record_loop_iteration(latency_ms=150.0)

    Args:
        persist_dir:  Optional directory for memory persistence.
        limits:       Optional dict overriding safety limits.
    """

    def __init__(self, persist_dir: Optional[str] = None, limits: Optional[Dict] = None):
        # Core layers
        self.ns = Namespace()
        self.perms = PermissionTable()
        self.safety = SafetyLayer(self.ns, self.perms, limits=limits)

        # Subsystems
        self.memory = MemoryStore(self.ns, persist_dir=persist_dir)
        self.context = ContextWindow(self.ns)
        self.proc = ProcFS(self.ns)

        # Bootstrap the standard directory tree
        self._bootstrap_tree()

    def _bootstrap_tree(self):
        """Create the standard directory hierarchy."""
        for d in ("/dev", "/etc", "/mnt", "/mnt/channels", "/mnt/providers", "/tmp/scratch"):
            self.ns.mkdir(d)
        # Device nodes are files, not directories, so data can be written to them
        self.ns.write("/dev/motor", None)
        self.ns.write("/dev/camera", None)
        self.ns.write("/dev/speaker", None)

    def boot(self, config: Optional[Dict] = None):
        """Initialise the filesystem with a loaded RCAN config.

        Call this once after constructing CastorFS and loading the
        robot configuration.
        """
        # Proc introspection
        self.proc.bootstrap(config)
        self.proc.update_status("active")

        # Store active config
        if config:
            self.ns.write("/etc/rcan", config)

            # Install safety limits from config
            agent = config.get("agent", {})
            if agent.get("safety_stop"):
                self.ns.write("/etc/safety/safety_stop", True)

        # Register default RCAN principals
        self._register_rcan_principals()

        logger.info("CastorFS booted")

    def _register_rcan_principals(self):
        """Register legacy principals with RCAN roles and scopes."""
        try:
            from castor.rcan.rbac import RCANRole, Scope

            defaults = {
                "root": (RCANRole.CREATOR, Scope.for_role(RCANRole.CREATOR)),
                "brain": (RCANRole.OWNER, Scope.for_role(RCANRole.OWNER)),
                "api": (RCANRole.LEASEE, Scope.for_role(RCANRole.LEASEE)),
                "channel": (RCANRole.USER, Scope.for_role(RCANRole.USER)),
                "driver": (RCANRole.GUEST, Scope.for_role(RCANRole.GUEST)),
            }
            for name, (role, scopes) in defaults.items():
                self.perms.register_principal(name, role=int(role), scopes=scopes)
        except Exception:
            pass  # RCAN module not available -- legacy caps remain

    # ------------------------------------------------------------------
    # Delegated read/write (through safety layer)
    # ------------------------------------------------------------------
    def read(self, path: str, principal: str = "root") -> Any:
        """Read with permission enforcement."""
        return self.safety.read(path, principal=principal)

    def write(self, path: str, data: Any, principal: str = "root") -> bool:
        """Write with permission enforcement, clamping, and auditing."""
        return self.safety.write(path, data, principal=principal)

    def append(self, path: str, entry: Any, principal: str = "root") -> bool:
        """Append with permission enforcement."""
        return self.safety.append(path, entry, principal=principal)

    def ls(self, path: str = "/", principal: str = "root") -> Optional[List[str]]:
        """List directory contents with permission enforcement."""
        return self.safety.ls(path, principal=principal)

    def stat(self, path: str, principal: str = "root") -> Optional[Dict]:
        """Stat a node with permission enforcement."""
        return self.safety.stat(path, principal=principal)

    def mkdir(self, path: str, principal: str = "root") -> bool:
        """Create a directory with permission enforcement."""
        return self.safety.mkdir(path, principal=principal)

    def exists(self, path: str) -> bool:
        """Check existence (no permission check)."""
        return self.safety.exists(path)

    # ------------------------------------------------------------------
    # Safety operations
    # ------------------------------------------------------------------
    def estop(self, principal: str = "root") -> bool:
        """Trigger emergency stop."""
        return self.safety.estop(principal=principal)

    def clear_estop(self, principal: str = "root") -> bool:
        """Clear emergency stop (requires root or CAP_SAFETY_OVERRIDE)."""
        return self.safety.clear_estop(principal=principal)

    @property
    def is_estopped(self) -> bool:
        return self.safety.is_estopped

    # ------------------------------------------------------------------
    # Pipeline builder
    # ------------------------------------------------------------------
    def pipeline(self, name: str, principal: str = "brain") -> Pipeline:
        """Create a new compound pipeline."""
        return Pipeline(name, self.ns, principal=principal, safety=self.safety)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def shutdown(self):
        """Flush memory and update proc status."""
        self.proc.update_status("shutdown")
        self.memory.flush_to_disk()
        logger.info("CastorFS shut down")

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------
    def tree(self, path: str = "/", depth: int = 3) -> str:
        """Return a tree-style string representation of the filesystem."""
        if depth < 0:
            depth = 0
        lines = []
        self._tree_recursive(path, "", depth, lines)
        return "\n".join(lines)

    def _tree_recursive(self, path: str, prefix: str, depth: int, lines: List[str]):
        if depth < 0:
            return

        node_name = path.split("/")[-1] or "/"
        stat_info = self.ns.stat(path)
        if stat_info is None:
            return

        is_dir = stat_info.get("type") == "dir"

        if is_dir:
            lines.append(f"{prefix}{node_name}/")
            children = self.ns.ls(path)
            if not children:
                return
            children = sorted(children)
            for i, child in enumerate(children):
                is_last = i == len(children) - 1
                child_prefix = prefix + ("    " if is_last else "|   ")
                connector = "`-- " if is_last else "|-- "
                child_path = f"{path.rstrip('/')}/{child}"
                child_stat = self.ns.stat(child_path)
                if child_stat is None:
                    continue
                child_is_dir = child_stat.get("type") == "dir"
                if child_is_dir:
                    lines.append(f"{prefix}{connector}{child}/")
                    self._tree_recursive(child_path, child_prefix, depth - 1, lines)
                else:
                    data = self.ns.read(child_path)
                    data_preview = repr(data)[:40]
                    lines.append(f"{prefix}{connector}{child} = {data_preview}")
        else:
            data = self.ns.read(path)
            data_preview = repr(data)[:40]
            lines.append(f"{prefix}{node_name} = {data_preview}")

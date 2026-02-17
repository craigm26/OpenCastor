"""
OpenCastor Virtual Filesystem -- Namespace.

A hierarchical, in-memory filesystem tree modeled after the Unix VFS.
Every piece of robot state -- hardware, memory, config, telemetry --
is addressed as a path (e.g. ``/dev/motor``, ``/var/memory/episodic``).

Nodes are lightweight dicts that can hold arbitrary data, metadata,
and permission bits.  The tree supports ``read``, ``write``, ``list``,
``stat``, ``mkdir``, and ``unlink`` operations, all gated by the
permission layer.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OpenCastor.FS")


class FSNode:
    """A single node in the virtual filesystem tree.

    Attributes:
        name:       Basename of this node (e.g. ``motor``).
        node_type:  ``"dir"`` or ``"file"``.
        data:       Arbitrary payload for file nodes.
        meta:       Free-form metadata dict (e.g. owner, mode).
        children:   Ordered dict of child nodes (dirs only).
        ctime:      Creation timestamp (epoch seconds).
        mtime:      Last modification timestamp.
    """

    __slots__ = ("name", "node_type", "data", "meta", "children", "ctime", "mtime")

    def __init__(self, name: str, node_type: str = "file", data: Any = None,
                 meta: Optional[Dict] = None):
        self.name = name
        self.node_type = node_type
        self.data = data
        self.meta = meta or {}
        self.children: Dict[str, "FSNode"] = {} if node_type == "dir" else {}
        now = time.time()
        self.ctime = now
        self.mtime = now

    @property
    def is_dir(self) -> bool:
        return self.node_type == "dir"

    def stat(self) -> Dict[str, Any]:
        """Return a stat-like dict for this node."""
        return {
            "name": self.name,
            "type": self.node_type,
            "ctime": self.ctime,
            "mtime": self.mtime,
            "size": len(str(self.data)) if self.data is not None else 0,
            "meta": dict(self.meta),
        }


class Namespace:
    """Thread-safe hierarchical virtual filesystem.

    The namespace is the core data structure -- a rooted tree of
    :class:`FSNode` objects.  All path operations normalise to
    absolute POSIX paths (``/proc/loop/latency``).

    Usage::

        ns = Namespace()
        ns.mkdir("/proc/loop")
        ns.write("/proc/loop/latency", 42.5)
        ns.read("/proc/loop/latency")  # -> 42.5
        ns.ls("/proc/loop")            # -> ["latency"]
    """

    def __init__(self):
        self._root = FSNode("/", node_type="dir")
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _split(path: str) -> List[str]:
        """Normalise and split an absolute path into components."""
        path = path.strip()
        if not path.startswith("/"):
            path = "/" + path
        parts = [p for p in path.split("/") if p]
        return parts

    def _walk(self, parts: List[str], create_parents: bool = False) -> Optional[FSNode]:
        """Walk the tree from root, optionally creating intermediate dirs."""
        node = self._root
        for part in parts:
            if part not in node.children:
                if create_parents:
                    node.children[part] = FSNode(part, node_type="dir")
                else:
                    return None
            node = node.children[part]
        return node

    def _parent_and_name(self, path: str):
        """Return (parent_node, basename) for a path, or (None, None)."""
        parts = self._split(path)
        if not parts:
            return None, None
        parent = self._walk(parts[:-1])
        return parent, parts[-1]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def mkdir(self, path: str, meta: Optional[Dict] = None) -> bool:
        """Create a directory (and parents) at *path*.  Returns True on success."""
        with self._lock:
            parts = self._split(path)
            node = self._root
            for part in parts:
                if part not in node.children:
                    node.children[part] = FSNode(part, node_type="dir", meta=meta)
                elif not node.children[part].is_dir:
                    logger.error("mkdir: %s exists as file", path)
                    return False
                node = node.children[part]
            if meta:
                node.meta.update(meta)
            return True

    def write(self, path: str, data: Any, meta: Optional[Dict] = None) -> bool:
        """Write *data* to a file node at *path*, creating parents as needed."""
        with self._lock:
            parts = self._split(path)
            if not parts:
                return False
            parent = self._walk(parts[:-1], create_parents=True)
            name = parts[-1]
            if name in parent.children and parent.children[name].is_dir:
                logger.error("write: %s is a directory", path)
                return False
            if name in parent.children:
                node = parent.children[name]
                node.data = data
                node.mtime = time.time()
                if meta:
                    node.meta.update(meta)
            else:
                parent.children[name] = FSNode(name, data=data, meta=meta)
            return True

    def read(self, path: str) -> Any:
        """Read the data payload of a file node.  Returns ``None`` if missing."""
        with self._lock:
            node = self._walk(self._split(path))
            if node is None:
                return None
            if node.is_dir:
                return {name: child.stat() for name, child in node.children.items()}
            return node.data

    def append(self, path: str, entry: Any) -> bool:
        """Append *entry* to a file whose data is a list.  Creates if absent."""
        with self._lock:
            parts = self._split(path)
            if not parts:
                return False
            parent = self._walk(parts[:-1], create_parents=True)
            name = parts[-1]
            if name not in parent.children:
                parent.children[name] = FSNode(name, data=[])
            node = parent.children[name]
            if not isinstance(node.data, list):
                node.data = [node.data]
            node.data.append(entry)
            node.mtime = time.time()
            return True

    def ls(self, path: str = "/") -> Optional[List[str]]:
        """List children of a directory node."""
        with self._lock:
            node = self._walk(self._split(path)) if path != "/" else self._root
            if node is None or not node.is_dir:
                return None
            return sorted(node.children.keys())

    def stat(self, path: str) -> Optional[Dict]:
        """Return stat info for a node, or ``None`` if not found."""
        with self._lock:
            node = self._walk(self._split(path)) if path != "/" else self._root
            if node is None:
                return None
            return node.stat()

    def exists(self, path: str) -> bool:
        """Check whether a node exists at *path*."""
        with self._lock:
            if path == "/":
                return True
            return self._walk(self._split(path)) is not None

    def unlink(self, path: str) -> bool:
        """Remove a node (file or empty dir).  Returns True on success."""
        with self._lock:
            parent, name = self._parent_and_name(path)
            if parent is None or name not in parent.children:
                return False
            target = parent.children[name]
            if target.is_dir and target.children:
                logger.error("unlink: %s is a non-empty directory", path)
                return False
            del parent.children[name]
            return True

    def walk(self, path: str = "/") -> List[str]:
        """Recursively list all paths under *path*."""
        results = []
        with self._lock:
            node = self._walk(self._split(path)) if path != "/" else self._root
            if node is None:
                return results
            self._walk_recursive(path.rstrip("/") or "/", node, results)
        return results

    def _walk_recursive(self, prefix: str, node: FSNode, results: List[str]):
        for name, child in node.children.items():
            child_path = f"{prefix}/{name}" if prefix != "/" else f"/{name}"
            results.append(child_path)
            if child.is_dir:
                self._walk_recursive(child_path, child, results)

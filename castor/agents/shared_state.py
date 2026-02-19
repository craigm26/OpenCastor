"""Thread-safe pub/sub state bus for inter-agent communication.

Agents use SharedState to share structured data (SceneGraph, NavigationPlan,
telemetry, etc.) without direct coupling. All operations are guarded by an
RLock so the store is safe for concurrent reads and writes across threads.
"""

import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("OpenCastor.SharedState")


class _Entry:
    """Internal container for a stored value with an optional TTL."""

    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl_s: Optional[float] = None) -> None:
        self.value = value
        self.expires_at: Optional[float] = (time.monotonic() + ttl_s) if ttl_s is not None else None

    def is_expired(self) -> bool:
        """Return True if this entry has passed its expiry time."""
        if self.expires_at is None:
            return False
        return time.monotonic() > self.expires_at


class SharedState:
    """Thread-safe key-value store with pub/sub callbacks and optional TTL.

    All methods are safe to call from multiple threads simultaneously.

    Example::

        state = SharedState()

        # Simple get/set
        state.set("speed", 0.5)
        speed = state.get("speed")

        # Subscribe to changes
        sub_id = state.subscribe("scene_graph", lambda key, val: print(val))
        state.set("scene_graph", my_scene)   # triggers callback immediately
        state.unsubscribe(sub_id)

        # TTL-based expiry (useful for sensor heartbeats)
        state.set("lidar_ping", True, ttl_s=1.0)
    """

    def __init__(self) -> None:
        self._lock: threading.RLock = threading.RLock()
        self._store: Dict[str, _Entry] = {}
        # key → {sub_id → callback}
        self._subscribers: Dict[str, Dict[str, Callable]] = {}

    # ------------------------------------------------------------------
    # Core store operations
    # ------------------------------------------------------------------

    def set(self, key: str, value: Any, ttl_s: Optional[float] = None) -> None:
        """Store *value* under *key*, optionally expiring after *ttl_s* seconds.

        Subscribers registered for *key* are notified synchronously
        (outside the lock to prevent deadlocks).

        Args:
            key: State key.
            value: Any picklable value.
            ttl_s: Optional time-to-live in seconds.  After this duration,
                ``get`` returns the default and the key is removed.
        """
        with self._lock:
            self._store[key] = _Entry(value, ttl_s)
            callbacks = list(self._subscribers.get(key, {}).values())

        for cb in callbacks:
            try:
                cb(key, value)
            except Exception as exc:
                logger.warning(f"Subscriber callback error for key '{key}': {exc}")

    def get(self, key: str, default: Any = None) -> Any:
        """Return the stored value for *key*, or *default* if missing or expired.

        Expired entries are removed lazily on access.

        Args:
            key: State key to look up.
            default: Fallback value when key is absent or expired.

        Returns:
            Stored value, or *default*.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return default
            if entry.is_expired():
                del self._store[key]
                return default
            return entry.value

    # ------------------------------------------------------------------
    # Pub/sub
    # ------------------------------------------------------------------

    def subscribe(self, key: str, callback: Callable) -> str:
        """Register *callback* to be called whenever *key* is updated via :meth:`set`.

        Args:
            key: State key to watch.
            callback: Callable with signature ``(key: str, value: Any) -> None``.

        Returns:
            Subscription ID string — pass to :meth:`unsubscribe` to remove.
        """
        sub_id = str(uuid.uuid4())
        with self._lock:
            self._subscribers.setdefault(key, {})[sub_id] = callback
        return sub_id

    def unsubscribe(self, sub_id: str) -> None:
        """Remove a subscription by its ID.

        Safe to call with an unknown ID (no-op).

        Args:
            sub_id: ID returned by :meth:`subscribe`.
        """
        with self._lock:
            for key_subs in self._subscribers.values():
                if sub_id in key_subs:
                    del key_subs[sub_id]
                    return

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def keys(self) -> List[str]:
        """Return all non-expired keys currently in the store."""
        with self._lock:
            expired = [k for k, e in self._store.items() if e.is_expired()]
            for k in expired:
                del self._store[k]
            return list(self._store.keys())

    def snapshot(self) -> Dict[str, Any]:
        """Return a deep copy of all current non-expired key/value pairs.

        Expired entries are pruned during the snapshot.
        Mutating the returned dict or its values does not affect the store.
        """
        import copy

        with self._lock:
            result: Dict[str, Any] = {}
            expired = []
            for k, entry in self._store.items():
                if entry.is_expired():
                    expired.append(k)
                else:
                    result[k] = copy.deepcopy(entry.value)
            for k in expired:
                del self._store[k]
            return result

"""Thread-safe pub/sub state bus for inter-agent communication.

Agents use SharedState to share structured data (SceneGraph, NavigationPlan,
telemetry, etc.) without direct coupling. All operations are guarded by an
RLock so the store is safe for concurrent reads and writes across threads.
"""

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.SharedState")


@dataclass
class Intent:
    """First-class orchestration intent.

    Attributes:
        goal: Human/task objective description.
        priority: Higher value means higher urgency.
        deadline_ts: Optional unix timestamp deadline/SLA target.
        safety_class: Safety class (normal, elevated, emergency, etc.).
        owner: Issuer/owner of the intent.
        scope: RCAN scope of the originating command.  Sub-agents must not
            exceed this scope.  Defaults to ``"chat"`` (conservative).
    """

    goal: str
    priority: int = 0
    deadline_ts: Optional[float] = None
    safety_class: str = "normal"
    owner: str = "system"
    scope: str = "chat"  # RCAN scope of originating command; agents must not exceed this
    intent_id: str = field(default_factory=lambda: f"intent-{uuid.uuid4().hex[:12]}")
    state: str = "queued"
    created_at: float = field(default_factory=time.time)
    paused: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at_iso"] = datetime.fromtimestamp(self.created_at).isoformat()
        if self.deadline_ts is not None:
            payload["deadline_iso"] = datetime.fromtimestamp(self.deadline_ts).isoformat()
        payload.setdefault("scope", self.scope)
        return payload


class IntentQueue:
    """Thread-safe intent queue with preemption support."""

    def __init__(self) -> None:
        self._lock: threading.RLock = threading.RLock()
        self._queue: list[Intent] = []
        self._current_id: Optional[str] = None

    @staticmethod
    def _preempts(a: Intent, b: Optional[Intent]) -> bool:
        if b is None:
            return True
        if a.safety_class == "emergency" and b.safety_class != "emergency":
            return True
        if a.priority > b.priority:
            return True
        if (
            a.deadline_ts is not None
            and b.deadline_ts is not None
            and a.deadline_ts < b.deadline_ts
            and a.priority >= b.priority
        ):
            return True
        return False

    def enqueue(self, intent: Intent) -> dict[str, Any]:
        with self._lock:
            self._queue = [i for i in self._queue if i.intent_id != intent.intent_id]
            self._queue.append(intent)
            active = self.current()
            preempted = None
            if self._preempts(intent, active):
                if active is not None and active.intent_id != intent.intent_id:
                    active.state = "queued"
                    preempted = active.intent_id
                intent.state = "active"
                self._current_id = intent.intent_id
            return {"accepted": True, "preempted": preempted, "current": self._current_id}

    def current(self) -> Optional[Intent]:
        with self._lock:
            if self._current_id is None:
                return None
            for i in self._queue:
                if i.intent_id == self._current_id:
                    return i
            self._current_id = None
            return None

    def list_intents(self) -> list[dict[str, Any]]:
        with self._lock:
            ranked = sorted(
                self._queue,
                key=lambda i: (
                    i.state != "active",
                    i.paused,
                    -i.priority,
                    i.deadline_ts if i.deadline_ts is not None else float("inf"),
                    i.created_at,
                ),
            )
            return [i.to_dict() for i in ranked]

    def pause(self, intent_id: str, paused: bool = True) -> bool:
        with self._lock:
            for intent in self._queue:
                if intent.intent_id == intent_id:
                    intent.paused = paused
                    intent.state = "paused" if paused else "queued"
                    if paused and self._current_id == intent_id:
                        self._current_id = None
                    if not paused and self._current_id is None:
                        self._current_id = intent_id
                        intent.state = "active"
                    return True
            return False

    def reprioritize(self, intent_id: str, priority: int) -> bool:
        with self._lock:
            for intent in self._queue:
                if intent.intent_id == intent_id:
                    intent.priority = int(priority)
                    active = self.current()
                    if self._preempts(intent, active):
                        if active is not None and active.intent_id != intent.intent_id:
                            active.state = "queued"
                        intent.state = "active"
                        self._current_id = intent_id
                    return True
            return False


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
        self._store: dict[str, _Entry] = {}
        # key → {sub_id → callback}
        self._subscribers: dict[str, dict[str, Callable]] = {}
        self._intents = IntentQueue()

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

    def keys(self) -> list[str]:
        """Return all non-expired keys currently in the store."""
        with self._lock:
            expired = [k for k, e in self._store.items() if e.is_expired()]
            for k in expired:
                del self._store[k]
            return list(self._store.keys())

    def snapshot(self) -> dict[str, Any]:
        """Return a deep copy of all current non-expired key/value pairs.

        Expired entries are pruned during the snapshot.
        Mutating the returned dict or its values does not affect the store.
        """
        import copy

        with self._lock:
            result: dict[str, Any] = {}
            expired = []
            for k, entry in self._store.items():
                if entry.is_expired():
                    expired.append(k)
                else:
                    result[k] = copy.deepcopy(entry.value)
            for k in expired:
                del self._store[k]
            return result

    # ------------------------------------------------------------------
    # Intent orchestration
    # ------------------------------------------------------------------

    def add_intent(self, intent: Intent) -> dict[str, Any]:
        """Add an intent to the queue and evaluate preemption."""
        result = self._intents.enqueue(intent)
        self.set("swarm.current_intent_id", result.get("current"))
        self.set("swarm.intent_queue", self._intents.list_intents())
        return result

    def list_intents(self) -> list[dict[str, Any]]:
        """Return all intents in queue order with active intent first."""
        return self._intents.list_intents()

    def pause_intent(self, intent_id: str, paused: bool = True) -> bool:
        """Pause or resume an intent by ID."""
        ok = self._intents.pause(intent_id, paused=paused)
        if ok:
            current = self._intents.current()
            self.set("swarm.current_intent_id", current.intent_id if current else None)
            self.set("swarm.intent_queue", self._intents.list_intents())
        return ok

    def reprioritize_intent(self, intent_id: str, priority: int) -> bool:
        """Update an intent's priority and re-run preemption selection."""
        ok = self._intents.reprioritize(intent_id, priority)
        if ok:
            current = self._intents.current()
            self.set("swarm.current_intent_id", current.intent_id if current else None)
            self.set("swarm.intent_queue", self._intents.list_intents())
        return ok

    def current_intent(self) -> Optional[dict[str, Any]]:
        """Return the current active intent, if present."""
        current = self._intents.current()
        return current.to_dict() if current else None

    def set_specialist_checkpoint(self, specialist: str, checkpoint: dict[str, Any]) -> None:
        """Persist a resume checkpoint for a long-running specialist."""
        key = f"swarm.checkpoint.{specialist}"
        payload = {"specialist": specialist, "updated_at": time.time(), **checkpoint}
        self.set(key, payload)

    def get_specialist_checkpoint(self, specialist: str) -> Optional[dict[str, Any]]:
        """Get the last checkpoint for the specialist."""
        return self.get(f"swarm.checkpoint.{specialist}")

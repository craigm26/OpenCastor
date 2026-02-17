"""
OpenCastor Virtual Filesystem -- Memory.

Three-tier persistent memory modeled after human cognitive architecture,
stored as filesystem paths under ``/var/memory``:

Episodic Memory (``/var/memory/episodic/``)
    Time-indexed records of observations, actions, and outcomes.
    Like a robot's diary -- what happened, when, and what resulted.

Semantic Memory (``/var/memory/semantic/``)
    Key-value knowledge store for persistent facts.  "The door on the
    left is always locked."  Survives across sessions.

Procedural Memory (``/var/memory/procedural/``)
    Named action sequences (compound behaviors).  "patrol_route" might
    be a sequence of moves.  Enables learning from repetition.

Working Memory (``/tmp/context/``)
    See :mod:`castor.fs.context` for the sliding context window.

MemoryStore operates directly on the underlying :class:`~castor.fs.namespace.Namespace`
for performance and because the safety layer calls into memory for context
building. Permission gating and auditing are enforced by
:class:`~castor.fs.safety.SafetyLayer` when external code interacts with
the virtual filesystem.
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from castor.fs.namespace import Namespace

logger = logging.getLogger("OpenCastor.FS.Memory")

# Max entries before automatic eviction (FIFO)
DEFAULT_EPISODIC_LIMIT = 500
DEFAULT_SEMANTIC_LIMIT = 200
DEFAULT_PROCEDURAL_LIMIT = 50


class MemoryStore:
    """Manages the three memory tiers inside the virtual filesystem.

    This class operates directly on the :class:`Namespace` (bypassing
    the safety layer) because the safety layer calls *into* memory for
    context building.  Permission enforcement happens at the
    :class:`~castor.fs.safety.SafetyLayer` level above.

    Args:
        ns:            The underlying namespace.
        persist_dir:   Optional real filesystem path for persistence.
                       If set, memory is periodically flushed to disk.
    """

    def __init__(self, ns: Namespace, persist_dir: Optional[str] = None):
        self.ns = ns
        self.persist_dir = Path(persist_dir) if persist_dir else None
        self._lock = threading.Lock()
        self._limits = {
            "episodic": DEFAULT_EPISODIC_LIMIT,
            "semantic": DEFAULT_SEMANTIC_LIMIT,
            "procedural": DEFAULT_PROCEDURAL_LIMIT,
        }
        self._bootstrap()

    def _bootstrap(self):
        """Create the memory directory hierarchy and load persisted data."""
        self.ns.mkdir("/var/memory/episodic")
        self.ns.mkdir("/var/memory/semantic")
        self.ns.mkdir("/var/memory/procedural")
        self.ns.write("/var/memory/episodic/events", [])
        self.ns.write("/var/memory/semantic/facts", {})
        self.ns.write("/var/memory/procedural/behaviors", {})

        if self.persist_dir:
            self._load_from_disk()

    # ------------------------------------------------------------------
    # Episodic memory
    # ------------------------------------------------------------------
    def record_episode(self, observation: str, action: Optional[Dict] = None,
                       outcome: Optional[str] = None,
                       tags: Optional[List[str]] = None) -> Dict:
        """Record a timestamped episode.

        Returns the episode dict that was stored.
        """
        episode = {
            "t": time.time(),
            "observation": observation,
            "action": action,
            "outcome": outcome,
            "tags": tags or [],
        }
        with self._lock:
            self.ns.append("/var/memory/episodic/events", episode)
            self._enforce_limit("episodic")
        return episode

    def get_episodes(self, limit: int = 20, tag: Optional[str] = None) -> List[Dict]:
        """Retrieve recent episodes, optionally filtered by tag."""
        events = self.ns.read("/var/memory/episodic/events") or []
        if tag:
            events = [e for e in events if tag in e.get("tags", [])]
        return events[-limit:]

    def get_episode_count(self) -> int:
        events = self.ns.read("/var/memory/episodic/events") or []
        return len(events)

    # ------------------------------------------------------------------
    # Semantic memory
    # ------------------------------------------------------------------
    def learn_fact(self, key: str, value: Any, source: str = "observation"):
        """Store or update a persistent fact.

        Args:
            key:    Unique identifier (e.g. ``"left_door.status"``).
            value:  The fact value.
            source: Where this fact came from.
        """
        with self._lock:
            facts = self.ns.read("/var/memory/semantic/facts") or {}
            facts[key] = {
                "value": value,
                "source": source,
                "updated": time.time(),
            }
            self.ns.write("/var/memory/semantic/facts", facts)
            self._enforce_limit("semantic")

    def recall_fact(self, key: str) -> Optional[Any]:
        """Retrieve a stored fact's value, or None."""
        facts = self.ns.read("/var/memory/semantic/facts") or {}
        entry = facts.get(key)
        return entry["value"] if entry else None

    def list_facts(self) -> Dict[str, Any]:
        """Return all stored facts as ``{key: value}``."""
        facts = self.ns.read("/var/memory/semantic/facts") or {}
        return {k: v["value"] for k, v in facts.items()}

    def forget_fact(self, key: str) -> bool:
        """Remove a fact from semantic memory."""
        with self._lock:
            facts = self.ns.read("/var/memory/semantic/facts") or {}
            if key in facts:
                del facts[key]
                self.ns.write("/var/memory/semantic/facts", facts)
                return True
            return False

    # ------------------------------------------------------------------
    # Procedural memory
    # ------------------------------------------------------------------
    def store_behavior(self, name: str, steps: List[Dict],
                       description: str = ""):
        """Store a named action sequence (compound behavior).

        Args:
            name:        Unique name (e.g. ``"patrol_hallway"``).
            steps:       List of action dicts to execute in order.
            description: Human-readable description.
        """
        with self._lock:
            behaviors = self.ns.read("/var/memory/procedural/behaviors") or {}
            behaviors[name] = {
                "steps": steps,
                "description": description,
                "created": time.time(),
                "executions": 0,
            }
            self.ns.write("/var/memory/procedural/behaviors", behaviors)
            self._enforce_limit("procedural")

    def get_behavior(self, name: str) -> Optional[Dict]:
        """Retrieve a stored behavior by name."""
        behaviors = self.ns.read("/var/memory/procedural/behaviors") or {}
        return behaviors.get(name)

    def list_behaviors(self) -> List[str]:
        """List all stored behavior names."""
        behaviors = self.ns.read("/var/memory/procedural/behaviors") or {}
        return list(behaviors.keys())

    def record_execution(self, name: str):
        """Increment the execution counter for a behavior."""
        with self._lock:
            behaviors = self.ns.read("/var/memory/procedural/behaviors") or {}
            if name in behaviors:
                behaviors[name]["executions"] += 1
                behaviors[name]["last_executed"] = time.time()
                self.ns.write("/var/memory/procedural/behaviors", behaviors)

    def remove_behavior(self, name: str) -> bool:
        """Remove a behavior from procedural memory."""
        with self._lock:
            behaviors = self.ns.read("/var/memory/procedural/behaviors") or {}
            if name in behaviors:
                del behaviors[name]
                self.ns.write("/var/memory/procedural/behaviors", behaviors)
                return True
            return False

    # ------------------------------------------------------------------
    # Limits & eviction
    # ------------------------------------------------------------------
    def _enforce_limit(self, tier: str):
        """FIFO eviction when a tier exceeds its limit."""
        limit = self._limits.get(tier, 500)
        if tier == "episodic":
            events = self.ns.read("/var/memory/episodic/events") or []
            if len(events) > limit:
                self.ns.write("/var/memory/episodic/events", events[-limit:])
        elif tier == "semantic":
            facts = self.ns.read("/var/memory/semantic/facts") or {}
            if len(facts) > limit:
                # Evict oldest entries
                sorted_keys = sorted(facts.keys(),
                                     key=lambda k: facts[k].get("updated", 0))
                for key in sorted_keys[:len(facts) - limit]:
                    del facts[key]
                self.ns.write("/var/memory/semantic/facts", facts)
        elif tier == "procedural":
            behaviors = self.ns.read("/var/memory/procedural/behaviors") or {}
            if len(behaviors) > limit:
                # Evict least-executed
                sorted_names = sorted(behaviors.keys(),
                                      key=lambda n: behaviors[n].get("executions", 0))
                for name in sorted_names[:len(behaviors) - limit]:
                    del behaviors[name]
                self.ns.write("/var/memory/procedural/behaviors", behaviors)

    # ------------------------------------------------------------------
    # Persistence (flush to / load from disk)
    # ------------------------------------------------------------------
    def flush_to_disk(self):
        """Persist all memory tiers to the configured directory."""
        if not self.persist_dir:
            return
        try:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            for tier in ("episodic", "semantic", "procedural"):
                data_path = f"/var/memory/{tier}"
                children = self.ns.ls(data_path) or []
                tier_data = {}
                for child in children:
                    tier_data[child] = self.ns.read(f"{data_path}/{child}")
                out_path = self.persist_dir / f"{tier}.json"
                with open(out_path, "w") as f:
                    json.dump(tier_data, f, indent=2, default=str)
            logger.info("Memory flushed to %s", self.persist_dir)
        except Exception as exc:
            logger.warning(
                "Failed to flush memory to %s: %s",
                self.persist_dir,
                exc,
            )

    def _load_from_disk(self):
        """Load persisted memory from disk into the namespace."""
        if not self.persist_dir or not self.persist_dir.exists():
            return
        for tier in ("episodic", "semantic", "procedural"):
            in_path = self.persist_dir / f"{tier}.json"
            if in_path.exists():
                try:
                    with open(in_path) as f:
                        tier_data = json.load(f)
                    for key, value in tier_data.items():
                        self.ns.write(f"/var/memory/{tier}/{key}", value)
                    logger.info("Loaded %s memory from disk (%d entries)",
                                tier, len(tier_data))
                except Exception as exc:
                    logger.warning("Failed to load %s memory: %s", tier, exc)

    # ------------------------------------------------------------------
    # Context summary (for provider system prompts)
    # ------------------------------------------------------------------
    def build_context_summary(self, max_episodes: int = 5,
                              max_facts: int = 10) -> str:
        """Build a compact text summary of relevant memory for the LLM.

        This is injected into the system prompt so the brain has
        access to its own memory without a separate retrieval step.
        """
        parts = []

        # Recent episodes
        episodes = self.get_episodes(limit=max_episodes)
        if episodes:
            parts.append("## Recent Events")
            for ep in episodes:
                line = f"- [{ep.get('observation', '?')}]"
                if ep.get("action"):
                    line += f" -> {ep['action']}"
                if ep.get("outcome"):
                    line += f" => {ep['outcome']}"
                parts.append(line)

        # Known facts
        facts = self.list_facts()
        if facts:
            items = list(facts.items())[:max_facts]
            parts.append("## Known Facts")
            for k, v in items:
                parts.append(f"- {k}: {v}")

        # Available behaviors
        behaviors = self.list_behaviors()
        if behaviors:
            parts.append("## Available Behaviors")
            for name in behaviors:
                beh = self.get_behavior(name)
                desc = beh.get("description", "") if beh else ""
                parts.append(f"- {name}: {desc} ({beh.get('executions', 0)} runs)"
                             if beh else f"- {name}")

        return "\n".join(parts) if parts else ""

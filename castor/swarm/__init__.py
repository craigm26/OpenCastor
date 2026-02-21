"""castor.swarm — Multi-Robot Swarm Coordination (Phase 4).

Provides distributed consensus, task allocation, and event broadcasting
across a fleet of OpenCastor robots connected via RCAN.

Key classes:

- :class:`SwarmCoordinator` — Orchestrates task distribution to a pool of
  robots. Discovers peers via mDNS or a static list and routes tasks to
  the most capable/available unit.
- :class:`SwarmConsensus` — Implements a majority-vote consensus protocol
  so multiple robots can agree on a shared action before any one executes.
- :class:`SwarmEvent` — Typed event envelope for pub/sub between swarm
  members (e.g. ``peer_joined``, ``task_assigned``, ``estop_broadcast``).
- :class:`SwarmPeer` — Represents a remote robot in the swarm with its
  RURI, capabilities, and current status.
- :class:`SharedMemory` — Distributed key-value store synchronized across
  swarm peers via RCAN messages.
- :class:`PatchSync` — Incremental config/state patch synchronization to
  keep all swarm members consistent.

Configuration (RCAN ``swarm`` section)::

    swarm:
      enabled: true
      mode: coordinator          # coordinator | worker | peer
      peers:
        - rcan://robotics.scout-01.abc12345
      consensus_threshold: 0.66  # fraction of peers required for consensus

See also :mod:`castor.fleet` (passive discovery) and :mod:`castor.agents`
(per-robot multi-agent framework).
"""

from castor.swarm.consensus import SwarmConsensus
from castor.swarm.coordinator import SwarmCoordinator
from castor.swarm.events import SwarmEvent
from castor.swarm.patch_sync import PatchSync
from castor.swarm.peer import SwarmPeer
from castor.swarm.shared_memory import SharedMemory

__all__ = [
    "SwarmPeer",
    "SwarmCoordinator",
    "SharedMemory",
    "SwarmConsensus",
    "PatchSync",
    "SwarmEvent",
]

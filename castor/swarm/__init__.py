"""castor.swarm — Multi-Robot Swarm Coordination (Phase 4)."""

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

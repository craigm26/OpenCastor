# Swarm Deployment Guide

OpenCastor's swarm layer enables multiple robots to coordinate autonomously on
the same local network. Robots discover each other via mDNS, elect a coordinator,
share observations, and synchronise self-improvement patches.

Source: `castor/swarm/`, `castor/rcan/mdns.py`, `castor/fleet.py`

## Architecture

```
Robot A (Coordinator)          Robot B (Worker)         Robot C (Worker)
┌──────────────────┐           ┌──────────────────┐     ┌──────────────────┐
│  SwarmCoordinator │ ◄── mDNS ─► SwarmPeer        │     │ SwarmPeer        │
│  TaskAssignment   │           │  SharedMemory     │     │ SharedMemory     │
│  Consensus        │ ◄── mesh ─► PatchSync        │ ──► │ PatchSync        │
│  PatchSync        │           └──────────────────┘     └──────────────────┘
└──────────────────┘
         │
    mDNS broadcast
    _rcan._tcp.local.
```

**Key components**

| Module | Role |
|---|---|
| `swarm/coordinator.py` | Task assignment, load balancing |
| `swarm/peer.py` | `SwarmPeer` data model |
| `swarm/consensus.py` | Distributed consensus (coordinator election) |
| `swarm/shared_memory.py` | Cross-robot observation sharing |
| `swarm/patch_sync.py` | Synchronised Sisyphus patch rollout |
| `rcan/mdns.py` | mDNS service advertisement + discovery |

## Hardware Requirements

- 2+ Raspberry Pi robots (or any Linux host running `castor gateway`)
- Same WiFi network or wired LAN
- mDNS / Bonjour not blocked by router (most home/office routers allow it)
- Port 8000 (or configured `OPENCASTOR_API_PORT`) open between robots
- Optional: dedicated 2.4 GHz SSID for swarm traffic to reduce latency

## Quick Start (2 robots)

### 1. Configure both robots

Add to each robot's RCAN config:

```yaml
rcan_protocol:
  enable_mdns: true          # Required for peer discovery
  robot_uuid: "rover-alpha"  # Unique per robot
  capabilities:
    - navigation
    - vision

swarm:
  enabled: true
  role: auto                 # auto = coordinator if no other coordinator seen
  consensus_timeout_ms: 3000
```

### 2. Start each robot

```bash
# Robot 1
castor gateway --config config/presets/rpi_rc_car.rcan.yaml

# Robot 2 (same command, different config file or same)
castor gateway --config config/presets/rpi_rc_car.rcan.yaml
```

Both robots will:
1. Broadcast themselves on `_rcan._tcp.local.`
2. Discover each other within ~3 seconds
3. Run consensus to elect a coordinator
4. Begin sharing observations

### 3. Verify discovery

```bash
# From any machine on the same network with castor installed
castor fleet status

# Expected output:
#   Fleet: 2 Robot(s)
#   ┌──────────┬───────┬─────────────────────┬───────────────┬────────┬──────────────┐
#   │ Name     │ Model │ RURI                │ Address       │ Status │ Capabilities │
#   ├──────────┼───────┼─────────────────────┼───────────────┼────────┼──────────────┤
#   │ rover-α  │ rover │ rcan://...rover-... │ 192.168.1.10  │ active │ nav, vision  │
#   │ rover-β  │ rover │ rcan://...rover-... │ 192.168.1.11  │ active │ nav, vision  │
#   └──────────┴───────┴─────────────────────┴───────────────┴────────┴──────────────┘
#   Health check: 2/2 reachable via HTTP
```

### 4. Send a swarm command

Via channel (e.g. WhatsApp):

```
"all robots: patrol the office"
```

The coordinator decomposes the goal and assigns sub-tasks to workers based on
their capabilities and current load score.

## Configuration Reference

### RCAN config

```yaml
rcan_protocol:
  enable_mdns: true                    # Enable mDNS broadcasting + discovery
  robot_uuid: "my-robot-001"          # Must be unique per robot in the fleet
  capabilities: [navigation, vision]   # Used for task assignment

swarm:
  enabled: true
  role: auto           # auto | coordinator | worker
  consensus_timeout_ms: 3000   # Time to wait for coordinator election
  heartbeat_interval_s: 5.0    # How often to send liveness pings
  peer_stale_after_s: 30.0     # Remove peer if not seen within this window
  task_timeout_s: 120.0        # Abort task if not completed within this time
```

### Environment variables

| Variable | Description |
|---|---|
| `OPENCASTOR_SWARM_SECRET` | Shared secret for swarm message authentication (optional but recommended) |

## Roles

| Role | Behaviour |
|---|---|
| `coordinator` | Force this robot to be coordinator (ignores consensus) |
| `worker` | Never become coordinator |
| `auto` | Participate in consensus election — lowest UUID wins |

## Troubleshooting

### No peers found by `castor fleet status`

1. **Check mDNS is enabled** in both robots' RCAN configs:
   `rcan_protocol.enable_mdns: true`

2. **Check zeroconf is installed**:
   ```bash
   pip install "opencastor[rcan]"
   python -c "import zeroconf; print('ok')"
   ```

3. **Check router settings**: Some enterprise routers block mDNS between
   VLAN segments. Try putting both robots on the same SSID.

4. **Firewall**: Ensure UDP port 5353 (mDNS) is not blocked between robots:
   ```bash
   sudo ufw allow 5353/udp
   ```

5. **Increase scan timeout**:
   ```bash
   castor fleet status --timeout 15
   ```

### Coordinator keeps changing (election instability)

Set one robot as permanent coordinator:

```yaml
swarm:
  role: coordinator
```

And all others as workers:

```yaml
swarm:
  role: worker
```

### Patch sync causes unexpected behaviour changes

Disable patch sync while keeping other swarm features:

```yaml
swarm:
  enabled: true
  patch_sync:
    enabled: false
```

Or roll back a specific patch on all robots:

```bash
castor improve --rollback --all
```

## Known Limitations

- Maximum tested fleet size: **8 robots** on a single LAN segment
- mDNS does not cross router boundaries (robots must be on the same subnet)
- Shared memory uses eventually-consistent replication (not strong consistency)
- Patch sync requires all robots to be running the same OpenCastor version

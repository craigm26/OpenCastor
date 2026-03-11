# Pollen Robotics Reachy Integration

OpenCastor provides a first-class driver and RCAN profiles for [Pollen Robotics](https://www.pollen-robotics.com/) Reachy 2 and Reachy Mini humanoid robots.

## Hardware Overview

| Model | Description | Connectivity |
|-------|-------------|-------------|
| **Reachy 2** | Full-size humanoid — dual 7-DOF arms, head with stereo cameras, mobile base (optional) | Ethernet / Wi-Fi; exposes gRPC server |
| **Reachy Mini** | Desktop humanoid — 5-DOF neck + speakers + microphone, no arms | Ethernet / Wi-Fi; same gRPC API |

Both robots run an on-board computer and expose the `reachy2-sdk` gRPC API. OpenCastor connects as a gRPC client.

## ReachyDriver Config

### `host: auto` (recommended)

```yaml
drivers:
- id: reachy
  protocol: reachy
  host: auto        # mDNS discovery: resolves reachy.local or reachy-mini.local
  port: 50051       # default gRPC port; omit to use default
```

When `host: auto` is set, the driver calls `detect_reachy_network()` at startup. Discovery probes `reachy.local` and `reachy-mini.local` in parallel; whichever resolves first wins. Reachy Mini is preferred if `reachy-mini.local` is found.

### Explicit IP

```yaml
drivers:
- id: reachy
  protocol: reachy
  host: 192.168.1.42
  port: 50051
```

Use an explicit IP when mDNS is unavailable (e.g. networks that block multicast).

## mDNS Auto-Discovery

Reachy robots broadcast via mDNS. On the same local network:

```python
from castor.hardware_detect import detect_reachy_network

result = detect_reachy_network()
# {"reachy2": "192.168.1.42", "reachy_mini": None}
```

The discovery runs in daemon threads so it never blocks the gateway startup sequence. Timeout is 3 seconds per hostname.

## RCAN Profiles

Two ready-to-use profiles are included:

### `pollen/reachy2.yaml`

```bash
castor hub install pollen/reachy2
castor run --config ~/.castor/profiles/pollen/reachy2.yaml
```

Configures Reachy 2 with:
- `ReachyDriver` (`host: auto`)
- Gemini 2.5 Flash as default brain
- `capabilities: [move, vision, speak]`
- Safety bounds for arm workspace

### `pollen/reachy-mini.yaml`

```bash
castor hub install pollen/reachy-mini
castor run --config ~/.castor/profiles/pollen/reachy-mini.yaml
```

Configures Reachy Mini with:
- `ReachyDriver` (`host: auto`)
- Gemini 2.5 Flash as default brain
- `capabilities: [move, speak, vision]`
- Head-only motion bounds

## Install

```bash
pip install opencastor[reachy]
```

This installs:
- `reachy2-sdk` — official Pollen Robotics Python SDK
- `zeroconf` — mDNS/DNS-SD for `host: auto` discovery

## Full Example

```yaml
rcan_version: "1.1.0"
metadata:
  robot_name: reachy-desk
agent:
  provider: anthropic
  model: claude-sonnet-4-6
rcan_protocol:
  capabilities: [move, vision, speak]
drivers:
- id: reachy
  protocol: reachy
  host: auto
channels:
- type: discord
  token: ${DISCORD_TOKEN}
  guild_id: ${DISCORD_GUILD_ID}
```

Start with:

```bash
castor gateway --config reachy-desk.rcan.yaml
```

Then send messages in Discord to control Reachy via natural language.

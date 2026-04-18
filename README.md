<p align="center">
  <img src="brand/icon-192.png" alt="OpenCastor" width="200"/>
</p>

<h1 align="center">OpenCastor</h1>

<p align="center">
  Open-source robot runtime — connect any AI model to any robot hardware through a single YAML config.
</p>

<p align="center">
  <a href="https://pypi.org/project/opencastor/"><img src="https://img.shields.io/pypi/v/opencastor?color=blue&label=PyPI" alt="PyPI"></a>
  <a href="https://rcan.dev/spec/"><img src="https://img.shields.io/badge/RCAN-v3.0-brightgreen" alt="RCAN v3.0"></a>
  <a href="https://rcan.dev/docs/safety/"><img src="https://img.shields.io/badge/Protocol%2066-94%25-orange" alt="Protocol 66"></a>
  <a href="https://github.com/craigm26/OpenCastor/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License"></a>
  <a href="https://github.com/craigm26/OpenCastor/actions"><img src="https://img.shields.io/github/actions/workflow/status/craigm26/OpenCastor/ci.yml?label=CI" alt="CI"></a>
  <a href="https://app.opencastor.com"><img src="https://img.shields.io/badge/Fleet%20UI-app.opencastor.com-orange" alt="Fleet UI"></a>
  <a href="https://docs.opencastor.com"><img src="https://img.shields.io/badge/Docs-docs.opencastor.com-00bcd4" alt="Docs"></a>
  <a href="https://discord.gg/jMjA8B26Bq"><img src="https://img.shields.io/badge/Discord-join-5865F2?logo=discord&logoColor=white" alt="Discord"></a>
</p>

<p align="center">
  <b>94,438 lines of Python · 7,334 tests · Python 3.10–3.13</b>
</p>

---

## What is OpenCastor

OpenCastor is an open-source runtime for embodied AI. It implements the [RCAN open protocol](https://rcan.dev/spec/) and handles the hard parts: safety gates, multi-provider AI routing, hardware drivers, messaging channels, and fleet management.

Point it at any LLM (Gemini, GPT-4.1, Claude, Ollama, and 13 more) and any robot body (Raspberry Pi, Jetson, Arduino, ESP32, LEGO) via a single YAML config. Your robot answers to WhatsApp, Telegram, Discord, Slack, and Home Assistant — and learns from its own experience through the Sisyphus self-improvement loop.

> **RCAN ≠ OpenCastor.** [RCAN](https://rcan.dev) is an independent open protocol — like DNS and ICANN, but for robotics. Any robot can implement RCAN without using OpenCastor. OpenCastor is one implementation that helped inform the spec.

## Quick Start

### 1. Install

```bash
pip install opencastor==2026.4.1.0
```

### 2. Run the setup wizard

```bash
castor setup
```

The wizard will:
- Name your robot and assign an RRN (Robot Registration Number)
- Generate a config file at `~/.config/opencastor/<name>.rcan.yaml`
- Show a QR code to connect to the Fleet UI at [app.opencastor.com](https://app.opencastor.com)
- Configure your AI brain provider (Gemini, Claude, OpenAI, or local Ollama)

### 3. Start your robot

```bash
# Start the AI brain + REST API (port 8000)
castor gateway --config ~/.config/opencastor/bob.rcan.yaml

# Start the cloud bridge (connects robot to Fleet UI — outbound-only)
castor bridge --config ~/.config/opencastor/bob.rcan.yaml
```

### 4. One-command systemd setup

```bash
castor bridge setup   # generates and optionally installs systemd services
```

### Docker

```bash
docker run -it \
  -v ~/.config/opencastor:/config \
  -e OPENCASTOR_CONFIG=/config/bob.rcan.yaml \
  ghcr.io/craigm26/opencastor:2026.4.1.0 \
  castor gateway
```


<!-- SETUP_CATALOG:BEGIN -->
| Profile | Description | Requires |
|---|---|---|
| `apple_native` | Mac with Apple Silicon (M1–M4) — runs models on-device via Apple Foundation Models. No API key needed. | macOS, Apple Silicon |
| `mlx_local_vision` | Mac with Apple Silicon — open-source models via MLX (Llama, Mistral, Qwen). More model choice than apple_native. | macOS, Apple Silicon |
| `ollama_universal_local` | Any machine — runs local models via Ollama. Works on Mac, Linux, and Windows. | [Ollama](https://ollama.com) installed |

**On Apple Silicon, `apple_native` is the default.** The wizard will ask which Apple model profile fits your use case:

| Apple Profile | Use case | Guardrails |
|---|---|---|
| `apple-balanced` ⭐ | General chat and robot commands — best starting point | Default |
| `apple-creative` | Creative tasks, less restrictive output | Permissive Content Transformations |
| `apple-tagging` | Classifying or labeling objects/scenes | Default |
<!-- SETUP_CATALOG:END -->

## Features

- **Protocol 66 safety** — ESTOP never blocked, local safety always wins, confidence gates run on-device
- **RCAN v1.6** — replay prevention, federation, constrained transport (BLE/LoRa), multi-modal payloads, Level of Assurance
- **Multi-provider AI** — Gemini, Claude, OpenAI, Ollama, MLX (Apple Silicon), Groq, DeepSeek, and 7 more; hot-swap via YAML
- **Fleet UI** — real-time fleet dashboard at [app.opencastor.com](https://app.opencastor.com); no port forwarding needed
- **SO-ARM101 arm support** — auto-detected via USB, guided setup for follower/leader/bimanual configurations
- **18 hardware presets** — Raspberry Pi, Jetson, Arduino, ESP32, LEGO Mindstorms, OAK-D, LeRobot SO-ARM101, and more
- **Self-improving loop** — Sisyphus PM→Dev→QA→Apply pipeline learns from every episode
- **Messaging channels** — WhatsApp, Telegram, Discord, Slack, Home Assistant, MQTT
- **castor setup wizard** — guided onboarding with QR codes; works headless on Pi

## Architecture

```
[ app.opencastor.com / Fleet UI ]
          │  Firebase / Firestore
          │
   [ Cloud Functions ]            R2RAM enforcement + rate limiting
          │
   [ castor bridge ]              outbound-only Firestore connection
          │
   [ castor gateway ]             FastAPI REST + messaging channels
          │
   ┌──────────────────────┐
   │   Protocol 66 Safety │       ESTOP | confidence gates | bounds
   └──────────────────────┘
          │
   [ Tiered Brain ]               Gemini / Claude / GPT / Ollama / …
          │
   [ RCAN Config + Drivers ]      .rcan.yaml → hardware abstraction
          │
   [ Robot Hardware ]             Pi / Jetson / Arduino / SO-ARM101 / …
```

## RCAN v1.6 Features

| Feature | Description |
|---|---|
| Replay prevention | Sliding-window `msg_id` cache; stale messages rejected |
| Federation | Cross-registry consent, DNS trust anchors, JWT verification |
| Constrained transport | Compact CBOR (512B), 32-byte ESTOP minimal for BLE/LoRa |
| Multi-modal payloads | Inline or referenced media chunks; streaming sensor data |
| Level of Assurance | LoA 1/2/3 on operator JWTs; configurable minimum per scope |

## castor CLI Reference

| Command | Description |
|---|---|
| `castor setup` | Interactive onboarding wizard — config, RRN, Fleet UI QR code |
| `castor gateway` | Start AI brain + REST API + messaging channels |
| `castor bridge` | Start cloud bridge (Fleet UI connection) |
| `castor scan` | Auto-detect connected hardware (USB, I2C, V4L2) |
| `castor doctor` | System health check — providers, channels, hardware |
| `castor fix` | Auto-fix common configuration issues |
| `castor status` | Provider and channel readiness summary |
| `castor improve` | Run Sisyphus self-improvement on recent episodes |
| `castor audit --verify` | Verify audit chain integrity |
| `castor approvals` | Review and approve pending high-risk commands |
| `castor deploy` | Push config to a remote robot over SSH |
| `castor fleet` | Discover and monitor all robots on the local network |
| `castor upgrade` | Pull latest version + pip install + service restart |

Full reference: [docs.opencastor.com/runtime/cli](https://docs.opencastor.com/runtime/cli/)

## Configuration

Minimal `bob.rcan.yaml`:

```yaml
rcan_version: "1.6"
metadata:
  robot_name: bob
agent:
  provider: google
  model: gemini-2.5-flash
drivers:
  - id: wheels
    protocol: pca9685
channels: {}
```

That's it. `castor gateway --config bob.rcan.yaml` starts the REST API, messaging channels, and the self-improving loop.

Swap the AI brain with one line:

```yaml
agent:
  provider: anthropic
  model: claude-sonnet-4-6
```

## Protocol 66

Protocol 66 is RCAN's mandatory safety layer. Current OpenCastor conformance: **94%**.

Key invariants:
- ESTOP delivered at QoS 2 (EXACTLY_ONCE) — never blocked by backpressure
- Local safety always wins — cloud commands pass through the same confidence gates as local commands
- Audit chain required for all flagged commands
- `GuardianAgent` has veto authority over unsafe actions

P66 manifest: [`sbom/`](sbom/) · Full spec: [rcan.dev/docs/safety/](https://rcan.dev/docs/safety/)

## Fleet UI

**[app.opencastor.com](https://app.opencastor.com)** — sign in with Google to see your fleet.

- Real-time status cards for all registered robots
- Robot detail: telemetry, command history, chat-scope instructions
- ESTOP button on every screen
- Consent management — approve/deny R2RAM access requests
- Revocation display — shows if a robot's identity has been revoked
- LoA display — shows operator Level of Assurance on control commands

Robots connect via `castor bridge` (outbound-only Firestore). No open ports on the robot side.

## SO-ARM101 Arm Support

The LeRobot SO-ARM101 6-DOF serial bus servo arm is auto-detected via USB VID/PID (CH340, `0x1A86/0x7523`).

```bash
pip install opencastor[lerobot]
castor scan                          # detects arm + suggests preset
castor wizard --preset so_arm101     # guided config: follower / leader / bimanual
castor gateway --config so_arm101.rcan.yaml
```

`castor scan` counts connected Feetech boards and automatically suggests the right preset: single arm (follower or leader), or bimanual pair (ALOHA-style). Koch arms use the same detection path.

## Where this fits in the stack

OpenCastor is the **reference runtime** — one implementation of the RCAN protocol. Everything else is independent; adopt one, or all seven.

| Layer | Piece | What it is |
|---|---|---|
| **Declaration** | [ROBOT.md](https://github.com/RobotRegistryFoundation/robot-md) | The file a robot ships at its root. YAML frontmatter + markdown prose. Declares identity, capabilities, safety gates. Spec + Python CLI. |
| **Agent bridge** | [robot-md-mcp](https://github.com/RobotRegistryFoundation/robot-md-mcp) | MCP server that exposes a `ROBOT.md` to Claude Code, Claude Desktop, Cursor, Zed, Gemini CLI — any MCP-aware agent. |
| **Wire protocol** | [RCAN](https://rcan.dev/spec/) | How robots, gateways, and planners talk. Signed envelopes, LoA enforcement, PQC crypto. Think HTTP for robots. |
| **Python SDK** | [rcan-py](https://github.com/continuonai/rcan-py) | `pip install rcan` — `RCANMessage`, `RobotURI`, `ConfidenceGate`, `HiTLGate`, `AuditChain`. |
| **TypeScript SDK** | [rcan-ts](https://github.com/continuonai/rcan-ts) | `npm install rcan-ts` — same API surface for Node + browser. |
| **Registry** | [Robot Registry Foundation](https://robotregistryfoundation.org) | Permanent RRN identities. Public resolver at `/r/<rrn>`. Like ICANN for robots. |
| **Reference runtime** ← *this* | [OpenCastor](https://github.com/craigm26/OpenCastor) | **This repo.** Open-source robot runtime — connects LLM brains to hardware bodies. Implements RCAN, adds safety gates, multi-provider AI routing, 18+ hardware drivers, messaging channels, fleet management. |

See also: the [`ROBOT.md` quickstart](docs/robot-md-claude-code.md) — one command puts a Bob-class robot in Claude Code via the MCP bridge.

## Ecosystem (versions)

| Project | Version |
|---|---|
| **OpenCastor** (this) | v2026.4.17.0 |
| [Fleet UI](https://app.opencastor.com) | live |
| [RCAN Protocol](https://rcan.dev/spec/) | v3.0 |
| [rcan-py](https://github.com/continuonai/rcan-py) | v2.0.0 |
| [rcan-ts](https://github.com/continuonai/rcan-ts) | v2.0.0 |
| [ROBOT.md](https://github.com/RobotRegistryFoundation/robot-md) | v0.2.0 (schema v1.1) |
| [robot-md-mcp](https://github.com/RobotRegistryFoundation/robot-md-mcp) | v0.1.3 |
| [Robot Registry Foundation](https://robotregistryfoundation.org) | v3.0 |

## Contributing

OpenCastor is Apache 2.0 and community-driven.

- **Discord**: [discord.gg/jMjA8B26Bq](https://discord.gg/jMjA8B26Bq)
- **Issues**: [github.com/craigm26/OpenCastor/issues](https://github.com/craigm26/OpenCastor/issues)
- **PRs**: See [CONTRIBUTING.md](CONTRIBUTING.md)
- **Docs**: [`docs/claude/`](docs/claude/) — structure, API reference, env vars

## License

Apache 2.0 · by [Craig Merry](https://github.com/craigm26)

Implements the [RCAN open protocol](https://rcan.dev/spec/). RCAN is an independent open standard — any robot or runtime can implement it.

<p align="center">
  <img src="brand/icon-192.png" alt="OpenCastor" width="200"/>
</p>

<h1 align="center">OpenCastor</h1>
<h3 align="center">The Universal Runtime for Embodied AI</h3>

<p align="center">
  <a href="https://pypi.org/project/opencastor/"><img src="https://img.shields.io/pypi/v/opencastor?color=blue&label=PyPI" alt="PyPI"></a>
  <a href="https://github.com/craigm26/OpenCastor/actions"><img src="https://img.shields.io/github/actions/workflow/status/craigm26/OpenCastor/ci.yml?label=CI" alt="CI"></a>
  <a href="https://github.com/craigm26/OpenCastor/blob/main/LICENSE"><img src="https://img.shields.io/github/license/craigm26/OpenCastor?color=green" alt="License"></a>
  <a href="https://pypi.org/project/opencastor/"><img src="https://img.shields.io/pypi/pyversions/opencastor" alt="Python"></a>
  <a href="https://discord.gg/jMjA8B26Bq"><img src="https://img.shields.io/badge/Discord-join%20server-5865F2?logo=discord&logoColor=white" alt="Discord"></a>
  <a href="./sbom/"><img src="https://img.shields.io/badge/SBOM-CycloneDX-blue" alt="SBOM"></a>
  <a href="https://app.opencastor.com"><img src="https://img.shields.io/badge/Fleet%20UI-app.opencastor.com-orange" alt="Fleet UI"></a>
  <a href="https://rcan.dev/spec/"><img src="https://img.shields.io/badge/RCAN-v1.6-brightgreen" alt="RCAN v1.6"></a>
</p>

<p align="center">
  <b>94,438 lines of Python · 6,459 tests · Python 3.10–3.13</b><br/>
  <i>Connect any AI model to any robot hardware through a single YAML config.</i>
</p>

---

OpenCastor is an open-source runtime for embodied AI — one implementation of the [RCAN open protocol](https://rcan.dev/spec/). Point it at any LLM (Gemini, GPT-4.1, Claude, Ollama, and 13 more) and any robot body (Raspberry Pi, Jetson, Arduino, ESP32, LEGO) via a single YAML config. The robot answers to WhatsApp, Telegram, Discord, Slack, and Home Assistant — and learns from its own experience.

> **RCAN is not OpenCastor.** RCAN ([rcan.dev](https://rcan.dev)) is an independent open protocol — like DNS and ICANN, but for robotics. Any robot, any runtime, any manufacturer can implement RCAN and register at the RCAN Registry. OpenCastor is a reference implementation that helped inform the spec. You don't need OpenCastor to use RCAN, and RCAN doesn't require OpenCastor.

## Quick Start

### 1. Install

```bash
pip install opencastor==2026.4.1.0
```

### 2. Run setup wizard

```bash
castor setup
```

The wizard will:
- Name your robot and assign an RRN
- Generate a config file (`~/.config/opencastor/<name>.rcan.yaml`)
- Show a QR code to connect to the Fleet UI
- Configure your AI brain provider

### 3. Connect to Fleet UI

Open **[app.opencastor.com](https://app.opencastor.com)** (or scan the QR code from the setup wizard).

Sign in with Google → copy your Firebase UID → paste into the terminal.

Your robot will appear in the Fleet dashboard within 30 seconds.

### 4. Start your robot

```bash
# Start the AI brain + REST API
castor gateway --config ~/.config/opencastor/bob.rcan.yaml

# Start the cloud bridge (connects to Fleet UI)
castor bridge --config ~/.config/opencastor/bob.rcan.yaml
```

### One-command systemd setup

```bash
castor bridge setup  # generates + optionally installs systemd service
```

### Docker Quick Start

```bash
# Run OpenCastor in Docker (for non-Pi hardware)
docker run -it \
  -v ~/.config/opencastor:/config \
  -e OPENCASTOR_CONFIG=/config/bob.rcan.yaml \
  ghcr.io/craigm26/opencastor:2026.4.1.0 \
  castor gateway

# Non-interactive setup (CI/Docker)
docker run -it \
  -v ~/.config/opencastor:/config \
  ghcr.io/craigm26/opencastor:2026.4.1.0 \
  castor setup --non-interactive

# Or use docker-compose (for ARM64/Pi)
# See: https://github.com/craigm26/OpenCastor/blob/main/docker-compose.arm.yml
```

---

## Legacy Install

```bash
curl -sL opencastor.com/install | bash
castor wizard          # guided setup: API keys, hardware, channels
castor gateway         # start the API gateway + messaging
```

The wizard detects your hardware and selects a stack profile automatically:

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

## Docker Quickstart

```bash
# 1. Clone and enter the repo
git clone https://github.com/craigm26/OpenCastor.git && cd OpenCastor

# 2. Copy and edit environment variables
cp .env.example .env
nano .env   # Add your AI provider key (ANTHROPIC_API_KEY, GOOGLE_API_KEY, etc.)

# 3. Start — a starter config is auto-generated on first run
docker compose up

# The first run will create ./config/robot.rcan.yaml with sensible defaults.
# Edit it to match your hardware, then restart:
docker compose restart
```

**Or use the setup wizard (recommended):**
```bash
# Interactive Fleet UI onboarding (shows QR codes, configures brain provider)
docker run -it --rm -v ~/.config/opencastor:/config opencastor castor setup

# Non-interactive: accept all defaults (CI/CD pipelines)
docker run -it --rm -v ~/.config/opencastor:/config opencastor castor setup --non-interactive

# With USB hardware (SO-ARM101, Dynamixel, etc.) — pass through USB bus for auto-detect:
docker run -it --rm --device=/dev/bus/usb --privileged -v ~/.config/opencastor:/config opencastor castor wizard
```

**Or generate the config manually:**
```bash
# Non-interactive: scaffold a starter config
castor init --output ./config/robot.rcan.yaml
```

## Minimal Config

```yaml
rcan_version: "1.6"
metadata:
  robot_name: my-robot
agent:
  provider: google
  model: gemini-2.5-flash
drivers:
- id: wheels
  protocol: pca9685
```

That's it. `castor gateway --config my-robot.rcan.yaml` starts the REST API, messaging channels, and the self-improving loop.

## Architecture

```
[ WhatsApp / Telegram / Discord / Slack / Home Assistant ]
                    │
            [ API Gateway ]                   FastAPI (castor/api.py)
                    │
        ┌───────────────────────┐
        │   Safety Layer        │            Anti-subversion, BoundsChecker
        └───────────────────────┘
                    │
    [ Tiered Brain: Gemini / GPT-4.1 / Claude / Ollama … ]
                    │
     ┌──────────────────────────────────┐
     │  Offline Fallback / ALMA Learner │   Episode memory, self-improvement
     └──────────────────────────────────┘
                    │
              [ RCAN Config ]               rcan.dev/spec validation
                    │
    ┌──────────────────────────────────────────────┐
    │  VFS  │  Agents  │  Sisyphus  │  Fleet/Swarm │
    └──────────────────────────────────────────────┘
                    │
    [ PCA9685 / Dynamixel / ROS2 / ESP32 / mock ]
                    │
              [ Your Robot ]
```

### Key Components

| Component | File | Role |
|---|---|---|
| API Gateway | `castor/api.py` | FastAPI entry point — REST, webhooks, SSE |
| BaseProvider | `castor/providers/base.py` | LLM adapter: `think(image, instruction)→Thought` |
| DriverBase | `castor/drivers/base.py` | Hardware driver: `move()`, `stop()`, `health_check()` |
| SisyphusLoop | `castor/learner/sisyphus.py` | PM→Dev→QA→Apply self-improvement cycle |
| ALMAConsolidation | `castor/learner/alma.py` | Cross-episode pattern analysis |
| EpisodeStore | `castor/learner/episode_store.py` | JSON-file episode storage (10k cap, FIFO eviction) |
| EpisodeMemory | `castor/memory.py` | SQLite episode store (10k cap, FIFO) |
| BehaviorRunner | `castor/behaviors.py` | YAML step sequences: waypoint/think/speak/stop |
| CastorFS | `castor/fs/__init__.py` | Unix-style VFS with capability permissions + e-stop |
| BaseChannel | `castor/channels/base.py` | Messaging integration: `start()`, `stop()`, `send_message()` |

## Memory & Learning

OpenCastor robots learn from experience through two interconnected systems.

### Episode Store

Every task execution is recorded as an `Episode`:

```python
@dataclass
class Episode:
    goal: str
    actions: list[dict]       # what the robot did
    sensor_readings: list[dict]
    success: bool
    duration_s: float
    metadata: dict
```

Episodes are stored as JSON files in `~/.opencastor/episodes/` (via `EpisodeStore`) and also in a SQLite database (via `EpisodeMemory`). Both are capped at **10,000 episodes with FIFO eviction** to prevent unbounded disk growth.

### Long-Running Sessions (Context Compaction)

For 24/7 deployments (Bob, Alex), enable rolling context compression so sessions never hit provider context window limits:

```yaml
memory:
  compaction:
    enabled: true          # disabled by default
    max_tokens: 80000      # trigger threshold
    target_tokens: 20000   # post-compaction target
    preserve_last_n: 10    # always keep last N messages verbatim
```

When the rolling context exceeds `max_tokens`, the `ContextCompactor` summarizes older messages via LLM (using `provider.think()`) and replaces them with a `[Compacted session summary]` block. If the provider is unavailable, a rule-based fallback summary is used. Requires at least one provider configured.

### The Sisyphus Loop

After each task, the self-improving PM→Dev→QA→Apply pipeline runs:

```
Episode
  │
  ▼ PM Stage    — analyze failure/inefficiency, score root cause
  │
  ▼ Dev Stage   — generate a config or behavior patch
  │
  ▼ QA Stage    — verify safety bounds, type correctness, semantic sense
  │
  ▼ Apply Stage — write patch to config or learned_behaviors.yaml
```

- **Disabled by default** — opt in via `castor wizard` or `improve.enabled: true` in RCAN
- **4 cost tiers** — free (local Ollama) to ~$5–15/mo (Claude/Gemini)
- **Rollback** — undo any change: `castor improve --rollback <id>`
- **Patch types**: `ConfigPatch` (YAML key/value), `BehaviorPatch` (named rule), `PromptPatch`
- **Applied patches** are deduplicated by `rule_name` — no duplicate accumulation

### ALMA Consolidation

After batches of episodes, the ALMA consolidator runs cross-episode pattern analysis:

```python
# Groups episodes by goal type, finds recurring failure patterns,
# scores by frequency × failure rate, returns top-5 patches.
patches = ALMAConsolidation().consolidate(episodes)
```

Pattern types detected: high failure rate for goal class, common failure action, sensor reading divergence between success/failure, long failure duration vs fast success.

```bash
castor improve --episodes 10    # analyze last 10 episodes
castor improve --status         # view improvement history
```

## Tiered Brain

```
Layer 3: Planner     Claude Opus / Gemini Pro    ~12s     complex multi-step
Layer 2: Fast Brain  HuggingFace / Gemini Flash  ~500ms   Q&A, classification
Layer 1: Reactive    Rule engine                 <1ms     e-stop, bounds
```

80% of decisions are handled free at the reactive layer. Only genuinely complex tasks escalate to the planner.

### Task-Aware Model Routing

By default, the planner runs every N ticks (`planner_interval`). You can override this with **task-category routing** so cheap tasks never touch the planner and high-stakes tasks always do.

Add a `task_routing:` block to your `.rcan.yaml`:

```yaml
task_routing:
  sensor_poll:  [ollama, gemini, anthropic]   # fast & free — never escalates
  navigation:   [anthropic, gemini, ollama]   # default interval logic
  reasoning:    [anthropic, openai, gemini]   # always uses planner when available
  code:         [deepseek, openai, anthropic] # always uses planner
  safety:       [anthropic, openai, gemini]   # NEVER downgraded — always planner
  vision:       [gemini, anthropic, openai]   # multimodal — always planner
  search:       [gemini, anthropic, openai]   # agentic — always planner
```

Pass the category when invoking a skill:

```python
thought = brain.think(image, "poll battery level", task_category="sensor_poll")
thought = brain.think(image, "plan escape route", task_category="reasoning")
```

Or via RCAN `INVOKE` message:

```yaml
skill: nav.plan_escape
params: { obstacle: front }
task_category: reasoning   # forces planner
```

**Category behaviour summary:**

| Category | Planner behaviour |
|---|---|
| `sensor_poll` | **Never** — fast provider only, saves tokens |
| `navigation` | Default interval logic |
| `reasoning` | **Always** when planner is available |
| `code` | Always |
| `safety` | Always — never downgraded |
| `vision` | Always (multimodal) |
| `search` | Always (agentic) |
| `None` / unset | Default interval logic |

## Supported AI Providers

| Provider | Models | Best For |
|---|---|---|
| **Google** | `gemini-2.5-flash`, `gemini-2.5-pro` | Multimodal, speed, recommended default |
| **Anthropic** | `claude-opus-4-6`, `claude-sonnet-4-6` | Complex reasoning, safety-critical |
| **OpenAI** | `gpt-4.1`, `gpt-4.1-mini` | Instruction following, 1M context |
| **OpenRouter** | 100+ models | Multi-model fallback, cost optimization |
| **Ollama** | Any local model | Privacy, offline, zero cost |
| **llama.cpp** | GGUF models | Edge inference, Raspberry Pi |
| **MLX** | Apple Silicon native | Mac M1–M4, 400+ tok/s |
| **Groq** | `llama-3.3-70b`, `mixtral-8x7b` | LPU-accelerated, fastest API |
| **HuggingFace** | Any hosted model | Fast brain layer, classification |
| **VLA** | OpenVLA, Octo, pi0 | End-to-end action models |
| **DeepSeek** | `deepseek-chat`, `deepseek-reasoner` | Chinese/multilingual, R1 reasoning |
| **Groq / xAI / Mistral / Kimi / Qwen** | Various | Regional / specialized |

Swap with one YAML change:

```yaml
agent:
  provider: google
  model: gemini-2.5-flash
```

Provider pool with fallback:

```yaml
provider_fallback:
  enabled: true
  provider: ollama
  model: llama3.2:3b
  quota_cooldown_s: 3600
```

## Supported Hardware

Run `castor scan` — OpenCastor auto-detects connected hardware by USB VID/PID, I2C address, V4L2, libcamera, and `/dev` entries. No manual config needed for supported kits.

### LeRobot / Hugging Face Arms

| Kit | Notes | Install |
|---|---|---|
| **LeRobot SO-ARM101** | 6-DOF serial bus servo arm. Auto-detected via CH340 USB adapter (`castor scan`). RCAN profiles for follower, leader, and bimanual. | `pip install opencastor[lerobot]` |
| **Koch arm** | Similar Feetech servo architecture, same detection path | `pip install opencastor[lerobot]` |
| **ALOHA / bimanual** | Dual SO-ARM101 leader+follower. `castor scan` detects both ports and suggests bimanual preset. | `pip install opencastor[lerobot]` |

**Quickstart for SO-ARM101:**
```bash
pip install opencastor[lerobot]
castor scan                          # auto-detects arm on /dev/ttyUSB0 or /dev/ttyACM0
castor wizard --preset so_arm101     # guided config: follower / leader / bimanual
castor gateway --config so_arm101.rcan.yaml
```

The SO-ARM101 uses Feetech STS3215 servos over a Waveshare Serial Bus Servo Driver Board. `castor scan` identifies it by USB VID `0x1A86` / PID `0x7523` (CH340 chip) and counts how many boards are connected to suggest the right preset (single arm vs bimanual pair).

### Other Supported Hardware

| Kit | Price | Preset |
|---|---|---|
| Waveshare AlphaBot / JetBot | ~$45 | `waveshare_alpha.rcan.yaml` |
| Adeept RaspTank / DarkPaw | ~$55 | `adeept_generic.rcan.yaml` |
| SunFounder PiCar-X | ~$60 | `sunfounder_picar.rcan.yaml` |
| Robotis Dynamixel (X-Series) | Varies | `dynamixel_arm.rcan.yaml` |
| Pollen Robotics Reachy 2 | — | `reachy2.rcan.yaml` |
| HLabs ACB v2.0 (BLDC) | — | `hlabs/acb-single.rcan.yaml` |
| LEGO Mindstorms EV3 / SPIKE Prime | $30–150 used | `lego_mindstorms_ev3.rcan.yaml` |
| Arduino + L298N (DIY) | ~$8–15 | `arduino_l298n.rcan.yaml` |
| ESP32 + Motor Driver (DIY) | ~$6–12 | `esp32_generic.rcan.yaml` |
| OAK-D / OAK-4 Pro | ~$150 | `oak4_pro.rcan.yaml` |
| RPLidar / YDLIDAR | Varies | `castor wizard` |
| Intel RealSense D435/D455/L515 | Varies | `castor wizard` |
| Google Coral USB / Hailo-8 | Varies | `castor wizard` |
| DIY (any) | Any | `castor wizard` |

18 presets total in `config/presets/`. OpenCastor explicitly supports second-hand hardware — donated school kits, eBay finds, makerspace bins. If you found it at Goodwill, there's probably a preset for it.

## Messaging Channels

| Channel | Auth | Notes |
|---|---|---|
| WhatsApp | neonize QR or Twilio | group JID routing per robot |
| Telegram | Bot token | |
| Discord | Bot token | |
| Slack | App token | |
| Home Assistant | HA webhook | |
| MQTT | Broker URL | |

## Safety

Defense-in-depth, RCAN spec compliant:

| Layer | What It Does |
|---|---|
| **Prompt injection guard** | `_check_instruction_safety()` on every `think()` call |
| **Physical bounds** | Dynamixel 0–4095, PCA9685 duty-cycle clamping, `BoundsChecker` |
| **Rate limiting** | 5 req/s/IP on `/api/command`, 10 req/min/sender on webhooks |
| **E-stop** | `POST /api/stop`, `fs.estop()`, or any messaging channel |
| **GuardianAgent** | Veto authority over unsafe actions |
| **Audit chain** | Hash-chained tamper-evident log; optional quantum commitment layer |

```bash
castor audit --verify        # verify chain integrity
castor approvals             # view/approve pending dangerous commands
```

## CLI Reference

```bash
# Setup
castor wizard                       # interactive setup
castor doctor                       # system health check
castor fix                          # auto-fix common issues

# Run
castor run --config robot.rcan.yaml          # perception-action loop
castor gateway --config robot.rcan.yaml      # API + channels
castor dashboard                             # Streamlit web UI
castor status                                # provider/channel readiness

# Fleet
castor fleet [--watch]              # discover + monitor robots
castor swarm status/command/sync    # multi-node ops
castor deploy pi@192.168.1.10 --config robot.rcan.yaml

# Learning
castor improve --episodes 10        # analyze + apply improvements
castor improve --rollback <id>      # undo a patch

# Safety
castor audit --verify               # verify audit chain
castor approvals                    # review pending commands
castor token --role operator        # issue JWT
```

Full reference: [`docs/claude/cli-reference.md`](docs/claude/cli-reference.md)

## What's New in v2026.4.1.0

### 🔌 v2026.4.1.0 — RCAN v1.6 Support

Five new hardware detectors and an expanded I2C lookup table:

- **Dynamixel U2D2** — explicit VID `0x0403`/PID `0x6014` detection; `suggest_preset("dynamixel_arm")`
- **RPLidar vs YDLIDAR** — differentiated by USB product string; STM32 path added
- **Raspberry Pi AI Camera** — libcamera + sysfs detection, IMX500 NPU firmware state check
- **LeRobot SO-ARM101 profiles** — Feetech board count → suggests follower / leader / bimanual preset automatically
- **I2C sensor table** — expanded to VL53L1X, SSD1306, ADS1115, BME280, LSM6DSO, HMC5883L, QMC5883L

### 🔧 v2026.3.13.12 — Install DX

- **`castor scan`** — hardware scan CLI with `--json` / `--refresh` / `--preset-only`
- **`castor doctor` hardware checks** — warns if depthai, reachy2-sdk, etc. are missing for detected hardware
- **`castor upgrade`** — git pull + pip install + service restart in one command; `castor stop` for clean shutdown
- **Gateway hardening** — PID file, port-in-use detection, `KillMode=control-group` in systemd services
- **Venv-agnostic systemd** — service templates now use `python -m castor.cli` and `python -m streamlit`
- **[Upgrade guide](docs/install/upgrade.md)** — Pi OS PEP 668, `--system-site-packages`, migration docs

> Previous: [v2026.3.13.12](CHANGELOG.md#20263110--2026-03-11) — Hardware Auto-Detection, LeRobot & Reachy support

### Previous: v2026.3.13.12

- **EmbeddingInterpreter** — local-first multimodal semantic perception with CLIP/SigLIP2 (Tier 0, free default, no API key), ImageBind/CLAP (Tier 1, experimental), or Gemini Embedding 2 (Tier 2, 3072-dim MRL). Builds a persistent episode vector store at `~/.opencastor/episodes/` and injects RAG context into TieredBrain pre/post hooks so robots recognize familiar patterns across sessions. Prometheus metrics (`opencastor_embedding_*`), Streamlit Embedding tab, and benchmark suite included.
- **HLabs ACB v2.0 hardware support** — first-class driver for the HLaboratories Actuator Control Board v2.0 (STM32G474, 3-phase BLDC, 12V–30V, 40A). USB-C serial + CAN Bus (1Mbit/s, 11-bit ARB ID) dual transport; `port: auto` VID/PID auto-detection; motor calibration flow (pole pairs → zero electrical angle → PID); real-time encoder telemetry at 50Hz; firmware flash via DFU mode (`castor flash`); three RCAN profiles (`hlabs/acb-single`, `hlabs/acb-arm-3dof`, `hlabs/acb-biped-6dof`). Install with `pip install opencastor[hlabs]`.

**Full changelog:** [CHANGELOG.md](CHANGELOG.md) · [Release notes](https://opencastor.com/changelog)

## Ecosystem

OpenCastor is part of a broader open robotics ecosystem:

| Project | Description | Link |
|---|---|---|
| **OpenCastor** | Robot runtime (this repo) | [github.com/craigm26/OpenCastor](https://github.com/craigm26/OpenCastor) |
| **OpenCastor Fleet UI** | Flutter web app — manage your robots remotely | [app.opencastor.com](https://app.opencastor.com) · [source (private)](https://github.com/craigm26/opencastor-client) |
| **RCAN Protocol** | Open spec for robot addressing & auth (v1.6) | [rcan.dev](https://rcan.dev) · [spec](https://rcan.dev/spec/) |
| **rcan-py** | Python RCAN SDK | [PyPI](https://pypi.org/project/rcan/) · [github](https://github.com/continuonai/rcan-py) |
| **rcan-ts** | TypeScript RCAN SDK | [npm](https://www.npmjs.com/package/@continuonai/rcan) · [github](https://github.com/continuonai/rcan-ts) |
| **Robot Registry Foundation** | Global robot identity registry (like ICANN for robots) | [robotregistryfoundation.org](https://robotregistryfoundation.org) |

### castor bridge — Remote Fleet Management

Run `castor bridge` alongside `castor gateway` to connect your robot to the OpenCastor cloud fleet:

```bash
pip install opencastor[cloud]

# Add to your .rcan.yaml:
# cloud:
#   provider: firebase
#   project_id: opencastor
#   service_account_key: ~/.config/opencastor/firebase-sa-key.json

castor bridge --config robot.rcan.yaml
```

The bridge establishes an **outbound-only** Firestore connection — your robot never listens on a public port. Commands are queued via Cloud Functions, validated by R2RAM scope rules, and executed locally. Protocol 66 safety layer remains enforced regardless of cloud command source.

View and control your fleet at **[app.opencastor.com](https://app.opencastor.com)**.

## Contributing

OpenCastor is Apache 2.0, community-driven.

- **Discord**: [discord.gg/jMjA8B26Bq](https://discord.gg/jMjA8B26Bq)
- **Issues**: [GitHub Issues](https://github.com/craigm26/OpenCastor/issues)
- **PRs**: See [CONTRIBUTING.md](CONTRIBUTING.md)

Reference docs: [`docs/claude/structure.md`](docs/claude/structure.md) · [`docs/claude/api-reference.md`](docs/claude/api-reference.md) · [`docs/claude/env-vars.md`](docs/claude/env-vars.md)

---

<p align="center">
  Implements the <a href="https://rcan.dev/spec/">RCAN open protocol</a> · Apache 2.0 · by <a href="https://github.com/craigm26">Craig Merry</a><br/>
  <sub>RCAN is an independent open standard. Any robot or runtime can implement it — OpenCastor is one implementation.</sub>
</p>

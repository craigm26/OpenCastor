# CLAUDE.md - OpenCastor Development Guide

## Project Overview

OpenCastor is a universal runtime for embodied AI. It connects LLM "brains" (Claude, Gemini, GPT-4.1, Ollama, HuggingFace, llama.cpp, MLX) to robot "bodies" (Raspberry Pi, Arduino, ESP32, LEGO, VEX) through a plug-and-play architecture, and exposes them to messaging platforms (WhatsApp, Telegram, Discord, Slack) for remote control. Configuration is driven by YAML files compliant with the [RCAN Standard](https://rcan.dev/spec/).

**Version**: 2026.2.21.1
**License**: Apache 2.0
**Python**: 3.10+
**Entry point**: `castor` or `opencastor` CLI commands (both registered in pyproject.toml)

## Quick Start

```bash
git clone https://github.com/craigm26/OpenCastor.git
cd OpenCastor
pip install -e ".[channels]"   # Install with all messaging channels
cp .env.example .env           # Copy env template
castor wizard                  # Interactive setup (API keys, hardware, channels)
castor gateway                 # Start the API gateway
```

Or with Docker:
```bash
cp .env.example .env && nano .env
docker compose up
```

Or one-line install:
```bash
curl -fsSL https://opencastor.com/install | bash
```

## Repository Structure

```
OpenCastor/
├── castor/                        # Main Python package (~131 files, ~36k LOC)
│   ├── __init__.py                # Version string (__version__ = "2026.2.21.1")
│   ├── __main__.py                # Entry point for `python -m castor`
│   ├── cli.py                     # Unified CLI (40+ commands, castor/opencastor)
│   ├── main.py                    # Core runtime: perception-action loop
│   ├── api.py                     # FastAPI gateway server
│   ├── auth.py                    # Unified auth manager (providers + channels)
│   ├── wizard.py                  # Interactive terminal setup wizard
│   ├── web_wizard.py              # Web-based setup wizard UI
│   ├── dashboard.py               # Streamlit web UI (CastorDash)
│   ├── dashboard_tui.py           # Terminal UI dashboard (tmux-based)
│   ├── registry.py                # ComponentRegistry (plugin system)
│   ├── plugins.py                 # Plugin loader
│   │
│   ├── providers/                 # LLM provider adapters (8 providers)
│   │   ├── __init__.py            # get_provider() factory + ComponentRegistry hook
│   │   ├── base.py                # BaseProvider ABC + Thought class
│   │   ├── anthropic_provider.py  # Claude Opus 4.6 / Sonnet 4.5
│   │   ├── google_provider.py     # Gemini 2.5 Flash/Pro, Gemini 3
│   │   ├── openai_provider.py     # GPT-4.1, GPT-4.1 Mini, GPT-5
│   │   ├── huggingface_provider.py # HuggingFace Transformers
│   │   ├── ollama_provider.py     # Local Ollama (offline, zero cost)
│   │   ├── llamacpp_provider.py   # llama.cpp GGUF (edge/RPi)
│   │   └── mlx_provider.py        # Apple Silicon MLX (M1-M4)
│   │
│   ├── drivers/                   # Hardware driver implementations
│   │   ├── __init__.py            # get_driver() factory
│   │   ├── base.py                # DriverBase ABC (move/stop/close)
│   │   ├── pca9685.py             # I2C PWM motor driver (RC cars, Amazon kits)
│   │   └── dynamixel.py           # Robotis servo controller (Protocol 2.0)
│   │
│   ├── channels/                  # Messaging channel integrations (5 platforms)
│   │   ├── __init__.py            # create_channel() factory + session store
│   │   ├── base.py                # BaseChannel ABC
│   │   ├── session.py             # Multi-channel session routing (ChannelSessionStore)
│   │   ├── whatsapp.py            # Re-export (default: neonize QR code)
│   │   ├── whatsapp_neonize.py    # WhatsApp via neonize (QR code scan)
│   │   ├── whatsapp_twilio.py     # WhatsApp via Twilio (legacy)
│   │   ├── telegram_channel.py    # Telegram Bot (long-polling)
│   │   ├── discord_channel.py     # Discord Bot
│   │   └── slack_channel.py       # Slack Bot (Socket Mode)
│   │
│   ├── agents/                    # Agent-based orchestration
│   │   ├── __init__.py
│   │   ├── base.py                # BaseAgent ABC
│   │   ├── registry.py            # Agent registry
│   │   ├── shared_state.py        # Shared agent state
│   │   ├── observer.py            # Observation agent
│   │   └── navigator.py           # Navigation agent
│   │
│   ├── specialists/               # Task-specific specialist agents
│   │   ├── base_specialist.py
│   │   ├── scout.py               # Exploration/mapping
│   │   ├── responder.py           # Reactive response
│   │   ├── dock.py                # Docking/charging
│   │   ├── manipulator.py         # Object manipulation
│   │   └── task_planner.py        # Multi-step planning
│   │
│   ├── learner/                   # Self-improving loop (Sisyphus pattern)
│   │   ├── alma.py                # Cross-episode learning consolidation
│   │   ├── sisyphus.py            # Main improvement orchestrator
│   │   ├── episode.py             # Episode recording + replay
│   │   ├── episode_store.py       # Episode persistence
│   │   ├── patches.py             # Code patch generation
│   │   ├── pm_stage.py            # Project Manager: analyze failures
│   │   ├── dev_stage.py           # Developer: generate patches
│   │   ├── qa_stage.py            # QA: verify patches
│   │   └── apply_stage.py         # Apply: rollout with rollback
│   │
│   ├── swarm/                     # Multi-robot swarm coordination
│   │   ├── peer.py                # Peer discovery + mesh networking
│   │   ├── coordinator.py         # Swarm coordinator
│   │   ├── events.py              # Swarm event bus
│   │   ├── consensus.py           # Distributed consensus
│   │   ├── patch_sync.py          # Synchronized patch rollout
│   │   └── shared_memory.py       # Shared swarm memory
│   │
│   ├── safety/                    # Defense-in-depth safety system
│   │   ├── base.py
│   │   ├── protocol.py            # Safety command execution protocol
│   │   ├── authorization.py       # Work approval/denial system
│   │   ├── state.py               # Safety state machine
│   │   ├── monitor.py             # Real-time safety monitor
│   │   ├── anti_subversion.py     # Prompt injection defense
│   │   └── bounds.py              # Physical bounds enforcement
│   │
│   ├── rcan/                      # RCAN spec core implementation
│   │   ├── capabilities.py        # Capability declarations
│   │   ├── jwt_auth.py            # JWT token auth
│   │   ├── mdns.py                # mDNS peer discovery
│   │   ├── message.py             # RCAN message types
│   │   ├── rbac.py                # Role-based access control
│   │   ├── router.py              # Message routing
│   │   └── ruri.py                # RCAN URI handling
│   │
│   ├── fs/                        # Virtual filesystem for state/memory
│   │   ├── __init__.py            # CastorFS main class
│   │   ├── context.py
│   │   ├── memory.py
│   │   ├── namespace.py
│   │   ├── permissions.py
│   │   ├── proc.py
│   │   └── safety.py
│   │
│   ├── [Infrastructure modules]
│   │   ├── daemon.py              # Systemd auto-start service
│   │   ├── connectivity.py        # Internet/provider connectivity
│   │   ├── offline_fallback.py    # Auto-fallback when offline
│   │   ├── healthcheck.py         # Health check system
│   │   ├── watchdog.py            # Process watchdog
│   │   ├── battery.py             # Battery monitoring
│   │   ├── hardware_detect.py     # Automatic peripheral detection
│   │   ├── peripherals.py         # Peripheral API
│   │   ├── telemetry.py           # Telemetry collection
│   │   ├── logs.py                # Logging system
│   │   ├── runtime_stats.py       # Runtime statistics
│   │   ├── crash.py               # Crash handler
│   │   └── backup.py              # Config backup/restore
│   │
│   ├── [Specialized features]
│   │   ├── tiered_brain.py        # Tiered brain (reactive→fast→planner)
│   │   ├── claude_proxy.py        # Claude OAuth proxy integration
│   │   ├── prompt_cache.py        # Prompt caching for cost reduction
│   │   ├── hailo_vision.py        # Hailo-8 NPU support
│   │   ├── geofence.py            # Geofencing controls
│   │   ├── approvals.py           # Command approval system
│   │   ├── audit.py               # Tamper-evident audit logging
│   │   ├── conformance.py         # RCAN conformance checking
│   │   ├── privacy.py             # Privacy controls
│   │   ├── doctor.py              # System diagnostics
│   │   ├── hub.py                 # Community recipe hub
│   │   ├── fleet.py               # Multi-robot fleet management
│   │   └── discover.py            # mDNS peer discovery
│   │
│   └── [CLI modules: doctor, demo, shell, repl, record, replay, watch,
│         configure, install_service, calibrate, benchmark, export, lint,
│         migrate, diff, profile, learn, update_check, ...]
│
├── config/
│   └── presets/                   # 16 hardware preset RCAN configs
│       ├── rpi_rc_car.rcan.yaml           # Recommended starter
│       ├── waveshare_alpha.rcan.yaml
│       ├── adeept_generic.rcan.yaml
│       ├── amazon_kit_generic.rcan.yaml
│       ├── sunfounder_picar.rcan.yaml
│       ├── dynamixel_arm.rcan.yaml
│       ├── arduino_l298n.rcan.yaml
│       ├── esp32_generic.rcan.yaml
│       ├── elegoo_tumbller.rcan.yaml
│       ├── freenove_4wd.rcan.yaml
│       ├── lego_mindstorms_ev3.rcan.yaml
│       ├── lego_spike_prime.rcan.yaml
│       ├── makeblock_mbot.rcan.yaml
│       ├── yahboom_rosmaster.rcan.yaml
│       ├── vex_iq.rcan.yaml
│       ├── cytron_maker_pi.rcan.yaml
│       └── rcan.schema.json               # RCAN validation schema
│
├── community-recipes/             # 7 community robot recipes
│   ├── index.json
│   ├── classroom-assistant-a1c3f7/
│   ├── garden-monitor-f2a8c4/
│   ├── llama-farm-scout-b4d2e8/
│   ├── pet-companion-b7d1e6/
│   ├── picar-home-patrol-e7f3a1/
│   ├── research-data-collector-c9f4a3/
│   └── warehouse-inventory-d5e9b2/
│
├── tests/                         # 2,233+ tests (60+ files)
│   ├── test_*.py                  # Core, API, providers, channels, drivers
│   ├── test_agents/               # Agent orchestration tests
│   ├── test_integration/          # End-to-end tests
│   ├── test_learner/              # Sisyphus loop stage tests
│   ├── test_specialists/          # Specialist agent tests
│   └── test_swarm/                # Multi-robot swarm tests
│
├── docs/
│   ├── hardware-guide.md
│   ├── peripherals.md
│   ├── safety-audit-report.md
│   └── community/
│
├── scripts/
│   ├── install.sh                 # One-line installer for RPi/Linux
│   └── sync-version.py
│
├── brand/                         # Logo variants (badge, flat-solid, geometric, neural-gradient)
├── site/                          # Static landing page (Cloudflare Pages)
├── .github/
│   ├── workflows/
│   │   ├── ci.yml                 # Full CI pipeline
│   │   ├── validate_rcan.yml      # RCAN spec validation
│   │   ├── release.yml
│   │   └── deploy-pages.yml
│   └── scripts/validate_rcan.py
├── .env.example                   # Environment variable template
├── .pre-commit-config.yaml        # Pre-commit hooks
├── pyproject.toml                 # Python packaging (pip install -e .)
├── requirements.txt               # Core dependencies
├── Dockerfile                     # Container image
├── docker-compose.yml             # 2 services: main + gateway profile
├── CHANGELOG.md                   # Version history
├── CONTRIBUTING.md                # How to add providers/drivers/channels
├── CONTRIBUTING-RECIPES.md        # Recipe contribution guide
├── SECURITY.md                    # Security policy
├── wrangler.toml                  # Cloudflare Pages config
└── README.md
```

## Architecture

```
[ WhatsApp / Telegram / Discord / Slack ]   <-- Messaging Channels (castor/channels/)
                    |
            [ API Gateway ]                  <-- FastAPI (castor/api.py)
                    |
         [ Safety Layer ]                   <-- castor/safety/ (anti-subversion, bounds)
                    |
   [ Tiered Brain: Reactive→Fast→Planner ]  <-- castor/tiered_brain.py
                    |
  [ Claude / Gemini / GPT / Ollama / ... ]  <-- Provider Layer (castor/providers/)
                    |
              [ RCAN Config ]               <-- Validation (config/presets/*.rcan.yaml)
                    |
        [ Dynamixel / PCA9685 ]             <-- Driver Layer (castor/drivers/)
                    |
              [ Your Robot ]               <-- The Body
```

### Core Abstractions

- **`Thought`** (`castor/providers/base.py`): Hardware-agnostic AI reasoning step. Contains `raw_text` and `action` (parsed JSON dict).
- **`BaseProvider`** (`castor/providers/base.py`): ABC for LLM adapters. Key method: `think(image_bytes, instruction) -> Thought`.
- **`DriverBase`** (`castor/drivers/base.py`): ABC for hardware drivers. Methods: `move()`, `stop()`, `close()`.
- **`BaseChannel`** (`castor/channels/base.py`): ABC for messaging integrations. Methods: `start()`, `stop()`, `send_message()`.
- **`BaseAgent`** (`castor/agents/base.py`): ABC for orchestration agents.
- **`ComponentRegistry`** (`castor/registry.py`): Plugin system for providers, drivers, and channels.
- **Factory functions**: `get_provider()` (providers), `get_driver()` (drivers), `create_channel()` (channels).

### Plugin / Registry System (`castor/registry.py`)

`ComponentRegistry` is the central plugin registry. All built-in providers, drivers, and channels are registered at startup. Third-party plugins can register new components without modifying core files. `castor/plugins.py` handles discovery and loading of external plugins.

### Authentication (`castor/auth.py`)

Credentials are resolved in priority order:
1. **Shell environment** (already exported vars win)
2. **`~/.opencastor/env`** file
3. **`.env` file** (loaded via python-dotenv)
4. **RCAN config fallback** (e.g. `config["api_key"]`)

Key functions:
- `resolve_provider_key(provider, config)` - Get API key for a provider
- `resolve_channel_credentials(channel, config)` - Get all creds for a channel
- `list_available_providers()` / `list_available_channels()` - Status maps
- `check_provider_ready()` / `check_channel_ready()` - Readiness booleans

### API Gateway (`castor/api.py`)

FastAPI server with multi-layer auth:
- `OPENCASTOR_JWT_SECRET` → JWT verification (RCAN protocol)
- `OPENCASTOR_API_TOKEN` → Static bearer token
- Neither set → Open access

Endpoints:
- `GET /health` - Health check + uptime
- `GET /api/status` - Runtime status, active providers/channels
- `POST /api/command` - Send instruction + base64 image, get action
- `POST /api/action` - Direct motor command (bypass brain)
- `POST /api/stop` - Emergency stop
- `GET /api/whatsapp/status` - WhatsApp (neonize) connection status
- `POST /webhooks/whatsapp` - Twilio WhatsApp incoming webhook (legacy)
- `POST /webhooks/slack` - Slack Events API fallback
- SSE endpoints for real-time telemetry streaming

### Channel System (`castor/channels/`)

All channels follow the same pattern:
- Constructor takes config dict + `on_message` callback
- SDKs are lazily imported (graceful degradation if not installed)
- `handle_message()` forwards to the brain and returns the reply
- `ChannelSessionStore` (`session.py`) routes messages across active channels

| Channel | SDK | Auth Env Vars |
|---------|-----|---------------|
| WhatsApp | `neonize` | None (QR code scan) |
| WhatsApp (Twilio) | `twilio` | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_NUMBER` |
| Telegram | `python-telegram-bot` | `TELEGRAM_BOT_TOKEN` |
| Discord | `discord.py` | `DISCORD_BOT_TOKEN` |
| Slack | `slack-bolt` | `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET` |

### Perception-Action Loop (`castor/main.py`)

Continuous OODA loop with auto hardware detection at startup:
1. **OBSERVE** - Capture frame (OpenCV USB / picamera2 CSI / OAK-D stereo / Hailo NPU)
2. **ORIENT & DECIDE** - Send frame + instruction to LLM provider via `brain.think()`
3. **ACT** - Translate `Thought.action` into motor commands via driver
4. **TELEMETRY** - Check latency against configurable budget, record episode

Hardware auto-detection runs at startup (`apply_hardware_overrides()`): camera type (OAK-D > RealSense > USB > CSI), PCA9685 I2C address, Hailo-8 NPU.

### Tiered Brain Architecture (`castor/tiered_brain.py`)

Three-layer routing:
- **Layer 1 (Reactive, <1ms)** — Rule-based reflexes (obstacle, e-stop)
- **Layer 2 (Fast, ~500ms)** — Local/small model (Ollama, llama.cpp, MLX)
- **Layer 3 (Planner, ~12s)** — Cloud LLM for complex reasoning (Claude, Gemini, GPT)

### Self-Improving Loop (`castor/learner/`)

Sisyphus pattern — 4 autonomous stages after each episode:
1. **PM stage** (`pm_stage.py`) — Analyze failures, identify root cause
2. **Dev stage** (`dev_stage.py`) — Generate code patches
3. **QA stage** (`qa_stage.py`) — Verify patches pass tests
4. **Apply stage** (`apply_stage.py`) — Roll out with automatic rollback on regression

Episodes are stored via `episode_store.py`; `alma.py` consolidates learning across episodes.

### Safety System (`castor/safety/`)

Defense-in-depth with multiple independent layers:
- **`anti_subversion.py`** — Prompt injection defense, blocks attempts to override safety rules
- **`bounds.py`** — Physical bounds enforcement (speed limits, geofencing, tilt limits)
- **`authorization.py`** — Work approval/denial; requires explicit operator sign-off for risky commands
- **`state.py`** — Safety state machine (nominal → caution → e-stop)
- **`monitor.py`** — Real-time safety monitoring with configurable thresholds
- **`protocol.py`** — Safety command execution protocol (atomic, audited)

### Provider Pattern

- Constructor resolves API key from env first, then config
- `think()` encodes image as base64 (OpenAI/Anthropic) or raw bytes (Google)
- System prompt forces strict JSON output only
- `_clean_json()` strips markdown fences from responses
- Providers degrade gracefully if SDK not installed

### Driver Pattern

- Hardware SDKs imported in try/except with module-level `HAS_<NAME>` boolean
- Drivers degrade to mock mode when SDK is missing (log actions, no physical output)
- Values clamped to safe physical ranges

## CLI Commands

```bash
# Core
castor run      --config robot.rcan.yaml             # Perception-action loop
castor run      --config robot.rcan.yaml --simulate  # Without hardware
castor gateway  --config robot.rcan.yaml             # API gateway + channels
castor wizard                                         # Interactive terminal setup
castor dashboard                                      # Streamlit web UI
castor status                                         # Provider/channel readiness

# Diagnostics
castor doctor                                         # Full system diagnostics + auto-fix hints
castor test-hardware                                  # Motor/sensor tester
castor calibrate                                      # Hardware calibration
castor benchmark                                      # Performance profiling
castor logs                                           # View/filter runtime logs
castor watch                                          # Live telemetry dashboard

# Config management
castor configure                                      # Interactive config editor
castor lint     --config robot.rcan.yaml             # Deep config validation
castor diff     a.rcan.yaml b.rcan.yaml              # Config diffing
castor migrate  --config robot.rcan.yaml             # Version migration
castor export   --config robot.rcan.yaml             # Bundle config for sharing
castor backup                                         # Backup configs
castor restore                                        # Restore from backup

# Recording
castor record   --config robot.rcan.yaml             # Record session to episode
castor replay   --episode <id>                        # Replay a recorded episode

# Advanced
castor improve                                        # Trigger self-improvement loop
castor agents                                         # List/manage active agents
castor fleet                                          # Multi-robot fleet status
castor hub      list/install/share                    # Community recipe hub
castor shell                                          # Interactive command shell
castor repl                                           # Python REPL with robot context
castor learn                                          # Interactive tutorial
castor install-service                                # Install systemd auto-start
castor update-check                                   # Check for new versions
```

Also available as Python modules:
```bash
python -m castor.main --config robot.rcan.yaml
python -m castor.api --config robot.rcan.yaml
python -m castor.wizard
streamlit run castor/dashboard.py
```

## Environment Variables

Copy `.env.example` to `.env` and fill in what you need. Secrets also load from `~/.opencastor/env`.

### AI Providers
| Variable | Provider |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic Claude (Opus 4.6 recommended) |
| `GOOGLE_API_KEY` | Google Gemini |
| `OPENAI_API_KEY` | OpenAI GPT-4.1 |
| `OPENROUTER_API_KEY` | OpenRouter (multi-model) |
| `OLLAMA_BASE_URL` | Local Ollama (no key, default: http://localhost:11434) |

### Messaging Channels
| Variable | Channel |
|---|---|
| *(none -- QR code scan)* | WhatsApp (neonize) |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_NUMBER` | WhatsApp (Twilio, legacy) |
| `TELEGRAM_BOT_TOKEN` | Telegram |
| `DISCORD_BOT_TOKEN` | Discord |
| `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET` | Slack |

### Gateway & Runtime
| Variable | Purpose |
|---|---|
| `OPENCASTOR_API_TOKEN` | Static bearer token (generate: `openssl rand -hex 32`) |
| `OPENCASTOR_JWT_SECRET` | JWT secret for RCAN protocol auth |
| `OPENCASTOR_API_HOST` | Bind address (default: 0.0.0.0) |
| `OPENCASTOR_API_PORT` | Port (default: 8000) |
| `OPENCASTOR_CONFIG` | Config file path (default: `config/presets/rpi_rc_car.rcan.yaml`) |
| `OPENCASTOR_CORS_ORIGINS` | CORS allowed origins (default: `*`) |
| `DYNAMIXEL_PORT` | Serial port override |
| `CAMERA_INDEX` | Camera device (default: 0) |
| `LOG_LEVEL` | Logging level |

## Dependencies

### Core (always installed)
- **Brain**: `anthropic`, `google-generativeai`, `openai`, `huggingface-hub`
- **Body**: `pyserial`
- **Eyes**: `opencv-python-headless`
- **Voice**: `gTTS`, `pygame`
- **Config**: `pyyaml`, `jsonschema`, `requests`
- **Gateway**: `fastapi`, `uvicorn[standard]`, `python-dotenv`, `httpx`, `python-multipart`
- **Dashboard**: `streamlit`, `SpeechRecognition`
- **CLI**: `rich`, `argcomplete`

### Optional extras (install via pyproject.toml)
```bash
pip install opencastor[rpi]            # picamera2, adafruit PCA9685, neonize
pip install opencastor[whatsapp]       # neonize (QR code scan)
pip install opencastor[whatsapp-twilio] # twilio (legacy)
pip install opencastor[telegram]       # python-telegram-bot
pip install opencastor[discord]        # discord.py
pip install opencastor[slack]          # slack-bolt
pip install opencastor[channels]       # All messaging channels
pip install opencastor[rcan]           # PyJWT, zeroconf (mDNS)
pip install opencastor[dynamixel]      # dynamixel-sdk
pip install opencastor[all]            # Everything
pip install opencastor[dev]            # pytest, pytest-asyncio, pytest-cov, ruff, qrcode
```

## Configuration (RCAN)

- All robot configs use the `.rcan.yaml` extension
- Configs follow the [RCAN Spec schema](https://rcan.dev/spec/)
- Required top-level keys: `rcan_version`, `metadata`, `agent`, `physics`, `drivers`, `network`, `rcan_protocol`
- Presets live in `config/presets/` (16 presets covering RPi RC cars, LEGO, VEX, Arduino, ESP32, Dynamixel arms, etc.)
- Schema: `config/presets/rcan.schema.json`
- The wizard (`castor wizard`) generates new configs interactively and saves API keys to `.env`

## Docker

```bash
# Default: main app (gateway + API)
docker compose up

# Optional gateway service
docker compose --profile gateway up
```

The `docker-compose.yml` uses `env_file: .env` so secrets stay out of the compose file. Two services defined: `opencastor` (default) and `gateway` (profile).

## CI/CD

- **CI** (`.github/workflows/ci.yml`): Full test suite on push/PR, Python 3.10–3.12
- **RCAN Validation** (`.github/workflows/validate_rcan.yml`): Validates all `*.rcan.yaml` against JSON schema
- **Release** (`.github/workflows/release.yml`): PyPI publish on tag
- **Pages** (`.github/workflows/deploy-pages.yml`): `site/` → Cloudflare Pages via `wrangler.toml`

## Code Style

- **PEP 8** with 100-char line length (enforced by Ruff)
- **snake_case** for functions/variables
- **Type hints** on public method signatures
- **Docstrings** on classes and non-trivial methods
- **Lazy imports** for optional SDKs with `HAS_<NAME>` boolean guards
- **Structured logging**: `logging.getLogger("OpenCastor.<Module>")`
- **Linting**: `ruff check castor/` / `ruff format castor/`
- **Pre-commit**: `.pre-commit-config.yaml` enforces style on commit

## Testing

Tests go in `tests/`, mirroring the `castor/` package structure. 2,233+ tests across 60+ files.

```bash
pip install -e ".[dev]"
pytest tests/
pytest tests/ --cov=castor --cov-report=html  # with coverage
```

Key test areas: CLI (200+ tests), API endpoints, providers (mocked), channels, drivers, safety invariants, anti-subversion, swarm, learner stages, integration.

## Adding New Components

### New AI Provider
1. Create `castor/providers/<name>_provider.py`, subclass `BaseProvider`
2. Implement `__init__` (resolve key from env then config) and `think(image_bytes, instruction) -> Thought`
3. Register in `castor/providers/__init__.py` (`_builtin_get_provider()` routing + `__all__`)
4. Add env var mapping to `castor/auth.py` `PROVIDER_AUTH_MAP`
5. Add SDK to `pyproject.toml` optional dependencies and `requirements.txt` (commented)
6. Add env var to `.env.example`

### New Hardware Driver
1. Create `castor/drivers/<name>.py`, subclass `DriverBase`
2. Implement `move()`, `stop()`, `close()` with mock fallback (try/except + `HAS_<NAME>`)
3. Register in `get_driver()` in `castor/drivers/__init__.py`
4. Add SDK to `pyproject.toml` optional dependencies

### New Messaging Channel
1. Create `castor/channels/<name>.py`, subclass `BaseChannel`
2. Implement `start()`, `stop()`, `send_message()`
3. Register in `castor/channels/__init__.py` (`_register_builtin_channels()`)
4. Add env vars to `castor/auth.py` `CHANNEL_AUTH_MAP` and `.env.example`
5. Add SDK to `pyproject.toml` optional dependencies
6. Add webhook endpoint to `castor/api.py` if needed

### New Hardware Preset
1. Create `config/presets/<name>.rcan.yaml`
2. Follow the RCAN schema (see `config/presets/rcan.schema.json`)
3. CI validates automatically on push

### New Community Recipe
1. Use `castor hub share --submit` to package and submit
2. See `CONTRIBUTING-RECIPES.md` for the full format
3. Recipes live in `community-recipes/`

See `CONTRIBUTING.md` for full examples and templates.

## Safety Considerations

- **Anti-subversion** (`castor/safety/anti_subversion.py`): Detects and blocks prompt injection attempts
- **Physical bounds** (`castor/safety/bounds.py`): Speed limits, tilt limits, geofencing
- **Authorization** (`castor/safety/authorization.py`): Risky commands require explicit operator approval
- **Audit chain** (`castor/audit.py`): Tamper-evident log of all commands and outcomes
- **Emergency stop**: Dashboard button, `POST /api/stop`, any channel message, or safety monitor trigger
- **Driver clamping**: Values clamped to safe physical ranges (e.g., Dynamixel: 0-4095 ticks)
- **`safety_stop: true`** in RCAN config enables hardware-level emergency stop
- **Latency budgets**: Configurable `latency_budget_ms` per RCAN config
- **Bearer/JWT auth**: API protected by `OPENCASTOR_API_TOKEN` or `OPENCASTOR_JWT_SECRET`
- **`.env` in `.gitignore`**: Secrets never committed
- **Graceful shutdown**: All drivers call `close()` in finally blocks

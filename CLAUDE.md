# CLAUDE.md - OpenCastor Development Guide

## Project Overview

OpenCastor is a universal runtime for embodied AI. It connects LLM "brains" (Gemini, GPT-4.1, Claude, Ollama, HuggingFace, llama.cpp, MLX) to robot "bodies" (Raspberry Pi, Jetson, Arduino, ESP32, LEGO) through a plug-and-play architecture, and exposes them to messaging platforms (WhatsApp, Telegram, Discord, Slack) for remote control. Configuration is driven by YAML files compliant with the [RCAN Standard](https://rcan.dev/spec/).

**Version**: 2026.2.21.13
**License**: Apache 2.0
**Python**: 3.10+

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

## Repository Structure

```
OpenCastor/
├── castor/                           # Main Python package (~231 Python files)
│   ├── __init__.py                   # Version string (__version__)
│   ├── __main__.py                   # Package entry point
│   ├── cli.py                        # Unified CLI entry point (48+ commands)
│   ├── main.py                       # Core runtime: perception-action loop
│   ├── api.py                        # FastAPI gateway server (all REST endpoints)
│   ├── api_errors.py                 # Structured JSON error handling for API
│   ├── auth.py                       # Unified auth manager (providers + channels)
│   ├── wizard.py                     # Interactive setup wizard
│   ├── web_wizard.py                 # Web-based configuration wizard
│   ├── dashboard.py                  # Streamlit web UI (single-page: status + episode history)
│   ├── dashboard_tui.py              # Terminal UI dashboard (tmux-based, preferred)
│   ├── config_validation.py          # RCAN config validation (fail-fast on startup)
│   ├── connectivity.py               # Internet & provider reachability checks
│   ├── offline_fallback.py           # Auto-switch to local provider on connectivity loss
│   ├── provider_fallback.py          # Auto-switch on quota/credit errors (ProviderFallbackManager)
│   ├── tiered_brain.py               # Multi-model switching by latency budget
│   ├── prompt_cache.py               # LLM response caching (reduces API cost)
│   ├── healthcheck.py                # Component health checks
│   ├── hardware_detect.py            # Auto-detect cameras and drivers
│   ├── hailo_vision.py               # Hailo-8 edge accelerator integration
│   ├── registry.py                   # Component registry
│   ├── crash.py                      # Crash handler
│   ├── watchdog.py                   # System health monitor + crash recovery
│   ├── telemetry.py                  # Performance metrics, memory usage tracking
│   ├── runtime_stats.py              # Runtime statistics
│   ├── battery.py                    # Battery monitoring
│   ├── geofence.py                   # Geofencing utilities
│   ├── peripherals.py                # Peripheral device management
│   ├── fleet.py                      # Multi-robot fleet management
│   ├── hub.py                        # Model hub integration
│   ├── plugins.py                    # Plugin system
│   ├── profiles.py                   # User profile management
│   ├── daemon.py                     # systemd service management
│   ├── audit.py                      # Audit logging
│   ├── approvals.py                  # Work approval workflow
│   ├── privacy.py                    # Privacy / data deletion utilities
│   ├── schedule.py                   # Task scheduling
│   ├── network.py                    # Network utilities
│   ├── backup.py / restore.py        # Config backup & restore
│   ├── export.py                     # Config bundle export
│   ├── migrate.py                    # RCAN config migration
│   ├── diff.py                       # Config diff viewer
│   ├── lint.py                       # Deep config validation
│   ├── conformance.py                # RCAN conformance checking
│   ├── configure.py                  # Configuration CLI helpers
│   ├── upgrade.py                    # Self-update + doctor
│   ├── fix.py                        # Auto-fix common issues
│   ├── update_check.py               # Version update checking
│   ├── record.py                     # Episode recording
│   ├── learn.py                      # Interactive learning tutorial
│   ├── demo.py                       # Cinematic terminal demo
│   ├── repl.py                       # Python REPL with robot objects
│   ├── shell.py                      # Interactive command shell
│   ├── watch.py                      # Live Rich TUI telemetry (episode memory panel)
│   ├── logs.py                       # Log viewing utilities
│   ├── benchmark.py                  # Performance profiling
│   ├── calibrate.py                  # Interactive hardware calibration
│   ├── test_hardware.py              # Hardware testing CLI
│   ├── memory.py                     # SQLite episode store (EpisodeMemory; CASTOR_MEMORY_DB)
│   ├── metrics.py                    # Prometheus-compatible metrics (MetricsRegistry; stdlib only)
│   ├── tools.py                      # LLM tool calling registry (ToolRegistry, ToolDefinition)
│   ├── memory_search.py              # Memory search utilities
│   ├── claude_proxy.py               # Claude API proxy
│   │
│   ├── providers/                    # LLM provider adapters
│   │   ├── __init__.py               # get_provider() factory
│   │   ├── base.py                   # BaseProvider ABC + Thought class
│   │   ├── google_provider.py        # Google Gemini
│   │   ├── openai_provider.py        # OpenAI GPT-4.1
│   │   ├── anthropic_provider.py     # Anthropic Claude
│   │   ├── ollama_provider.py        # Local Ollama
│   │   ├── huggingface_provider.py   # HuggingFace Hub
│   │   ├── llamacpp_provider.py      # llama.cpp local inference
│   │   └── mlx_provider.py           # Apple MLX acceleration
│   │
│   ├── drivers/                      # Hardware driver implementations
│   │   ├── __init__.py
│   │   ├── base.py                   # DriverBase ABC (move/stop/close/health_check)
│   │   ├── pca9685.py                # I2C PWM motor driver (Amazon/Adafruit kits)
│   │   ├── dynamixel.py              # Robotis Dynamixel servo (Protocol 2.0)
│   │   └── composite.py              # CompositeDriver: routes action keys to sub-drivers
│   │
│   ├── channels/                     # Messaging channel integrations
│   │   ├── __init__.py               # Channel registry + create_channel() factory
│   │   ├── base.py                   # BaseChannel ABC
│   │   ├── session.py                # Session management
│   │   ├── whatsapp.py               # Re-export (defaults to neonize)
│   │   ├── whatsapp_neonize.py       # WhatsApp via neonize (QR code scan)
│   │   ├── whatsapp_twilio.py        # WhatsApp via Twilio (legacy)
│   │   ├── telegram_channel.py       # Telegram Bot (long-polling)
│   │   ├── discord_channel.py        # Discord Bot
│   │   ├── slack_channel.py          # Slack Bot (Socket Mode)
│   │   └── mqtt_channel.py           # MQTT (paho-mqtt; subscribe/publish topics)
│   │
│   ├── fs/                           # Virtual Filesystem (Unix-inspired)
│   │   ├── __init__.py               # CastorFS facade class
│   │   ├── namespace.py              # Hierarchical namespace (/dev, /etc, /proc, etc.)
│   │   ├── permissions.py            # PermissionTable, ACL, Cap (capabilities)
│   │   ├── safety.py                 # SafetyLayer (bounds, rate limiting, e-stop)
│   │   ├── memory.py                 # MemoryStore (episodic, semantic, procedural)
│   │   ├── context.py                # ContextWindow (multi-turn), Pipeline (Unix pipes)
│   │   └── proc.py                   # ProcFS (read-only runtime introspection)
│   │
│   ├── safety/                       # Safety & authorization subsystem
│   │   ├── __init__.py
│   │   ├── anti_subversion.py        # Input scanning (check_input_safety, ScanVerdict)
│   │   ├── authorization.py          # WorkAuthority, WorkOrder, audit log
│   │   ├── bounds.py                 # BoundsChecker (joint, force, workspace)
│   │   ├── monitor.py                # Continuous safety monitoring
│   │   ├── protocol.py               # Safety protocol definitions
│   │   └── state.py                  # SafetyStateSnapshot, SafetyTelemetry
│   │
│   ├── rcan/                         # RCAN protocol implementation
│   │   ├── __init__.py
│   │   ├── ruri.py                   # RURI addressing (rcan://domain.name.id)
│   │   ├── message.py                # RCANMessage envelope, MessageType, Priority
│   │   ├── rbac.py                   # RCANRole (CREATOR→GUEST), Scope, RCANPrincipal
│   │   ├── router.py                 # MessageRouter (dispatch RCAN messages)
│   │   ├── capabilities.py           # Capability, CapabilityRegistry
│   │   ├── jwt_auth.py               # RCANTokenManager (JWT sign/verify)
│   │   └── mdns.py                   # mDNS robot discovery (optional)
│   │
│   ├── agents/                       # Multi-agent framework
│   │   ├── __init__.py
│   │   ├── base.py                   # BaseAgent ABC, AgentStatus
│   │   ├── shared_state.py           # SharedState (pub/sub event bus)
│   │   ├── registry.py               # AgentRegistry (lifecycle management)
│   │   ├── observer.py               # ObserverAgent (scene understanding)
│   │   ├── navigator.py              # NavigatorAgent (path planning)
│   │   ├── manipulator_agent.py      # ManipulatorAgent (arm/gripper)
│   │   ├── communicator.py           # CommunicatorAgent (NL intent routing)
│   │   ├── guardian.py               # GuardianAgent (safety meta-agent, veto + e-stop)
│   │   └── orchestrator.py           # OrchestratorAgent (master, single RCAN output)
│   │
│   ├── specialists/                  # Task specialist agents
│   │   ├── __init__.py
│   │   ├── base_specialist.py        # BaseSpecialist ABC, Task, TaskResult
│   │   ├── scout.py                  # ScoutSpecialist (visual exploration)
│   │   ├── manipulator.py            # ManipulatorSpecialist (grasping)
│   │   ├── dock.py                   # DockSpecialist (docking/charging)
│   │   ├── responder.py              # ResponderSpecialist (alert responses)
│   │   └── task_planner.py           # TaskPlanner (decompose → typed tasks)
│   │
│   ├── learner/                      # Self-improving loop (Sisyphus pattern)
│   │   ├── __init__.py
│   │   ├── episode.py                # Episode (observation/action/outcome)
│   │   ├── episode_store.py          # EpisodeStore (persistent JSON storage)
│   │   ├── sisyphus.py               # SisyphusLoop + ImprovementResult + SisyphusStats
│   │   ├── pm_stage.py               # PMStage (analyze episodes, find failures)
│   │   ├── dev_stage.py              # DevStage (propose patches)
│   │   ├── qa_stage.py               # QAStage (validate patches)
│   │   ├── apply_stage.py            # ApplyStage (deploy approved patches)
│   │   ├── patches.py                # Patch, ConfigPatch, PromptPatch, BehaviorPatch
│   │   └── alma.py                   # ALMAConsolidation (swarm patch aggregation)
│   │
│   └── swarm/                        # Multi-robot coordination
│       ├── __init__.py
│       ├── peer.py                   # SwarmPeer (remote robot proxy)
│       ├── coordinator.py            # SwarmCoordinator (task distribution)
│       ├── consensus.py              # SwarmConsensus (majority-vote protocol)
│       ├── events.py                 # SwarmEvent (pub/sub envelope)
│       ├── shared_memory.py          # SharedMemory (distributed key-value)
│       └── patch_sync.py             # PatchSync (incremental config sync)
│
├── config/
│   └── presets/                      # 16 hardware preset RCAN configs
│       ├── amazon_kit_generic.rcan.yaml
│       ├── adeept_generic.rcan.yaml
│       ├── waveshare_alpha.rcan.yaml
│       ├── sunfounder_picar.rcan.yaml
│       ├── dynamixel_arm.rcan.yaml
│       ├── rpi_rc_car.rcan.yaml
│       ├── arduino_l298n.rcan.yaml
│       ├── esp32_generic.rcan.yaml
│       ├── cytron_maker_pi.rcan.yaml
│       ├── elegoo_tumbller.rcan.yaml
│       ├── freenove_4wd.rcan.yaml
│       ├── lego_mindstorms_ev3.rcan.yaml
│       ├── lego_spike_prime.rcan.yaml
│       ├── makeblock_mbot.rcan.yaml
│       ├── vex_iq.rcan.yaml
│       └── yahboom_rosmaster.rcan.yaml
│
├── tests/                            # 96 test files, 2578 tests (0 failures)
│   ├── test_api_endpoints.py         # FastAPI gateway (133 tests)
│   ├── test_config_validation.py     # Config validation
│   ├── test_offline_fallback.py      # OfflineFallbackManager
│   ├── test_learner/                 # Sisyphus loop (12 test files)
│   ├── test_agents/                  # Agent framework (11 test files)
│   ├── test_swarm/                   # Multi-robot swarm (6 test files)
│   ├── test_fs/                      # Virtual filesystem
│   ├── test_safety/                  # Safety subsystem
│   ├── test_rcan/                    # RCAN protocol
│   ├── test_channels/                # Messaging channels
│   ├── test_providers/               # AI providers
│   └── test_drivers/                 # Hardware drivers
│
├── scripts/
│   ├── install.sh / install.ps1      # One-line installers (Linux/Windows)
│   ├── install-check.sh / .ps1       # Install verification
│   ├── uninstall.sh                  # Uninstaller
│   ├── start_dashboard.sh            # Kiosk mode tmux launcher
│   └── sync-version.py               # Keep version strings in sync
│
├── site/                             # Static landing page (Cloudflare Pages)
├── brand/                            # Brand assets (logos, badges)
├── .github/
│   ├── workflows/
│   │   ├── ci.yml                    # Tests + lint + type check
│   │   ├── validate_rcan.yml         # RCAN schema validation on *.rcan.yaml changes
│   │   ├── install-test.yml          # Multi-platform install verification (scheduled)
│   │   ├── release.yml               # PyPI release automation (on tag push)
│   │   ├── deploy-pages.yml          # Cloudflare Pages deploy (on push to main)
│   │   ├── auto-label.yml            # PR auto-labeling
│   │   ├── create_backlog_issues.yml # Backlog maintenance (scheduled)
│   │   └── stale.yml                 # Stale issue/PR management (scheduled)
│   └── scripts/validate_rcan.py
├── .env.example                      # Environment variable template
├── pyproject.toml                    # Python packaging (pip install -e .)
├── requirements.txt                  # Core Python dependencies
├── Dockerfile                        # Container with health check
├── docker-compose.yml                # Gateway + runtime + dashboard services
├── CONTRIBUTING.md                   # How to add providers/drivers/channels
├── wrangler.toml                     # Cloudflare Pages config
└── README.md
```

## Architecture

```
[ WhatsApp / Telegram / Discord / Slack ]     <-- Messaging Channels
                    |
            [ API Gateway ]                    <-- FastAPI (castor/api.py)
                    |
        ┌──────────────────────┐
        │   Safety Layer       │               <-- Anti-subversion, BoundsChecker
        └──────────────────────┘
                    |
    [ Gemini / GPT-4.1 / Claude / Ollama ]    <-- Brain (Provider Layer)
                    |
     ┌─────────────────────────────────┐
     │  Offline Fallback / Tiered Brain │      <-- Connectivity-aware routing
     └─────────────────────────────────┘
                    |
              [ RCAN Config ]                  <-- Spinal Cord (Validation)
                    |
    ┌───────────────────────────────┐
    │  VFS  │  Agents  │  Learner  │          <-- Runtime Subsystems
    └───────────────────────────────┘
                    |
        [ Dynamixel / PCA9685 ]               <-- Drivers (Nervous System)
                    |
              [ Your Robot ]                   <-- The Body
```

### Core Abstractions

- **`Thought`** (`castor/providers/base.py`): Hardware-agnostic AI reasoning step. Contains `raw_text` and `action` (parsed JSON dict).
- **`BaseProvider`** (`castor/providers/base.py`): ABC for LLM adapters. Key methods: `think(image_bytes, instruction) -> Thought`, `think_stream(image_bytes, instruction) -> Iterator[str]`, `health_check() -> dict`.
- **`DriverBase`** (`castor/drivers/base.py`): ABC for hardware drivers. Methods: `move()`, `stop()`, `close()`, `health_check() -> dict`.
- **`BaseChannel`** (`castor/channels/base.py`): ABC for messaging integrations. Methods: `start()`, `stop()`, `send_message()`.
- **`CastorFS`** (`castor/fs/__init__.py`): Virtual filesystem with Unix-style paths, capability-based permissions, memory tiers, and e-stop.
- **`SisyphusLoop`** (`castor/learner/sisyphus.py`): Orchestrates PM→Dev→QA→Apply continuous improvement. Tracks per-stage timing via `ImprovementResult.stage_durations` and `SisyphusStats`.
- **`EpisodeMemory`** (`castor/memory.py`): SQLite episode store; persists every brain decision. Max 10k episodes, FIFO eviction.
- **`MetricsRegistry`** (`castor/metrics.py`): Stdlib-only Prometheus metrics; `get_registry()` singleton; exposed at `GET /api/metrics`.
- **`ToolRegistry`** (`castor/tools.py`): Named LLM-callable tools; 4 built-ins; `call(name, /, **kwargs)`.
- **`CompositeDriver`** (`castor/drivers/composite.py`): Routes action keys to sub-drivers via RCAN `routing:` config.
- **Factory functions**: `get_provider()` (providers), `create_channel()` (channels).

### Authentication (`castor/auth.py`)

Credentials are resolved in priority order:
1. **Environment variable** (e.g. `GOOGLE_API_KEY`)
2. **`.env` file** (loaded via python-dotenv)
3. **RCAN config fallback** (e.g. `config["api_key"]`)

Key functions:
- `resolve_provider_key(provider, config)` — Get API key for a provider
- `resolve_channel_credentials(channel, config)` — Get all creds for a channel
- `list_available_providers()` / `list_available_channels()` — Status maps
- `check_provider_ready()` / `check_channel_ready()` — Readiness booleans

### API Gateway (`castor/api.py`)

FastAPI server providing:

**Health & Status:**
- `GET /health` — Health check (uptime, brain, driver, channels); used by Docker HEALTHCHECK
- `GET /api/status` — Runtime status, active providers/channels

**Command & Control:**
- `POST /api/command` — Send instruction to brain, receive `{raw_text, action}` (rate-limited 5/s/IP)
- `POST /api/command/stream` — NDJSON streaming of LLM tokens (uses `think_stream()`, falls back to `think()`)
- `POST /api/action` — Direct motor command (bypasses brain)
- `POST /api/stop` — Emergency stop
- `POST /api/estop/clear` — Clear emergency stop

**Driver:**
- `GET /api/driver/health` — Driver health check (`{ok, mode, error, driver_type}`); 503 if no driver

**Learner / Sisyphus:**
- `GET /api/learner/stats` — Sisyphus stats (`episodes_analyzed`, `avg_duration_ms`, …); `{available: false}` when not running
- `GET /api/learner/episodes` — Recent episodes from EpisodeStore (`?limit=N`, max 100)
- `POST /api/learner/episode` — Submit episode, optionally run improvement loop (`?run_improvement=true`)

**Command History:**
- `GET /api/command/history` — Last N instruction→thought→action pairs (ring buffer, max 50; `?limit=N`)

**Virtual Filesystem:**
- `POST /api/fs/read` / `POST /api/fs/write` — Read/write VFS paths
- `GET /api/fs/ls` / `GET /api/fs/tree` — Directory listing/tree view
- `GET /api/fs/proc` — Runtime introspection (/proc snapshot)
- `GET /api/fs/memory` — Query memory stores (episodic, semantic, procedural)
- `GET /api/fs/permissions` — Permission table dump

**Authentication & Security:**
- `POST /api/auth/token` — Issue JWT token (RCAN RBAC)
- `GET /api/auth/whoami` — Authenticated principal identity
- `GET /api/audit` — Audit log (work orders, approvals, denials)
- `GET /api/rbac` — RBAC roles and principals

**Streaming:**
- `GET /api/stream/mjpeg` — MJPEG live camera stream (max 3 concurrent)

**Metrics & Runtime Control:**
- `GET /api/metrics` — Prometheus-format text metrics (counters, gauges, histograms via `MetricsRegistry`)
- `POST /api/runtime/pause` — Pause the perception-action loop (sets VFS `/proc/paused` flag)
- `POST /api/runtime/resume` — Resume the perception-action loop
- `GET /api/runtime/status` — Loop running/paused state + loop count
- `POST /api/config/reload` — Hot-reload `robot.rcan.yaml` without restart
- `GET /api/provider/health` — Brain provider health check ({ok, latency_ms, error, usage_stats})

**Episode Memory:**
- `GET /api/memory/episodes` — Recent episodes from SQLite store (`?limit=N`, max 100)
- `GET /api/memory/export` — Export all episodes as JSONL download
- `DELETE /api/memory/episodes` — Clear all episode memory

**Webhooks (messaging channels):**
- `POST /webhooks/whatsapp` — Twilio WhatsApp (rate-limited 10/min/sender)
- `POST /webhooks/slack` — Slack Events API (rate-limited 10/min/sender)

Protected by optional `OPENCASTOR_API_TOKEN` (bearer) or `OPENCASTOR_JWT_SECRET` (JWT/RCAN). JWT is checked before the static token when both are configured.

### Channel System (`castor/channels/`)

| Channel | SDK | Auth Env Vars |
|---------|-----|---------------|
| WhatsApp (neonize) | `neonize>=0.3.10` | None (QR code scan) |
| WhatsApp (Twilio) | `twilio` | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_NUMBER` |
| Telegram | `python-telegram-bot>=21.0` | `TELEGRAM_BOT_TOKEN` |
| Discord | `discord.py>=2.3.0` | `DISCORD_BOT_TOKEN` |
| Slack | `slack-bolt>=1.18.0` | `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET` |
| MQTT | `paho-mqtt>=2.0.0` | `MQTT_BROKER_HOST`, `MQTT_USERNAME`, `MQTT_PASSWORD` |

All channel `handle_message()` dispatchers are async-safe: coroutine callbacks are awaited directly; sync callbacks are offloaded with `asyncio.to_thread()`.

### Perception-Action Loop (`castor/main.py`)

Continuous OODA loop:
1. **OBSERVE** — Capture camera frame via OpenCV
2. **ORIENT & DECIDE** — Send frame + instruction to LLM provider (safety-checked first)
3. **ACT** — Translate `Thought.action` into motor commands
4. **TELEMETRY** — Check latency vs budget; call `get_registry().record_loop(latency)` (Prometheus)
5. **MEMORY** — Call `EpisodeMemory().log_episode(...)` to persist observation→action in SQLite

Pause/resume: after e-stop check the loop reads a VFS `/proc/paused` flag; `POST /api/runtime/pause` sets it.

### Provider Pattern

- Constructor resolves API key from env → .env → config
- `think()` encodes image as base64 (OpenAI/Anthropic) or raw bytes (Google)
- Every `think()` call passes through `_check_instruction_safety()` first (prompt injection defense)
- System prompt forces strict JSON output only
- `_clean_json()` strips markdown fences from responses
- `think_stream()` yields text chunks; all providers implement it (Anthropic CLI path yields single chunk)
- `health_check()` returns `{ok: bool, latency_ms: float, error: str|None}`
- `get_usage_stats()` returns provider-specific token/cost stats (Anthropic and OpenAI implement it; base returns `{}`)

### Episode Memory (`castor/memory.py`)

- `EpisodeMemory` — SQLite-backed store; default DB at `~/.castor/memory.db`; override with `CASTOR_MEMORY_DB`
- Max 10,000 episodes; FIFO eviction when full
- Key methods: `log_episode(instruction, image_hash, thought, latency_ms)`, `query_recent(limit)`, `get_episode(id)`, `export_jsonl()`, `clear()`, `hash_image(bytes)`, `count()`
- Called in the perception-action loop after every brain decision

### Prometheus Metrics (`castor/metrics.py`)

- `MetricsRegistry` — stdlib-only Prometheus counter/gauge/histogram implementation (no external deps)
- `get_registry()` singleton; 13 pre-registered metrics including `loop_latency_ms`, `brain_calls_total`, `motor_commands_total`, `errors_total`
- Helper functions: `record_loop(latency_ms, robot)`, `record_command(action_type)`, `record_error(source)`, `update_status(running, paused)`
- Exposed at `GET /api/metrics` as Prometheus text format

### LLM Tool Calling (`castor/tools.py`)

- `ToolRegistry` — named callable tools the LLM brain can invoke
- 4 built-ins: `get_status`, `take_snapshot`, `announce_text`, `get_distance`
- `call(name, /, **kwargs)` — `name` is positional-only (Python 3.10+) to avoid keyword conflicts with tool parameters named `name`
- `call_from_dict(tool_call)` — handles OpenAI-style (JSON string `arguments`) and Anthropic-style (`input` dict)
- `to_openai_tools()` / `to_anthropic_tools()` — schema export for LLM function calling
- Register custom tools from RCAN `agent.tools` list via `_register_from_config()`

### Composite Driver (`castor/drivers/composite.py`)

- `CompositeDriver` — routes action dict keys to sub-drivers via RCAN `routing:` config
- Each sub-driver handles a specific action namespace (e.g., `wheels` → PCA9685, `arm` → Dynamixel)
- `_NullDriver` fallback for unknown protocols (logs + no-ops)
- `health_check()` aggregates sub-driver health; reports `"degraded"` if any sub-driver fails

### Driver Pattern

- Hardware SDKs imported in `try/except` with module-level `HAS_<NAME>` boolean
- Drivers degrade to mock mode when SDK is missing (log actions, no physical output)
- Values clamped to safe physical ranges
- `health_check()` returns `{ok: bool, mode: "hardware"|"mock", error: str|None}`

### Safety Subsystem (`castor/safety/`)

- **`check_input_safety(instruction, principal)`** — Scans every incoming instruction; returns `ScanVerdict.BLOCK` on prompt injection
- **`BaseProvider._check_instruction_safety()`** — Called at the top of every `think()` and `think_stream()`; returns a blocking `Thought` on BLOCK verdict
- **`BoundsChecker`** — Validates motor commands against joint/force/workspace limits
- **`WorkAuthority`** — Approves/denies `WorkOrder` requests with full audit trail
- **`GuardianAgent`** — Safety meta-agent that can veto actions and trigger e-stop

### Virtual Filesystem (`castor/fs/`)

Unix-inspired filesystem with:
- **Namespaces**: `/dev/motor`, `/etc/config`, `/var/log`, `/tmp`, `/proc`, `/mnt`
- **Capabilities**: `CAP_MOTOR_WRITE`, `CAP_ESTOP`, `CAP_SAFETY_OVERRIDE`
- **Memory tiers**: episodic (recorded episodes), semantic (facts/KB), procedural (behaviors)
- **ContextWindow**: sliding multi-turn context for agents
- **Pipeline**: Unix-pipe-style operation chaining
- **E-stop**: `fs.estop()` / `fs.clear_estop()` propagates to all drivers

### Provider Quota Fallback (`castor/provider_fallback.py`)

- `ProviderFallbackManager` — detects `ProviderQuotaError` from the primary provider and transparently switches to a backup
- Triggered by HuggingFace HTTP 402/429 or keywords (`credits`, `quota`, `rate limit`, etc.)
- `think()` wraps the primary and auto-retries with the fallback on quota error
- After `quota_cooldown_s` (default 3600s), the next request retries the primary automatically
- `probe_fallback()` health-checks the backup at startup so issues surface before a live outage
- `state.provider_fallback` takes priority over `state.offline_fallback` in `_get_active_brain()`

RCAN config::

    provider_fallback:
      enabled: true
      provider: ollama        # or: google | openai | anthropic | llamacpp | mlx
      model: llama3.2:3b
      quota_cooldown_s: 3600
      alert_channel: telegram

- `ProviderQuotaError` defined in `castor/providers/base.py`; has `provider_name` and `http_status` attrs

### Offline Fallback (`castor/offline_fallback.py`)

- `OfflineFallbackManager` monitors connectivity via `ConnectivityMonitor`
- On connectivity loss, auto-switches to local provider (Ollama, llama.cpp, MLX)
- Probes fallback provider health at startup
- Alerts via configured channel when switching
- Config block: `offline_fallback.enabled`, `.provider`, `.model`, `.check_interval_s`, `.alert_channel`
- Usage: `state.offline_fallback.get_active_provider().think(...)` instead of `state.brain.think(...)`

### RCAN Protocol (`castor/rcan/`)

- **RURI addressing**: `rcan://domain.robot-name.id` (e.g., `rcan://opencastor.my-bot.a1b2c3d4`)
- **RBAC**: 5 roles: `CREATOR > OWNER > LEASEE > USER > GUEST`
- **JWT auth**: `RCANTokenManager` (sign/verify; `POST /api/auth/token`)
- **mDNS discovery**: Optional, auto-discovers local robots
- **MessageRouter**: Dispatches `RCANMessage` envelopes by type and target RURI

### Multi-Agent Framework (`castor/agents/`)

- All agents inherit `BaseAgent` and communicate via `SharedState` pub/sub event bus
- `OrchestratorAgent` — master agent; resolves multi-agent input to a single RCAN action
- `GuardianAgent` — safety meta-agent; veto authority over all motor commands
- `ObserverAgent` — parses vision output, publishes detections
- `NavigatorAgent` — path planning (potential fields)
- `CommunicatorAgent` — routes NL intent from messaging channels
- `AgentRegistry` — spawns, monitors, and restarts agents

### Self-Improving Loop (`castor/learner/`)

4-stage Sisyphus cycle:
1. **Record** (`episode.py`, `episode_store.py`) — Save observation→action→outcome tuples
2. **PM** (`pm_stage.py`) — Analyze episodes, identify failure patterns
3. **Dev** (`dev_stage.py`) — Generate `ConfigPatch` / `PromptPatch` / `BehaviorPatch`
4. **QA** (`qa_stage.py`) — Validate patches; suggest retry or approve
5. **Apply** (`apply_stage.py`) — Deploy approved patches live

`SisyphusLoop.run_episode()` tracks per-stage timing in `ImprovementResult.stage_durations` (keys: `pm_ms`, `dev_ms_attempt0`, `qa_ms_attempt0`, `apply_ms`). `SisyphusStats.avg_duration_ms` averages across applied/rejected episodes.

### Swarm Coordination (`castor/swarm/`)

- `SwarmCoordinator` — distributes tasks across `SwarmPeer` robots
- `SwarmConsensus` — majority-vote protocol for shared decisions
- `SharedMemory` — distributed key-value store
- `PatchSync` — incremental config synchronization across robots
- `ALMAConsolidation` (`learner/alma.py`) — aggregates patches from multiple robots

## CLI Commands (48+)

```bash
# Core operations
castor run      --config robot.rcan.yaml             # Perception-action loop
castor run      --config robot.rcan.yaml --simulate  # Without hardware
castor gateway  --config robot.rcan.yaml             # API gateway + channels
castor wizard                                         # Interactive setup
castor wizard   --simple / --web                      # Minimal / browser wizard
castor dashboard                                      # tmux terminal dashboard
castor demo                                           # Cinematic demo (no hardware)
castor status                                         # Provider/channel readiness
castor doctor                                         # System health diagnostics

# Hardware
castor test-hardware                                  # Test individual motors
castor calibrate                                      # Interactive calibration
castor benchmark                                      # Performance profiling

# Configuration
castor configure                                      # Configuration CLI
castor validate                                       # RCAN conformance check
castor lint                                           # Deep config validation
castor migrate                                        # RCAN config migration
castor diff                                           # Config diff viewer
castor backup / restore                               # Backup and restore configs
castor export                                         # Export config bundle

# Development & debugging
castor shell                                          # Interactive command shell
castor repl                                           # Python REPL with robot objects
castor watch                                          # Live telemetry dashboard
castor logs                                           # View logs
castor fix                                            # Auto-fix common issues
castor test                                           # Run test suite
castor learn                                          # Interactive tutorial
castor quickstart                                     # Quick start guide
castor record / replay                                # Session recording/replay

# Advanced
castor improve  --enable/--disable/--episodes/--status  # Sisyphus self-improvement
castor agents   list/status/spawn                     # Agent management
castor fleet    status                                # Multi-robot status
castor token    [--create/--verify]                   # JWT token management
castor discover                                       # Auto-discover local robots
castor safety                                         # Safety controls
castor install-service                                # Generate systemd unit
castor upgrade                                        # Self-update + doctor
castor plugin(s)                                      # Plugin management
castor hub                                            # Model hub integration
castor login                                          # Authentication
castor privacy                                        # Privacy/data deletion
castor schedule / network / approvals / profile       # Misc utilities
castor update-check                                   # Version updates
```

Also available as Python modules:
```bash
python -m castor.main --config robot.rcan.yaml
python -m castor.api --config robot.rcan.yaml
python -m castor.wizard
```

## Environment Variables

Copy `.env.example` to `.env` and fill in what you need.

### AI Providers
| Variable | Provider |
|---|---|
| `GOOGLE_API_KEY` | Google Gemini |
| `OPENAI_API_KEY` | OpenAI GPT-4.1 |
| `ANTHROPIC_API_KEY` | Anthropic Claude |
| `OPENROUTER_API_KEY` | OpenRouter (multi-model) |
| `OLLAMA_BASE_URL` | Local Ollama (no key needed) |
| `GOOGLE_AUTH_MODE=adc` | Google Application Default Credentials |
| `HF_AUTH_MODE=cli` | HuggingFace CLI auth |

### Messaging Channels
| Variable | Channel |
|---|---|
| *(none — QR code scan)* | WhatsApp (neonize) |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_NUMBER` | WhatsApp (Twilio, legacy) |
| `TELEGRAM_BOT_TOKEN` | Telegram |
| `DISCORD_BOT_TOKEN` | Discord |
| `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET` | Slack |
| `MQTT_BROKER_HOST`, `MQTT_USERNAME`, `MQTT_PASSWORD` | MQTT |

### Gateway & Runtime
| Variable | Default | Purpose |
|---|---|---|
| `OPENCASTOR_API_TOKEN` | None | Bearer token for API auth (`openssl rand -hex 32`) |
| `OPENCASTOR_JWT_SECRET` | None | JWT signing secret (RCAN auth; checked before API_TOKEN) |
| `OPENCASTOR_CORS_ORIGINS` | `*` | Comma-separated allowed CORS origins (restrict in prod) |
| `OPENCASTOR_API_HOST` | 127.0.0.1 | Bind address |
| `OPENCASTOR_API_PORT` | 8000 | Port |
| `OPENCASTOR_COMMAND_RATE` | 5 | Max `/api/command` calls/sec/IP |
| `OPENCASTOR_WEBHOOK_RATE` | 10 | Max webhook calls/min/sender |
| `OPENCASTOR_MAX_STREAMS` | 3 | Max concurrent MJPEG clients |
| `OPENCASTOR_CONFIG` | robot.rcan.yaml | Config file path |
| `OPENCASTOR_MEMORY_DIR` | — | Memory persistence directory |
| `CASTOR_MEMORY_DB` | `~/.castor/memory.db` | SQLite episode memory database path |
| `DYNAMIXEL_PORT` | — | Serial port override |
| `CAMERA_INDEX` | 0 | Camera device index |
| `LOG_LEVEL` | INFO | Logging level |

## Dependencies

### Core (always installed)
- **Brain**: `google-generativeai`, `openai`, `anthropic`
- **Body**: `dynamixel-sdk`, `pyserial`
- **Eyes**: `opencv-python-headless`
- **Config**: `pyyaml`, `jsonschema`, `requests`
- **Gateway**: `fastapi`, `uvicorn`, `python-dotenv`, `httpx`
- **Dashboard**: `streamlit`, `SpeechRecognition`, `gTTS`
- **CLI**: `rich`

### Optional Extras (pyproject.toml)

```bash
pip install opencastor[rpi]             # RPi: PCA9685 + picamera2 + neonize
pip install opencastor[whatsapp]        # neonize==0.3.10 (QR code scan)
pip install opencastor[whatsapp-twilio] # twilio (legacy)
pip install opencastor[telegram]        # python-telegram-bot>=21.0
pip install opencastor[discord]         # discord.py>=2.3.0
pip install opencastor[slack]           # slack-bolt>=1.18.0
pip install opencastor[mqtt]            # paho-mqtt>=2.0.0
pip install opencastor[channels]        # All messaging channels (including mqtt)
pip install opencastor[rcan]            # PyJWT + zeroconf (RCAN protocol)
pip install opencastor[dynamixel]       # dynamixel-sdk>=3.7.31
pip install opencastor[all]             # Everything
pip install opencastor[dev]             # pytest, pytest-asyncio, ruff, qrcode
```

**Neonize version pin**: Always use `neonize==0.3.10`. Versions 0.3.14+ require `protobuf>=6.32.1` which conflicts with the system `protobuf 5.x`. Fix: `pip install "neonize==0.3.10" -q`.

Hardware-specific (RPi only): `adafruit-circuitpython-pca9685`, `adafruit-circuitpython-motor`, `busio`, `board`, `picamera2`

## Configuration (RCAN)

- All robot configs use the `.rcan.yaml` extension
- Configs follow the [RCAN Spec schema](https://rcan.dev/spec/)
- Required top-level keys: `rcan_version`, `metadata`, `agent`, `physics`, `drivers`, `network`, `rcan_protocol`
  - `metadata.robot_name` — required
  - `agent.model` — required
  - `drivers` — must be non-empty list
- Validated by `castor/config_validation.py` on gateway startup (`log_validation_result()`)
- 16 presets in `config/presets/`
- The wizard (`castor wizard`) generates new configs and saves API keys to `.env`

## Docker

```bash
docker compose up                                    # Gateway only
docker compose --profile hardware up                 # Gateway + hardware runtime
docker compose --profile dashboard up                # Gateway + Streamlit
docker compose --profile hardware --profile dashboard up  # Everything
```

The `docker-compose.yml` uses `env_file: .env` so secrets stay out of the compose file.

## CI/CD

| Workflow | Trigger | Purpose |
|---|---|---|
| `ci.yml` | Push, PR | Tests + ruff lint + type check |
| `validate_rcan.yml` | Push/PR on `*.rcan.yaml` | JSON schema validation |
| `install-test.yml` | Scheduled | Multi-platform install test |
| `release.yml` | Tag push | PyPI release automation |
| `deploy-pages.yml` | Push to main | Cloudflare Pages deploy |
| `stale.yml` | Scheduled | Stale issue/PR cleanup |

## Code Style

- **PEP 8** with 100-char line length (enforced by Ruff)
- **snake_case** for functions/variables
- **Type hints** on public method signatures
- **Docstrings** on classes and non-trivial methods
- **Lazy imports** for optional SDKs (hardware libs, channel SDKs)
- **Structured logging**: `logging.getLogger("OpenCastor.<Module>")`
- **Linting**: `ruff check castor/` / `ruff format castor/`

## Testing

Tests in `tests/`, mirroring `castor/` package structure.

```bash
pip install -e ".[dev]"
pytest tests/
```

Current: **2578 tests, 8 skipped, 0 failures**

Key fixture: `_reset_state_and_env` (autouse in `test_api_endpoints.py`) — resets all `AppState` fields before every test, including `thought_history = deque(maxlen=50)`, `learner = None`, `offline_fallback = None`, and clears `_command_history`/`_webhook_history` rate-limiter dicts.

## Adding New Components

### New AI Provider
1. Create `castor/providers/<name>_provider.py`, subclass `BaseProvider`
2. Implement `__init__` (resolve key), `think()`, `think_stream()`, `health_check()`
3. Call `self._check_instruction_safety(instruction)` at the top of `think()` and `think_stream()`
4. Register in `castor/providers/__init__.py` (`get_provider()`)
5. Add env var mapping to `castor/auth.py` `PROVIDER_AUTH_MAP`
6. Add SDK to `pyproject.toml` and `requirements.txt`, env var to `.env.example`

### New Hardware Driver
1. Create `castor/drivers/<name>.py`, subclass `DriverBase`
2. Implement `move()`, `stop()`, `close()` with mock fallback (`HAS_<NAME>` pattern)
3. Implement `health_check()` returning `{ok, mode, error}`
4. Register in `get_driver()` in `castor/main.py`; add SDK to `pyproject.toml`

### New Messaging Channel
1. Create `castor/channels/<name>.py`, subclass `BaseChannel`
2. Implement `start()`, `stop()`, `send_message()`
3. Wrap all event handlers in `try/except`; log errors, don't propagate
4. Register in `castor/channels/__init__.py`
5. Add env vars to `castor/auth.py` `CHANNEL_AUTH_MAP` and `.env.example`
6. Add SDK to `pyproject.toml` optional dependencies
7. Add webhook endpoint to `castor/api.py` with `_check_webhook_rate()` applied

### New Hardware Preset
1. Create `config/presets/<name>.rcan.yaml`
2. Follow RCAN schema (see existing presets); CI validates on push

See `CONTRIBUTING.md` for detailed examples and templates.

## Safety Considerations

- **Prompt injection defense**: `_check_instruction_safety()` scans every LLM instruction; returns blocking `Thought` on BLOCK verdict
- **Webhook rate limiting**: 10 req/min/sender on `/webhooks/whatsapp` and `/webhooks/slack`
- **Command rate limiting**: 5 req/sec/IP on `/api/command` and `/api/command/stream`
- **Driver bounds clamping**: Dynamixel 0–4095 ticks; PCA9685 duty cycle limits
- **`safety_stop: true`** in RCAN config enables emergency stop
- **`BoundsChecker`** validates motor commands against joint/force/workspace limits
- **`GuardianAgent`** has veto authority over all motor commands in multi-agent mode
- Configurable latency budgets (`latency_budget_ms`)
- Emergency stop via dashboard, `POST /api/stop`, `fs.estop()`, or messaging channels
- Optional bearer-token or JWT auth on the API gateway
- `.env` in `.gitignore` — secrets never committed
- Drivers gracefully shut down via `close()` in `finally` blocks

# OpenCastor Repository Structure

## Overview

OpenCastor is a universal runtime for embodied AI (~241 Python files, 110+ test files, 2816 tests).

## Full Repository Tree

```
OpenCastor/
├── castor/                           # Main Python package
│   ├── __init__.py                   # Version string (__version__)
│   ├── __main__.py                   # Package entry point
│   ├── cli.py                        # Unified CLI entry point (48+ commands)
│   ├── main.py                       # Core runtime: perception-action loop
│   ├── api.py                        # FastAPI gateway server (all REST endpoints)
│   ├── api_errors.py                 # Structured JSON error handling for API
│   ├── auth.py                       # Unified auth manager (providers + channels)
│   ├── wizard.py                     # Interactive setup wizard
│   ├── web_wizard.py                 # Web-based configuration wizard
│   ├── dashboard.py                  # Streamlit web UI
│   ├── dashboard_tui.py              # Terminal UI dashboard (tmux-based, preferred)
│   ├── config_validation.py          # RCAN config validation (fail-fast on startup)
│   ├── connectivity.py               # Internet & provider reachability checks
│   ├── offline_fallback.py           # Auto-switch to local provider on connectivity loss
│   ├── provider_fallback.py          # Auto-switch on quota/credit errors
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
│   ├── watch.py                      # Live Rich TUI telemetry
│   ├── logs.py                       # Log viewing utilities
│   ├── benchmark.py                  # Performance profiling
│   ├── calibrate.py                  # Interactive hardware calibration
│   ├── test_hardware.py              # Hardware testing CLI
│   ├── memory.py                     # SQLite episode store (EpisodeMemory)
│   ├── metrics.py                    # Prometheus-compatible metrics (MetricsRegistry)
│   ├── tools.py                      # LLM tool calling registry (ToolRegistry)
│   ├── memory_search.py              # Memory search utilities
│   ├── claude_proxy.py               # Claude API proxy
│   ├── depth.py                      # OAK-D depth overlay + obstacle zone detection
│   ├── nav.py                        # WaypointNav dead-reckoning navigation
│   ├── behaviors.py                  # BehaviorRunner (YAML step sequences)
│   ├── auth_jwt.py                   # Multi-user JWT auth (OPENCASTOR_USERS env var)
│   ├── usage.py                      # UsageTracker (SQLite token/cost tracking)
│   ├── camera.py                     # CameraManager (multi-camera support)
│   ├── stream.py                     # WebRTC stream (aiortc optional)
│   │
│   ├── commands/                     # CLI sub-commands
│   │   ├── __init__.py
│   │   ├── swarm.py                  # castor swarm status/command/stop/sync
│   │   ├── hub.py                    # castor hub list/search/install/publish
│   │   ├── update.py                 # castor update (git pull / pip upgrade + swarm SSH)
│   │   ├── benchmark.py              # castor benchmark --providers comparison
│   │   └── deploy.py                 # castor deploy (SSH-push config + restart)
│   │
│   ├── providers/                    # LLM provider adapters
│   │   ├── __init__.py               # get_provider() factory
│   │   ├── base.py                   # BaseProvider ABC + Thought + ProviderQuotaError
│   │   ├── google_provider.py        # Google Gemini
│   │   ├── openai_provider.py        # OpenAI GPT-4.1
│   │   ├── anthropic_provider.py     # Anthropic Claude
│   │   ├── ollama_provider.py        # Local Ollama
│   │   ├── huggingface_provider.py   # HuggingFace Hub
│   │   ├── llamacpp_provider.py      # llama.cpp local inference
│   │   ├── mlx_provider.py           # Apple MLX acceleration
│   │   └── vertex_provider.py        # Google Vertex AI (google-genai SDK)
│   │
│   ├── drivers/                      # Hardware driver implementations
│   │   ├── __init__.py
│   │   ├── base.py                   # DriverBase ABC (move/stop/close/health_check)
│   │   ├── pca9685.py                # I2C PWM motor driver (Amazon/Adafruit kits)
│   │   ├── dynamixel.py              # Robotis Dynamixel servo (Protocol 2.0)
│   │   ├── composite.py              # CompositeDriver: routes action keys to sub-drivers
│   │   └── ros2_driver.py            # ROS2 bridge driver (rclpy, mock mode)
│   │
│   ├── channels/                     # Messaging channel integrations
│   │   ├── __init__.py               # Channel registry + create_channel() factory
│   │   ├── base.py                   # BaseChannel ABC
│   │   ├── session.py                # Session management
│   │   ├── whatsapp_neonize.py       # WhatsApp via neonize (QR code scan)
│   │   ├── whatsapp_twilio.py        # WhatsApp via Twilio (legacy)
│   │   ├── telegram_channel.py       # Telegram Bot (long-polling)
│   │   ├── discord_channel.py        # Discord Bot
│   │   ├── slack_channel.py          # Slack Bot (Socket Mode)
│   │   ├── mqtt_channel.py           # MQTT (paho-mqtt)
│   │   └── homeassistant_channel.py  # Home Assistant (websocket)
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
│   ├── swarm.yaml                    # Swarm node registry (name/host/ip/port/token/tags)
│   ├── hub_index.json                # Model hub index (16 presets, GitHub raw URLs)
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
├── tests/                            # 110+ test files, 2816 tests (0 failures)
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
│   └── workflows/
│       ├── ci.yml                    # Tests + lint + type check
│       ├── validate_rcan.yml         # RCAN schema validation
│       ├── install-test.yml          # Multi-platform install verification (scheduled)
│       ├── release.yml               # PyPI release automation (on tag push)
│       ├── deploy-pages.yml          # Cloudflare Pages deploy (on push to main)
│       ├── auto-label.yml            # PR auto-labeling
│       ├── create_backlog_issues.yml # Backlog maintenance (scheduled)
│       └── stale.yml                 # Stale issue/PR management (scheduled)
├── .env.example
├── pyproject.toml
├── requirements.txt
├── Dockerfile                        # Container with health check
├── docker-compose.yml                # Gateway + runtime + dashboard services
├── CONTRIBUTING.md
├── wrangler.toml                     # Cloudflare Pages config
└── README.md
```

## Subsystem Descriptions

| Subsystem | Path | Purpose |
|-----------|------|---------|
| Providers | `castor/providers/` | 9 LLM adapters (Gemini, GPT-4.1, Claude, Ollama, HuggingFace, llama.cpp, MLX, Vertex, OpenRouter) |
| Drivers | `castor/drivers/` | Hardware: PCA9685, Dynamixel, CompositeDriver, ROS2 bridge |
| Channels | `castor/channels/` | Messaging: WhatsApp (neonize/Twilio), Telegram, Discord, Slack, MQTT, Home Assistant |
| VFS | `castor/fs/` | Unix-inspired virtual filesystem with capabilities, memory tiers, e-stop |
| Safety | `castor/safety/` | Anti-subversion, BoundsChecker, WorkAuthority, GuardianAgent |
| RCAN | `castor/rcan/` | Protocol: RURI addressing, RBAC (5 roles), JWT auth, mDNS discovery |
| Agents | `castor/agents/` | Multi-agent: Observer, Navigator, Manipulator, Communicator, Guardian, Orchestrator |
| Specialists | `castor/specialists/` | Task agents: Scout, Responder, Dock, Manipulator, TaskPlanner |
| Learner | `castor/learner/` | Sisyphus self-improving loop: PM→Dev→QA→Apply |
| Swarm | `castor/swarm/` | Multi-robot: coordinator, consensus, shared memory, patch sync |
| API | `castor/api.py` | FastAPI gateway; 50+ REST endpoints + WebSocket telemetry |
| Memory | `castor/memory.py` | SQLite episode store; 10k episode FIFO; `CASTOR_MEMORY_DB` |
| Metrics | `castor/metrics.py` | Stdlib Prometheus; `GET /api/metrics`; 13 pre-registered metrics |
| Tools | `castor/tools.py` | LLM tool calling; 4 built-ins; positional-only `name` param |
| Nav | `castor/nav.py` | Dead-reckoning `WaypointNav`; reads RCAN `physics` block |
| Behaviors | `castor/behaviors.py` | YAML step sequences; `BehaviorRunner` dispatch table |
| Camera | `castor/camera.py` | Multi-camera `CameraManager`; tile/primary/depth_overlay modes |
| Stream | `castor/stream.py` | WebRTC via aiortc (optional); fallback to MJPEG |
| Usage | `castor/usage.py` | SQLite token/cost tracker; `GET /api/usage` |
| Depth | `castor/depth.py` | OAK-D JET colormap overlay; obstacle zone detection |

## RCAN Config Requirements

Required top-level keys: `rcan_version`, `metadata`, `agent`, `physics`, `drivers`, `network`, `rcan_protocol`

- `metadata.robot_name` — required
- `agent.model` — required
- `drivers` — must be non-empty list

Validated by `castor/config_validation.py` on gateway startup. 16 presets in `config/presets/`.

## CI/CD Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | Push, PR | Tests + ruff lint + type check |
| `validate_rcan.yml` | Push/PR on `*.rcan.yaml` | JSON schema validation |
| `install-test.yml` | Scheduled | Multi-platform install test |
| `release.yml` | Tag push | PyPI release automation |
| `deploy-pages.yml` | Push to main | Cloudflare Pages deploy |
| `stale.yml` | Scheduled | Stale issue/PR cleanup |

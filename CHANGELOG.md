# Changelog

All notable changes to OpenCastor are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [CalVer](https://calver.org/) versioning: `YYYY.M.DD.PATCH`.

## [2026.2.20.12] - 2026-02-20 🔄 Auto-Start Daemon + Offline Fallback

### Highlights
OpenCastor can now survive reboots and internet outages. A one-command install
puts the gateway in systemd so it auto-starts on boot and auto-restarts on
crashes. When the internet goes down, `OfflineFallbackManager` transparently
switches the brain to a locally-running model (Ollama, LlamaCpp, or MLX) and
notifies the user via their configured channel.

### Added
- **`castor daemon` CLI** — manage the systemd auto-start service:
  - `castor daemon enable --config bob.rcan.yaml` — install, enable, start
  - `castor daemon status` — installed / enabled / running / PID / uptime
  - `castor daemon logs [--lines N]` — stream journal output
  - `castor daemon restart` / `castor daemon disable`
- **`castor/daemon.py`** — systemd service management module:
  - `generate_service_file()` — produces a well-formed `.service` file with
    `After=network-online.target`, `Restart=on-failure`, `RestartSec=5s`,
    `MemoryMax=1G`, `StandardOutput=journal`
  - `enable_daemon()` / `disable_daemon()` — write service file + systemctl
  - `daemon_status()` / `daemon_logs()` — introspect running state
- **`castor/connectivity.py`** — lightweight internet and provider probes:
  - `is_online(timeout)` — TCP probe to 1.1.1.1 + 8.8.8.8 port 53; no HTTP deps
  - `check_provider_reachable(name)` — per-provider hostname check (local
    providers always return True)
  - `ConnectivityMonitor` — background thread polling every N seconds with
    `on_change(online: bool)` callback; safe to crash in callback
- **`castor/offline_fallback.py`** — automatic provider switching:
  - `OfflineFallbackManager` — monitors connectivity, swaps brain to fallback
    provider when offline, swaps back when restored
  - Notifies via configured channel on switch (e.g. WhatsApp)
  - `get_active_provider()` — transparent API; callers need no change
- **`offline_fallback` RCAN config block**:
  ```yaml
  offline_fallback:
    enabled: true
    provider: ollama          # ollama | llamacpp | mlx
    model: llama3.2:3b
    check_interval_s: 30
    alert_channel: whatsapp   # optional notify-on-switch
  ```
- **`api.py` integration**: all `think()` call sites route through
  `offline_fallback.get_active_provider()` when manager is active
- **Tests**: `tests/test_daemon.py` (8 tests), `tests/test_connectivity.py`
  (10 tests) covering service file generation, status parsing, TCP probes,
  monitor change callbacks

### Changed
- `AppState` gains `offline_fallback` field
- `/command`, `/cap/chat`, `_handle_channel_message` all use
  `offline_fallback.get_active_provider()` when configured

## [2026.2.20.11] - 2026-02-20 💬 Messaging Prompt System

### Highlights
Introduces a canonical, surface-aware messaging pre-prompt that lives in all
OpenCastor releases. `BaseProvider.build_messaging_prompt()` is the single
source of truth for how robots communicate with humans via text or voice —
across WhatsApp, terminal, dashboard, Discord, and future surfaces. All seven
providers now honour the `surface=` parameter and use the shared prompt when
operating in text-only (no camera) mode.

### Added
- **`BaseProvider.build_messaging_prompt()`** — canonical conversational system
  prompt shared by all providers and all surfaces. Accepts:
  - `robot_name` — from RCAN metadata
  - `surface` — `"whatsapp"` | `"terminal"` | `"dashboard"` | `"voice"` |
    `"discord"` | `"slack"` | `"irc"` | `"signal"` | `"sms"`
  - `hardware` — live subsystem status dict (motors, camera, speaker)
  - `capabilities` — RCAN capability names gate command vocabulary
  - `sensor_snapshot` — live telemetry (distance, battery, speed, heading)
  - `memory_context` — episodic/semantic memory from the virtual filesystem
  - Front-loads natural-language → JSON command mappings; response rules
    route commands to reply + action JSON, questions to plain English only
- **`surface=` parameter on `BaseProvider.think()`** — all seven providers
  (Anthropic, HuggingFace, OpenAI, Ollama, Google, MLX, LlamaCpp) accept and
  thread `surface` through to the messaging prompt at call time
- **`_CHANNEL_SURFACE` routing table in `api.py`** — `_handle_channel_message`
  resolves channel name → surface automatically:
  - WhatsApp / Telegram / Signal / SMS → `"whatsapp"`
  - Discord / Slack / dashboard → `"dashboard"`
  - IRC / terminal → `"terminal"`
  - Voice channel → `"voice"`
- **Surface-aware call sites wired**:
  - `castor shell` (`do_look`, `do_think`) → `surface="terminal"`
  - Streamlit dashboard chat → `surface="dashboard"`
  - `castor/repl.py` `look()` → `surface="terminal"`
  - Anthropic provider uses `build_messaging_prompt()` in text-only mode
    (keeps cached system blocks for vision/action path)

### Fixed
- `_capture_live_frame()` now checks `camera.is_available()` and sniffs 16
  bytes to reject null-padding (`b"\x00"*N`) returned on CSI capture failure;
  returns `b""` so vision inference is never called for text-only messages
- `HuggingFaceProvider.think()` guard: `if self.is_vision and image_bytes`
  (empty bytes are falsy — routes to `_think_text` instead of `_think_vision`)
- WhatsApp group policy evaluated before self-chat guard so owner can message
  their own groups (`is_from_me=True` in group context no longer short-circuits)
- Channel YAML config now passed to `create_channel()` so `group_policy`,
  `self_chat_mode`, `allow_from` take effect from `bob.rcan.yaml`
- Telegram channel `NameError: 'Update' is not defined` when
  `python-telegram-bot` is not installed — stub fallback types added
- neonize `err-client-outdated` (405): rebuilt `neonize-linux-arm64.so` from
  source with whatsmeow updated 2025-12-05 → 2026-02-19
- All blank-frame call sites changed from `b"\x00"*1024` to `b""` (shell,
  dashboard, repl) so vision guard works correctly

## [2026.2.20.10] - 2026-02-19 📡 Channel messaging wired into main loop

### Highlights
Channels (WhatsApp, Telegram, etc.) are now fully integrated into the robot's
main loop. Incoming messages inject into the brain context; the next brain
thought is sent back as a reply. Also fixes blank-frame blocking when
`camera_required: false` so the robot stays responsive even without a live
camera feed.

### Added
- **Channel startup in `main.py` (§6h)** — parses `config["channels"]`, creates
  and starts all enabled channels in persistent daemon event loops (loop stays
  alive so `asyncio.run_coroutine_threadsafe` keeps working)
- **Brain→channel reply loop** — `_reply_queue` collects `(channel_obj, chat_id)`
  pairs from `on_message` callbacks; main loop drains the queue after each
  brain thought and fires `send_message()` in a background thread
- **`camera_required: false`** in RCAN camera config — when set, blank/missing
  frames no longer trigger a reactive `wait` action; the brain runs in
  text/sensor-only mode (messaging still works without a camera)
- **`castor/tiered_brain.py`** — `ReactiveLayer` reads `camera.camera_required`
  (default `true`); blank-frame rules are skipped when `false`
- **`bob.rcan.yaml` fixes** — `camera.type: oakd` (was `csi`), `camera_required: false`,
  full WhatsApp block with `allow_from`, `self_chat_mode`, `ack_reaction`

### Fixed
- Robot was stuck in `blank_frame` wait loop when CSI camera fails at boot;
  now continues autonomously (messaging-only mode) if `camera_required: false`
- WhatsApp `allow_from`/`self_chat_mode` config was missing from `bob.rcan.yaml`
- `castor/channels/__init__.py` — channels were never launched from `main.py`;
  they are now started with a persistent event loop per channel

## [2026.2.20.9] - 2026-02-19 💬 OpenClaw-style WhatsApp access control

### Highlights
The WhatsApp channel now works exactly like OpenClaw's messaging integration:
owner can message their own linked number, access is controlled by `allow_from`,
unknown senders get a pairing flow, and groups are policy-gated.

### Added
- **`dm_policy`** — `allowlist` | `pairing` | `open` (default: `allowlist`)
- **`allow_from`** — E.164 phone number allowlist; owner auto-added at connect time
- **`self_chat_mode: true`** — owner can message their own linked WhatsApp number
  and the robot responds (was previously blocked by `IsFromMe` filter)
- **`group_policy`** — `disabled` | `open` | `allowlist` (default: `disabled`)
- **`ack_reaction`** — emoji reaction sent on receipt (e.g. `"👀"`)
- **`_dispatch(coro)`** — extracted helper for scheduling async coroutines from
  neonize's sync thread; makes unit testing clean (no asyncio in tests)
- **`approve_pairing(code)`** — approve a pending pairing request by code
- **`list_pairing_requests()`** — return all pending pairing codes
- **Owner auto-detected** — `_owner_number` set from `client.get_me()` on connect;
  auto-appended to `allow_from` so the robot always responds to its owner
- **`bob.rcan.yaml` updated** — `allow_from: ["+19169967105"]`, `self_chat_mode: true`,
  `ack_reaction: "👀"`
- **25 new tests** covering all dm_policy modes, self-chat, group policy,
  pairing flow, ack reactions, owner auto-add

### How to chat with your robot via WhatsApp
1. Run: `castor run --config bob.rcan.yaml` (or `castor dashboard`)
2. Open WhatsApp on your phone
3. Tap your own name / "Saved Messages" (message yourself)
4. Type anything — the robot reads it and replies

### Stats
- **2,233 tests** (2,222 passing, 11 skipped) | **55,499 LOC** | 8 providers

## [2026.2.20.8] - 2026-02-19 📊 Dashboard auto-start + live exchange stats bar

### Highlights
`castor dashboard` now starts the robot immediately on entry — no separate
`castor run` needed. A Claude Code-style status bar at the bottom of every tmux
session shows live token counts, cached tokens, API call count, data volume,
current tick, and last action — updated every 2 seconds from the running robot.

### Added
- **`castor/runtime_stats.py`** — thread-safe singleton stats tracker:
  `record_api_call(tokens_in, tokens_out, tokens_cached, bytes_in, bytes_out, model)`,
  `record_tick(tick, action)`, `reset()`, `get_status_bar_string()`.
  Writes `~/.opencastor/runtime_stats.json` (structured) and
  `/tmp/opencastor_status_bar.txt` (compact one-liner) on every call.
- **Live tmux status bar** — `launch_dashboard()` now sets `status-right` to
  `#(cat /tmp/opencastor_status_bar.txt)` with `status-interval 2`.
  Displays: `⏱ uptime │ 🧠 model │ ↓Xtok ↑Ytok │ 💾 cached │ 🔁 N calls │ ↕ data │ tick N │ action`
- **`castor dashboard` starts robot immediately** — `cmd_dashboard` now drives
  the tmux TUI directly (was Streamlit). Accepts `--config`, `--layout`,
  `--simulate`, `--kill`. Auto-detects `*.rcan.yaml` in cwd if `--config` omitted.
- **`_auto_detect_config()`** helper in `cli.py` — used by both `castor dashboard`
  and `castor dashboard-tui`. Finds single `*.rcan.yaml` in cwd automatically.
- **Provider hooks** — `anthropic_provider`, `huggingface_provider`,
  `google_provider` each call `record_api_call()` after every LLM response.
  Anthropic path records `cache_read_input_tokens` for the cache savings display.
- **Main loop hook** — `castor/main.py` calls `record_tick()` each tick and
  `reset()` at startup for clean per-session stats.
- **Status pane expanded** — `_run_status_loop()` now includes an Exchange Stats
  section with per-field breakdown (tokens in/out, cached, calls, data vol, tick, action).
- **34 new tests** (`tests/test_runtime_stats.py`): accumulation, reset,
  file persistence, formatting helpers, thread safety.

### Changed
- `castor dashboard` argparser now accepts `--config`, `--layout`, `--simulate`,
  `--kill` (was a no-arg Streamlit launcher).
- tmux status bar style: dark grey background, green active pane border,
  pane titles visible at top of each pane.

### Stats
- **2,207 tests** (2,196 passing, 11 skipped) | **55,006 LOC** | 8 providers

## [2026.2.20.7] - 2026-02-19 🔧 Installer PATH fix

### Fixed
- **Installer auto-adds `castor` to PATH** — `scripts/install.sh` now writes
  `export PATH="~/opencastor/venv/bin:$PATH"` to the correct shell profile
  (`~/.bashrc` for bash, `~/.zshrc`/`~/.zprofile` for zsh, `config.fish` for fish).
  Idempotent — won't duplicate on reinstall. Also `export`s immediately so `castor`
  works in the same terminal session without needing `source venv/bin/activate`.
  Success banner updated: no longer shows the manual activate step.
- **Uninstaller cleans up PATH entry** — `scripts/uninstall.sh` now strips the
  opencastor PATH line from all shell profiles on removal.
- **`scripts/sync-version.py`** — now also patches `scripts/install.sh` VERSION
  variable so the installer always ships the correct version string.
- **`install.sh VERSION`** — was frozen at `2026.2.20.0`; now correctly tracks
  the current release via sync-version.py.

### Stats
- **2,173 tests** (2,162 passing, 11 skipped) | **55,006 LOC** | 8 providers

## [2026.2.20.6] - 2026-02-19 🧠 Brand · Vision · Peripherals · Prompt Cache

### Highlights
New brain+neural identity, Gemini 3 Flash agentic vision, universal plug-and-play
peripheral detection (`castor scan`), prompt-cache-first Anthropic architecture, and
a hardware-wins boot sequence that gets the robot moving even when the config file
lags behind reality.

### Added
- **New brand identity** — brain+neural+connector icon design replaces old C-shape robot.
  `site/assets/logo.svg`, `logo-white.svg`, `icon.svg`, `icon-dark.svg`, `favicon.svg`.
  `brand/` directory with 4 canonical variants:
  `neural-gradient/`, `flat-solid/`, `geometric/`, `badge/` + `VARIANTS.md` spec.
  Full PNG export set (16×16 → 512×512) via `rsvg-convert`. All nav/footer logos updated
  across `index.html`, `hub.html`, `docs.html`.
- **Gemini 3 Flash Agentic Vision** (`castor/providers/google_provider.py`) —
  `_AGENTIC_VISION_MODELS` set; `code_execution` tool auto-enabled for `gemini-3-flash-preview`;
  system prompt addendum injected. Opt-in/opt-out via `agentic_vision:` RCAN key.
  5 new tests.
- **Plug-and-play peripheral auto-detection** (`castor/peripherals.py`) —
  30+ USB VID:PIDs, 18 I2C addresses, V4L2/CSI/NPU/serial scanning.
  `castor scan` CLI prints detected hardware + RCAN config snippets.
  `castor doctor` — peripheral health section appended.
  `docs/peripherals.md` — 740-line guide for all supported hardware classes.
  16 new tests.
- **Prompt cache-first architecture** (`castor/prompt_cache.py`) —
  `CacheStats` dataclass, `build_cached_system_prompt()` (static robot identity + safety rules),
  `build_sensor_reminder()` delivers per-tick sensor state as `<castor-state>` XML in user
  messages (never system prompt). `anthropic_provider.py` updated with `cache_control`
  breakpoints and `extra_headers: anthropic-beta: prompt-caching-2024-07-31`.
  Cache hit-rate alert surfaced in `castor doctor` (warns below 50% after 10-call warmup).
  RCAN schema: `prompt_caching: bool`, `cache_alert_threshold: float`.
  28 new tests.
- **Hardware-detection-wins boot sequence** (`castor/main.py`) —
  `_load_env_file()`: reads `~/.opencastor/env` on every boot, loads `HF_TOKEN`,
  `GOOGLE_API_KEY`, etc. into `os.environ` before any provider init (shell exports win).
  `apply_hardware_overrides(config)`: real hardware detected at boot ALWAYS overrides
  RCAN config. Camera priority: `oakd → realsense → usb → csi`. PCA9685: scans 0x40–0x47
  for actual address if configured one is absent. Logs `⚡ Hardware override` warnings with
  update hints. Validated on Pi: OAK-D online, Qwen firing at 700–930 ms, real move/stop
  decisions from live frames.
- **Website reframe** — OG description, hero sub-headline, and feature cards now emphasize
  universal plug-and-play; OAK-D/Hailo-8 are examples, not requirements.

### Fixed
- Camera config: `camera.type: "auto"` now tries `oakd → realsense → usb → csi → none`
  preventing blank-frame loops when the configured camera isn't present at boot.

### Stats
- **2,173 tests** (2,162 passing, 11 skipped) | **55,006 LOC** | 8 providers

## [2026.2.20.5] - 2026-02-20 🎮 Demo · Validate · Community

### Highlights
Three new pillars: a 5-act live demo showing the full agent stack, a `castor validate`
conformance checker that scores your robot config, and a proper community hub with recipes,
contributing guide, and GitHub discoverability.

### Added
- **`castor demo` overhaul** — 5-act pipeline show: ObserverAgent + NavigatorAgent process
  real simulated Hailo detections, reactive E-STOP fires on obstacles, TaskPlanner dispatches
  grasp to ManipulatorSpecialist, Sisyphus mock improvement loop. No hardware needed.
  `--layout minimal` for quick preview, `--no-color` for CI.
- **`castor validate`** — RCAN conformance checker (`castor/conformance.py`):
  20 behavioral invariant checks across Safety, Provider, Protocol, Performance, Hardware.
  Scored 0-100. `--json` for CI integration, `--strict` for hard failures on warnings.
- **Community Hub** (`site/hub.html`) — featured recipe cards, Get Involved section,
  social links, community stats bar
- **`CONTRIBUTING.md`** — contribution guide: recipes, bugs, providers, tests, docs
- **`docs/community-recipes.md`** — 400-line recipe authoring guide
- **`docs/community/reddit-launch-post.md`** — r/robotics launch post draft
- **GitHub topics** — 10 topics set for discoverability
- **`castor doctor`** — now hints `castor validate` for deep config checks
- **115 new tests** (110 conformance + 5 demo)

### Stats
- **2,124 tests** passing (11 skipped) | **52,314 LOC** | 8 providers

## [2026.2.20.4] - 2026-02-20 🔌 Agent Runtime Wiring + Dashboard

### Highlights
Agents are now live — the robot spawns ObserverAgent and NavigatorAgent at startup,
feeds them real sensor data, blends their output into the tiered brain, and shuts them
down gracefully. Sisyphus patches broadcast to the fleet automatically. The TUI shows
everything in real-time.

### Added
- **Agent roster runtime integration** (`castor/main.py`):
  - Reads `agent_roster` config at startup, spawns agents with shared `SharedState`
  - ObserverAgent fed Hailo detections + depth data every tick
  - NavigatorAgent suggestion (`nav_direction`, `nav_speed`) blended into tiered brain
  - Graceful `stop_all()` on shutdown
- **Sisyphus → PatchSync hookup** (`castor/learner/apply_stage.py`):
  - `set_swarm_config()` injects swarm credentials
  - `_broadcast_to_swarm()` publishes every applied patch to fleet SharedMemory
  - Auto-injected from main.py when swarm is enabled
- **Dashboard TUI overhaul** (`castor/dashboard_tui.py`):
  - Agents panel — live status from `~/.opencastor/agent_status.json`
  - Swarm panel — fleet peers + synced patches from `swarm_memory.json`
  - Improvements panel — last 5 Sisyphus patches with ✅/❌ icons
  - Episode counter — live count from `~/.opencastor/episodes/`
- **`AgentRegistry.write_status_file()`** — runtime writes agent health for TUI
- **`bob.rcan.yaml`** — Pi config updated with `agent_roster` + `swarm` sections
- **27 new integration tests**

### Stats
- **1,998 tests** passing (11 skipped) | **49,267 LOC** | 8 providers

## [2026.2.20.3] - 2026-02-20 🤖 Agent Swarm Architecture (Phase 2-4)

### Highlights
Three new layers of intelligence: Observer + Navigator (Phase 2),
Task Specialists + TaskPlanner (Phase 3), and Multi-Robot Swarm Coordination (Phase 4).

### Added
- **Phase 2 — Observer + Navigator** (`castor/agents/`):
  - `BaseAgent` ABC — lifecycle (start/stop), observe/act interface, health reporting
  - `ObserverAgent` — converts Hailo-8/depth sensor data into structured `SceneGraph`
  - `NavigatorAgent` — potential-field path planning; publishes RCAN-compatible action dicts
  - `SharedState` — thread-safe pub/sub state bus for inter-agent communication
  - `AgentRegistry` — spawn, list, stop, health-check agents by name
- **Phase 3 — Task Specialists** (`castor/specialists/`):
  - `ManipulatorSpecialist` — 6-DOF arm/gripper planning (grasp, place, home)
  - `ScoutSpecialist` — frontier-based autonomous exploration, 20×20 occupancy grid
  - `DockSpecialist` — smooth deceleration dock approach, battery threshold checks
  - `ResponderSpecialist` — human-readable status formatting, alert severity
  - `TaskPlanner` — heapq priority queue, capability matching, concurrent execution
- **Phase 4 — Swarm Coordination** (`castor/swarm/`):
  - `SwarmPeer` — represents a discovered fleet peer with load scoring
  - `SwarmCoordinator` — capability-matched, load-balanced task dispatch
  - `SharedMemory` — cross-robot knowledge store with TTL, JSON persistence, merge
  - `SwarmConsensus` — optimistic task claiming, TTL-based locks, leader election
  - `PatchSync` — broadcast Sisyphus improvement patches fleet-wide
- **`castor agents` CLI** — list, status, spawn, stop agents
- **RCAN schema** — `agent_roster` and `swarm` config sections
- **468 new tests** (168 Phase 2 + 185 Phase 3 + 115 Phase 4)

### Stats
- **1,912 tests** passing (11 skipped) | **~42,000 LOC** | 8 providers

---

## [2026.2.20.2] - 2026-02-20 📋 RCAN Schema + WhatsApp Pairing Fix

### Fixed
- **RCAN schema updated** — Added `tiered_brain`, `reactive`, `learner` sections. Added all 8 provider aliases to `agent.provider` enum (huggingface, llamacpp, mlx, claude_oauth, etc.). Added `depth_enabled` and `base_url` fields.
- **WhatsApp pairing** — Rewrote wizard pairing to use `Popen` for real-time QR output, added `QREvent` handler, 3-minute timeout, explicit session persistence on connect, and better error messaging.
- **RCAN validator** — Skip `community-recipes/` (partial configs by design, not full RCAN).

---

## [2026.2.20.1] - 2026-02-20 🎛️ Improve CLI Shortcuts

### Added
- **`castor improve --enable`** — Enable self-improving loop from CLI with sensible defaults (HF free tier, every 5 episodes, config auto-apply only)
- **`castor improve --disable`** — Pause self-improving loop, preserves config and history
- Auto-detects RCAN config when single `*.rcan.yaml` in cwd
- Preserves existing learner settings when re-enabling (won't overwrite provider/model)
- 7 new tests for enable/disable toggle (`test_improve_toggle.py`)

### Stats
- **1,444 tests** passing (11 skipped) | **40,287 LOC** | 8 providers

---

## [2026.2.20.0] - 2026-02-20 🧠 Self-Improving Loop

### Highlights
The robot learns from its mistakes. The **Sisyphus Loop** analyzes episodes,
identifies failures, generates fixes, verifies them, and applies improvements
automatically. Inspired by Oh-My-OpenCode's PM→Dev→QA/QC pattern.

### Added
- **Self-Improving Loop** (`castor/learner/` package — 10 modules):
  - `episode.py` — Episode data model with full serialization
  - `episode_store.py` — JSON file persistence with retention policy
  - `pm_stage.py` — Analyzes episodes, identifies failures and root causes
  - `dev_stage.py` — Generates config/behavior/prompt patches from analysis
  - `qa_stage.py` — Safety bounds verification, consistency checks
  - `apply_stage.py` — Applies verified patches with rollback support
  - `sisyphus.py` — Orchestrates PM→Dev→QA→Apply with retry (up to 3x)
  - `alma.py` — Cross-episode pattern analysis (ALMA consolidation)
  - `patches.py` — ConfigPatch, BehaviorPatch, PromptPatch types
- **`castor improve` CLI** — analyze episodes, view history, rollback patches
- **Wizard Step 7: Self-Improving Loop** — opt-in setup with 4 cost presets:
  - Free ($0): Ollama local analysis
  - Budget ($0): HuggingFace free API
  - Smart (~$1-3/mo): Gemini Flash-Lite
  - Premium (~$5-15/mo): Claude Sonnet
- **Episode recording** in main control loop (saved on shutdown)
- **Auto-apply preferences**: config-only, config+behavior, or manual review
- 107 new tests (1437 total)

### Changed
- Learner is **disabled by default** — users must opt-in via wizard or YAML
- Wizard now has 8 steps (added self-improving loop setup)

### Design
```
Episode → PM (Analyze) → Dev (Patch) → QA (Verify) → Apply (if pass)
                                              ↓
                                        Retry (up to 3x)
                                              ↓
                                     Human review queue
```

## [2026.2.19.1] - 2026-02-19

### Added
- **MLX Provider** (`castor/providers/mlx_provider.py`): Apple Silicon native
  inference via mlx-lm (direct) or vLLM-MLX/MLX-OpenAI-Server (OpenAI API).
  400+ tok/s on M4 Max. Vision support via mlx-vlm. Provider aliases:
  `mlx`, `mlx-lm`, `vllm-mlx`.
- MLX models in wizard: Qwen2.5-VL-7B, Llama 3.3 8B, Mistral Small 3.1 24B
- llama.cpp and MLX providers added to wizard provider selection
- 8 new provider tests (1330 total)

### Changed
- Provider count: 7 → 8 (Anthropic, Google, OpenAI, HuggingFace, Ollama,
  llama.cpp, MLX, Claude OAuth)

## [2026.2.19.0] - 2026-02-19 🚀 Major Release

### Highlights
OpenCastor v2026.2.19.0 is a landmark release that transforms the framework into a
production-ready, cost-effective AI robotics runtime. **8 AI providers**, a **tiered
brain architecture** that starts at $0/month, **Hailo-8 NPU vision**, **OAK-D depth
camera** support, and an interactive wizard that guides users through optimal setup.

### Added
- **Tiered Brain Architecture** (`castor/tiered_brain.py`): Three-layer system —
  Reactive (<1ms rules), Fast Brain (~500ms HF/Gemini), Planner (~12s Claude).
  Configurable planner interval, uncertainty escalation, per-layer stats.
- **Hailo-8 NPU Vision** (`castor/hailo_vision.py`): YOLOv8s object detection at
  ~250ms on Hailo-8. 80 COCO classes, obstacle classification, clear-path analysis.
  Zero API cost for reactive obstacle avoidance.
- **OAK-D Stereo Depth Camera**: RGB + depth streaming via DepthAI v3 API.
  Depth-based obstacle distance (5th percentile center region). Camera type `oakd`
  with `depth_enabled: true` in config.
- **llama.cpp Provider** (`castor/providers/llamacpp_provider.py`): Local LLM
  inference via Ollama OpenAI API or direct GGUF loading. Model pre-loading with
  keep-alive. Provider aliases: `llamacpp`, `llama.cpp`, `llama-cpp`.
- **HuggingFace Vision Models**: Added Qwen2.5-VL-7B/3B, Llama-4-Scout/Maverick
  to vision model registry. Free Inference API = $0 robot brain.
- **Brain Architecture Wizard**: New Step 6 in wizard — 5 cost-tier presets from
  Free ($0) to Maximum Intelligence. Auto-detects Hailo-8 NPU. Shows estimated
  monthly cost. Explains the tiered approach to new users.
- **Graceful Shutdown**: SIGTERM/SIGINT handler with phased cleanup — motors →
  watchdog → battery → hardware → speaker → camera → filesystem → audit.
- **Claude OAuth Proxy** (`castor/claude_proxy.py`): Native `ClaudeOAuthClient`
  wraps `claude -p` CLI for setup-token auth without per-token billing.
- 16 new tests (1319 total across Python 3.10-3.12)

### Changed
- Primary brain defaults to open-source model (Qwen2.5-VL via HuggingFace)
- Tiered brain wiring: primary config = fast brain, secondary[0] = planner
- Camera class supports three modes: OAK-D, CSI (picamera2), USB (OpenCV)
- Watchdog timeout increased to 30s for Claude CLI latency on ARM
- Hailo vision defaults to opt-in (`hailo_vision: false`) to avoid CI segfaults
- Doctor test patched for env file leak from credential store

### Fixed
- Anthropic provider auto-routes OAuth tokens through Claude CLI
- OpenAI provider supports `base_url` for custom endpoints (Ollama, etc.)
- Installer version synced to release version

### Architecture
```
┌─────────────────────────────────────────────────────┐
│  Layer 0: Reactive (<1ms)                           │
│  ├─ Blank frame → wait                              │
│  ├─ Depth obstacle < 0.3m → stop                    │
│  ├─ Battery critical → stop                         │
│  └─ Hailo-8 YOLOv8 (~250ms) → avoid/stop          │
├─────────────────────────────────────────────────────┤
│  Layer 1: Fast Brain (~500ms)                       │
│  └─ Qwen2.5-VL / Gemini Flash / Ollama             │
├─────────────────────────────────────────────────────┤
│  Layer 2: Planner (~10-15s, every N ticks)          │
│  └─ Claude Sonnet / Opus                            │
└─────────────────────────────────────────────────────┘
```

### Providers (8 total)
| Provider | Models | Auth |
|----------|--------|------|
| Anthropic | Claude 4 family | API key or setup-token (OAuth) |
| Google | Gemini 2.5 Flash/Pro | API key |
| OpenAI | GPT-4o, o1 | API key |
| HuggingFace | Qwen-VL, Llama 4, any Hub model | HF token (free) |
| Ollama | Any GGUF model | Local (no auth) |
| llama.cpp | Direct GGUF or Ollama API | Local (no auth) |
| MLX | Apple Silicon native (mlx-lm, vLLM-MLX) | Local (no auth) |
| Claude OAuth | Max/Pro subscription | setup-token |

## [2026.2.18.13] - 2026-02-18

### Added
- **Tiered Brain Architecture** (`castor/tiered_brain.py`) — three-layer brain pipeline:
  - Layer 0 (Reactive): Rule-based safety (<1ms) — obstacle stop, blank frame wait, battery critical
  - Layer 1 (Fast Brain): Primary perception-action loop (Gemini Flash / Ollama, ~1-2s)
  - Layer 2 (Planner): Complex reasoning (Claude, ~10-15s) — periodic or on escalation
- **Graceful shutdown** — SIGTERM/SIGINT caught with phased cleanup: motors → services → hardware → filesystem
- **Ollama installed on Pi** — gemma3:1b available (CPU-only, ~15s — Gemini Flash recommended for fast brain)
- 17 new tests (1303 total)

## [2026.2.18.12] - 2026-02-18

### Added
- **Claude OAuth client** (`castor/claude_proxy.py`) — native integration with Claude Max/Pro subscriptions via OAuth token. Works like OpenClaw: `castor login anthropic` generates a setup-token, the brain routes through Claude CLI with proper model selection and system prompts. No per-token billing.
- **OpenAI provider `base_url` support** — point at any OpenAI-compatible endpoint

### Fixed
- Anthropic provider auto-detects OAuth tokens and routes correctly (no more 401 errors with setup-tokens)

## [2026.2.18.11] - 2026-02-18

### Added
- **Terminal Dashboard** (`castor dashboard-tui`) — tmux-based multi-pane robot monitor. Watch your robot's brain, eyes, body, safety, and messaging subsystems in real-time across split panes. Three layouts: `full` (6 panes), `minimal` (3), `debug` (4). Mouse-enabled, zoom with Ctrl+B z.
- **tmux added to installer** — auto-installed as a system dependency on Linux

## [2026.2.18.10] - 2026-02-18

### Added
- **WhatsApp setup flow** — wizard now verifies neonize is installed (auto-installs if missing), checks for existing session, explains QR pairing flow, and optionally starts a live QR pairing session right from the wizard
- **Telegram bot verification** — wizard collects bot token and verifies it by calling Telegram's `getMe` API, showing bot name and username on success
- **Generic channel setup** — all channels now get proper credential collection and validation

## [2026.2.18.9] - 2026-02-18

### Changed
- **Credentials moved to `~/.opencastor/`** — `.env` vars now written to `~/.opencastor/env` (0600 perms) alongside `anthropic-token` and `wizard-state.yaml`. Local `.env` still written for backward compat.
- **Uninstaller redesigned** — removes install dir but keeps `~/.opencastor/` by default. Asks user: "[1] Keep credentials (recommended)" or "[2] Delete everything". Migrates legacy `.env` to `~/.opencastor/env` during uninstall.
- **Auth loads `~/.opencastor/env` first** — `load_dotenv_if_available()` reads the safe env file before local `.env`, without overriding already-set vars.

## [2026.2.18.8] - 2026-02-18

### Added
- **AI accelerator detection** — health check now detects Hailo AI Hat (PCIe + /dev/hailo*), Google Coral TPU, Intel Movidius/MyriadX (OAK-D), and Nvidia Jetson
- **Auth module knows about token store** — `check_provider_ready("anthropic")` now checks `~/.opencastor/anthropic-token`, fixing the "no key set" false positive in post-wizard health check

### Fixed
- Post-wizard health check no longer says "FAIL: Provider key (anthropic) no key set" when setup-token is stored
- Removed stale `ANTHROPIC_AUTH_MODE=oauth` check from auth module

## [2026.2.18.7] - 2026-02-18

### Fixed
- **Installer version synced** — `install.sh` and `install.ps1` now show correct version (were stuck at v2026.2.17.20)

## [2026.2.18.6] - 2026-02-18

### Added
- **Deep hardware discovery** — startup health check now enumerates:
  - USB devices (via `lsusb`) — shows what's connected at each port
  - I2C devices (via `i2cdetect`) — identifies PCA9685, IMUs, sensors, OLEDs by address
  - SPI bus availability
  - Serial ports (UART, USB-serial adapters like Arduino/ESP32)
  - Loaded kernel drivers (I2C, SPI, PWM, V4L2, USB-serial, audio)

### Changed
- **Anthropic model fetch no longer uses API** — setup-tokens return 401 on `/v1/models`. Now parses model IDs from the public docs page (no auth needed). Falls back to static list if docs unreachable.

## [2026.2.18.5] - 2026-02-18

### Fixed
- **Token priority fix** — OpenCastor stored token (`~/.opencastor/anthropic-token`) now takes priority over `ANTHROPIC_API_KEY` env var and `.env` file. Prevents using OpenClaw's stale API key instead of the setup-token you just saved.
- **Stop importing OpenClaw's Anthropic key** — wizard no longer auto-detects `ANTHROPIC_API_KEY` from OpenClaw config (other provider keys like Google/OpenAI are still imported). This prevents the token sink problem.
- **Wizard detects existing setup-token** — on re-run, if `~/.opencastor/anthropic-token` exists, offers to reuse it instead of asking for a new one.
- **Health check shows correct auth source** — now reports "setup-token stored" when using token store, not "ANTHROPIC_API_KEY set" from the wrong source.

## [2026.2.18.4] - 2026-02-18

### Added
- **Startup health check** — `castor run` now performs a full system health check at boot: Python version, package version, dependencies, config validation, AI provider auth, camera, GPIO, I2C, speaker, disk space, memory, CPU temperature. Prints a formatted health card with ✅⚠️❌ status.
- **Wizard state memory** — wizard remembers previous selections (project name, provider, model) and shows them as defaults on re-run. Saved to `~/.opencastor/wizard-state.yaml`.
- **Dynamic version** — `__version__` now reads from installed package metadata via `importlib.metadata` instead of a hardcoded string. Falls back to current version if not pip-installed.
- 21 new tests (1286 total)

### Fixed
- Wizard version display was stuck on old version due to hardcoded `__version__` in `__init__.py`

## [2026.2.18.3] - 2026-02-18

### Fixed
- Lint error: extraneous f-string prefix in cli.py (F541)

## [2026.2.18.2] - 2026-02-18

### Added
- **Dynamic model lists** — Anthropic and OpenAI model selection now fetches live from their APIs, showing the 3 latest models with an option to expand the full list
- Falls back gracefully to built-in static list if API is unreachable or no key is available
- 6 new tests for dynamic model fetching (1271 total)

## [2026.2.18.1] - 2026-02-18

### Changed
- **Separate token store** — OpenCastor now stores Anthropic tokens at `~/.opencastor/anthropic-token`, NOT in Claude CLI's credentials. Prevents the "token sink" problem where sharing tokens between OpenCastor/OpenClaw/Claude CLI causes mutual invalidation.
- **`castor login anthropic`** — option [1] now runs `claude setup-token` directly to generate a fresh token for OpenCastor
- Wizard setup-token flow saves to `~/.opencastor/` instead of `.env`
- Token file has 0600 permissions for security
- 8 new tests (1265 total)

## [2026.2.17.21] - 2026-02-17

### Added
- **Anthropic setup-token auth** — use your Claude Max/Pro subscription instead of pay-per-token API keys
- **`castor login anthropic`** (alias: `castor login claude`) — interactive setup-token or API key auth
- **Auto-read Claude CLI credentials** — reads setup-token from `~/.claude/.credentials.json` as fallback
- Wizard now recommends setup-token as option [1] over API key
- 7 new Anthropic auth tests (1264 total)

## [2026.2.17.20] - 2026-02-17

### Added
- **Wizard redesign** — QuickStart now has distinct steps: Provider → Auth → Primary Model → Secondary Models → Messaging
- **Provider-specific auth flows** — Anthropic (Max/Pro OAuth or API key), Google (ADC via `gcloud` or API key), HuggingFace (`huggingface-cli login` or paste token), OpenAI (API key), Ollama (connection check)
- **Primary model selection** — curated model list per provider with recommendations and descriptions
- **Secondary models** — optional specialized models (Gemini Robotics ER 1.5, GPT-4o vision, custom) with cross-provider auth
- **Uninstall script** — `curl -sL opencastor.com/uninstall | bash`
- 21 new wizard tests (1244 total)

## [2026.2.17.17] - 2026-02-17

### Added
- **"Start your robot now?"** — wizard offers to launch `castor run` immediately after setup completes

### Fixed
- **Post-install instructions** — simplified Quick Start to `cd && source venv/bin/activate && castor run` (castor requires venv active)

## [2026.2.17.19] - 2026-02-17

### Added
- **Wizard redesign** — QuickStart now has distinct steps: Provider → Auth → Primary Model → Secondary Models → Messaging
- **Provider-specific auth flows** — Anthropic (Max/Pro OAuth or API key), Google (ADC via `gcloud` or API key), HuggingFace (`huggingface-cli login` or paste token), OpenAI (API key), Ollama (connection check)
- **Primary model selection** — curated model list per provider with recommendations and descriptions
- **Secondary models** — optional specialized models (Gemini Robotics ER 1.5, GPT-4o vision, custom) with cross-provider auth
- 21 new wizard tests (1244 total)

## [2026.2.17.18] - 2026-02-17

### Added
- **Claude Max/Pro plan support** — wizard offers OAuth sign-in as option 1 when choosing Anthropic. Auto-detects Claude CLI, installs if needed, runs `claude login` for browser-based auth. Falls back to API key gracefully.
- **Uninstall script** — `curl -sL opencastor.com/uninstall | bash`

## [2026.2.17.16] - 2026-02-17

### Added
- **QuickStart now includes provider + messaging choice** — users pick their AI provider (Anthropic, Google, OpenAI, HuggingFace, Ollama) and optionally connect WhatsApp or Telegram, all in the streamlined QuickStart flow

### Fixed
- **RCAN schema** — added `created_at` to metadata schema (was causing validation error)

## [2026.2.17.15] - 2026-02-17

### Fixed
- **Installer** — wizard stdin redirected to `/dev/tty` for `curl | bash` piped installs (wizard reads user input properly instead of script lines)
- **Post-install messaging** — clear "Useful Commands" section showing `castor wizard`, `castor --help`, `castor status`, `castor doctor`, `castor dashboard`; explicit tip that wizard can be re-run anytime

## [2026.2.17.14] - 2026-02-17

### Fixed
- **Installer** — wizard runs with `--accept-risk` (skips safety prompt, goes straight to config), no longer swallows wizard output, properly reports exit code
- **Wizard version** — now displays correct dynamic version via f-string

## [2026.2.17.13] - 2026-02-17

### Fixed
- **neonize version pin** — `>=1.0.0` → `>=0.3.10` (1.0 doesn't exist)
- **Installer resilience** — `[rpi]` extras failure falls back to core install instead of aborting

## [2026.2.17.12] - 2026-02-17

### Fixed
- **Installer** — `libatlas-base-dev` detection uses `apt-cache policy` (handles Bookworm "no candidate" correctly), `DEBIAN_FRONTEND=noninteractive` suppresses kernel upgrade dialogs, detached HEAD handled in `git pull`
- **`python -m castor`** — Added `__main__.py` so the package is runnable as a module
- **Install verification** — `install-check.sh` tries `castor` binary before `python -m castor` fallback

## [2026.2.17.11] - 2026-02-17

### Added
- **Cross-platform installer** — `install.sh` supports macOS (Homebrew), Fedora (dnf), Arch (pacman), Alpine (apk) alongside Debian/Ubuntu/RPi. New `install.ps1` for native Windows PowerShell. Post-install verification scripts (`install-check.sh`, `install-check.ps1`). CI matrix testing on ubuntu/macos/windows.
- **Safety Protocol Engine** (`castor/safety/protocol.py`) — 10 configurable rules adapted from Protocol 66, YAML config overrides, `castor safety rules` CLI
- **Continuous sensor monitoring** (`castor/safety/monitor.py`) — CPU temp, memory, disk, CPU load with background thread, auto e-stop after 3 consecutive criticals, `/proc/sensors` in virtual FS, `castor monitor --watch` CLI
- **Ollama provider improvements** — model cache with TTL, auto-pull, model aliases, remote host profiles via `OLLAMA_HOST`, configurable timeouts, helpful error messages

### Changed
- **BREAKING: RCAN role alignment** — `ADMIN` → `OWNER`, `OPERATOR` → `LEASEE` per RCAN spec. Backward compatibility layer accepts old names with deprecation warning.
- **Cross-platform Python** — platform markers on RPi deps (`; sys_platform == 'linux'`), `[core]`/`[all]` extras groups, conditional imports for hardware modules, cross-platform TTS/crontab/service commands

### Fixed
- **Installer** — friendly skip for `libatlas-base-dev` on Bookworm/RPi5, default config fallback (`robot.rcan.yaml`) when wizard is skipped
- **Safety module polish** — wrapped integration points in try/except, fixed CLI syntax error, cleaned imports, reformatted files
- **Website** — shrunk oversized wizard-creates icons, fixed mobile nav hamburger menu cutoff

## [2026.2.17.10] - 2026-02-17

### Added
- **Anti-subversion module** (`castor/safety/anti_subversion.py`) — prompt injection defense with 15 regex patterns, forbidden path detection, anomaly rate-spike flagging, wired into SafetyLayer and BaseProvider
- **Work authorization** (`castor/safety/authorization.py`) — work order lifecycle for destructive actions (request → approve → execute/revoke), role-gated approval, self-approval prevention, auto-expiry, destructive action detection for GPIO/motor paths
- **Physical bounds enforcement** (`castor/safety/bounds.py`) — workspace sphere/box/forbidden zones, per-joint position/velocity/torque limits, force limits (50N default, 10N human-proximity), pre-built configs for differential_drive/arm/arm_mobile
- **Tamper-evident audit log** — SHA-256 hash chain on every audit entry, `castor audit --verify` CLI, backward-compatible with existing logs
- **Safety state telemetry** (`castor/safety/state.py`) — `SafetyStateSnapshot` with composite health score exposed at `/proc/safety`
- **Recipe submission issue template** (`.github/ISSUE_TEMPLATE/recipe-submission.yml`)
- **`castor hub share --submit`** — auto-fork, branch, and PR via `gh` CLI

### Fixed
- **RCAN Safety Invariants 4 & 5** — `check_role_rate_limit()` and `check_session_timeout()` now enforced in all SafetyLayer public methods (read/write/append/ls/stat/mkdir)
- **E-stop authorization** — `clear_estop()` requires auth code via `OPENCASTOR_ESTOP_AUTH` env var when set

### Changed
- **PyPI publishing** — Trusted Publisher (OIDC) with API token fallback, all actions pinned to SHA, scoped permissions, concurrency groups, timeouts, twine check

## [2026.2.17.9] - 2026-02-17

### Added
- **Ollama provider** — run local LLMs with zero API keys
  - Text generation and vision support (LLaVA, BakLLaVA, Moondream, etc.)
  - Streaming token output via `/api/chat`
  - Model listing and pulling via Ollama API
  - `castor login ollama` — test connection, configure host, list available models
  - Proper `OllamaConnectionError` with helpful "ollama serve" message
  - Auto-detection of vision-capable models

## [2026.2.17.8] - 2026-02-17

### Added
- **Community Hub** — browse, share, and install tested robot configs
  - `castor hub browse` — list recipes with category/difficulty/provider filters
  - `castor hub search` — full-text search across all recipes
  - `castor hub show` — view recipe details and README
  - `castor hub install` — copy a recipe config to your project
  - `castor hub share` — interactive wizard to package and scrub your config
  - `castor hub categories` — list all categories and difficulty levels
- **PII scrubbing engine** — automatically removes API keys, emails, phone numbers, public IPs, home paths, and secrets from shared configs
- **2 seed recipes** — PiCar-X Home Patrol (beginner/home) and Farm Scout Crop Inspector (intermediate/agriculture)
- **Hub website page** at opencastor.com/hub with category browser and recipe cards
- Hub link added to site navigation across all pages
- 17 new tests for hub (PII scrubbing, packaging, listing, filtering)

## [2026.2.17.7] - 2026-02-17

### Added
- **Hugging Face provider** — access 1M+ models via the Inference API
  - Text-generation and vision-language models (LLaVA, Qwen-VL, etc.)
  - Supports Inference Endpoints for dedicated deployments
  - Auto-detects vision-capable models
- **`castor login` CLI command** — authenticate with Hugging Face
  - Interactive token prompt with secure input
  - `--list-models` flag to discover trending models by task
  - Saves token to both `~/.cache/huggingface/` and local `.env`
- `huggingface-hub` added as core dependency
- Hugging Face option added to setup wizard (option 5)
- 10 new tests for HF provider and login CLI

### Changed
- Provider count: 4 → 5 (website, docs, stats updated)
- Ollama moved from wizard option 5 → 6

## [2026.2.17.6] - 2026-02-17

### Fixed
- Removed deprecated `License :: OSI Approved` classifier (PEP 639 compliance) — newer setuptools rejected it when `license` expression was already set
- Ran `ruff format` across all 73 source and test files to pass CI formatting check
- Added `python-multipart>=0.0.7` as explicit dependency — required by FastAPI for `request.form()`, was failing on Python 3.10/3.11 in CI
- Replaced invalid PyPI classifier `Topic :: Scientific/Engineering :: Robotics` with valid `Artificial Intelligence` classifier
- Synced package version in `pyproject.toml` with git tag

## [2026.2.17.5] - 2026-02-17

### Added
- `py.typed` marker for PEP 561 type hint support
- `__all__` exports to core modules (providers, drivers, channels, root)
- Return type annotations (`-> None`) on all 41 CLI command handlers
- Type hints and docstrings on `DriverBase` abstract methods (move, stop, close)
- Signal handling (SIGTERM/SIGINT) in API gateway for graceful shutdown
- CLI commands reference table in CONTRIBUTING.md
- Comprehensive test suites: CLI, API endpoints, drivers, channels
- Dependabot config for Python dependencies (already existed for GitHub Actions)

### Fixed
- `castor schedule` command not dispatching due to `--command` arg shadowing subparser `dest="command"`

### Changed
- `DriverBase.move()` now has explicit `linear: float, angular: float` signature
- CONTRIBUTING.md now documents all 41 CLI commands and how to add new ones
- API gateway version updated to 2026.2.17.5

## [2026.2.17.4] - 2026-02-17

### Added
- 41-command CLI with grouped help (`castor --help`)
- `castor doctor` -- system health checks
- `castor fix` -- auto-repair common issues with backup-before-repair
- `castor demo` -- simulated perception-action loop (no hardware/API keys)
- `castor quickstart` -- one-command setup (wizard + demo)
- `castor configure` -- interactive post-wizard config editor
- `castor shell` / `castor repl` -- interactive command shells
- `castor record` / `castor replay` -- session recording and playback
- `castor benchmark` -- perception-action loop performance profiling
- `castor lint` -- deep config validation beyond JSON schema
- `castor learn` -- interactive 7-lesson tutorial
- `castor test` -- pytest wrapper for running test suite
- `castor diff` -- structured RCAN config comparison
- `castor profile` -- named config profile management
- `castor plugins` -- extensible plugin hook system (`~/.opencastor/plugins/`)
- `castor audit` -- append-only event audit log viewer
- `castor approvals` -- approval queue for dangerous motor commands
- `castor schedule` -- cron-like task scheduling
- `castor search` -- semantic search over operational logs
- `castor network` -- Tailscale integration and network status
- `castor fleet` -- multi-robot fleet management via mDNS
- `castor export` -- config bundle export with secrets auto-redacted
- `castor watch` -- live Rich telemetry dashboard
- `castor logs` -- structured colored log viewer with filtering
- `castor backup` / `castor restore` -- config and credential backup
- `castor migrate` -- RCAN config version migration
- `castor upgrade` -- self-update with health check
- `castor update-check` -- PyPI version check with cache
- `castor install-service` -- systemd service unit generation
- `castor privacy` -- sensor access privacy policy viewer
- `castor calibrate` -- interactive servo/motor calibration
- `castor test-hardware` -- individual motor/servo testing
- Safety: watchdog timer (auto-stop on brain timeout)
- Safety: geofence (operating radius limit with dead reckoning)
- Safety: approval gate (queue dangerous commands for human review)
- Safety: privacy policy (default-deny for camera, audio, location)
- Safety: battery monitor with low-voltage emergency stop
- Safety: crash recovery with automatic crash reports
- Contextual error messages with fix suggestions
- Plugin system with startup/shutdown/action/error hooks
- Shell completions via argcomplete
- SECURITY.md with vulnerability disclosure policy
- CHANGELOG.md
- pytest-cov integration for code coverage
- Pre-commit hooks (ruff, ruff-format, secrets scanning)

### Fixed
- CI workflows: `actions/checkout@v6` -> `@v4`, `actions/setup-python@v6` -> `@v5`
- Dockerfile: added non-root user, updated to `bookworm` base
- docker-compose: added `depends_on`, log rotation limits
- Moved `argcomplete` from dev to core dependencies

### Changed
- README updated with all 41 CLI commands (was 6)
- Architecture diagram now includes API gateway layer
- Ruff rules expanded: added bugbear (B), bandit security (S), pyupgrade (UP)
- CORS in api.py now configurable via `OPENCASTOR_CORS_ORIGINS` env var
- PyPI classifiers updated (License, OS, Environment, Robotics topic)

## [2026.2.17.3] - 2026-02-17

### Added
- Initial public release
- Provider adapters: Google Gemini, OpenAI GPT-4.1, Anthropic Claude
- Hardware drivers: PCA9685 (I2C PWM), Dynamixel (Protocol 2.0)
- Messaging channels: WhatsApp (neonize), Telegram, Discord, Slack
- FastAPI gateway with REST API and webhook endpoints
- Streamlit dashboard (CastorDash)
- RCAN Standard compliance with JSON Schema validation
- Virtual filesystem with RBAC and safety layers
- RCAN Protocol: JWT auth, mDNS discovery, message routing, capability registry
- Interactive setup wizard
- Docker and docker-compose support
- One-line installer script for Raspberry Pi
- Cloudflare Pages static site

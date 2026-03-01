# Changelog

All notable changes to OpenCastor are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [CalVer](https://calver.org/) versioning: `YYYY.M.DD.PATCH`.

## [2026.3.1.14] - 2026-03-01 🚀 Release: 15-Feature Mega Release — BLE, Signal, Madgwick, Clustering, Shadow Mode & More

### Added

#### Metrics & Observability
- **ProviderLatencyTracker p50/p95/p99 percentiles (#347)** — stores exact sorted samples (up to 10k per provider) for precise percentile computation; new `render_percentiles()` emits `opencastor_provider_latency_p50/p95/p99_ms` Prometheus gauges; `MetricsRegistry.provider_latency_percentile(provider, pct)` for direct access.

#### BehaviorRunner Steps
- **`event_wait` step (#346)** — suspends behavior execution until a `driver.field` sensor crosses a threshold (`gt/lt/gte/lte/eq/ne`); configurable `timeout_s`; polls at 100 ms intervals; respects `stop()`.
- **`foreach_file` step (#341)** — iterates JSONL file rows (one JSON object per line), substituting `$item` and `$item.<key>` placeholders into nested steps; supports `limit` and comment (`#`) lines.

#### Memory
- **EpisodeMemory k-means clustering (#342)** — `cluster_episodes(n_clusters, by, limit)` groups recent episodes using action-type frequency vectors; pure stdlib k-means (no sklearn); returns labels, centroids, representative episode IDs.

#### Drivers
- **IMUDriver Madgwick filter (#343)** — `MadgwickFilter` class implements IMU-only Madgwick AHRS (accel+gyro, no magnetometer); quaternion stays unit-normalised; enabled with `imu_filter: madgwick` + `imu_beta: 0.1`; `reset()` returns to identity.
- **LidarDriver map persistence (#344)** — `save_map(path, label)` captures occupancy grid to SQLite JSON BLOB; `load_map(path, map_id)` retrieves by ID or most-recent; metadata column stores resolution/origin/mode.
- **ESP32 BLE driver (#287)** — `castor/drivers/esp32_ble_driver.py`; sends JSON commands (`move`, `stop`, `grip`) over GATT characteristic via `bleak`; `HAS_BLEAK` guard; mock mode when library absent or no address configured.

#### Channels
- **Signal Messenger channel (#285)** — `castor/channels/signal_channel.py`; polls signal-cli JSON-RPC REST API for incoming messages; `send_message()` with per-recipient or group-ID targets; registered in `channels.__init__`.

#### ProviderPool
- **Cost tracking (#345)** — per-provider token usage and USD cost accumulation in `_cost_tracker`; configurable `pool_cost_per_1k_tokens` rate dict; `cost_summary()` returns per-provider and aggregate totals; health_check includes per-member `cost_usd`, `tokens_total`, `calls`.
- **Shadow mode (#340)** — `pool_shadow_provider` + `pool_shadow_log_path` config; secondary provider fires in a daemon thread on each successful think(); logs primary vs shadow action comparison (with `match` flag) to JSONL; primary response is always returned; shadow failures are logged and silently ignored.

#### CLI
- **`castor snapshot` command (#348)** — `castor snapshot take | latest | history [N]` sub-commands; delegates to `castor.snapshot.get_manager()`; formatted JSON output.

#### Dashboard
- **Memory timeline module (#349)** — `castor/dashboard_memory_timeline.py`; `MemoryTimeline` class: `get_timeline()` (bucketed counts/latency/outcomes), `get_outcome_summary()` (ok-rate), `get_latency_percentiles()` (p50/p95/p99 from DB).
- **Mission Control panel (#283)** — new expandable Mission Control section in `dashboard.py`; shows active mission status, launch/stop buttons via `/api/behavior/run` and `/api/behavior/stop`; episode outcome KPIs from `MemoryTimeline`.

#### Channels (Base)
- **Mission trigger (#282)** — `BaseChannel.parse_mission_trigger(text)` detects `!mission <name>` / `/mission <name>` patterns; `handle_mission_trigger(name, chat_id)` launches named behavior in background thread; intercepts before normal on_message callback in `handle_message()`.

#### Doctor
- **castor doctor improvements (#280)** — three new checks: `check_memory_db_size()` warns when DB exceeds 100 MB; `check_ble_driver()` reports bleak availability; `check_signal_channel()` verifies Signal channel import.

### Tests
- 215 new tests across 15 test files (≥12 per issue); all passing.
- Pre-existing ruff errors in 6 test files fixed (ambiguous `l` variables, unused imports, B017/B023/B018 patterns).

### Validation
- `python -m ruff check castor/ tests/` — **0 errors**
- `python -m pytest tests/ --ignore=tests/test_deepseek_provider.py -q` — **1533+ passed** (pre-existing `test_deepseek_provider` skipped: `openai` not installed)

## [2026.2.26.3] - 2026-02-26 🚀 Release: Google Setup Hardening + Catalog Expansion Follow-up

### Added
- **Google model preflight regression coverage** — added focused wizard tests for model-available/no-fallback, unavailable-model fallback, missing-credentials no-op, exception no-op, and `used_fallback` tracking in preflight return state.

### Changed
- **Google preflight fallback accounting** — `ensure_provider_preflight()` now sets `used_fallback=True` when Google model preflight switches to a different model.
- **Google model availability probe guardrails** — `_ensure_google_model_ready()` now runs availability probing only when `GOOGLE_API_KEY` is present/configured, avoiding misleading ADC-only checks for this Gemini provider path.
- **Google auth flow clarity** — wizard auth text and flow now keep ADC sign-in optional while explicitly requiring Gemini API key capture for model invocations in this provider.
- **Release/version metadata sync** — synchronized runtime, installer, docs, and site touchpoints to `v2026.2.26.3` (`VERSION="2026.2.26.3"` in installer).

### Documentation / Website
- Updated release/version labels in README and website surfaces (`site/index.html`, `site/docs.html`, `site/about.html`, `site/styles.css`) to `v2026.2.26.3`.
- Refreshed CLI recipe docs examples to current release version where they displayed "current/latest" version outputs.

### Validation
- `.\.venv\Scripts\python.exe -m pytest -q tests/test_wizard_models.py tests/test_setup_catalog.py` → **55 passed**

## [2026.2.26.2] - 2026-02-26 🚀 Release: ESP32 + LEGO Runtime Support, Setup/Wizard Expansion, Docs/Site Refresh

### Added
- **Native ESP32 + LEGO runtime drivers** — added first-party protocol handlers for `esp32_websocket`, `ev3dev_tacho_motor` / `ev3dev_sensor`, and `spike_hub_serial` / `spike_hub_internal` with graceful mock-mode fallback.
- **Optional STEM hardware extras** — added optional dependency groups for ESP32/EV3/SPIKE paths in `pyproject.toml` (`esp32`, `ev3`, `spike`, and `stem-hardware`).
- **Regression coverage for new hardware paths** — added focused tests covering driver factory resolution, mock fallback behavior, setup verification errors, preset exposure, and tutorial reveal wiring.

### Changed
- **Setup catalog + wizard onboarding** — exposed `esp32_generic`, `lego_mindstorms_ev3`, and `lego_spike_prime` in setup catalog responses and wizard hardware selection.
- **Driver capability introspection** — expanded built-in driver registry names for new ESP32/LEGO protocols.
- **Auto-detect suggestions** — enhanced hardware detection heuristics for LEGO and ESP32-friendly suggestion paths.
- **Release metadata** — synchronized release surfaces to `v2026.2.26.2` and installer `VERSION=\"2026.2.26.2\"`.

### Fixed
- **Tutorial page visibility regression** — restored reveal observer wiring so content is visible on load in `site/tutorials.html`.
- **Setup verification hardening** — unknown/unsupported driver protocols now fail fast with actionable diagnostics.
- **Linting hardening for driver protocols** — unsupported driver protocols now emit explicit lint errors; disabled driver entries are skipped consistently.
- **ESP32 preset references** — removed broken firmware path references and documented endpoint contract requirements (`/status`, `/cmd`, `/ws`).
- **Beginner command/path correctness** — corrected non-working command examples and preset config paths across docs/site content.

### Documentation / Website
- Updated release/version labels in README and site surfaces to `v2026.2.26.2`.
- Updated tutorials with executable beginner flows for ESP32/EV3/SPIKE.
- Updated hardware guide and README examples to align with current CLI and preset locations under `config/presets/`.

### Validation
- Targeted verification for this release:
  - `tests/test_stem_hardware_support.py tests/test_setup_catalog.py tests/test_setup_service_v3.py tests/test_wizard_models.py tests/test_registry.py tests/test_doctor.py` → **118 passed**
  - `tests/test_hub.py` + setup verification targeted cases → **38 passed**

## [2026.2.23.12] - 2026-02-23 🧹 Strip JSON from channel replies + import fix

### Fixed
- **`castor/api.py`** — Added `_strip_action_json()` helper that removes the inline JSON action block (`{"type": ...}`) from AI replies before sending to users via messaging channels or TTS. The system prompt instructs the brain to append JSON for runtime parsing; this function strips it so end users only receive the human-readable portion.
- **`castor/api.py`** — Moved `import re as _re` to the correct alphabetical position in the import block (between `posixpath` and `signal`); fixes ruff **I001** import-sort violation.

---

## [2026.2.23.11] - 2026-02-23 🚗 Fix channel principal ACL — messaging channels now drive hardware

### Fixed
- **`castor/fs/permissions.py`** — The `channel` principal's ACL on `/dev/motor` was `"---"` (deny all), which silently blocked every WhatsApp / Telegram / Discord motor command from ever executing hardware. The `check_access()` function evaluates ACL before capabilities, so even though `channel` holds the `MOTOR_WRITE` capability, the path check returned False first. Fixed to `"rw-"` — the `required_caps=Cap.MOTOR_WRITE` gate remains the security control.
- **`tests/test_fs.py`** — Updated `test_permission_enforcement` to assert that `channel` **can** write to `/dev/motor` (was incorrectly asserting the opposite).

---

## [2026.2.23.10] - 2026-02-23 ⏱️ WaypointNav minimum drive duration for RC ESCs

### Fixed
- **`castor/nav.py`** — RC car ESCs require ~150–400 ms to spool up before the wheels move. Short distances (e.g. 1 inch ≈ 0.0254 m at speed 0.6 ≈ 0.19 s) computed a drive duration below the ESC response floor, so the command completed before the motor responded. Added `min_drive_s = 0.4` floor so every DRIVE phase runs at least 400 ms. Configurable via `physics.min_drive_s` in the RCAN config.

---

## [2026.2.23.9] - 2026-02-23 🎤 neonize 0.3.14 audio transcription fix

### Fixed
- **`castor/channels/whatsapp_neonize.py`** — neonize 0.3.14 renamed `client.download_media_message(sub_msg)` to `client.download_any(Message)` where `Message` is the full protobuf object (not the audio sub-message). `_transcribe_audio_message()` now accepts an optional `full_msg` parameter and prefers `download_any(full_msg)` when available, falling back to `download_media_message(audio_msg)` for older neonize versions.

---

## [2026.2.23.3] - 2026-02-23 🔧 CI lint & RCAN schema fixes

### Fixed
- **B904** — added `raise ... from exc` / `raise ... from None` to 5 `except` blocks in `castor/api.py` (fleet proxy and WebRTC endpoints)
- **F841** — removed unused `center_cm` variable in `castor/avoidance.py`; removed unused `audio` variable in `castor/tts_local.py`
- **B905** — added `strict=False` to `zip()` calls in `castor/episode_search.py` and `castor/providers/sentence_transformers_provider.py`
- **F401** — replaced bare try-import aliases with `importlib.util.find_spec` in `castor/pointcloud.py` (open3d), `castor/sim_bridge.py` (mujoco), `castor/workspace.py` (jwt)
- **B023** — fixed lambda not binding loop variable `woke` in `castor/voice_loop.py` → `lambda e=woke: e.set()`
- **RCAN schema** — added `safety` top-level property (`obstacle_stop_cm`, `estop_on_startup`), `fps` and `imu_enabled` to `camera` properties, and `groq` to provider enum in `config/rcan.schema.json` — fixes `groq_rover.rcan.yaml` and `oak4_pro.rcan.yaml` validation failures

---

## [2026.2.23.2] - 2026-02-23 🔭 8 New Features — OpenRouter, IMU, LiDAR, Avoidance, Cache, JS SDK, Finetuning, Embeddings — issues #166–#173

### Added
- **`castor/providers/openrouter_provider.py`** (issue #166) — OpenRouter provider giving access to 100+ models (GPT-4.1, Claude, Gemini, Mistral, DeepSeek, LLaMA 3.3, etc.) via a single `OPENROUTER_API_KEY`. Routes through `https://openrouter.ai/api/v1`; sends required `HTTP-Referer` and `X-Title` headers for API compliance.
- **`castor/drivers/imu_driver.py`** (issue #167) — IMU driver for MPU6050, BNO055, and ICM-42688 sensors via smbus2. Auto-detects chip by probing I2C addresses. Returns `{accel_g, gyro_dps, mag_uT, temp_c, mode}`. Mock mode when smbus2 unavailable. REST: `GET /api/imu/reading`, `GET /api/imu/health`.
- **`castor/drivers/lidar_driver.py`** (issue #168) — 2D LiDAR driver for RPLidar A1/A2/C1/S2 via rplidar SDK. 4-sector obstacle mapping (front/right/back/left). Returns `{angle_deg, distance_mm, quality}` per point; `obstacles()` → `{min_distance_mm, sectors}`. REST: `GET /api/lidar/scan`, `GET /api/lidar/obstacles`, `GET /api/lidar/health`.
- **`castor/avoidance.py`** (issue #169) — Reactive obstacle avoidance layer. Integrates LiDAR + depth sensors. E-stop zone (< 200 mm by default) → `driver.stop()`; slow zone (< 500 mm) → scales linear velocity by configurable `slow_factor`. REST: `GET /api/avoidance/status`, `POST /api/avoidance/configure`.
- **`castor/response_cache.py`** (issue #170) — SQLite-backed LRU response cache keyed by SHA-256(instruction + image_hash). Dramatically reduces API costs on repeated scenes/commands. `CachedProvider` wrapper is transparent to callers. REST: `GET /api/cache/stats`, `POST /api/cache/clear`, `POST /api/cache/enable`, `POST /api/cache/disable`. Env vars: `CASTOR_CACHE_DB`, `CASTOR_CACHE_MAX_AGE`, `CASTOR_CACHE_MAX_SIZE`, `CASTOR_CACHE_ENABLED`.
- **`sdk/js/`** (issue #172, renumbered) — TypeScript/JavaScript client SDK. `CastorClient` class with methods for `command()`, `stream()`, `status()`, `stop()`, `health()`, `listRecordings()`, and more. Ships with `package.json`, `tsconfig.json`, and JSDoc comments. Zero runtime dependencies.
- **`castor/finetune.py`** (issue #172) — Fine-tune data export CLI and API. Exports episode memory to JSONL in OpenAI chat-completion format. REST: `GET /api/finetune/export?limit=N&provider=openai`. CLI: `castor export-finetune [--output FILE] [--limit N] [--provider openai]`.
- **`castor/providers/sentence_transformers_provider.py`** (issue #173) — Sentence Transformers embedding provider. Encodes text into dense vectors via HuggingFace sentence-transformers. `think()` returns cosine similarity score in `raw_text`; suitable for semantic search and RAG pipelines. `pip install sentence-transformers`.
- **Dashboard** — new battery-level gauge panel and live object-detection overlay panel (issue #171).
- **API endpoints** — `/api/imu/*`, `/api/lidar/*`, `/api/avoidance/*`, `/api/cache/*`, `/api/finetune/*`.

### Tests
- 77 new tests covering imu_driver, lidar_driver, avoidance, response_cache, openrouter_provider, sentence_transformers_provider, finetune.

---

## [2026.2.23.1] - 2026-02-23 🗺️ 7 New Features — Point Cloud, Object Detection, VLA, Sim Bridge, Gamepad, SLAM, JS SDK — issues #149/#150/#154/#157–#161

### Added
- **`castor/pointcloud.py`** (issue #157) — 3D point cloud capture from OAK-D or simulated depth maps. Exports PLY files. Downsampling, normals estimation, clustering. REST: `GET /api/depth/pointcloud`, `GET /api/depth/pointcloud.ply`, `GET /api/depth/pointcloud/stats`.
- **`castor/detection.py`** (issue #160) — Real-time object detection via YOLOv8/HuggingFace DETR/mock. 80-class COCO. Configurable confidence threshold. Annotation overlays. REST: `GET /api/detection/frame`, `GET /api/detection/latest`, `POST /api/detection/configure`.
- **`castor/providers/vla_provider.py`** (issue #158) — Vision-Language-Action (VLA) provider. Wraps OpenVLA, Octo, or pi0 model checkpoints. Maps LLM-style think() interface to robot action tokens. `pip install opencastor[vla]`.
- **`castor/sim_bridge.py`** (issue #161) — Simulation bridge for MuJoCo, Gazebo, and Webots. Generates MJCF/SDF config from RCAN spec. REST: `GET /api/sim/formats`, `POST /api/sim/export`, `POST /api/sim/import`, `GET /api/sim/config`.
- **Dashboard gamepad panel** (issue #149) — HTML5 Gamepad API panel in the Streamlit dashboard. Live axis/button readout; maps joystick axes to `forward/backward/left/right` commands via REST.
- **Dashboard SLAM map panel** (issue #150) — Occupancy grid map rendered as an HTML5 canvas overlay in the dashboard. Reads from `/api/slam/map` (placeholder) and updates on each tick.
- **`sdk/js/`** (issue #154) — JavaScript/TypeScript SDK with full API coverage (see above).

---

## [2026.2.23.0] - 2026-02-23 🤖 10 New Features — Workspace isolation, ONNX, Chinese models, OAK-4, episode search, voice loop, finetuning, personalities, gesture API, WebRTC fixes

### Added
- **`castor/workspace.py`** — Multi-robot workspace isolation. Each robot gets its own sandboxed namespace and config scope. Supports workspace create/list/switch/delete. REST: `GET /api/workspace/list`, `POST /api/workspace/create`, `POST /api/workspace/switch`.
- **`castor/providers/onnx_provider.py`** — ONNX Runtime provider for quantized on-device inference. Loads `.onnx` model files locally. `ONNX_MODEL_PATH` env var. `pip install opencastor[onnx]`.
- **Chinese model support** — `castor/providers/kimi_provider.py` (Moonshot AI), `castor/providers/minimax_provider.py` (MiniMax), `castor/providers/qwen_provider.py` (Qwen3 local via Ollama). Env vars: `MOONSHOT_API_KEY`, `MINIMAX_API_KEY`.
- **OAK-4 Pro support** — `castor/depth.py` extended for OAK-4 Pro with IMU data, 4K RGB, and auto-detection. New preset: `config/presets/oak4_pro.rcan.yaml`.
- **`castor/episode_search.py`** — BM25 full-text search over episode memory. REST: `GET /api/memory/search?q=<query>&limit=N`.
- **`castor/voice_loop.py`** — Wake-word detection voice loop (Porcupine / pvporcupine). Separate thread: listens → wakes → transcribes → sends to brain. `PORCUPINE_ACCESS_KEY` env var.
- **`castor/finetune.py`** — LLM fine-tune data export (see v2026.2.23.2 for full description).
- **`castor/personalities.py`** — Robot personality profiles. Pre-defined personalities (friendly, military, scientist, child, etc.) inject system-level tone instructions into every brain prompt. REST: `GET /api/personality`, `POST /api/personality/set`.
- **Gesture REST API** — `POST /api/gesture/frame`, `GET /api/gesture/gestures` endpoints wired to `castor/gestures.py` (MediaPipe hand recognition).
- **WebRTC stability fixes** — `close_all_peers()` called on gateway shutdown; connection-state change handler added.

---

## [2026.2.22.5] - 2026-02-22

### Changed
- Version bump only (pre-release tag).

---

## [2026.2.22.4] - 2026-02-22 🎙️ 6 New Features — Voice conversation, wake word, WebRTC, recordings, webhooks, gestures

### Added
- **Voice conversation layer** — end-to-end audio pipeline: browser mic → transcribe → brain → TTS → speaker. Dashboard voice mode toggle (sidebar).
- **`castor/stream.py`** — WebRTC video stream via aiortc. `CameraTrack(VideoStreamTrack)`. `handle_webrtc_offer()`. `close_all_peers()` on shutdown. `pip install opencastor[webrtc]`.
- **`castor/recorder.py`** — `VideoRecorder`: MP4 recording via OpenCV. `get_recorder()` singleton. `start(name)`, `write_frame()`, `stop()→meta`. `CASTOR_RECORDINGS_DIR` env override. REST: `POST /api/recording/start`, `POST /api/recording/stop`, `GET /api/recording/list`.
- **`castor/webhooks.py`** — `WebhookDispatcher`: outbound POST hooks on robot events. `get_dispatcher()` singleton. `emit(event, data)` async. REST: `GET/POST /api/webhooks`, `POST /api/webhooks/test`.
- **`castor/gestures.py`** — `GestureController`: MediaPipe hand gesture → robot action. 8 default gestures. Mock mode when mediapipe absent. `pip install opencastor[gestures]`.

---

## [2026.2.22.3] - 2026-02-22 🚀 8 New Features — Deploy CLI, swarm, nav, behaviors, hub, JWT, ROS2, WebRTC

### Added
- **`castor/commands/deploy.py`** — `castor deploy pi@host --config ...`: SSH-push RCAN + restart service. `--full` for pip install. `--status`/`--dry-run`. Hosts cached in `~/.castor/hosts.json`.
- **`castor/commands/swarm.py`** — `castor swarm status/command/stop/sync`. Concurrent httpx queries. Rich table. `--json` flag.
- **`castor/nav.py`** — `WaypointNav`: dead-reckoning nav via `wheel_circumference_m` + `turn_time_per_deg_s`. REST: `POST /api/nav/waypoint`, `GET /api/nav/status`.
- **`castor/behaviors.py`** — `BehaviorRunner`: YAML step sequences (waypoint/wait/think/speak/stop/command). REST: `POST /api/behavior/run`, `POST /api/behavior/generate`.
- **`castor/commands/hub.py`** — `castor hub list/search/install/publish`. Index at `config/hub_index.json`. `CASTOR_HUB_URL` override.
- **Multi-user JWT** (`castor/auth_jwt.py`) — `OPENCASTOR_USERS=user:pass:role,...` admin/operator/viewer roles. `POST /auth/token`, `GET /auth/me`.
- **ROS2 driver** (`castor/drivers/ros2_driver.py`) — Publishes Twist to `/cmd_vel`, subscribes `/odom`. Mock mode. `pip install opencastor[ros2]`.
- **WebRTC offer endpoint** — `POST /api/webrtc/offer` wired to `castor/stream.py`.

---

## [2026.2.22.2] - 2026-02-22 🧪 Finetuning + Personality Profiles

### Added
- **`castor/finetune.py`** — Fine-tune data export in OpenAI and Anthropic formats. CLI: `castor export-finetune`.
- **`castor/personalities.py`** — Personality profile injection (friendly, military, scientist, child, pirate, chef). REST: `GET /api/personality`, `POST /api/personality/set`.

---

## [2026.2.22.1] - 2026-02-22 🔭 8 New Features — OAK-4 Pro, episode search, voice loop, workspace, ONNX, Chinese models, gestures

### Added
- OAK-4 Pro DepthAI camera with auto-detection and 4K+IMU support
- Episode memory BM25 search (`castor/episode_search.py`)
- Wake-word voice loop (`castor/voice_loop.py`)
- Multi-robot workspace isolation (`castor/workspace.py`)
- ONNX Runtime on-device inference provider
- Chinese model providers: Kimi (Moonshot), MiniMax, Qwen3
- MediaPipe gesture controller (`castor/gestures.py`)
- New preset: `config/presets/oak4_pro.rcan.yaml`

---

## [2026.2.22.0] - 2026-02-22 🧠 7 New Features — Provider fallback health cache, dashboard channel sorting, JS SDK foundation

### Changed
- Provider health-check cache (30s TTL) prevents flooding dead providers on `/api/status` refresh
- Dashboard channels table: active-first sort, 250px height, WhatsApp row no longer hidden
- `ProviderFallbackManager.health_check()` delegates to active provider
- CLAUDE.md slimmed from 50.9 KB → 12.4 KB with detailed docs split into `docs/claude/`

### Added
- `docs/claude/` reference docs: `api-reference.md`, `cli-reference.md`, `env-vars.md`, `structure.md`, `subsystems.md`

---

## [2026.2.21.13] - 2026-02-21 🧠 Memory, Metrics, Tools, MQTT — issues #92–#101

### Added
- **`castor/memory.py`** (issue #92) — SQLite-backed episode memory store (`EpisodeMemory`).
  Logs every brain decision (instruction → thought → action → latency → outcome) with FIFO eviction,
  UUID per episode, image hash dedup, JSONL export, and `CASTOR_MEMORY_DB` env override.
- **`castor/metrics.py`** (issue #99) — Prometheus text exposition metrics registry (stdlib-only,
  zero external dependencies). Pre-registers 13 standard metrics: loop counter, command counter,
  error counter, uptime gauge, latency gauge, camera FPS, brain/driver up flags, channel count,
  loop duration histogram, and more. `get_registry()` singleton. `record_loop()`, `record_command()`,
  `record_error()`, `update_status()` convenience helpers.
- **`castor/tools.py`** (issue #97) — LLM function/tool calling registry. `ToolRegistry` with 4
  built-in tools (`get_status`, `take_snapshot`, `announce_text`, `get_distance`). Auto-registers
  placeholder tools from RCAN `agent.tools` config. Exports OpenAI and Anthropic tool schemas.
  `call_from_dict()` handles both formats. `name` param made positional-only to avoid keyword conflict.
- **`castor/drivers/composite.py`** (issue #96) — `CompositeDriver` stacks multiple sub-drivers
  (base, gripper, pan-tilt) under one `DriverBase`. Routes action dict keys to the correct sub-driver
  via RCAN `routing:` config. `health_check()` aggregates sub-driver health. `_NullDriver` fallback.
- **`castor/channels/mqtt_channel.py`** (issue #98) — MQTT channel bridge via `paho-mqtt`.
  Subscribes to `opencastor/input`, publishes replies to `opencastor/output` (configurable).
  Handles TLS, auth, custom client ID, QoS. paho network loop runs in daemon thread; callbacks
  dispatch via `asyncio.run_coroutine_threadsafe`.
- **`castor/service.py`** (issue #100) — Satisfied by existing `castor/daemon.py` +
  `castor daemon enable/disable/status/logs/restart` CLI commands.
- **API endpoints** (issues #92 #93 #94 #95 #99):
  - `GET /api/metrics` — Prometheus text format (no auth required, safe for scrapers)
  - `POST /api/runtime/pause` / `POST /api/runtime/resume` — pause/resume perception loop
  - `GET /api/runtime/status` — runtime pause state, uptime, brain/driver readiness
  - `POST /api/config/reload` — hot-reload RCAN YAML without gateway restart
  - `GET /api/provider/health` — detailed provider health + token usage stats
  - `GET /api/memory/episodes` — list recent brain-decision episodes
  - `GET /api/memory/export` — download all episodes as JSONL
  - `DELETE /api/memory/episodes` — clear all episodes

### Changed
- **`castor/main.py`** — Integration: calls `get_registry().record_loop()` after each iteration (#99);
  calls `EpisodeMemory.log_episode()` after each brain decision (#92); checks `/proc/paused` VFS flag
  to honor `POST /api/runtime/pause` (#93).
- **`castor/providers/base.py`** — Added `get_usage_stats() -> dict` method (default returns `{}`).
- **`castor/providers/anthropic_provider.py`** — Implements `get_usage_stats()` via `runtime_stats`
  and `_cache_stats` (cache hit/miss counts). (#95)
- **`castor/providers/openai_provider.py`** — Implements `get_usage_stats()` via `runtime_stats`. (#95)
- **`castor/channels/__init__.py`** — Registers `MQTTChannel` with graceful `ImportError` fallback.
- **`castor/auth.py`** — Added `mqtt` entry to `CHANNEL_AUTH_MAP` (`MQTT_BROKER_HOST`, `MQTT_USERNAME`,
  `MQTT_PASSWORD`).
- **`castor/api.py`** — Added `asyncio` import, `paused: bool` field to `AppState`, new endpoints.
- **`castor/dashboard.py`** (issue #101) — Added `GET /api/memory/episodes` fetch; new collapsible
  "Episode Memory" expander showing episode history as a dataframe.
- **`castor/watch.py`** (issue #101) — `_learner_panel()` now accepts `episodes` dict; shows SQLite
  memory episode count and 3 most recent episodes (time, action type, latency) when Sisyphus learner
  is off. Fetch loop polls `GET /api/memory/episodes?limit=5` each tick.
- **`pyproject.toml`** — Added `mqtt = ["paho-mqtt>=2.0.0"]` optional extra; `paho-mqtt` included in
  `channels` meta-extra.

### Tests
- `tests/test_memory.py` — 9 tests covering log, query, count, get, clear, export, FIFO eviction,
  image hashing, source filtering, env var override. ✅ 9/9 pass
- `tests/test_metrics.py` — 12 tests covering Counter, Gauge, Histogram, MetricsRegistry convenience
  helpers, singleton. ✅ 12/12 pass
- `tests/test_tools.py` — 14 tests covering schema generation, ToolResult, registry CRUD, OpenAI/
  Anthropic call dispatch, built-in tools, config registration. ✅ 14/14 pass
- `tests/test_composite_driver.py` — 9 tests covering instantiation, stop/close, float/dict move
  forms, health_check, empty config, unknown protocol fallback. ✅ 9/9 pass
- `tests/test_mqtt_channel.py` — 13 tests covering defaults, custom config, env vars, start without
  paho, send when disconnected, callbacks, registry and auth map. ✅ 13/13 pass

## [2026.2.21.12] - 2026-02-21 📺 Single-page dashboards — Rich TUI + Streamlit rewrite, live OAK-D stream, MJPEG token auth

### Rewritten
- **`castor/watch.py`** — Complete rewrite as a full-screen Rich terminal dashboard mirroring the web layout.
  Uses `rich.layout.Layout` with `Live(screen=True)`. Header row (robot · brain · driver · channels · uptime),
  body split left (camera ASCII viewfinder with OAK-D USB3 stats + stream URL · recent commands) and right
  (status/telemetry · driver · channels · learner), footer with keyboard hints. Polls `/health`, `/api/status`,
  `/api/fs/proc`, `/api/driver/health`, `/api/learner/stats`, `/api/command/history`. `__main__` block added
  for `python -m castor.watch` invocation. `OPENCASTOR_API_TOKEN` env var used for all requests.
- **`castor/dashboard.py`** — Complete rewrite as single-page Streamlit (no tabs). HTML status bar header
  with brain/driver/channels/camera/uptime colour-coded dots. Left column: live MJPEG `<img>` with
  `?token=` query-param auth (works in browser without `Authorization` header) · voice mic button · chat
  input. Right column: `st.metric()` cards (uptime, loops, latency, camera, speaker) · driver panel ·
  channels dataframe · learner stats. Bottom: command history dataframe. Auto-refresh via
  `time.sleep(refresh_s); st.rerun()`. Sidebar: settings, emergency stop, voice mode toggle.
  Added `sys.path` guard to prevent `castor/watchdog.py` from shadowing the `watchdog` package.

### Fixed
- **`castor/api.py`** — `_frame_generator()` now uses `asyncio.to_thread(_capture_live_frame)` so the
  blocking DepthAI `_oakd_rgb_q.get()` call doesn't stall the event loop. Without this fix the MJPEG
  stream would return only the multipart boundary header (22 bytes) and no frames.
- **`castor/api.py`** — `verify_token()` now accepts a `?token=` query parameter as a fallback when no
  `Authorization: Bearer` header is present. Required for browser `<img>` and `<video>` tags which cannot
  set custom request headers.
- **`castor/dashboard.py`** — Launch with `--server.fileWatcherType none` to prevent Streamlit's
  `watchdog` import from colliding with `castor/watchdog.py` (motor safety watchdog module).

### Roadmap (new issues opened)
- #92 — `castor/memory.py`: persistent episode memory store (SQLite)
- #93 — `POST /api/runtime/pause` + `POST /api/runtime/resume` lifecycle control
- #94 — `POST /api/config/reload`: hot-reload RCAN YAML without restart
- #95 — `GET /api/provider/health`: per-provider token quota, rate limit, cost estimate
- #96 — Composite driver: stack PCA9685 + gripper + pan-tilt in one RCAN config
- #97 — LLM function/tool calling: define robot tools in RCAN, call from brain
- #98 — MQTT channel bridge: subscribe to broker topics as robot command source
- #99 — `GET /api/metrics`: Prometheus-compatible metrics endpoint
- #100 — `castor service install/uninstall`: systemd service management
- #101 — Learner improvement history panel + episode replay in dashboard and TUI

---

## [2026.2.21.11] - 2026-02-21 🎤 Full voice conversation layer — channels, transcription API, Speaker chunking

### Added (closes #84, #85, #86, #87, #88, #89, #90, #91)
- **`castor/voice.py`** — New shared audio transcription module with tiered engine pipeline:
  Whisper API (OpenAI) → local `openai-whisper` → Google SpeechRecognition → `None`.
  Exposed via `transcribe_bytes(audio_bytes, hint_format, engine)` and `available_engines()`.
  Engine selection controllable via `CASTOR_VOICE_ENGINE` env var or per-call `engine` param (#85)
- **`castor/channels/telegram_channel.py`** — `VOICE | AUDIO` message handler: downloads voice note
  via Telegram Bot API, detects MIME type, transcribes with `castor.voice`, routes as text (#87)
- **`castor/channels/discord_channel.py`** — Audio attachment detection in `on_message`: downloads
  audio via httpx, transcribes, routes reply — supports all common audio formats (#84)
- **`castor/channels/whatsapp_neonize.py`** — `audioMessage`/`voiceMessage` handling in
  `_handle_incoming()`: downloads media via `client.download_media_message()`, transcribes with
  `castor.voice`, falls back gracefully when voice module unavailable (#86)
- **`castor/channels/slack_channel.py`** — `file_share` subtype handling in `handle_dm`: detects
  audio MIME types, downloads via Slack Files API with Bearer auth, transcribes (#88)
- **`castor/api.py`** — `POST /api/audio/transcribe`: multipart audio upload endpoint using
  `castor.voice.transcribe_bytes()`; returns `{text, engine, duration_ms}`;
  503 when no engines available or transcription fails; 422 for empty file (#89)

### Fixed (closes #90, #91)
- **`castor/main.py`** `Speaker._speak()` — Removed hard 200-character truncation (`text[:200]`).
  Added `Speaker._split_sentences(text, max_chunk=500)` static method: splits on sentence boundaries
  (`[.!?]`) with whitespace fallback for long sentences; each chunk spoken sequentially with 150ms
  inter-sentence pause (#91)
- **`castor/dashboard.py`** — Voice Mode sidebar toggle; continuous listen mode via
  browser-native `SpeechRecognition` JavaScript bridge; browser `speechSynthesis` TTS for
  voice-mode replies (no 200-char limit); `sr.Recognizer.listen()` timeout extended to 8s with
  phrase_time_limit 30s; gTTS dashboard path now sentence-chunked (no truncation) (#90)

### Tests (closes #85, #89)
- **`tests/test_voice.py`** — 17 tests covering: `available_engines()`, `transcribe_bytes()` with
  all 4 engine modes (`auto`, `whisper_api`, `whisper_local`, `google`), env var override,
  hint format passthrough, and `Speaker._split_sentences()` correctness including no-truncation
  regression guard (#85)
- **`tests/test_api_endpoints.py`** — `TestAudioTranscribe`: 5 tests covering success path,
  503 when no engines available, 503 when transcription returns None, 422 for empty file,
  and engine parameter routing (#89)

---

## [2026.2.21.10] - 2026-02-21 🛡️ Provider streaming default, driver mock fix, guardian API, offline validation

### Fixed (closes #79)
- **`castor/drivers/base.py`** — `DriverBase.health_check()` default now returns `{ok: True, mode: "mock"}`.
  Mock mode is functioning correctly; returning `ok=False` was causing false alarms in telemetry/monitoring.

### Added (closes #76, #77, #78, #80, #81)
- **`castor/providers/base.py`** — `BaseProvider.think_stream()` default: yields the full `think()` response
  as a single chunk with a debug log line. All providers now have a guaranteed streaming interface; the
  `/api/command/stream` endpoint no longer silently degrades without logging (#76)
- **`castor/providers/base.py`** — `BaseProvider.health_check()` now respects
  `config["health_check_timeout_s"]` (default 5 s) via `concurrent.futures`; cross-platform, no SIGALRM (#80)
- **`castor/config_validation.py`** — `validate_rcan_config()` validates the optional `offline_fallback`
  block: when `enabled: true`, rejects unknown `provider` values (only `ollama`, `llamacpp`, `mlx` are
  accepted) with a clear error listing the valid options (#78)
- **`castor/api.py`** — `GET /api/status` now returns a structured `offline_fallback` dict:
  `{enabled, using_fallback, fallback_ready, fallback_provider, fallback_model}` instead of top-level
  `fallback_ready`/`using_fallback` fields (#77)
- **`castor/api.py`** — `GET /api/guardian/report`: returns the last `GuardianAgent` safety report from
  `swarm.guardian_report` in SharedState; `{available: false}` when guardian is not initialized (#81)

### Tests (closes #82, #83)
- **`tests/test_api_endpoints.py`** — `TestStreamCommandRateLimit`: 3 tests verifying `/api/command/stream`
  correctly enforces 429 rate limiting for both `think_stream()` and `think()` fallback code paths (#82)
- **`tests/test_api_endpoints.py`** — `TestStatusOfflineFallback`: 3 tests for the structured
  `offline_fallback` field in `/api/status` (#77)
- **`tests/test_api_endpoints.py`** — `TestGuardianReport`: 3 tests for `GET /api/guardian/report` (#81)
- **`tests/test_offline_fallback.py`** — `TestConnectivityChange`: 6 tests verifying that
  `_on_connectivity_change()` correctly switches the active provider and fires alerts (#83)
- **`tests/test_config_validation.py`** — `TestOfflineFallbackBlock`: 9 tests for the new
  `offline_fallback` block validation logic (#78)

## [2026.2.21.9] - 2026-02-21 🌊 Streaming API, learner endpoints, config validation

### Added (closes #68, #69, #70, #71, #72, #73, #74, #75)
- **`castor/api.py`** — `POST /api/command/stream`: NDJSON streaming endpoint; yields
  tokens from `think_stream()` if available, falls back to `think()`; records thought
  history and executes action on the driver (#68)
- **`castor/api.py`** — `GET /api/driver/health`: exposes `driver.health_check()` dict
  plus `driver_type` key; HTTP 503 when no driver is initialized (#69)
- **`castor/api.py`** — `GET /api/learner/stats`: returns Sisyphus loop statistics
  (`episodes_analyzed`, `improvements_applied`, `avg_duration_ms`, …); `available: false`
  when learner is not running (#70)
- **`castor/api.py`** — `GET /api/learner/episodes`: lists recent episodes from
  `EpisodeStore`; `limit` query param (max 100) (#70)
- **`castor/api.py`** — `POST /api/learner/episode`: saves a submitted episode and
  optionally runs the Sisyphus improvement loop (`run_improvement=true`) (#74)
- **`castor/api.py`** — `GET /api/command/history`: returns a ring buffer (max 50) of
  recent instruction→thought→action pairs (#75)
- **`castor/api.py`** — Ring buffer (`collections.deque(maxlen=50)`) in `AppState` and
  `_record_thought()` helper to populate it on every `/api/command` call (#75)
- **`castor/api.py`** — RCAN config validation via `log_validation_result()` called on
  startup; logs errors but does not block startup (#71)
- **`castor/api.py`** — `SisyphusLoop` auto-initialized in `on_startup()` when config
  provides an agent/provider (#71)
- **`castor/config_validation.py`** — New module: `validate_rcan_config()` returns
  `(is_valid, errors)` tuple; checks top-level keys, `agent.model`, `metadata.robot_name`,
  non-empty `drivers` list; `log_validation_result()` helper logs all errors (#71)
- **`castor/learner/sisyphus.py`** — Per-stage timing (`stage_durations` dict) and
  total/average duration tracking via `SisyphusStats.total_duration_ms` and
  `avg_duration_ms` property; provider wired into PM/Dev/QA stages (#66/#73)

### Tests
- `tests/test_api_endpoints.py` — 6 new test classes covering all new endpoints:
  `TestStreamCommand`, `TestDriverHealth`, `TestLearnerStats`, `TestLearnerEpisodes`,
  `TestSubmitEpisode`, `TestCommandHistory` (72 new tests total across all new files)
- `tests/test_config_validation.py` — 18 tests for `validate_rcan_config()` and
  `log_validation_result()`
- `tests/test_offline_fallback.py` — 17 tests for `OfflineFallbackManager`
  (probe, switching, lifecycle, alert notifications)
- `tests/test_learner/test_sisyphus_telemetry.py` — 17 tests for timing fields,
  stats accumulation, and provider wiring

## [2026.2.21.8] - 2026-02-21 🔒 Prompt injection defense, streaming think, driver health checks

### Security (closes #59, #65)
- **`castor/providers/base.py`** — `_check_instruction_safety()`: scans every incoming
  instruction for prompt injection before forwarding to the LLM; returns a blocking
  `{"type":"stop","reason":"prompt_injection_blocked"}` Thought on BLOCK verdict
- All four providers (Anthropic, Google, OpenAI, Ollama) call the new guard at the top of
  `think()` and `think_stream()`
- **`castor/api.py`** — Per-sender webhook rate limiting (`_check_webhook_rate()`):
  sliding 60-second window on `/webhooks/whatsapp` (by phone number) and
  `/webhooks/slack` (by user ID); `OPENCASTOR_WEBHOOK_RATE` env (default 10/min)

### Added (closes #60, #61, #62, #64, #66, #67)
- **`castor/channels/base.py`** — Async-safe callback dispatch in `handle_message()`:
  coroutine callbacks are `await`-ed; synchronous callbacks are offloaded with
  `asyncio.to_thread()` to avoid blocking the event loop (#60)
- **`castor/providers/ollama_provider.py`** — `health_check()` override: pings the
  Ollama root endpoint (no model loading, uses `health_timeout`) (#61)
- **`castor/providers/openai_provider.py`** — `health_check()` override: calls
  `client.models.list()` (no inference cost) (#61)
- **`castor/providers/google_provider.py`** — `health_check()` override: calls
  `genai.list_models()` (no inference cost) (#61)
- **`castor/channels/discord_channel.py`** — `on_message` handler wrapped in
  `try/except`; errors are logged, not propagated (#62)
- **`castor/channels/telegram_channel.py`** — `_on_text` handler wrapped in
  `try/except` (#62)
- **`castor/channels/slack_channel.py`** — `handle_mention` and `handle_dm` handlers
  wrapped in `try/except` (#62)
- **`castor/providers/google_provider.py`** — `think_stream()`: streams Gemini tokens
  via `generate_content(stream=True)` (#64)
- **`castor/providers/openai_provider.py`** — `think_stream()`: streams GPT tokens via
  `chat.completions.create(stream=True)` (#64)
- **`castor/providers/anthropic_provider.py`** — `think_stream()`: streams Claude tokens
  via `messages.stream()`; CLI path falls back to non-streaming (#64)
- **`castor/learner/sisyphus.py`** — Per-stage timing in `ImprovementResult`
  (`duration_ms`, `stage_durations`); `SisyphusStats.total_duration_ms` /
  `avg_duration_ms`; `provider=` wired into `PMStage`, `DevStage`, `QAStage` (#66)
- **`castor/drivers/base.py`** — `DriverBase.health_check() -> dict`; default returns
  `{"ok": False, "mode": "mock", "error": None}` (#67)
- **`castor/drivers/dynamixel.py`** — `health_check()`: pings the first connected
  Dynamixel motor via `packetHandler.ping()` (#67)
- **`castor/drivers/pca9685.py`** — `health_check()` on both `PCA9685RCDriver` and
  `PCA9685Driver`: returns `ok=True` when I2C hardware is active (#67)

### Closed (duplicate / already implemented)
- #63 — PCA9685 mock fallback already existed via `HAS_PCA9685` flag and
  `self.pca = None` pattern; driver `health_check()` added as part of #67

## [2026.2.21.7] - 2026-02-21 🔒 Security, health checks, LLM learner stages, TieredBrain tests

### Security (closes #51, #52)
- **`castor/api.py`** — Path traversal guard on all VFS endpoints: `_validate_vfs_path()`
  normalises paths with `posixpath.normpath` and rejects null bytes before any read/write
- **`castor/api.py`** — Per-IP rate limiting: sliding-window on `POST /api/command`
  (`OPENCASTOR_COMMAND_RATE` env, default 5 req/s) and concurrent-stream cap on
  `GET /api/stream/mjpeg` (`OPENCASTOR_MAX_STREAMS` env, default 3); returns 429 with
  `Retry-After` header

### Added (closes #53, #54, #55, #56, #58)
- **`castor/providers/base.py`** — `health_check() -> dict` on `BaseProvider`; returns
  `{ok, latency_ms, error}`; called at startup via `GET /api/status`
- **`castor/offline_fallback.py`** — `probe_fallback()` method + `fallback_ready` property;
  startup probe so broken fallbacks are discovered before they're needed;
  `GET /api/status` now exposes `fallback_ready` and `using_fallback`
- **`castor/api_errors.py`** — `CastorAPIError` exception + `register_error_handlers()`;
  all errors now return `{"error": str, "code": str, "status": int}` JSON envelope
- **`castor/learner/pm_stage.py`** — `PMStage(provider=…)`: optional LLM augmentation
  after heuristic analysis; merges additional improvements and refined root-cause from LLM
- **`castor/learner/dev_stage.py`** — `DevStage(provider=…)`: optional LLM fill for
  patches where heuristic produces `new_value=None`
- **`castor/learner/qa_stage.py`** — `QAStage(provider=…)`: optional LLM semantic check
  added to heuristic safety/consistency/type checks
- **`tests/test_tiered_brain.py`** — 22 new tests: `camera_required=False` bypass,
  Layer 3 swarm pass-through / override / error fallback, `get_stats()` edge cases

### Changed
- `castor/api.py` — `GET /api/status` now includes `provider_health`, `fallback_ready`,
  `using_fallback`; error responses standardised to new envelope

## [2026.2.21.6] - 2026-02-21 🤖 Layer 3 Agent Swarm (closes #12)

### Added
- **`castor/agents/manipulator_agent.py`** — `ManipulatorAgent`: wraps `ManipulatorSpecialist`
  for async swarm use; reads `swarm.manipulation_task`, publishes `swarm.manipulation_result`
- **`castor/agents/communicator.py`** — `CommunicatorAgent`: keyword-based NL intent parser
  + router; routes intents to navigator/manipulator/guardian via SharedState
- **`castor/agents/guardian.py`** — `GuardianAgent`: safety meta-agent with `SafetyVeto`
  dataclass; enforces forbidden action types, speed limit, and e-stop rules;
  publishes `swarm.guardian_report`
- **`castor/agents/orchestrator.py`** — `OrchestratorAgent`: master agent resolving all
  swarm outputs (estop → guardian veto → manipulation → nav plan → idle) into one RCAN
  action; exposes `sync_think()` for `TieredBrain` integration
- **`castor/tiered_brain.py`** — Layer 3 (Agent Swarm) hook: opt-in via
  `agents.enabled: true`; `swarm_count` / `swarm_pct` stats; graceful fallback on error
- **`tests/test_agents/`** — 90 tests covering all four new agents and Layer 3 integration

### Changed
- `castor/agents/__init__.py` — exports `CommunicatorAgent`, `GuardianAgent`,
  `ManipulatorAgent`, `OrchestratorAgent`, `SafetyVeto`

## [2026.2.21.5] - 2026-02-21 ✨ MLX Streaming, Hailo-8 Distance Safety, Hub Rate Command

### Added
- **`castor/providers/mlx_provider.py`** — streaming + vision improvements (#44):
  - `think_stream()` generator: yields tokens from both server-mode SSE and direct
    `mlx_lm.stream_generate()` (falls back to `generate()` when not available)
  - `_stream_server()` SSE parser: re-raises on error so `think_stream` emits error token
  - `__del__` clears `_mlx_model` and `_mlx_tokenizer` on garbage collection
- **`castor/hailo_vision.py`** — distance-based obstacle safety (#45):
  - `ObstacleEvent` dataclass: `distance_m`, `confidence`, `label`, `area`, `bbox`
  - `HailoDetection.estimate_distance_m(calibration)` — inverse-area distance estimate
  - `HailoDetection.to_obstacle_event(calibration)` — converts detection to safety event
  - `DEFAULT_AREA_CALIBRATION = 0.25` constant (area 0.25 ≈ 1.0m, area 0.5 ≈ 0.5m)
- **`castor/tiered_brain.py`** — configurable Hailo distance thresholds (#45):
  - `hailo_stop_distance_m` (default 0.5m): triggers e-stop
  - `hailo_warn_distance_m` (default 1.0m): triggers slow-down/avoid
  - `hailo_calibration` (default 0.25): per-camera distance calibration
  - `ReactiveLayer.evaluate()` now uses estimated distance instead of raw bbox area
- **`config/rcan.schema.json`** — three new `reactive.*` keys: `hailo_stop_distance_m`,
  `hailo_warn_distance_m`, `hailo_calibration` (#45)
- **`castor/hub.py`** — version fix + helper (#50):
  - `_opencastor_version()` helper reads live version from `castor.__version__`
  - `create_recipe_manifest()` now stamps current version instead of stale `2026.2.17.7`
- **`castor/cli.py`** — community hub `rate` command (#50):
  - `castor hub rate <recipe-id> --rating <1-5>` submits a GitHub issue with star rating
  - `_submit_rating()` helper; gracefully falls back with manual URL when `gh` not installed
  - Fixed f-string bug in `_interactive_share()` (config path was not interpolated)

### Tests Added
- `tests/test_mlx_provider.py` — `TestMLXProviderStreaming` (4 tests: token yield, final
  Thought via StopIteration, empty response, error token) + `TestMLXProviderVisionCI`
  (3 tests: image_url payload shape, small-image text-only path, `__del__` cleanup)
- `tests/test_hailo_vision.py` — `TestObstacleEvent` (4 tests: distance calc, zero-area,
  to_obstacle_event) + 5 new `TestReactiveLayerHailo` tests (config defaults, e-stop
  at stop_distance, slow-down at warn_distance)
- `tests/test_hub.py` — `TestRecipeVersion` (1 test: manifest version matches installed)

### Test Results
- **2348 passed, 8 skipped, 0 failed** (was 2332 before this release)

## [2026.2.21.4] - 2026-02-21 ✨ mDNS Fleet API, llama.cpp Streaming + Vision, 4 Operator Docs

### Added
- **`castor/fleet.py`** — public `get_peers(timeout=3.0) -> list` API for programmatic peer
  discovery; wraps existing `_discover_peers()` with documented return shape (#42)
- **`castor/providers/llamacpp_provider.py`** — major improvement (#43):
  - `think_stream()` generator: yields tokens one-by-one from both Ollama SSE and direct
    llama-cpp-python backends
  - Vision support: auto-detects llava/bakllava/moondream/minicpm-v model names and passes
    base64 image in prompt; accepts `clip_model_path` config key for GGUF clip models
  - Typed exceptions: `LlamaCppModelNotFoundError`, `LlamaCppConnectionError`,
    `LlamaCppOOMError` (all subclass `LlamaCppError`)
  - Validates GGUF file exists before attempting to load (raises `LlamaCppModelNotFoundError`)
  - Distinguishes Ollama unreachable (URLError → `LlamaCppConnectionError`) vs generic error
  - `__del__` frees direct model memory on garbage collection
- **`docs/agents.md`** — agent orchestration operator manual: all 5 specialists, RCAN config
  keys, monitoring commands, custom agent template (#46)
- **`docs/swarm.md`** — swarm deployment guide: 2-robot quick start, mDNS setup, role
  config, troubleshooting, known limitations (#47)
- **`docs/learner.md`** — Sisyphus operator manual: enable/disable, all CLI commands,
  auto-apply modes, reading PM reports, tuning parameters, rollback, cost estimates (#48)
- **`docs/recipes-cli.md`** — advanced CLI recipes for fleet, self-improvement, recording,
  replay, interactive REPL, diagnostics, config management, hub (#49)

### Tests Added
- `tests/test_providers.py` — 7 new `TestLlamaCppProvider` tests (typed exceptions, vision
  flag, Ollama think, streaming, mock model-not-found)
- `tests/test_rcan_mdns.py` — 14 new tests: `TestParseServiceInfo` (11 cases covering TXT
  record parsing, byte/str keys, fallback addresses) + `TestBrowserCallbacks` (3 cases)

### Test Results
- **2332 passed, 8 skipped, 0 failed** (was 2311 before this release)

## [2026.2.21.3] - 2026-02-21 🟢 Fix 5 Failing Tests + Stale README

### Fixed
- **`castor/channels/base.py`** — `handle_message()` now passes the original `text` to the
  `on_message` callback instead of the session-enriched string. Conversation context is stored
  in the session store but no longer leaked into the callback's text parameter (#40)
- **`castor/providers/base.py`** — `_clean_json()` rewritten to use brace-counting when
  scanning backwards, correctly handling nested JSON objects like
  `{"action": "move", "params": {"speed": 0.5}}`. Falls back to direct `json.loads()` first (#40)
- **`castor/safety/state.py`** — `SafetyTelemetry.snapshot_dict()` method added; delegates
  to `snapshot().to_dict()` for callers that need a plain dict (#40)
- **`tests/test_drivers.py`** — `test_pca9685_flag_false_in_test_env` now skips gracefully
  when Adafruit libs are installed (e.g., on a Raspberry Pi) rather than failing (#40)

### Changed
- **`README.md`** — "What's New" section updated to v2026.2.21.2/3; previously showed
  v2026.2.20.10 which was 7 patch versions behind (#41)

### Test Results
- **2311 passed, 8 skipped, 0 failed** (was 5 failed before this release)

## [2026.2.21.2] - 2026-02-21 📚 Documentation Refresh + 11 New Issues

### Highlights
Full documentation refresh bringing `CLAUDE.md` in sync with the actual codebase state
(v2026.2.21.1 shipped 131 modules, 8 providers, 16 presets, and 7 major subsystems that
were not yet reflected in the developer guide). A structured sprint backlog of 11 GitHub
issues has been created covering test fixes, local-provider improvements, fleet discovery,
Hailo-8 safety integration, and three new operator manuals.

### Changed
- **`CLAUDE.md`** — complete rewrite to match current repo state:
  - Version corrected to 2026.2.21.1 (was 2026.2.17.3)
  - 8 AI providers documented (Anthropic, Google, OpenAI, HuggingFace, Ollama, llama.cpp, MLX, OpenRouter)
  - 16 hardware presets documented (was 5)
  - All 7 new subsystems documented: `agents/`, `specialists/`, `learner/`, `swarm/`, `safety/`, `rcan/`, `fs/`
  - Plugin/registry system (`castor/registry.py`) documented
  - Tiered brain architecture documented
  - 40+ CLI commands listed (was 6)
  - New env vars: `OPENCASTOR_JWT_SECRET`, `OPENCASTOR_CORS_ORIGINS`, `SLACK_SIGNING_SECRET`
  - New package extras: `[rpi]`, `[rcan]`, `[dynamixel]`, `[all]`
  - Docker compose services corrected (2 services, not 4 profiles)
  - Full `castor/safety/` subsystem documented

### Issues Opened (Sprint Backlog)
- **#40** `fix` — Resolve 5 failing tests (channels, providers, safety state)
- **#41** `fix` — README.md version reference stale (v2026.2.20.10 vs v2026.2.21.1)
- **#42** `feat` — Implement mDNS peer discovery for fleet management
- **#43** `feat` — llama.cpp provider: streaming + robust error handling + vision
- **#44** `feat` — MLX provider: MLX-VL vision CI tests + streaming support
- **#45** `feat` — Wire Hailo-8 NPU detections into reactive safety layer
- **#46** `docs` — Agent orchestration operator manual
- **#47** `docs` — Swarm deployment guide
- **#48** `docs` — Learner/Sisyphus operator manual (tuning, patches, rollback)
- **#49** `docs` — Advanced CLI recipes (fleet, swarm, improve, record/replay)
- **#50** `feat` — Community hub: publish, browse, and rate recipes from CLI

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

# OpenCastor Subsystems Reference

Detailed documentation for all major subsystems.

---

## Provider Pattern (`castor/providers/`)

### BaseProvider (`castor/providers/base.py`)

All LLM adapters subclass `BaseProvider`. Key methods:

| Method | Signature | Description |
|--------|-----------|-------------|
| `think` | `(image_bytes, instruction) -> Thought` | Single inference call |
| `think_stream` | `(image_bytes, instruction) -> Iterator[str]` | Streaming token output |
| `health_check` | `() -> dict` | Returns `{ok, latency_ms, error}` |
| `get_usage_stats` | `() -> dict` | Token/cost stats (Anthropic/OpenAI implement; base returns `{}`) |

### Implementation Conventions

- Constructor resolves API key: env var → `.env` → RCAN config
- `think()` encodes image as base64 (OpenAI/Anthropic) or raw bytes (Google)
- Every `think()` and `think_stream()` call passes through `self._check_instruction_safety(instruction)` at the top — returns a blocking `Thought` on prompt injection detection
- System prompt forces strict JSON output only
- `_clean_json()` strips markdown fences from LLM responses
- `think_stream()` yields text chunks; all providers implement it (Anthropic CLI path yields a single chunk)

### Available Providers

| Provider | File | Key Env Var |
|----------|------|-------------|
| Google Gemini | `google_provider.py` | `GOOGLE_API_KEY` |
| OpenAI GPT-4.1 | `openai_provider.py` | `OPENAI_API_KEY` |
| Anthropic Claude | `anthropic_provider.py` | `ANTHROPIC_API_KEY` |
| Local Ollama | `ollama_provider.py` | `OLLAMA_BASE_URL` |
| HuggingFace Hub | `huggingface_provider.py` | HF CLI auth |
| llama.cpp | `llamacpp_provider.py` | Local binary |
| Apple MLX | `mlx_provider.py` | Local (macOS only) |
| Google Vertex AI | `vertex_provider.py` | `VERTEX_PROJECT` |
| OpenRouter (100+ models) | `openrouter_provider.py` | `OPENROUTER_API_KEY` |
| Sentence Transformers | `sentence_transformers_provider.py` | none (local) |
| VLA (OpenVLA/Octo/pi0) | `vla_provider.py` | none (local) |
| ONNX Runtime | `onnx_provider.py` | `ONNX_MODEL_PATH` |
| Kimi (Moonshot AI) | `kimi_provider.py` | `MOONSHOT_API_KEY` |
| MiniMax | `minimax_provider.py` | `MINIMAX_API_KEY` |
| Qwen3 (via Ollama) | `qwen_provider.py` | `OLLAMA_BASE_URL` |

Factory: `get_provider(config)` in `castor/providers/__init__.py`.

---

## Episode Memory (`castor/memory.py`)

### EpisodeMemory

SQLite-backed store for all brain decisions.

| Property | Value |
|----------|-------|
| Default DB path | `~/.castor/memory.db` |
| Override | `CASTOR_MEMORY_DB` env var |
| Max episodes | 10,000 (FIFO eviction when full) |

| Method | Description |
|--------|-------------|
| `log_episode(instruction, image_hash, thought, latency_ms)` | Record a brain decision |
| `query_recent(limit)` | Fetch N most recent episodes |
| `get_episode(id)` | Fetch single episode by ID |
| `export_jsonl()` | Export all episodes as JSONL string |
| `clear()` | Delete all episodes |
| `hash_image(bytes)` | SHA-256 hash of image bytes |
| `count()` | Total episode count |

Called in the perception-action loop after every brain decision. Also exposed via `GET /api/memory/episodes`, `GET /api/memory/export`, and `DELETE /api/memory/episodes`.

---

## Prometheus Metrics (`castor/metrics.py`)

### MetricsRegistry

Stdlib-only Prometheus implementation — no external dependencies.

- `get_registry()` — singleton accessor
- 13 pre-registered metrics including: `loop_latency_ms`, `brain_calls_total`, `motor_commands_total`, `errors_total`
- Exposed at `GET /api/metrics` as Prometheus text format

### Helper Functions

| Function | Description |
|----------|-------------|
| `record_loop(latency_ms, robot)` | Record perception-action loop timing |
| `record_command(action_type)` | Increment motor command counter |
| `record_error(source)` | Increment error counter |
| `update_status(running, paused)` | Update runtime state gauges |

---

## LLM Tool Calling (`castor/tools.py`)

### ToolRegistry

Named callable tools the LLM brain can invoke.

| Built-in Tool | Description |
|---------------|-------------|
| `get_status` | Return current robot status |
| `take_snapshot` | Capture camera frame |
| `announce_text` | Speak text via TTS |
| `get_distance` | Read distance sensor |

### API

```python
registry.call(name, /, **kwargs)           # name is positional-only
registry.call_from_dict(tool_call)          # OpenAI or Anthropic format
registry.to_openai_tools()                  # Schema for OpenAI function calling
registry.to_anthropic_tools()               # Schema for Anthropic tool use
```

**Important**: `call(name, /, **kwargs)` uses a positional-only `name` parameter (Python 3.10+ syntax). This avoids `TypeError: got multiple values for argument 'name'` when a tool has its own parameter named `name`.

`call_from_dict()` handles:
- **OpenAI format**: `arguments` is a JSON string
- **Anthropic format**: `input` is a dict

Register custom tools from RCAN `agent.tools` list via `_register_from_config()`.

---

## Composite Driver (`castor/drivers/composite.py`)

### CompositeDriver

Routes action dict keys to sub-drivers via RCAN `routing:` config.

```yaml
# Example RCAN routing config
drivers:
  - protocol: composite
    routing:
      wheels: pca9685
      arm: dynamixel
```

- Each sub-driver handles a specific action namespace
- `_NullDriver` fallback for unknown protocols (logs + no-ops)
- `health_check()` aggregates sub-driver health; reports `"degraded"` if any sub-driver fails

---

## Driver Pattern (`castor/drivers/`)

### DriverBase (`castor/drivers/base.py`)

All hardware drivers subclass `DriverBase`. Methods:

| Method | Description |
|--------|-------------|
| `move(action)` | Execute a motor action |
| `stop()` | Halt all motors |
| `close()` | Clean up hardware connections |
| `health_check()` | Returns `{ok, mode, error}` |

### Implementation Conventions

- Hardware SDKs imported in `try/except` with module-level `HAS_<NAME>` boolean
- Drivers degrade to **mock mode** when SDK is missing (log actions, no physical output)
- Values clamped to safe physical ranges (Dynamixel: 0–4095 ticks; PCA9685: duty cycle limits)
- `health_check()` returns `{ok: bool, mode: "hardware"|"mock", error: str|None}`
- `close()` must be called in `finally` blocks for clean shutdown

---

## Safety Subsystem (`castor/safety/`)

### Defense-in-Depth Architecture

| Component | File | Function |
|-----------|------|---------|
| `check_input_safety` | `anti_subversion.py` | Scans every instruction; returns `ScanVerdict.BLOCK` on prompt injection |
| `_check_instruction_safety` | `base.py` (providers) | Called at top of every `think()`/`think_stream()`; returns blocking `Thought` on BLOCK |
| `BoundsChecker` | `bounds.py` | Validates motor commands against joint/force/workspace limits |
| `WorkAuthority` | `authorization.py` | Approves/denies `WorkOrder` requests with full audit trail |
| `GuardianAgent` | `agents/guardian.py` | Safety meta-agent with veto authority + e-stop trigger |

### Safety Flow

```
Instruction → check_input_safety() → ScanVerdict
              BLOCK → return blocking Thought (no hardware movement)
              ALLOW → provider.think() → BoundsChecker → motor command
                                                         ↑
                                               GuardianAgent veto
```

---

## Virtual Filesystem (`castor/fs/`)

### CastorFS (`castor/fs/__init__.py`)

Unix-inspired filesystem with capability-based permissions.

### Namespaces

| Path | Purpose |
|------|---------|
| `/dev/motor` | Motor control nodes |
| `/etc/config` | Robot configuration |
| `/var/log` | Log storage |
| `/tmp` | Temporary data |
| `/proc` | Read-only runtime introspection |
| `/mnt` | External mounts |

### Capabilities

| Capability | Purpose |
|-----------|---------|
| `CAP_MOTOR_WRITE` | Write to motor nodes |
| `CAP_ESTOP` | Trigger emergency stop |
| `CAP_SAFETY_OVERRIDE` | Clear e-stop, override bounds |

### Memory Tiers

| Tier | Purpose |
|------|---------|
| `episodic` | Recorded robot episodes |
| `semantic` | Facts and knowledge base |
| `procedural` | Learned behaviors |

### Key Operations

```python
fs = CastorFS()
fs.read("/etc/config/robot_name")
fs.write("/dev/motor/speed", 0.5)        # Requires CAP_MOTOR_WRITE
fs.estop()                                # Propagates to all drivers
fs.clear_estop()                          # Requires CAP_SAFETY_OVERRIDE
```

- `ContextWindow`: sliding multi-turn context for agents
- `Pipeline`: Unix-pipe-style operation chaining
- `ProcFS`: read-only runtime introspection at `/proc`

---

## Provider Quota Fallback (`castor/provider_fallback.py`)

### ProviderFallbackManager

Transparent fallback on quota/credit errors.

| Feature | Detail |
|---------|--------|
| Trigger | `ProviderQuotaError` (HF HTTP 402/429 or quota keywords: `credits`, `quota`, `rate limit`) |
| Default cooldown | 3600s before retrying primary |
| Startup check | `probe_fallback()` health-checks backup at startup |
| Priority | Takes priority over `offline_fallback` in `_get_active_brain()` |

### RCAN Config Block

```yaml
provider_fallback:
  enabled: true
  provider: ollama          # ollama | google | openai | anthropic | llamacpp | mlx
  model: llama3.2:3b
  quota_cooldown_s: 3600
  alert_channel: telegram   # Optional: notify on switch
```

- `ProviderQuotaError` defined in `castor/providers/base.py`; has `provider_name` and `http_status` attrs
- `ProviderFallbackManager.health_check()` delegates to `get_active_provider().health_check()`
- `/api/status` health check routes through `_get_active_brain()` with 30-second cache to avoid flooding dead endpoints

---

## Offline Fallback (`castor/offline_fallback.py`)

### OfflineFallbackManager

Auto-switches to local inference on connectivity loss.

| Feature | Detail |
|---------|--------|
| Monitor | `ConnectivityMonitor` checks internet reachability |
| Local providers | Ollama, llama.cpp, MLX |
| Alert | Notifies via configured channel when switching |

### RCAN Config Block

```yaml
offline_fallback:
  enabled: true
  provider: ollama
  model: llama3.2:3b
  check_interval_s: 30
  alert_channel: telegram
```

Usage pattern:
```python
# Use this instead of state.brain.think(...)
state.offline_fallback.get_active_provider().think(image, instruction)
```

---

## RCAN Protocol (`castor/rcan/`)

### Overview

[RCAN Spec](https://rcan.dev/spec/) — current version 1.1.0.

| Component | Description |
|-----------|-------------|
| RURI | `rcan://domain.robot-name.id` addressing |
| RBAC | 5 roles: `CREATOR > OWNER > LEASEE > USER > GUEST` |
| JWT | `RCANTokenManager` signs/verifies tokens (`POST /api/auth/token`) |
| mDNS | Optional auto-discovery via `_rcan._tcp`; updates `discovered_peers` dict |
| Router | `MessageRouter` dispatches `RCANMessage` envelopes by type and RURI |

### RBAC Roles

| Role | Level | Permissions |
|------|-------|-------------|
| `CREATOR` | 5 | Full control, config write |
| `OWNER` | 4 | All operations |
| `LEASEE` | 3 | Temporary operator access |
| `USER` | 2 | Commands, no config |
| `GUEST` | 1 | Status read only |

---

## Multi-Agent Framework (`castor/agents/`)

### Architecture

All agents inherit `BaseAgent` and communicate via `SharedState` pub/sub event bus.

| Agent | Role |
|-------|------|
| `OrchestratorAgent` | Master; resolves multi-agent input to a single RCAN action |
| `GuardianAgent` | Safety meta-agent; veto authority over all motor commands |
| `ObserverAgent` | Parses vision output, publishes scene detections |
| `NavigatorAgent` | Path planning (potential fields algorithm) |
| `CommunicatorAgent` | Routes NL intent from messaging channels |
| `ManipulatorAgent` | Arm and gripper control |

### AgentRegistry

Spawns, monitors, and automatically restarts agents. All lifecycle managed here.

---

## Self-Improving Loop (`castor/learner/`) — Sisyphus Pattern

### 4-Stage Cycle

```
1. RECORD  → observation + action + outcome tuples
2. PM      → analyze episodes, find failure patterns
3. DEV     → generate patches (ConfigPatch / PromptPatch / BehaviorPatch)
4. QA      → validate patches; suggest retry or approve
5. APPLY   → deploy approved patches live
```

### Timing Tracking

`ImprovementResult.stage_durations` (dict):
- `pm_ms` — PM stage duration
- `dev_ms_attempt0`, `dev_ms_attempt1`, ... — Dev stage (one key per retry attempt)
- `qa_ms_attempt0`, `qa_ms_attempt1`, ... — QA stage (one key per retry attempt)
- `apply_ms` — Apply stage duration

`SisyphusStats.avg_duration_ms` — average across applied/rejected episodes.

### Patch Types

| Type | Purpose |
|------|---------|
| `ConfigPatch` | Modify RCAN config values |
| `PromptPatch` | Modify system prompt |
| `BehaviorPatch` | Modify behavior YAML sequences |

### Stage Internals

- `PMStage`, `DevStage`, `QAStage` store provider as `self._provider` (not `.provider`)
- `ALMAConsolidation` (`learner/alma.py`) aggregates patches from multiple swarm robots

---

## Swarm Coordination (`castor/swarm/`)

### Components

| Component | File | Purpose |
|-----------|------|---------|
| `SwarmCoordinator` | `coordinator.py` | Distributes tasks across `SwarmPeer` robots |
| `SwarmConsensus` | `consensus.py` | Majority-vote protocol for shared decisions |
| `SharedMemory` | `shared_memory.py` | Distributed key-value store |
| `PatchSync` | `patch_sync.py` | Incremental RCAN config sync across robots |
| `SwarmPeer` | `peer.py` | Remote robot proxy with HTTP client |
| `ALMAConsolidation` | `learner/alma.py` | Aggregates patches from multiple robots |

### Swarm Node Registry (`config/swarm.yaml`)

```yaml
nodes:
  - name: alex
    host: alex.local          # mDNS hostname
    ip: 192.168.68.91         # Static IP fallback
    port: 8000
    token: <OPENCASTOR_API_TOKEN>
    rcan: ~/OpenCastor/alex.rcan.yaml
    tags: [rpi5, camera, i2c, rover]
    added: "2026-02-21"
```

---

## Multi-Camera Support (`castor/camera.py`)

### CameraManager

Manages N simultaneous camera captures.

| Method | Description |
|--------|-------------|
| `get_frame(camera_id)` | Get frame from specific camera |
| `get_composite()` | Get composite view |

### Composite Modes

| Mode | Description |
|------|-------------|
| `tile` | Side-by-side grid of all cameras |
| `primary_only` | Single primary camera (backwards compatible) |
| `most_recent` | Frame from most recently updated camera |
| `depth_overlay` | RGB + OAK-D depth overlay combined |

`CAMERA_INDEX` env var selects primary camera (backwards compatible).
`GET /api/stream/mjpeg?camera=id` streams a specific camera.

---

## WebRTC Streaming (`castor/stream.py`)

- `CameraTrack(VideoStreamTrack)` wraps OpenCV capture
- `POST /api/stream/webrtc/offer` — SDP offer/answer exchange via aiortc
- ICE server config in RCAN: `network.ice_servers`
- Graceful fallback to MJPEG if aiortc not installed

---

## Home Assistant Channel (`castor/channels/homeassistant_channel.py`)

- Polls HA websocket for `input_text.castor_command` state changes
- Auth: `HA_LONG_LIVED_TOKEN` env var
- Exposes `switch.castor_<name>` and `sensor.castor_last_action` entities
- RCAN config:
  ```yaml
  channels:
    homeassistant:
      ha_url: http://homeassistant.local:8123
      ha_token: "${HA_LONG_LIVED_TOKEN}"
      entity_id: input_text.castor_command
  ```

---

## Fleet Management (`castor/fleet.py`)

- Discovers robots via mDNS `_rcan._tcp`
- `state.fleet_peers`: `ruri → {ip, port, last_seen}`
- `GET /api/fleet` — lists all discovered peers
- `POST /api/fleet/{ruri}/command` — proxies commands via RCAN bearer token
- `GET /api/fleet/{ruri}/status` — proxies status fetch

---

## Perception-Action Loop (`castor/main.py`)

Continuous OODA loop:

```
1. OBSERVE    → Capture camera frame via OpenCV
2. ORIENT     → check_input_safety() (anti-subversion)
3. DECIDE     → provider.think(frame, instruction) → Thought
4. ACT        → Thought.action → motor commands → driver.move()
5. TELEMETRY  → get_registry().record_loop(latency)
6. MEMORY     → EpisodeMemory().log_episode(...)
7. PAUSE?     → Check VFS /proc/paused flag (set by POST /api/runtime/pause)
8. ESTOP?     → Check VFS estop state before next iteration
```

### Voice Input

- `Listener` class in `castor/main.py` — SpeechRecognition-based voice input
- `listen_once() -> Optional[str]`
- Gated by `HAS_SR` boolean (graceful degradation when SpeechRecognition missing)

### Speaker

- `Speaker._split_sentences(text, max_chunk=500)` — sentence-chunked TTS
- 150ms pause between sentences
- No 200-char truncation (full text spoken)

---

## Authentication (`castor/auth.py`)

### Credential Resolution Order

1. **Environment variable** (e.g., `GOOGLE_API_KEY`)
2. **`.env` file** (loaded via python-dotenv)
3. **RCAN config fallback** (e.g., `config["api_key"]`)

### Key Functions

| Function | Description |
|----------|-------------|
| `resolve_provider_key(provider, config)` | Get API key for a provider |
| `resolve_channel_credentials(channel, config)` | Get all credentials for a channel |
| `list_available_providers()` | Dict of provider → readiness status |
| `list_available_channels()` | Dict of channel → readiness status |
| `check_provider_ready(provider)` | Boolean readiness check |
| `check_channel_ready(channel)` | Boolean readiness check |

### Multi-user JWT (`castor/auth_jwt.py`)

- `OPENCASTOR_USERS=user:pass:role,user2:pass2:role2` (SHA-256 passwords)
- `JWT_SECRET` → `OPENCASTOR_API_TOKEN` → random fallback for signing
- Roles: `admin(3) > operator(2) > viewer(1)`

---

## LLM Response Cache (`castor/response_cache.py`)

SQLite-backed LRU cache to avoid redundant API calls.

| Property | Default |
|----------|---------|
| DB path | `~/.castor/response_cache.db` (`CASTOR_CACHE_DB`) |
| TTL | 3600 s (`CASTOR_CACHE_MAX_AGE`) |
| Max entries | 10,000 (`CASTOR_CACHE_MAX_SIZE`) |
| Eviction | LRU (oldest first) |

### Key classes

- `ResponseCache` — singleton, thread-safe SQLite cache; `.get()`, `.put()`, `.clear()`, `.stats()`
- `CachedProvider` — transparent wrapper around any `BaseProvider`; delegates on miss, returns stored `Thought` on hit

Key: `SHA-256(instruction_utf8 + md5_hex(image_bytes))`.

```python
from castor.response_cache import get_cache, CachedProvider
cached_brain = CachedProvider(brain, get_cache())
thought = cached_brain.think(image_bytes, instruction)
```

REST: `GET /api/cache/stats`, `POST /api/cache/clear`, `POST /api/cache/enable`, `POST /api/cache/disable`.

---

## Reactive Obstacle Avoidance (`castor/avoidance.py`)

Fuses LiDAR and OAK-D depth readings to enforce safe distances.

| Zone | Default | Action |
|------|---------|--------|
| E-stop | < 200 mm | `driver.stop()` immediately |
| Slow | < 500 mm | Scale `linear` component by `slow_factor` (default 0.3) |

Sensor priority: LiDAR (RPLidar) → OAK-D depth → mock fallback.

REST: `GET /api/avoidance/status`, `POST /api/avoidance/configure`.

---

## IMU Driver (`castor/drivers/imu_driver.py`)

Supports MPU6050, BNO055, ICM-42688 via smbus2. Auto-detects by probing I2C addresses on startup.

```python
imu = IMUDriver(config)
reading = imu.read()
# -> {accel_g: {x,y,z}, gyro_dps: {x,y,z}, mag_uT, temp_c, mode}
```

Mock mode returns simulated values when smbus2 is unavailable.
REST: `GET /api/imu/reading`, `GET /api/imu/health`.

---

## 2D LiDAR Driver (`castor/drivers/lidar_driver.py`)

Supports RPLidar A1/A2/C1/S2 via the rplidar Python SDK.

```python
lidar = LidarDriver(config)
scan = lidar.scan()   # -> [{angle_deg, distance_mm, quality}, ...]
obs = lidar.obstacles()  # -> {min_distance_mm, sectors: {front,right,back,left}}
```

Mock mode generates synthetic scan data when rplidar is unavailable.
REST: `GET /api/lidar/scan`, `GET /api/lidar/obstacles`, `GET /api/lidar/health`.

---

## Point Cloud (`castor/pointcloud.py`)

Captures 3D point clouds from OAK-D stereo depth frames (or simulated data).

- `PointCloudCapture.capture() -> list[dict]` — `[{x,y,z}]` in metres
- `export_ply(points) -> bytes` — PLY file export for MeshLab / Open3D
- `get_stats(points) -> dict` — count, centroid, bounding box
- Open3D used if available (`HAS_OPEN3D`); detected via `importlib.util.find_spec`

REST: `GET /api/depth/pointcloud`, `GET /api/depth/pointcloud.ply`, `GET /api/depth/pointcloud/stats`.

---

## Object Detection (`castor/detection.py`)

Real-time object detection with annotation overlays.

- Backends: YOLOv8 (`ultralytics`), HuggingFace DETR, mock fallback
- 80 COCO classes; configurable `confidence_threshold`
- `ObjectDetector.detect(frame) -> list[Detection]` — `{class, confidence, bbox: {x,y,w,h}}`
- `annotate(frame, detections) -> JPEG bytes` — draws bounding boxes + labels

REST: `GET /api/detection/frame`, `GET /api/detection/latest`, `POST /api/detection/configure`.

---

## Simulation Bridge (`castor/sim_bridge.py`)

Generates and imports simulation config files from RCAN specs.

- `SimBridge.generate_sim_config(rcan, sim="mujoco") -> str` — MJCF/SDF string
- `SimBridge.export_to_file(rcan, fmt, output_path) -> Path`
- Supports: MuJoCo (MJCF), Gazebo (SDF/URDF), Webots (WBT)
- MuJoCo detected via `importlib.util.find_spec`; H5PY for HDF5 trajectory export

REST: `GET /api/sim/formats`, `POST /api/sim/export`, `POST /api/sim/import`, `GET /api/sim/config`.

---

## Voice Loop (`castor/voice_loop.py`)

Wake-word detection pipeline using Porcupine.

1. Background thread listens for wake word (`PORCUPINE_ACCESS_KEY` required)
2. On wake: transcribes audio via `castor/voice.py` (Whisper → Google SR)
3. Sends transcript to brain via REST `/api/command`
4. Robot responds and speaks via TTS

State machine: `idle` → `waiting` → `listening` → `thinking` → back to `waiting`.
Lambda fix: `lambda e=woke: e.set()` (properly binds loop variable).

---

## Personality Profiles (`castor/personalities.py`)

Injects persona tone into every brain prompt.

| Profile | System Prefix |
|---------|---------------|
| `friendly` | Warm, encouraging assistant |
| `military` | Terse, mission-focused |
| `scientist` | Analytical, data-driven |
| `child` | Simple vocabulary, curious |
| `pirate` | Nautical metaphors, "Arrr!" |
| `chef` | Cooking/food metaphors |

REST: `GET /api/personality`, `POST /api/personality/set`.

---

## Fine-Tune Export (`castor/finetune.py`)

Exports episode memory as JSONL training data.

- **OpenAI format**: `{"messages": [{"role": "user", "content": ...}, {"role": "assistant", "content": ...}]}`
- **Anthropic format**: `{"prompt": ..., "completion": ...}`
- Filters episodes with valid instruction + action pairs
- CLI: `castor export-finetune [--output FILE] [--limit N] [--provider openai|anthropic]`
- REST: `GET /api/finetune/export?limit=500&provider=openai`

---

## Workspace Manager (`castor/workspace.py`)

Provides isolated namespaces for multi-robot deployments.

- Each workspace has its own RCAN config, episode memory, and usage DB
- JWT availability detected via `importlib.util.find_spec("jwt")`
- `WorkspaceManager.create(name)`, `.switch(name)`, `.list()`, `.delete(name)`
- Stored at `~/.castor/workspaces/` (override: `CASTOR_WORKSPACE_DIR`)

REST: `GET /api/workspace/list`, `POST /api/workspace/create`, `POST /api/workspace/switch`.

---

## JavaScript/TypeScript SDK (`sdk/js/`)

Zero-dependency TypeScript client for browser and Node.js environments.

```typescript
import { CastorClient } from '@opencastor/sdk';

const client = new CastorClient({
  baseUrl: 'http://robot.local:8000',
  token: process.env.OPENCASTOR_API_TOKEN,
});

await client.command({ instruction: 'go forward 1 metre' });
const status = await client.status();
for await (const chunk of client.stream({ instruction: 'describe what you see' })) {
  process.stdout.write(chunk);
}
```

Methods: `command()`, `stream()`, `status()`, `stop()`, `health()`, `listRecordings()`, `getDepthFrame()`.

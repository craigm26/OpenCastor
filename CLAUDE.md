# CLAUDE.md - OpenCastor Development Guide

## Project Overview

OpenCastor is a universal runtime for embodied AI. It connects LLM "brains" (Gemini, GPT-4.1, Claude, Ollama, HuggingFace, llama.cpp, MLX) to robot "bodies" (Raspberry Pi, Jetson, Arduino, ESP32, LEGO) through a plug-and-play architecture, and exposes them to messaging platforms (WhatsApp, Telegram, Discord, Slack, Home Assistant) for remote control. Configuration is driven by YAML files compliant with the [RCAN Standard](https://rcan.dev/spec/).

**Version**: 2026.2.22.0 | **License**: Apache 2.0 | **Python**: 3.10+

> **Reference docs**: [`docs/claude/structure.md`](docs/claude/structure.md) · [`docs/claude/api-reference.md`](docs/claude/api-reference.md) · [`docs/claude/env-vars.md`](docs/claude/env-vars.md) · [`docs/claude/cli-reference.md`](docs/claude/cli-reference.md) · [`docs/claude/subsystems.md`](docs/claude/subsystems.md)

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

## Architecture

```
[ WhatsApp / Telegram / Discord / Slack / Home Assistant ]  <-- Messaging Channels
                    |
            [ API Gateway ]                    <-- FastAPI (castor/api.py)
            [ Web Wizard: /setup ]             <-- Browser setup wizard
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
    ┌───────────────────────────────────────┐
    │  VFS  │  Agents  │  Learner  │ Fleet  │  <-- Runtime Subsystems
    └───────────────────────────────────────┘
                    |
    [ Dynamixel / PCA9685 / ROS2 / mock ]     <-- Drivers (Nervous System)
                    |
              [ Your Robot ]                   <-- The Body
```

## Core Abstractions

| Class | File | Purpose |
|---|---|---|
| `Thought` | `castor/providers/base.py` | AI reasoning step: `raw_text` + `action` dict |
| `BaseProvider` | `castor/providers/base.py` | LLM adapter ABC: `think()`, `think_stream()`, `health_check()` |
| `DriverBase` | `castor/drivers/base.py` | Hardware driver ABC: `move()`, `stop()`, `close()`, `health_check()` |
| `BaseChannel` | `castor/channels/base.py` | Messaging integration ABC: `start()`, `stop()`, `send_message()` |
| `CastorFS` | `castor/fs/__init__.py` | Unix-style VFS with capability permissions, memory tiers, e-stop |
| `EpisodeMemory` | `castor/memory.py` | SQLite episode store; max 10k, FIFO; `CASTOR_MEMORY_DB` |
| `MetricsRegistry` | `castor/metrics.py` | Stdlib-only Prometheus; `get_registry()` singleton |
| `ToolRegistry` | `castor/tools.py` | LLM-callable tools; `call(name, /, **kwargs)` positional-only |
| `SisyphusLoop` | `castor/learner/sisyphus.py` | PM→Dev→QA→Apply continuous improvement loop |
| `BehaviorRunner` | `castor/behaviors.py` | YAML step sequences: waypoint/wait/think/speak/stop |
| `WaypointNav` | `castor/nav.py` | Dead-reckoning nav via `wheel_circumference_m` + `turn_time_per_deg_s` |
| `UsageTracker` | `castor/usage.py` | SQLite token/cost at `~/.castor/usage.db`; `CASTOR_USAGE_DB` |
| `ProviderFallbackManager` | `castor/provider_fallback.py` | Quota-error auto-switch; `ProviderQuotaError` |
| `CameraManager` | `castor/camera.py` | Multi-camera: tile/primary/most_recent composite modes |
| `CompositeDriver` | `castor/drivers/composite.py` | Routes action keys to sub-drivers via RCAN `routing:` config |
| Factory: `get_provider()` | `castor/providers/__init__.py` | LLM provider factory |
| Factory: `create_channel()` | `castor/channels/__init__.py` | Messaging channel factory |

## Authentication (`castor/auth.py`)

Credentials resolved in priority order:
1. **Environment variable** (e.g. `GOOGLE_API_KEY`)
2. **`.env` file** (loaded via python-dotenv)
3. **RCAN config fallback** (e.g. `config["api_key"]`)

Auth layers on API (in order): (1) Multi-user JWT (`JWT_SECRET` + `OPENCASTOR_USERS`) → (2) RCAN JWT (`OPENCASTOR_JWT_SECRET`) → (3) static bearer (`OPENCASTOR_API_TOKEN`) → (4) open. Roles: `admin(3) > operator(2) > viewer(1)`.

Key functions: `resolve_provider_key()`, `resolve_channel_credentials()`, `list_available_providers()`, `check_provider_ready()`.

→ See [docs/claude/api-reference.md](docs/claude/api-reference.md) for all API endpoints.
→ See [docs/claude/env-vars.md](docs/claude/env-vars.md) for all environment variables.

## Providers & Drivers

**Providers** (`castor/providers/`): Google Gemini, OpenAI GPT-4.1, Anthropic Claude, Ollama, HuggingFace, llama.cpp, MLX, Vertex AI. All implement `think(image_bytes, instruction) -> Thought`.

**Drivers** (`castor/drivers/`): PCA9685 (I2C PWM/Amazon kits), Dynamixel (Protocol 2.0), CompositeDriver (multi-driver routing), ROS2Driver (Twist publisher, mock mode).

**Channels** (`castor/channels/`): WhatsApp (neonize QR), WhatsApp (Twilio), Telegram, Discord, Slack, MQTT, Home Assistant.

## Configuration (RCAN)

- Config files use `.rcan.yaml` extension; follow [RCAN Spec](https://rcan.dev/spec/) v1.1.0
- Required keys: `rcan_version`, `metadata.robot_name`, `agent.model`, non-empty `drivers` list
- Validated by `castor/config_validation.py` on gateway startup
- 16 presets in `config/presets/`; generate new configs with `castor wizard`

```yaml
# Minimal RCAN config example
rcan_version: "1.1.0"
metadata:
  robot_name: my-robot
agent:
  provider: google
  model: gemini-1.5-flash
drivers:
- id: wheels
  protocol: pca9685
```

Provider quota fallback:
```yaml
provider_fallback:
  enabled: true
  provider: ollama
  model: llama3.2:3b
  quota_cooldown_s: 3600
```

Multi-camera support:
```yaml
cameras:
- id: front
  type: usb
  index: 0
  role: primary
- id: rear
  type: usb
  index: 1
  role: secondary
```

ROS2 driver:
```yaml
drivers:
- id: ros2_driver
  protocol: ros2
  cmd_vel_topic: /cmd_vel
  odom_topic: /odom
  max_linear_vel: 1.0
```

## CLI Commands

```bash
castor run      --config robot.rcan.yaml             # Perception-action loop
castor gateway  --config robot.rcan.yaml             # API gateway + channels
castor wizard   [--simple|--web]                     # Interactive setup
castor deploy   pi@192.168.1.10 --config robot.rcan.yaml  # SSH-push + restart
castor fleet    [--watch]                            # Discover + monitor robots
castor swarm    status/command/stop/sync             # Multi-node swarm ops
castor hub      list/search/install/publish          # Hardware preset registry
castor dashboard / castor demo / castor status       # UI + diagnostics
```

→ See [docs/claude/cli-reference.md](docs/claude/cli-reference.md) for all 50+ commands.

## Swarm Node Registry (`config/swarm.yaml`)

```yaml
nodes:
  - name: alex
    host: alex.local
    ip: 192.168.68.91
    port: 8000
    token: <OPENCASTOR_API_TOKEN>
    rcan: ~/OpenCastor/alex.rcan.yaml
    tags: [rpi5, camera, i2c, rover]
```

`castor swarm status` queries all nodes concurrently. `castor swarm command --instruction "go forward"` broadcasts to all nodes.

## Docker

```bash
docker compose up                                    # Gateway only
docker compose --profile hardware up                 # + hardware runtime
docker compose --profile dashboard up                # + Streamlit
docker compose --profile hardware --profile dashboard up  # Everything
```

## CI/CD

| Workflow | Trigger | Purpose |
|---|---|---|
| `ci.yml` | Push, PR | Tests + ruff lint + type check |
| `validate_rcan.yml` | Push/PR on `*.rcan.yaml` | JSON schema validation |
| `install-test.yml` | Scheduled | Multi-platform install test |
| `release.yml` | Tag push | PyPI release automation |
| `deploy-pages.yml` | Push to main | Cloudflare Pages deploy |

## Code Style

- **PEP 8**, 100-char line length (enforced by Ruff)
- **snake_case** functions/variables; **Type hints** on public signatures
- **Docstrings** on classes and non-trivial methods
- **Lazy imports** for optional SDKs (`HAS_<NAME>` boolean pattern)
- **Structured logging**: `logging.getLogger("OpenCastor.<Module>")`
- Lint: `ruff check castor/` / `ruff format castor/`

## Testing

```bash
pip install -e ".[dev]"
pytest tests/
```

**Current**: 2816 tests, 8 skipped, 0 failures (110+ test files)

Key fixture: `_reset_state_and_env` (autouse in `test_api_endpoints.py`) resets all `AppState` fields before every test including `thought_history = deque(maxlen=50)`, `learner = None`, `offline_fallback = None`, and clears `_command_history`/`_webhook_history`.

Test gotchas:
- `monkeypatch.setattr("castor.api.time.time", ...)` causes RecursionError — use `_command_history.clear()` instead
- `MagicMock` answers True to `hasattr(mock, anything)` — use `del mock._shared_state` to force False
- Structured error responses: `{"error": "...", "code": "HTTP_NNN"}` not `{"detail": "..."}`

## Adding New Components

### New AI Provider
1. Create `castor/providers/<name>_provider.py`, subclass `BaseProvider`
2. Implement `__init__` (resolve key), `think()`, `think_stream()`, `health_check()`
3. Call `self._check_instruction_safety(instruction)` at top of `think()` and `think_stream()`
4. Register in `castor/providers/__init__.py` (`get_provider()`)
5. Add env var to `castor/auth.py` `PROVIDER_AUTH_MAP` and `.env.example`

### New Hardware Driver
1. Create `castor/drivers/<name>.py`, subclass `DriverBase`
2. Implement `move()`, `stop()`, `close()` with mock fallback (`HAS_<NAME>` pattern)
3. Implement `health_check()` → `{ok, mode: "hardware"|"mock", error}`
4. Register in `get_driver()` in `castor/main.py`

### New Messaging Channel
1. Create `castor/channels/<name>.py`, subclass `BaseChannel`
2. Implement `start()`, `stop()`, `send_message()`; wrap handlers in `try/except`
3. Register in `castor/channels/__init__.py`
4. Add env vars to `castor/auth.py` `CHANNEL_AUTH_MAP` and `.env.example`
5. Add webhook endpoint to `castor/api.py` with `_check_webhook_rate()` applied

### New Hardware Preset
1. Create `config/presets/<name>.rcan.yaml`; follow RCAN schema; CI validates on push

See `CONTRIBUTING.md` for detailed examples and templates.

## Safety Considerations

- **Prompt injection**: `_check_instruction_safety()` at top of every `think()`/`think_stream()`
- **Rate limiting**: 5 req/sec/IP (`/api/command`), 10 req/min/sender (webhooks)
- **Bounds clamping**: Dynamixel 0–4095 ticks; PCA9685 duty cycle limits
- **E-stop**: `POST /api/stop`, `fs.estop()`, or via any messaging channel
- **`BoundsChecker`** validates motor commands; **`GuardianAgent`** has veto authority
- **`safety_stop: true`** in RCAN config enables emergency stop on startup
- `.env` in `.gitignore` — secrets never committed; JWT auth optional

## RPi5 Hardware Notes

- GPIO I2C: add `dtparam=i2c_arm=on` to `/boot/firmware/config.txt` → reboot → `/dev/i2c-1`
- PCA9685: won't respond until powered (external power rail)
- OAK-D: `pip install depthai==3.3.0` + `sudo udevadm control --reload-rules`
- USB speaker: `~/.asoundrc` → `defaults.pcm.card 2`; set `SDL_AUDIODRIVER=alsa` + `AUDIODEV=plughw:2,0`
- **Neonize pin**: Always `neonize==0.3.10` — v0.3.14+ breaks with system protobuf 5.x
- Dashboard: use `OPENCASTOR_API_TOKEN=... python -m streamlit run castor/dashboard.py --server.fileWatcherType none`

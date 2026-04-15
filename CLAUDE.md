# CLAUDE.md — OpenCastor Development Guide

> **Agent context file.** Read this before making any changes. Keep it up to date.

## What Is OpenCastor?

OpenCastor is the open-source **reference implementation of the RCAN protocol** (v1.9.0)). It connects LLM "brains" to robot "bodies" through a plug-and-play architecture and exposes them to messaging platforms for remote control.

- **Version**: 2026.4.15.0 (date-based: `YYYY.MM.DD.patch`)
- **RCAN**: v1.9.0 — see [rcan.dev/spec](https://rcan.dev/spec/)
- **License**: Apache 2.0 | **Python**: 3.10+ | **Tests**: 7804+ passing

## Quick Start

```bash
git clone https://github.com/craigm26/OpenCastor.git
cd OpenCastor
pip install -e ".[channels]"
cp .env.example .env
castor wizard        # interactive setup
castor gateway       # start API gateway
```

## Repository Layout

```
OpenCastor/
├── castor/                 # Core runtime
│   ├── api.py              # FastAPI gateway (main entry point)
│   ├── tiered_brain.py     # TieredBrain: fast/planner routing by task_category
│   ├── providers/          # LLM adapters (Gemini, Claude, GPT, Ollama, ...)
│   │   ├── task_router.py  # TaskRouter — routes tasks by category to providers
│   │   └── base.py         # BaseProvider ABC + Thought dataclass
│   ├── drivers/            # Hardware drivers (see full list below)
│   ├── channels/           # Messaging channels (WhatsApp, Telegram, Discord, ...)
│   │   └── rcan_mqtt_transport.py  # RCAN-over-MQTT carrier (compact/minimal encoding)
│   ├── contribute/         # Idle compute donation skill
│   │   ├── coordinator.py  # BOINC + simulated coordinators
│   │   ├── runner.py       # Work unit runner with cancellation
│   │   ├── work_unit.py    # WorkUnit / WorkUnitResult dataclasses
│   │   └── hardware_profile.py  # NPU/CPU detection
│   ├── rcan/               # RCAN protocol implementation
│   │   ├── registry.py     # RRN validation, REGISTRY_REGISTER/RESOLVE (§21)
│   │   ├── invoke.py       # InvokeRequest/Result, SkillRegistry (§19)
│   │   ├── parallel_invoke.py  # invoke_all(), invoke_race()
│   │   ├── message.py      # MessageType enum, RCANMessage
│   │   └── sdk_compat.py   # Compatibility layer for rcan-py SDK
│   ├── safety/             # Safety subsystem
│   │   ├── p66_manifest.py # P66 safety manifest — capability declarations
│   │   ├── bounds.py       # BoundsChecker — motor command validation
│   │   ├── monitor.py      # SensorMonitor — sensor health wiring
│   │   ├── authorization.py # HiTL authorization (§8)
│   │   ├── protocol.py     # SafetyLayer — wraps driver calls
│   │   └── state.py        # Safety state machine
│   ├── hardware/
│   │   └── so_arm101/      # SO-ARM101 6-DOF arm
│   │       ├── safety_bridge.py  # Routes arm commands through SafetyLayer
│   │       ├── vision.py         # Arm camera/vision pipeline
│   │       ├── rcan_bridge.py    # RCAN→arm command translation
│   │       └── cli.py            # Arm CLI utilities
│   ├── fleet/              # Fleet management, group policies
│   ├── privacy_mode.py     # Privacy mode — blocks cloud egress
│   └── sdk/                # Python SDK wrapper
├── sdk/js/                 # TypeScript/JS SDK (@opencastor/sdk)
│   └── src/index.ts        # CastorClient — typed wrappers for all API endpoints
├── website/                # Astro-based website (replaces old site/)
│   └── src/pages/          # Astro pages (index.astro, docs.astro, ...)
├── tests/                  # Test suite (pytest)
├── config/presets/         # RCAN config presets for common hardware
├── bob.rcan.yaml           # Bob robot config (gitignored — device-specific)
└── CHANGELOG.md            # Version history
```

## Key Abstractions

| Class | File | What it does |
|---|---|---|
| `TieredBrain` | `castor/tiered_brain.py` | Routes prompts: fast model or planner based on `task_category` |
| `TaskRouter` | `castor/providers/task_router.py` | Selects provider by `TaskCategory` (SENSOR_POLL → local-only, SAFETY → planner) |
| `BaseProvider` | `castor/providers/base.py` | LLM adapter ABC: `think()`, `think_stream()`, `health_check()` |
| `DriverBase` | `castor/drivers/base.py` | Hardware ABC: `move()`, `stop()`, `close()`, `health_check()`. Subclasses implement `_move()` — raw hardware call — while `move()` routes through `SafetyLayer` first |
| `SafetyLayer` | `castor/safety/protocol.py` | Wraps all driver commands; enforces bounds, HiTL gates, safety state |
| `BoundsChecker` | `castor/safety/bounds.py` | Validates motor commands against configured limits |
| `SensorMonitor` | `castor/safety/monitor.py` | Polls sensors; wires health signals into safety state |
| `P66Manifest` | `castor/safety/p66_manifest.py` | Declares robot capabilities and safety constraints (P66 standard) |
| `RegistryMessage` | `castor/rcan/registry.py` | RCAN §21 wire message. `RRNCategory` enum, `_validate_rrn()`, `metadata` block |
| `InvokeRequest` | `castor/rcan/invoke.py` | §19 INVOKE — skill name + params + timeout |
| `SkillRegistry` | `castor/rcan/invoke.py` | Maps skill names to handler callables |
| `FleetManager` | `castor/fleet/group_policy.py` | Group policies, config deep-merge |
| `CastorClient` | `sdk/js/src/index.ts` | TypeScript SDK — `invoke()`, `invokeAll()`, `invokeRace()`, `registryRegister()`, `registryResolve()` |

## Safety Architecture

```
move(cmd)  ──►  SafetyLayer.check(cmd)  ──►  _move(cmd)  ──►  hardware
                    │
                    ├── BoundsChecker     (hard limits)
                    ├── SensorMonitor     (sensor health gates)
                    ├── P66Manifest       (capability declarations)
                    └── HiTL AuthGate     (human-in-the-loop §8)
```

- **`DriverBase._move()`** — subclasses override this; raw hardware call, no safety logic
- **`DriverBase.move()`** — public method; always routes through `SafetyLayer` before calling `_move()`
- **`safety_bridge.py`** (SO-ARM101) — translates RCAN arm commands, enforces joint limits via `SafetyLayer`
- **`SensorMonitor`** — wired to driver `health_check()`; halts motion if sensor health degrades
- **`P66Manifest`** — machine-readable capability/constraint declarations; exposed at `GET /api/safety/manifest`
- `SAFETY` task category **always** uses planner (never downgraded by TieredBrain)
- `SENSOR_POLL` task category **never** escalates to planner (token budget guard)

## Drivers (full list)

| Driver | File | Protocol |
|---|---|---|
| PCA9685 | `pca9685.py` | I²C PWM (servos/ESC) |
| Dynamixel | `dynamixel.py` | TTL/RS485 serial |
| Feetech | `feetech_driver.py` | SCS/SMS serial |
| GPIO | `gpio_driver.py` | RPi GPIO |
| Arduino | `arduino_driver.py` | Serial |
| ESP32 BLE | `esp32_ble_driver.py` | BLE GATT |
| ESP32 WebSocket | `esp32_websocket.py` | WebSocket |
| ODrive | `odrive_driver.py` | USB/CAN |
| CAN | `can_transport.py` | SocketCAN |
| ROS2 | `ros2_driver.py` | ROS2 topics/actions |
| Stepper | `stepper_driver.py` | Step/dir GPIO |
| IMU | `imu_driver.py` | I²C/SPI |
| LiDAR | `lidar_driver.py` | Serial/UDP |
| Thermal | `thermal_driver.py` | I²C (MLX90640) |
| Battery | `battery_driver.py` | I²C/ADC |
| PiCamera2 | `picamera2_driver.py` | libcamera |
| ACB | `acb_driver.py` | HLabs ACB |
| EV3dev | `ev3dev_driver.py` | ev3dev2 |
| SPIKE | `spike_driver.py` | LEGO SPIKE |
| Reachy | `reachy_driver.py` | Pollen Robotics |
| Simulation | `simulation_driver.py` | In-process mock |
| Composite | `composite.py` | Multi-driver aggregator |

## API Endpoints (key)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/safety/manifest` | P66 safety manifest (capabilities + constraints) |
| `POST` | `/rcan` | RCAN message dispatch (INVOKE, COMMAND, REGISTRY_*) |
| `GET` | `/health` | Gateway health + driver status |
| `POST` | `/invoke` | Direct skill invocation shortcut |
| `GET` | `/api/contribute` | Idle compute contribution status |
| `GET` | `/api/harness` | Current harness configuration |

## RCAN Protocol (v1.9.0)

### MessageTypes
```python
DISCOVER = 1       # Robot announces presence
STATUS = 2         # Health/state query
COMMAND = 3        # Action instruction
STREAM = 4         # Continuous data stream
EVENT = 5          # Triggered state change
HANDOFF = 6        # Session transfer
ACK = 7            # Acknowledgment
ERROR = 8          # Error response
AUTHORIZE = 9      # HiTL approval (§8)
PENDING_AUTH = 10  # HiTL gate awaiting (§8)
INVOKE = 11        # Skill invocation (§19)
INVOKE_RESULT = 12 # Skill result (§19)
REGISTRY_REGISTER = 13    # Register with RRF (§21)
REGISTRY_RESOLVE = 14     # Resolve RRN→RURI (§21)
INVOKE_CANCEL = 15        # Cancel in-flight INVOKE (§19)
REGISTRY_REGISTER_RESULT = 16  # Registration result (§21)
REGISTRY_RESOLVE_RESULT = 17   # Resolution result (§21)
```

### Robot Registration Numbers (RRN)
```
RRN-000000000001                           # numeric (12 digits, RRF-assigned)
rrn://org/category/model/id               # URI 4-segment (recommended)
rrn://org/category/id                     # URI 3-segment
rrn://org/id                              # URI legacy 2-segment (category=robot)
```
Valid categories: `robot` | `component` | `sensor` | `assembly`

### Task Categories (TieredBrain routing)
```python
SENSOR_POLL  → fast model only (never escalates to planner — token budget guard)
NAVIGATION   → standard routing
REASONING    → planner preferred
CODE         → planner preferred
SAFETY       → planner ALWAYS (never downgraded)
VISION       → planner preferred
SEARCH       → planner preferred
```

## RCAN Config Format (v1.9.0)

```yaml
rcan_version: "1.6.1"
metadata:
  robot_name: my-robot
  rrn: RRN-000000000001
  rrn_uri: rrn://org/robot/model/id
  rcan_uri: rcan://robot.local:8000/my-robot
  version: 2026.3.21.1
agent:
  provider: google
  model: gemini-1.5-flash
task_routing:
  enabled: true
  categories:
    sensor_poll: {planner: false}
    safety:      {planner: true}
drivers:
  - id: wheels
    protocol: pca9685
```

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/                          # All tests
pytest tests/test_rcan_registry.py     # Registry + RRN tests
pytest tests/test_tiered_brain_task_routing.py  # Task routing
pytest tests/test_rcan_integration.py  # RCAN integration
pytest tests/test_config_validation.py # Config validation
pytest tests/test_conformance.py       # Conformance suite
ruff check castor/                     # Lint
ruff format castor/                    # Format
```

**Key test gotchas:**
- `_reset_state_and_env` autouse fixture resets `AppState` before every test
- `MagicMock` answers `True` to any `hasattr()` — use `del mock._shared_state` to force False
- Error responses use `{"error": "...", "code": "HTTP_NNN"}` not `{"detail": "..."}`
- `tick_count = 998` not `999` in routing tests (999 is divisible by default interval 10)

## Key Files

```
castor/safety/p66_manifest.py              # P66 capability/constraint manifest
castor/safety/protocol.py                  # SafetyLayer implementation
castor/safety/monitor.py                   # SensorMonitor
castor/hardware/so_arm101/safety_bridge.py # Arm safety routing
castor/hardware/so_arm101/vision.py        # Arm vision pipeline
castor/hardware/so_arm101/rcan_bridge.py   # RCAN→arm translation
castor/drivers/base.py                     # DriverBase (_move() pattern)
castor/rcan/registry.py                    # RRN validation + §21 registry
castor/rcan/invoke.py                      # §19 INVOKE + SkillRegistry
castor/api.py                              # FastAPI gateway
```

## CI/CD

| Workflow | Trigger | Action |
|---|---|---|
| `ci.yml` | Push / PR | pytest + ruff + mypy |
| `validate_rcan.yml` | `*.rcan.yaml` changes | JSON schema validation |
| `release.yml` | Tag push | PyPI publish |
| `deploy-pages.yml` | Main push | Cloudflare Pages (website/) |

Versioning: `YYYY.MM.DD.patch` — bump patch for each commit, date when date changes.

## Code Style

- **Python**: PEP 8, 100-char lines, snake_case, type hints on public signatures
- **Imports**: Ruff enforces — run `ruff format castor/ && ruff check castor/` before commit
- **Lazy imports**: `HAS_<NAME>` boolean pattern for optional hardware SDKs
- **Logging**: `logging.getLogger("OpenCastor.<Module>")`
- **TypeScript**: strict mode, no `any` on public surfaces

## Extending OpenCastor

### New Provider
1. `castor/providers/<name>_provider.py` → subclass `BaseProvider`
2. Implement `think()`, `think_stream()`, `health_check()`
3. Call `self._check_instruction_safety(instruction)` at start of `think()`
4. Register in `castor/providers/__init__.py` (`get_provider()`)
5. Add to `castor/auth.py` `PROVIDER_AUTH_MAP` + `.env.example`

### New Driver
1. `castor/drivers/<name>.py` → subclass `DriverBase`
2. Implement `_move()`, `stop()`, `close()` — **not** `move()`; safety routing is automatic
3. Add `HAS_<NAME>` mock fallback for optional hardware SDKs
4. Register in `castor/main.py` `get_driver()`

### New RCAN Skill (§19)
1. Define handler: `async def my_skill(params: dict) -> dict`
2. Register: `skill_registry.register("my_skill", my_skill)`
3. Test via `POST /rcan` with `{"msg_type": 11, "skill": "my_skill", "params": {...}}`

## Bob (the reference robot)

- **Hardware**: Raspberry Pi 5 16GB + Hailo-8 NPU + SO-ARM101 6-DOF follower arm + Luxonis OAK-D camera
- **RRN**: `RRN-000000000003` / `rrn://craigm26/robot/opencastor-rpi5-hailo-soarm101/bob-001`
- **Config**: `~/opencastor/bob.rcan.yaml` (gitignored — RCAN 3.0)
- **Host**: `robot.local`
- **RURI**: `rcan://robot.local:8001/bob`
- **Agent**: `claude-sonnet-4-6` via Claude Max/Pro (OAuth token at `~/.opencastor/anthropic-token`)
- **Servos**: Feetech STS3215 ×6 on `/dev/ttyUSB0`
- **Camera**: DepthAI OAK-D (`type: depthai` in config)

## Useful Links

- Spec: https://rcan.dev/spec/
- §19 Invoke: https://rcan.dev/spec/section-19/
- §21 Registry: https://rcan.dev/spec/section-21/
- Robot Registry Foundation: https://robotregistryfoundation.org/
- rcan-py SDK: https://github.com/continuonai/rcan-py

## Recent Features (2026-03-19)

- **Harness research pipeline**: `opencastor-autoresearch/harness_research/` — discovers optimal agent harness YAML configurations
- **Default harness**: `castor/harness/default_harness.yaml` (canonical source, auto-updated via harness-promote workflow)
- **Security fixes**: RCAN-Signature HMAC verification, None principal scope enforcement, `/setup` token removed, WebSocket JWT auth, webhook SSRF validation, LoA enforcement default=True
- **CLI consistency**: `/pause` `/resume` `/shutdown` `/snapshot` added to API + dashboard + client
- **pytest-asyncio**: `asyncio_mode=auto` configured project-wide

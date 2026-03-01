<p align="center">
  <img src="docs/assets/opencastor-logo.png" alt="OpenCastor" width="200"/>
</p>

<h1 align="center">OpenCastor</h1>
<h3 align="center">The Universal Runtime for Embodied AI</h3>

<p align="center">
  <a href="https://pypi.org/project/opencastor/"><img src="https://img.shields.io/pypi/v/opencastor?color=blue&label=PyPI" alt="PyPI"></a>
  <a href="https://github.com/craigm26/OpenCastor/actions"><img src="https://img.shields.io/github/actions/workflow/status/craigm26/OpenCastor/ci.yml?label=CI" alt="CI"></a>
  <a href="https://github.com/craigm26/OpenCastor/blob/main/LICENSE"><img src="https://img.shields.io/github/license/craigm26/OpenCastor?color=green" alt="License"></a>
  <a href="https://pypi.org/project/opencastor/"><img src="https://img.shields.io/pypi/pyversions/opencastor" alt="Python"></a>
  <a href="https://discord.gg/jMjA8B26Bq"><img src="https://img.shields.io/discord/1234567890?label=Discord&color=5865F2" alt="Discord"></a>
</p>

<p align="center">
  <b>99,547 lines of Python · 3,431 tests · Python 3.10–3.13</b><br/>
  <i>Connect any AI model to any robot hardware through a single YAML config.</i>
</p>

---

## 🚀 Install in 10 Seconds

```bash
curl -sL opencastor.com/install | bash
```

<details>
<summary>Other platforms</summary>

**Windows 11 (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/craigm26/OpenCastor/main/scripts/install.ps1 | iex
```

**Docker:**
```bash
docker compose up
```

**Manual:**
```bash
git clone https://github.com/craigm26/OpenCastor.git
cd OpenCastor
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
```

Supports **Linux, macOS (Apple Silicon & Intel), Windows 11, Raspberry Pi, Docker**.
Installer flags: `--dry-run`, `--no-rpi`, `--skip-wizard`
</details>

## ✨ What's New in v2026.3.1.14

- **Metrics p50/p95/p99 percentiles (#347)** — `ProviderLatencyTracker` now stores exact sorted samples and exposes `opencastor_provider_latency_p50/p95/p99_ms` Prometheus gauge per provider
- **BehaviorRunner event_wait step (#346)** — new `event_wait` step type polls a sensor until a threshold condition is met or timeout expires
- **BehaviorRunner foreach_file step (#341)** — new `foreach_file` step type iterates JSONL rows substituting `$item` placeholders into nested steps
- **EpisodeMemory k-means clustering (#342)** — `cluster_episodes()` groups episodes by action-type frequency using stdlib-only k-means (no sklearn)
- **IMUDriver Madgwick filter (#343)** — `MadgwickFilter` class fuses accel+gyro with configurable beta gain; enabled via `imu_filter: madgwick` config key
- **LidarDriver map persistence (#344)** — `save_map()` and `load_map()` persist occupancy grids to SQLite (JSON-encoded BLOB)
- **ProviderPool cost tracking (#345)** — per-provider token usage and USD cost accounting via `cost_summary()`; per-1k-token rate configurable
- **ProviderPool shadow mode (#340)** — parallel shadow provider fires in background thread, logs primary vs shadow action comparison to JSONL
- **ESP32 BLE driver (#287)** — `ESP32BLEDriver` sends JSON commands over BLE GATT characteristic via bleak (HAS_BLEAK guard)
- **Signal Messenger channel (#285)** — `SignalChannel` polls signal-cli REST API for messages; supports send and receive
- **castor snapshot CLI (#348)** — `castor snapshot take/latest/history` captures and displays system diagnostic snapshots
- **Dashboard memory timeline (#349)** — `MemoryTimeline` class buckets episodes by time for trend charts; p50/p95/p99 latency per window
- **Dashboard Mission Control panel (#283)** — new expandable Mission Control panel in CastorDash with launch/stop mission, outcome metrics, and latency KPIs
- **WhatsApp mission trigger (#282)** — `!mission <name>` keyword in any channel triggers named behavior launch; handled in `BaseChannel`
- **castor doctor improvements (#280)** — new checks: memory DB size, BLE (bleak) availability, Signal channel import status

## ✨ What's New in v2026.2.28.0

- **DeepSeek provider** — `provider: deepseek` unlocks `deepseek-chat`, `deepseek-reasoner` (R1), and `deepseek-coder` via the OpenAI-compatible API. Set `DEEPSEEK_API_KEY`. Vision input supported for `deepseek-vl2` models.
- **xAI Grok provider** — `provider: grok` adds `grok-2`, `grok-2-vision`, and `grok-2-mini` from xAI. Set `XAI_API_KEY`. Multi-modal vision supported for `grok-2-vision`.
- **Mistral AI provider** — `provider: mistral` adds `mistral-large-latest`, `mistral-small-latest`, `codestral-latest`, and `mistral-nemo`. Set `MISTRAL_API_KEY`. Vision supported for Pixtral models.
- **Dashboard channel display names** — Channels table now shows friendly names (e.g. "Microsoft Teams", "Matrix", "Home Assistant") instead of raw internal keys.
- **3,513 tests passing** across 128 test files.

## ✨ What's New in v2026.2.27.0

- **Quantum Commitment Audit Trail** — every RCAN action is now optionally sealed into a cryptographic commitment chain via [QuantumLink-Sim](https://github.com/craigm26/Quantum-link-Sim). Three key modes: `classical` (HKDF-SHA256), `quantum` (BB84 QKD-derived), and `hybrid` (XOR of both — requires breaking both channels). Configurable under `security.commitment` in RCAN YAML.
- **ESC reverse-arming** — PCA9685 driver now detects forward→reverse transitions and sends a configurable neutral pulse (`esc_arm_neutral_ms`, default 200ms) before engaging reverse, preventing ESC lockout on RC motor controllers. Configurable: `esc_reverse_arming`, `esc_arm_neutral_ms`, `esc_double_tap_reverse`.
- **QKDKeyPool** — background BB84 key-generation thread keeps a pre-warmed pool of quantum-derived keys so audit commit latency stays below 0.2ms on the hot path (vs ~10ms for live BB84).
- **HMAC chain integrity** — audit chain now uses HMAC-SHA256 keyed with a session-bound chain secret instead of raw SHA-256, preventing offline chain forgery even with full log file access.
- **`castor commit` CLI** — new subcommand: `verify`, `stats`, `export`, `proof <id>` for inspecting and verifying the quantum commitment chain.
- **3,441 tests passing** (29 skipped due to optional provider deps: `openai`, `groq` — not installed on Pi).

## ✨ What's New in v2026.2.26.3

- **Google setup reliability hardening (2026-02-26)** — setup preflight now reports Google model fallback usage accurately, model availability probing only runs when `GOOGLE_API_KEY` is present, and Google auth guidance now clearly distinguishes optional ADC setup from required Gemini API-key model calls

- **Stability pass (2026-02-25)** — full-suite hardening complete: 3,431 tests passing; fixed cross-platform daemon path rendering, JWT fallback edge cases, plugin SHA newline normalization, and async warning cleanup in Teams/WhatsApp channels
- **Messaging channels now drive hardware** — fixed a silent VFS ACL bug where the `channel` principal was denied write access to `/dev/motor`, causing every WhatsApp/Telegram/Discord motor command to reply but never move the wheels
- **Clean channel replies** — AI replies sent to messaging channels and TTS no longer include the raw JSON action block (`{"type": ...}`) the runtime uses internally
- **WaypointNav ESC floor** — added `min_drive_s = 0.4 s` so RC ESCs have time to spool up on short-distance commands (configurable via `physics.min_drive_s`)
- **neonize 0.3.14 audio** — voice messages on WhatsApp work again; uses `download_any()` API introduced in neonize 0.3.14
- **OpenRouter provider** — one API key unlocks 100+ models (GPT-4.1, Claude, Gemini, Mistral, DeepSeek, LLaMA 3.3…)
- **IMU driver** — MPU6050 / BNO055 / ICM-42688 accelerometer + gyroscope via smbus2, auto-detected
- **2D LiDAR driver** — RPLidar A1/A2/C1/S2, 4-sector obstacle mapping, REST API
- **Reactive obstacle avoidance** — fuses LiDAR + OAK-D depth; e-stop zone, slow zone, configurable via REST
- **LLM response cache** — SQLite cache keyed by SHA-256(instruction + image); dramatically cuts API cost on repeated scenes
- **JS/TS SDK** (`sdk/js/`) — zero-dependency TypeScript client: `command()`, `stream()`, `status()`, `stop()`
- **Fine-tune export** — `castor export-finetune` / `GET /api/finetune/export` — JSONL in OpenAI/Anthropic format from episode memory
- **Sentence Transformers provider** — dense embeddings for semantic search and RAG pipelines
- **VLA provider** — Vision-Language-Action model support (OpenVLA / Octo / pi0)
- **3D point cloud** (`castor/pointcloud.py`) — PLY export from OAK-D depth frames
- **Object detection** (`castor/detection.py`) — YOLOv8/DETR real-time detection with REST overlays
- **Simulation bridge** (`castor/sim_bridge.py`) — generate MuJoCo MJCF / Gazebo SDF from RCAN spec
- **Personality profiles** — inject tone characters (friendly, military, scientist…) into every brain prompt
- **Wake-word voice loop** (`castor/voice_loop.py`) — Porcupine → STT → brain pipeline
- **Groq provider preset** — `groq_rover.rcan.yaml` for LPU-accelerated inference
- **OAK-4 Pro preset** — `oak4_pro.rcan.yaml` with 4K + depth + IMU auto-detection

> Previous highlights (v2026.2.22.x): ROS2 driver · swarm CLI · dead-reckoning nav · YAML behavior runner · multi-user JWT · WebRTC streaming · MediaPipe gestures · video recording · outbound webhooks · BM25 episode search

## 🔄 Self-Improving Loop (Sisyphus Pattern)

Your robot learns from its mistakes. After each task, the **Sisyphus Loop** analyzes what happened, identifies failures, generates fixes, verifies them, and applies improvements automatically.

```
Episode → PM (Analyze) → Dev (Patch) → QA (Verify) → Apply
```

- **Disabled by default** — opt-in via `castor wizard` or YAML config
- **4 cost tiers** — from $0 (local Ollama) to ~$5-15/mo (Claude)
- **Auto-apply preferences** — config tuning only, behavior rules, or manual review
- **Rollback** — undo any improvement with `castor improve --rollback <id>`
- **ALMA consolidation** — cross-episode pattern analysis for deeper learning

```bash
castor improve --episodes 10    # Analyze last 10 episodes
castor improve --status         # View improvement history
```

## 🧠 Tiered Brain Architecture

OpenCastor doesn't send every decision to a $0.015/request cloud API. Instead, it routes through three layers — only escalating when needed:

```
┌─────────────────────────────────────────────────────────┐
│                    MESSAGING LAYER                       │
│         WhatsApp · Telegram · Discord · Slack            │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                   API GATEWAY                            │
│            FastAPI · REST · Webhooks · JWT               │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│               TIERED BRAIN STACK                         │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Layer 3: PLANNER         Claude Opus · ~12s       │  │
│  │  Complex reasoning, multi-step plans, novel tasks  │  │
│  ├────────────────────────────────────────────────────┤  │
│  │  Layer 2: FAST BRAIN      HF / Gemini · ~500ms    │  │
│  │  Classification, simple Q&A, routine decisions     │  │
│  ├────────────────────────────────────────────────────┤  │
│  │  Layer 1: REACTIVE        Rule engine · <1ms       │  │
│  │  Obstacle stop, boundary enforce, emergency halt   │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                  PERCEPTION                              │
│  Hailo-8 NPU (YOLOv8) · OAK-D Depth · Camera · IMU    │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│               RCAN SAFETY KERNEL                         │
│    Physical bounds · Anti-subversion · Audit chain       │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                  DRIVER LAYER                            │
│       PCA9685 · Dynamixel · GPIO · Serial · I2C         │
└──────────────────────┬──────────────────────────────────┘
                       │
                  [ YOUR ROBOT ]
```

**Cost-effective by default.** The reactive layer handles 80% of decisions at zero cost. The fast brain handles another 15%. The planner only fires for genuinely complex tasks.

## 🤖 Supported AI Providers

| Provider | Models | Latency | Best For |
|---|---|---|---|
| **Anthropic** | `claude-opus-4-6`, `claude-sonnet-4-6` | ~12s | Complex planning, safety-critical reasoning |
| **Google** | `gemini-2.5-flash`, `gemini-2.5-pro` | ~500ms | Multimodal, video, speed |
| **OpenAI** | `gpt-4.1`, `gpt-4.1-mini` | ~2s | Instruction following, 1M context |
| **OpenRouter** | 100+ models (Mistral, DeepSeek, LLaMA…) | Varies | Multi-model fallback, cost optimization |
| **HuggingFace** | Transformers, any hosted model | ~500ms | Fast brain layer, classification |
| **Ollama** | `llava:13b`, any local model | Varies | Privacy, offline, zero cost |
| **llama.cpp** | GGUF models | ~200ms | Edge inference, Raspberry Pi |
| **MLX** | Apple Silicon native (mlx-lm, vLLM-MLX) | ~50ms | Mac M1–M4, 400+ tok/s |
| **Apple Foundation Models** | `apple-balanced`, `apple-creative`, `apple-tagging` | ~50-300ms | Apple Intelligence on eligible Macs |
| **Groq** | `llama-3.3-70b`, `mixtral-8x7b` | ~100ms | LPU-accelerated, fastest API |
| **VLA** | OpenVLA, Octo, pi0 | Varies | End-to-end robot action models |
| **Sentence Transformers** | `all-MiniLM-L6-v2`, others | ~10ms | Semantic search, RAG, embeddings |
| **ONNX Runtime** | Any `.onnx` model | ~50ms | Quantized on-device inference |
| **Kimi / MiniMax / Qwen** | Chinese LLMs | Varies | Chinese language, local Qwen3 |
| **Claude OAuth** | Proxy-authenticated Claude | ~12s | Team/org deployments |

Swap providers with one YAML change:

```yaml
agent:
  provider: "anthropic"
  model: "claude-opus-4-6"
```

## 👁️ Vision & Perception

### Hailo-8 NPU — On-Device Object Detection

No cloud API calls needed. The Hailo-8 neural processing unit runs YOLOv8 locally:

- **80 COCO object classes** — people, vehicles, animals, furniture, and more
- **~250ms inference** — fast enough for real-time obstacle avoidance
- **Zero API cost** — all processing happens on the edge

### OAK-D Stereo Depth Camera

RGB + depth streaming via DepthAI v3. Get 3D spatial awareness for navigation, manipulation, and mapping.

```yaml
perception:
  camera:
    type: "oakd"
    depth: true
    resolution: "1080p"
  npu:
    type: "hailo8"
    model: "yolov8n"
    confidence: 0.5
```

## 🛡️ Safety First

OpenCastor implements defense-in-depth safety, inspired by [ContinuonAI](https://github.com/craigm26) principles and fully [RCAN spec](https://rcan.dev/spec/) compliant:

| Layer | What It Does |
|---|---|
| **Physical Bounds** | Workspace limits, joint constraints, force capping |
| **Anti-Subversion** | Prompt injection defense, input sanitization |
| **Work Authorization** | Dangerous commands require explicit approval |
| **Tamper-Evident Audit** | Hash-chained logs at `/proc/safety` — any tampering is detectable |
| **Emergency Stop** | Hardware and software e-stop, reactive layer < 1ms |

```bash
castor audit --verify        # Verify audit chain integrity
castor approvals             # View/approve dangerous commands
castor privacy --config r.yaml  # Show sensor access policy
```

## 🔐 Quantum Commitment Audit Trail

OpenCastor integrates with [QuantumLink-Sim](https://github.com/craigm26/Quantum-link-Sim) to provide **cryptographically verifiable audit trails** for every robot action. Every RCAN event can be sealed into a QKD-keyed commitment chain — making tampering detectable even if the log file itself is modified.

### Why This Matters

Standard audit logs are hash-chained (SHA-256 of previous entry). That's tamper-*evident* — you can detect changes — but not tamper-*proof*: anyone with write access to the log file can recompute valid SHA-256 hashes and forge the chain without a trace.

OpenCastor's quantum commitment layer adds a **keyed HMAC chain** backed by cryptographic keys derived from BB84 Quantum Key Distribution simulation. Without the session-bound chain secret, forging the chain is computationally infeasible — and in hybrid mode, requires simultaneously breaking both a classical HKDF key and eavesdropping on the BB84 channel.

This matters in the context of autonomous robots operating in safety-critical environments: the audit trail is your forensic record. It needs to be trustworthy.

### Key Modes

| Mode | Key Source | Security Model | Commit Latency |
|---|---|---|---|
| `classical` | `HKDF(os.urandom(32), SHA-256)` | Computational — breaks only if HKDF/AES is broken | < 0.05 ms |
| `quantum` | BB84 QKD simulation → HKDF expand | Information-theoretic — eavesdropping detectable via QBER | < 0.15 ms (warm pool) |
| `hybrid` *(recommended)* | `XOR(classical_key, quantum_key)` | Adversary must break **both** simultaneously | < 0.20 ms (warm pool) |

**Hybrid is the default.** An attacker needs to compromise the classical HKDF key **and** eavesdrop the quantum channel simultaneously — a significantly harder problem than either alone.

### How It Works

```
RCAN Action (motor_command, nav, etc.)
         │
         ▼
  AuditLog.log()          ← SHA-256 hash chain (tamper-evident)
         │
         ▼
  CommitmentEngine.commit()
    │
    ├── Serialize payload → JSON (sorted keys)
    ├── SHA3-256 hash (quantum-resistant content address)
    ├── Derive key:
    │    ├── CLASSICAL: HKDF(os.urandom(32))
    │    ├── QUANTUM:   BB84 key from QKDKeyPool → HKDF expand to 32 bytes
    │    └── HYBRID:    XOR(classical_key, quantum_key)
    ├── AES-256-GCM encrypt (AAD = payload_hash)
    ├── HMAC-SHA256(chain_secret, prev_hash || payload_hash) → chain_hash
    └── CommitmentRecord → appended to in-memory chain + JSONL file
```

Each audit log entry gains three fields:
```json
{
  "ts": "2026-02-27T19:35:46",
  "event": "motor_command",
  "source": "brain",
  "action_type": "move",
  "commitment_id": "a1b2c3d4-...",
  "commitment_mode": "hybrid",
  "commitment_qber": 0.0021,
  "commitment_secure": true,
  "prev_hash": "sha256-of-previous-entry"
}
```

### Configuration

```yaml
# In your RCAN config (e.g. bob.rcan.yaml)
security:
  commitment:
    enabled: true
    mode: hybrid                              # classical | quantum | hybrid
    pool_size: 32                             # pre-generated QKD keys in memory
    n_qkd_bits: 512                           # raw BB84 qubits per key generation run
    qber_threshold: 0.11                      # max QBER (>11% = likely eavesdropping)
    use_qiskit: false                         # Qiskit circuit backend (set true for max fidelity)
    storage_path: .opencastor-commitments.jsonl
    export_secret_path: .opencastor-chain-secret.hex
```

### QKD Key Pool

BB84 key generation takes ~8–15ms per key on a Raspberry Pi 5. To prevent this from blocking the audit hot path, OpenCastor maintains a **QKDKeyPool** — a background thread that pre-generates and buffers up to N quantum-derived keys:

```
Background thread (10ms poll interval)
  └── BB84Protocol(n_bits=512).run()
        └── sift → error correct → privacy amplify → HKDF expand → 32 bytes
              └── enqueue to pool (bounded, ~32 slots)

Audit hot path (< 0.2ms with warm pool):
  └── pool.get()  →  AES-256-GCM encrypt  →  HMAC chain step  →  append
```

If the pool runs dry (e.g., high-frequency audit events exhaust it), it falls back to live BB84 (~10ms) or classical HKDF (< 0.05ms) depending on configuration.

### BB84 Quantum Bit Error Rate (QBER)

The QBER measures the error rate on the quantum channel. In an ideal BB84 run with no noise or eavesdropping, QBER = 0. OpenCastor rejects keys with QBER > 11% — the theoretical threshold above which Eve's interception is detectable:

```
QBER = 0.0%      Ideal — no noise, no eavesdropper
QBER < 11%       Acceptable — minor channel noise, key used
QBER 11–25%      Suspect — possible eavesdropper, key rejected
QBER > 25%       Definite — Eve present (theoretical maximum for BB84 intercept-resend)
```

The QBER of every quantum key used is recorded in the commitment record (`commitment_qber` field), giving you a per-action eavesdropping signal in the audit log.

### Qiskit Backend

For maximum fidelity, enable the Qiskit quantum circuit backend:

```bash
pip install "quantumlink-sim[quantum]"  # adds qiskit + qiskit-aer
```

```yaml
security:
  commitment:
    use_qiskit: true   # real quantum circuit simulation (StatevectorSampler)
```

The Qiskit backend uses actual quantum gate operations (`H`, `X`, measurement) rather than numpy random arrays. Key generation takes ~50–200ms per key but provides the highest-fidelity simulation of real QKD hardware. Falls back to numpy BB84 automatically if Qiskit is unavailable.

### CLI: Verify, Inspect, Export

```bash
# Verify the HMAC commitment chain (separate from SHA-256 audit hash chain)
castor commit verify
# → ✅ Chain intact — 1,247 records verified. Head: a3f9b21c...

# Pool and chain statistics
castor commit stats
# → {"mode": "hybrid", "chain_length": 1247, "pool": {"pool_size_current": 28, ...}}

# Export all commitment records as JSONL
castor commit export > audit-chain-2026-02-27.jsonl

# Prove a specific action was committed (shareable without the encryption key)
castor commit proof a1b2c3d4-e5f6-7890-abcd-ef1234567890
# → {"record": {...}, "chain_position": 42, "preceding_hash": "..."}
```

The proof bundle contains everything needed to verify a commitment record's existence and chain position without revealing the encryption key — suitable for sharing with auditors, regulators, or legal proceedings.

### Cross-Session Verification

The chain secret is saved to `.opencastor-chain-secret.hex` on first run. Store this separately from the JSONL commitment file. Together, they enable offline chain verification on any machine:

```python
from quantumlink_sim.commitment import CommitmentEngine

secret = bytes.fromhex(open(".opencastor-chain-secret.hex").read())
engine = CommitmentEngine(chain_secret=secret, storage_path="commitments.jsonl")
ok, broken = engine.verify_chain()
```

### Installation

```bash
pip install quantumlink-sim          # core (numpy BB84 + commitment engine)
pip install "quantumlink-sim[quantum]"  # + Qiskit circuit backend
```

QuantumLink-Sim is an optional dependency — OpenCastor's audit log works without it (falls back to SHA-256 hash chain only). With it, every audit entry gains the full cryptographic commitment layer.

---

## 📦 Quick Start

## 🧩 Agent Skills

External agents can load production-ready runbooks from `skills/`:

- `skills/opencastor-operator/SKILL.md` — robot operations, diagnostics, config checks, safety guardrails, and failure recovery.
- `skills/opencastor-developer/SKILL.md` — API checks, RCAN validate/lint/migrate workflow, and dashboard/watch debugging flow.

If you are integrating with coding agents (Codex, Claude, etc.), point them to these skill files first so they follow OpenCastor-safe command sequences.

### 1. Install & Configure

```bash
curl -sL opencastor.com/install | bash
castor wizard
```

The wizard now uses a shared CLI/web setup-v2 flow: device probe, stack selection, model profiles, provider preflight, guided fallback, hardware preset, and optional messaging setup (WhatsApp/Telegram). It remembers your previous choices.

Setup now includes device-aware stack profiles:

<!-- SETUP_CATALOG:BEGIN -->
- `apple_native` — Apple Native (Recommended on eligible Mac)
- `mlx_local_vision` — MLX Local Vision
- `ollama_universal_local` — Ollama Universal Local

| Apple Profile | Meaning |
|---|---|
| `apple-balanced` | Apple Balanced |
| `apple-creative` | Apple Creative |
| `apple-tagging` | Apple Tagging |
<!-- SETUP_CATALOG:END -->

If Apple Foundation Models are unavailable, setup shows guided fallback choices automatically.

### Apple Foundation Models Troubleshooting

- Apple Intelligence disabled: enable it in macOS System Settings and wait for model assets.
- Device not eligible: use MLX or Ollama fallback stack.
- Model not ready: keep device online and retry setup later.
- Xcode requirement: install Xcode 26+ and ensure `xcodebuild` is available.

### 2. Run

```bash
castor run --config my_robot.rcan.yaml
```

### 3. Open the Dashboard

```
http://localhost:8501
```

### 4. Diagnose Issues

```bash
castor doctor
```

### Minimal Python Example

```python
from castor.providers import get_provider
from castor.drivers.pca9685 import PCA9685Driver

brain = get_provider({"provider": "anthropic", "model": "claude-opus-4-6"})
driver = PCA9685Driver(config["drivers"][0])

while True:
    frame = camera.capture()
    thought = brain.think(frame, "Navigate to the kitchen, avoid obstacles.")
    if thought.action:
        driver.move(thought.action["linear"], thought.action["angular"])
```

## 🏪 Community Hub

Browse, install, and share robot recipes:

```bash
castor hub search "delivery bot"
castor hub install @alice/warehouse-picker
castor hub publish my_robot.rcan.yaml
```

Recipes are shareable RCAN configs — complete robot personalities with perception, planning, and safety settings bundled together.

## 🔧 CLI Reference

<details>
<summary><b>Setup & Config</b></summary>

```bash
castor wizard                          # Interactive setup wizard
castor quickstart                      # Wizard + demo in one command
castor configure --config robot.yaml   # Interactive config editor
castor install-service --config r.yaml # Generate systemd unit file
castor learn                           # Step-by-step tutorial
castor doctor                          # Full system health check
castor fix                             # Auto-fix common issues
```
</details>

<details>
<summary><b>Run & Monitor</b></summary>

```bash
castor run --config robot.yaml         # Perception-action loop
castor run --config robot.yaml --simulate  # No hardware needed
castor gateway --config robot.yaml     # API gateway + messaging
castor dashboard                       # Streamlit web UI
castor demo                            # Simulated demo
castor shell --config robot.yaml       # Interactive command shell
castor repl --config robot.yaml        # Python REPL with robot objects
castor status                          # Provider/channel readiness
castor logs -f                         # Structured colored logs
castor benchmark --config robot.yaml   # Performance profiling
```
</details>

<details>
<summary><b>Hardware & Recording</b></summary>

```bash
castor test-hardware --config robot.yaml  # Test motors individually
castor calibrate --config robot.yaml      # Interactive calibration
castor record --config robot.yaml         # Record a session
castor replay session.jsonl               # Replay a recorded session
castor watch --gateway http://127.0.0.1:8000  # Live telemetry
```
</details>

<details>
<summary><b>Hub & Fleet</b></summary>

```bash
castor hub search "patrol bot"         # Browse community recipes
castor hub install @user/recipe        # Install a recipe
castor hub publish config.yaml         # Share your recipe
castor discover                        # Find RCAN peers on LAN
castor fleet                           # Multi-robot status (mDNS)
```
</details>

<details>
<summary><b>Safety & Admin</b></summary>

```bash
castor approvals                       # View/approve dangerous commands
castor audit --since 24h               # View audit log
castor audit --verify                  # Verify chain integrity
castor privacy --config robot.yaml     # Sensor access policy
castor token --role operator           # Issue JWT
castor upgrade                         # Self-update + health check
```
</details>

## 🏗️ Supported Hardware

Pre-made RCAN presets for popular kits, or generate your own:

| Kit | Price | Preset |
|---|---|---|
| Waveshare AlphaBot / JetBot | ~$45 | `config/presets/waveshare_alpha.rcan.yaml` |
| Adeept RaspTank / DarkPaw | ~$55 | `config/presets/adeept_generic.rcan.yaml` |
| SunFounder PiCar-X | ~$60 | `config/presets/sunfounder_picar.rcan.yaml` |
| Robotis Dynamixel (X-Series) | Varies | `config/presets/dynamixel_arm.rcan.yaml` |
| OAK-4 Pro + Depth + IMU | ~$150 | `config/presets/oak4_pro.rcan.yaml` |
| DIY (ESP32, Arduino, custom) | Any | Generate with `castor wizard` |

## 🏫 STEM & Second-Hand Hardware

OpenCastor explicitly supports the parts that students, educators, and hobbyists
**actually have** — donated kits, school surplus, eBay finds, and sub-$50 Amazon
staples. If you found it at Goodwill, a school auction, or a makerspace scrap bin,
there's probably a preset for it.

| Kit | Typical New Price | Where to Find Used | Preset |
|---|---|---|---|
| LEGO Mindstorms EV3 | ~$300 new | School surplus, eBay $30-80 | `config/presets/lego_mindstorms_ev3.rcan.yaml` |
| LEGO SPIKE Prime | ~$320 new | STEM program donations, eBay $80-150 | `config/presets/lego_spike_prime.rcan.yaml` |
| VEX IQ System | ~$250 new | Robotics team surplus, school auctions $50-120 | `config/presets/vex_iq.rcan.yaml` |
| Makeblock mBot | ~$50 new | eBay $10-25, Amazon Warehouse | `config/presets/makeblock_mbot.rcan.yaml` |
| Arduino + L298N (DIY) | ~$8-15 total | Makerspace bins, AliExpress | `config/presets/arduino_l298n.rcan.yaml` |
| ESP32 + Motor Driver (DIY) | ~$6-12 total | AliExpress, hackerspaces | `config/presets/esp32_generic.rcan.yaml` |
| Yahboom ROSMASTER X3 | ~$150-200 | Amazon Warehouse, eBay | `config/presets/yahboom_rosmaster.rcan.yaml` |
| Elegoo Tumbller / Smart Car | ~$35-40 new | Amazon Warehouse $15-25, eBay | `config/presets/elegoo_tumbller.rcan.yaml` |
| Freenove 4WD Car (Pi-based) | ~$40 new | eBay $15-25 (Pi not included) | `config/presets/freenove_4wd.rcan.yaml` |
| Cytron Maker Pi RP2040 | ~$10 new | Hackerspaces, STEM lab surplus | `config/presets/cytron_maker_pi.rcan.yaml` |

> **🔍 Not sure what you have?** See the [Hardware Identification Guide](docs/hardware-guide.md)
> for a decision tree: *"I found this at a thrift store, now what?"*

### Tips for Second-Hand Hardware

- **Test first, code later.** Run `castor test-hardware --config config/presets/<preset>.rcan.yaml -y`
  to verify motion and stop behavior before writing autonomy code.
- **Cables are the most common failure point.** LEGO connector cables, USB-B ports,
  and servo leads are all cheap to replace.
- **Clone boards are fine.** Arduino Uno clones with CH340 USB chips work perfectly.
  You may need to install the CH341SER driver on Windows.
- **Battery health matters.** Test battery packs under load — many donated robots have
  degraded cells that drop voltage and confuse motor drivers.
- **Firmware toolchains vary by kit.** Use each preset's `notes` section for the
  expected firmware/runtime path and connection mode.

## 🤝 Contributing

OpenCastor is fully open source (Apache 2.0) and community-driven.

- **Discord**: [discord.gg/jMjA8B26Bq](https://discord.gg/jMjA8B26Bq)
- **Issues**: [GitHub Issues](https://github.com/craigm26/OpenCastor/issues)
- **PRs**: See [CONTRIBUTING.md](CONTRIBUTING.md)
- **Twitter/X**: [@opencastor](https://twitter.com/opencastor)

**Areas we need help with:** driver adapters (ODrive, VESC, ROS2), new AI providers (Mistral, Grok, Cohere), messaging channels (Matrix, Signal), sim-to-real (Gazebo / MuJoCo), and tests.

## 📄 License

Apache 2.0 — built for the community, ready for the enterprise.

---

<p align="center">
  Built on the <a href="https://rcan.dev/spec/">RCAN Spec</a> by <a href="https://github.com/craigm26">Craig Merry</a>
</p>

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
  <b>55,916 lines of code · 2,233 tests · Python 3.10–3.12</b><br/>
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

## ✨ What's New in v2026.2.20.9

- **8 AI providers** — Anthropic, Google, OpenAI, HuggingFace, Ollama, llama.cpp, MLX, Claude OAuth proxy
- **Tiered brain architecture** — reactive rules → fast inference → deep planning
- **Hailo-8 NPU vision** — on-device YOLOv8 object detection at ~250ms
- **OAK-D stereo depth camera** — RGB + depth via DepthAI v3
- **Community Hub** — browse, install, and share robot recipes with `castor hub`
- **Interactive wizard** — provider selection, auth, messaging setup with recall of previous choices
- **`castor doctor`** — full system health diagnostic

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
| **Anthropic** | `claude-opus-4-6`, `claude-sonnet-4-5` | ~12s | Complex planning, safety-critical reasoning |
| **Google** | `gemini-2.5-flash`, `gemini-2.5-pro`, `gemini-3-flash-preview` | ~500ms | Multimodal, video, speed |
| **OpenAI** | `gpt-4.1`, `gpt-4.1-mini`, `gpt-5` | ~2s | Instruction following, 1M context |
| **HuggingFace** | Transformers, any hosted model | ~500ms | Fast brain layer, classification |
| **Ollama** | `llava:13b`, any local model | Varies | Privacy, offline, zero cost |
| **llama.cpp** | GGUF models | ~200ms | Edge inference, Raspberry Pi |
| **MLX** | Apple Silicon native (mlx-lm, vLLM-MLX) | ~50ms | Mac M1-M4, 400+ tok/s |
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

## 📦 Quick Start

### 1. Install & Configure

```bash
curl -sL opencastor.com/install | bash
castor wizard
```

The wizard walks you through provider selection, API keys, hardware config, and optional messaging setup (WhatsApp/Telegram). It remembers your previous choices.

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
| Waveshare AlphaBot / JetBot | ~$45 | `presets/waveshare_alpha.rcan.yaml` |
| Adeept RaspTank / DarkPaw | ~$55 | `presets/adeept_generic.rcan.yaml` |
| SunFounder PiCar-X | ~$60 | `presets/sunfounder_picar.rcan.yaml` |
| Robotis Dynamixel (X-Series) | Varies | `presets/dynamixel_arm.rcan.yaml` |
| Hailo-8 + OAK-D Vision Stack | ~$150 | `presets/hailo_oakd_vision.rcan.yaml` |
| DIY (ESP32, Arduino, custom) | Any | Generate with `castor wizard` |

## 🏫 STEM & Second-Hand Hardware

OpenCastor explicitly supports the parts that students, educators, and hobbyists
**actually have** — donated kits, school surplus, eBay finds, and sub-$50 Amazon
staples. If you found it at Goodwill, a school auction, or a makerspace scrap bin,
there's probably a preset for it.

| Kit | Typical New Price | Where to Find Used | Preset |
|---|---|---|---|
| LEGO Mindstorms EV3 | ~$300 new | School surplus, eBay $30-80 | `presets/lego_mindstorms_ev3.rcan.yaml` |
| LEGO SPIKE Prime | ~$320 new | STEM program donations, eBay $80-150 | `presets/lego_spike_prime.rcan.yaml` |
| VEX IQ System | ~$250 new | Robotics team surplus, school auctions $50-120 | `presets/vex_iq.rcan.yaml` |
| Makeblock mBot | ~$50 new | eBay $10-25, Amazon Warehouse | `presets/makeblock_mbot.rcan.yaml` |
| Arduino + L298N (DIY) | ~$8-15 total | Makerspace bins, AliExpress | `presets/arduino_l298n.rcan.yaml` |
| ESP32 + Motor Driver (DIY) | ~$6-12 total | AliExpress, hackerspaces | `presets/esp32_generic.rcan.yaml` |
| Yahboom ROSMASTER X3 | ~$150-200 | Amazon Warehouse, eBay | `presets/yahboom_rosmaster.rcan.yaml` |
| Elegoo Tumbller / Smart Car | ~$35-40 new | Amazon Warehouse $15-25, eBay | `presets/elegoo_tumbller.rcan.yaml` |
| Freenove 4WD Car (Pi-based) | ~$40 new | eBay $15-25 (Pi not included) | `presets/freenove_4wd.rcan.yaml` |
| Cytron Maker Pi RP2040 | ~$10 new | Hackerspaces, STEM lab surplus | `presets/cytron_maker_pi.rcan.yaml` |

> **🔍 Not sure what you have?** See the [Hardware Identification Guide](docs/hardware-guide.md)
> for a decision tree: *"I found this at a thrift store, now what?"*

### Tips for Second-Hand Hardware

- **Test first, code later.** Run `castor test-hardware --config <preset>.rcan.yaml` to verify
  each motor and sensor before writing any autonomy code.
- **Cables are the most common failure point.** LEGO connector cables, USB-B ports,
  and servo leads are all cheap to replace.
- **Clone boards are fine.** Arduino Uno clones with CH340 USB chips work perfectly.
  You may need to install the CH341SER driver on Windows.
- **Battery health matters.** Test battery packs under load — many donated robots have
  degraded cells that drop voltage and confuse motor drivers.
- **Community firmware exists** for almost every kit. Check the `firmware/` directory
  in this repo for Arduino sketches and MicroPython scripts.

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

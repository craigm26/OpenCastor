# OpenCastor

**The Universal Runtime for Embodied AI.**

> Stop writing boilerplate. Start building robot agents.

OpenCastor connects any AI model to any robot hardware through a single YAML config file. Swap brains (Claude, Gemini, GPT, Ollama) or bodies (Raspberry Pi, Jetson, Arduino) without changing a line of code.

Whether you have a $50 Amazon robot kit or a $50,000 industrial arm, OpenCastor makes it work.

## Why OpenCastor?

| Feature | What it means |
|---|---|
| **Universal Adapter** | Switch between Claude, Gemini, GPT, or Ollama with one config change |
| **Hardware Agnostic** | Built-in drivers for PCA9685, Dynamixel, Serial, and more |
| **Zero Friction** | Unbox to agent in under 5 minutes |
| **Safety First** | Hard-coded safety layers prevent LLM hallucinations from causing physical damage |
| **RCAN Compliant** | Built on the open [RCAN Standard](https://rcan.dev/spec/) for interoperability |
| **Messaging Built-in** | Control your robot via WhatsApp, Telegram, Discord, or Slack |

## Quick Start

### 1. Install

```bash
# One-liner (Raspberry Pi / Linux)
curl -sL https://opencastor.com/install | bash

# Or manually
git clone https://github.com/craigm26/OpenCastor.git
cd OpenCastor
pip install -e ".[dev]"
```

### 2. Run the Wizard

```
$ castor wizard

OpenCastor Setup Wizard v2026.2.17.3

Which Brain do you want to use?
[1] Anthropic Claude Opus 4.6 (Recommended)
[2] Google Gemini 2.5 Flash
[3] Google Gemini 3 Flash (Preview)
[4] OpenAI GPT-4.1
[5] Local Llama (via Ollama)

> Selection: 1
```

The wizard generates an RCAN config file, collects your API key, and optionally sets up messaging channels.

### 3. Run

```bash
castor run --config my_robot.rcan.yaml
```

Your robot is now online. Open `http://localhost:8501` for the CastorDash web interface.

## Swap Your Brain in One Line

Your robot's entire personality lives in a YAML config file powered by the [RCAN Standard](https://rcan.dev/spec/). Switch AI providers by editing one block:

```yaml
# Option A: Anthropic Claude (Recommended)
agent:
  provider: "anthropic"
  model: "claude-opus-4-6"       # Best reasoning & safety

# Option B: Google Gemini
# agent:
#   provider: "google"
#   model: "gemini-2.5-flash"    # Stable. Also: gemini-2.5-pro, gemini-3-flash-preview

# Option C: OpenAI GPT
# agent:
#   provider: "openai"
#   model: "gpt-4.1"             # 1M context, strong vision. Also: gpt-5

# Option D: Local / Offline
# agent:
#   provider: "ollama"
#   model: "llava:13b"
#   url: "http://localhost:11434"
```

## Architecture

```
[ Claude / Gemini / GPT / Ollama ]      <-- The Brain (Provider Layer)
               |
         [ RCAN Config ]                 <-- The Spinal Cord (Validation)
               |
     [ PCA9685 / Dynamixel / GPIO ]      <-- The Nervous System (Drivers)
               |
         [ Your Robot ]                  <-- The Body
```

- **Provider Layer**: Normalizes AI outputs into a standard `Thought` object (text + action JSON).
- **RCAN Validation**: Checks actions against physical constraints (speed limits, range of motion, collision).
- **Driver Layer**: Translates high-level intent (`move_forward`) into low-level signals (PWM, serial, I2C).

## Supported Models (Feb 2026)

| Provider | Models | Best For |
|---|---|---|
| **Anthropic** | `claude-opus-4-6`, `claude-sonnet-4-5-20250929` | Reasoning, safety, complex planning |
| **Google** | `gemini-2.5-flash`, `gemini-2.5-pro`, `gemini-3-flash-preview`, `gemini-3-pro-preview` | Video, multimodal, speed |
| **OpenAI** | `gpt-4.1`, `gpt-4.1-mini`, `gpt-5` | Instruction following, 1M context |
| **Ollama** | `llava:13b`, any local model | Privacy, offline, no API cost |

## Supported Hardware

Pre-made RCAN presets for popular kits, or bring your own config:

| Kit | Price | Preset |
|---|---|---|
| Waveshare AlphaBot / JetBot | ~$45 | `presets/waveshare_alpha.rcan.yaml` |
| Adeept RaspTank / DarkPaw | ~$55 | `presets/adeept_generic.rcan.yaml` |
| SunFounder PiCar-X | ~$60 | `presets/sunfounder_picar.rcan.yaml` |
| Robotis Dynamixel (X-Series) | Varies | `presets/dynamixel_arm.rcan.yaml` |
| DIY (ESP32, Arduino, custom) | Any | Generate with `castor wizard` |

## The Perception-Action Loop

OpenCastor runs a continuous observe-reason-act cycle:

1. **Observe** -- capture camera frame + sensor telemetry
2. **Reason** -- send to AI model, receive structured action JSON
3. **Act** -- translate intent into motor commands with safety checks
4. **Repeat** -- configurable latency budget (default 200ms)

```python
from castor.providers import get_provider
from castor.drivers.pca9685 import PCA9685Driver

brain = get_provider(config["agent"])
driver = PCA9685Driver(config["drivers"][0])

while True:
    frame = camera.capture()
    thought = brain.think(frame, "Sort the recycling from the trash.")
    if thought.action:
        driver.move(thought.action.get("linear", 0), thought.action.get("angular", 0))
```

## Docker

```bash
cp .env.example .env          # Add your API keys
castor wizard                 # Generate config
docker compose up             # Launch
```

## CLI Reference

```bash
castor run       --config robot.rcan.yaml    # Perception-action loop
castor run       --config robot.rcan.yaml --simulate  # No hardware
castor gateway   --config robot.rcan.yaml    # API server + messaging
castor wizard                                 # Interactive setup
castor dashboard                              # Streamlit web UI
castor status                                 # Provider/channel readiness
```

## Contributing

OpenCastor is fully open source (Apache 2.0) and community-driven. We want your help.

**Get involved:**
- **Discord**: [discord.gg/jMjA8B26Bq](https://discord.gg/jMjA8B26Bq) -- chat with maintainers and the community
- **Issues**: [GitHub Issues](https://github.com/craigm26/OpenCastor/issues) -- report bugs or request features
- **PRs**: Fork, branch, and submit -- see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines
- **Twitter/X**: [@opencastor](https://twitter.com/opencastor)

**Areas we need help with:**
- **Driver Adapters**: ODrive, VESC, ROS2 bridges, ESP32 serial
- **AI Providers**: Mistral, Grok, Cohere, local vision models
- **Messaging Channels**: Matrix, Signal, Google Chat
- **Sim-to-Real**: Gazebo / MuJoCo integration
- **Tests**: Unit tests, integration tests, hardware mock tests

Every contribution matters -- from fixing a typo to adding a new driver.

## License

Apache 2.0. Built for the community, ready for the enterprise.

---

*Built on the [RCAN Spec](https://rcan.dev/spec/) by [Continuon AI](https://github.com/craigm26).*

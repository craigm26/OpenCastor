# OpenCastor

**The Universal Runtime for Embodied AI.**

> Stop writing boilerplate. Start building agents.

OpenCastor is a plug-and-play operating system that turns any robot into an intelligent agent. It acts as a universal translation layer between **The Brain** (Gemini, GPT-4o, Claude) and **The Body** (Raspberry Pi, Jetson, Arduino).

Whether you have a $50 Amazon robot kit or a $50,000 industrial arm, OpenCastor lets you swap AI models as easily as you swap batteries.

> *"If Gemini is the brain, Castor is the body."*

## Why OpenCastor?

- **Universal Adapter**: Switch from Google Gemini to OpenAI GPT-4o with one line of config. No code changes required.
- **Hardware Agnostic**: Built-in drivers for standard hobbyist hardware (PCA9685, Dynamixel, Serial).
- **Zero Friction**: From "Unbox" to "Agent" in under 5 minutes.
- **Safety First**: Hard-coded safety layers prevent LLM hallucinations from causing physical damage.
- **RCAN Compliant**: Built on the [RCAN Standard](https://github.com/continuonai/rcan-spec) for interoperability.

## Quick Start: The "Zero-to-Hero" Flow

### 1. Install

Run this on your Raspberry Pi or Linux machine:

```bash
curl -sL https://opencastor.com/install | bash
```

Or install manually:

```bash
git clone https://github.com/continuonai/OpenCastor.git
cd OpenCastor
pip install -r requirements.txt
```

### 2. Choose Your Brain

The setup wizard will ask which AI provider you want to use:

```
$ python -m castor.wizard

OpenCastor Setup Wizard v0.1.0

Which Brain do you want to use?
[1] Google Gemini (Recommended for Vision)
[2] OpenAI GPT-4o
[3] Anthropic Claude 3.5
[4] Local Llama (via Ollama)

> Selection: 1
```

### 3. Run the Agent

```bash
python -m castor.main --config my_robot.rcan.yaml
```

Your robot is now online. Open `http://localhost:8501` to see the CastorDash interface.

## The Magic Switch

OpenCastor is powered by the [RCAN Standard](https://github.com/continuonai/rcan-spec) (Robotic Control & Automation Network). Your robot's personality is defined in a simple YAML file.

Want to switch from Gemini to Claude? Just edit `robot.rcan.yaml`:

```yaml
# CHANGE YOUR ROBOT'S BRAIN IN 1 LINE

# Option A: Google Gemini (Best for Video/Multimodal)
agent:
  provider: "google"
  model: "gemini-1.5-flash"

# Option B: OpenAI (Best for Instruction Following)
# agent:
#   provider: "openai"
#   model: "gpt-4o"

# Option C: Anthropic (Best for Complex Reasoning/Safety)
# agent:
#   provider: "anthropic"
#   model: "claude-3-5-sonnet"

# Option D: Local/Offline (Best for Privacy/No Internet)
# agent:
#   provider: "ollama"
#   model: "llava:13b"
#   url: "http://localhost:11434"
```

## Architecture

OpenCastor decouples reasoning from execution:

```
[ Gemini / GPT-4o / Claude / Ollama ]     <-- The Brain (Provider Layer)
              |
        [ RCAN Config ]                    <-- The Spinal Cord (Validation)
              |
    [ Dynamixel / PCA9685 / GPIO ]         <-- The Nervous System (Drivers)
              |
        [ Your Robot ]                     <-- The Body
```

- **The Cortex** (Provider Layer): Normalizes inputs/outputs from different LLMs into a standard `Thought` object.
- **The Spinal Cord** (RCAN): Validates actions against physical constraints (speed limits, range of motion).
- **The Nervous System** (Drivers): Translates high-level intent (`move_forward`) into low-level signals (PWM 50Hz, Duty Cycle 0.5).

## Supported Hardware

We support "Bring Your Own Config," but we have pre-made presets for popular kits:

| Manufacturer | Kit Name | Status | Preset Config |
|---|---|---|---|
| Waveshare | AlphaBot / JetBot | Supported | `presets/waveshare_alpha.rcan.yaml` |
| Adeept | RaspTank / DarkPaw | Supported | `presets/adeept_generic.rcan.yaml` |
| SunFounder | PiCar-X | Supported | `presets/sunfounder_picar.rcan.yaml` |
| Robotis | Dynamixel (X-Series) | Supported | `presets/dynamixel_arm.rcan.yaml` |
| Custom | DIY ESP32 / Arduino | Beta | Use `castor.wizard` to configure |

## Deploy in 30 Seconds (Docker)

OpenCastor is fully containerized.

```bash
# 1. Define your Robot
python -m castor.wizard

# 2. Set your Keys
export GOOGLE_API_KEY=your_key_here

# 3. Launch the Brain
docker-compose up
```

## The Perception-Action Loop

OpenCastor implements a continuous Perception-Reasoning-Action loop optimized for latency:

1. **Observe**: Streams video frames + telemetry to the AI API.
2. **Orient**: AI analyzes the scene (e.g., "I see a red cup and a gripper").
3. **Decide**: AI outputs a high-level intention (e.g., `pick_up(target="red_cup")`).
4. **Act**: Castor's local solver translates the intent into low-level motor commands, checking collision constraints in real-time.

```python
from castor.providers import get_provider
from castor.drivers.pca9685 import PCA9685Driver

# Initialize from config
brain = get_provider(config['agent'])
driver = PCA9685Driver(config['drivers'][0])

# The loop
while True:
    frame = camera.capture()
    thought = brain.think(frame, "Sort the recycling from the trash.")
    if thought.action:
        driver.move(thought.action.get('linear', 0), thought.action.get('angular', 0))
```

## Contributing

We are building the standard for open robotics. We need help with:

- **Driver Adapters**: Writing interfaces for ODrive, VESC, or ROS2 bridges.
- **New Brains**: Add adapters for Mistral, Grok, or specialized vision models.
- **Sim-to-Real**: Improving our Gazebo/MuJoCo integration.
- **Latency Optimization**: Reducing the time-to-first-token for real-time reflex arcs.

## License

Apache 2.0. Built for the community, ready for the enterprise.

---

*OpenCastor is compliant with the [RCAN Spec](https://github.com/continuonai/rcan-spec) by [Continuon AI](https://github.com/continuonai).*

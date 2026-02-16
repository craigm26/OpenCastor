# CLAUDE.md - OpenCastor Development Guide

## Project Overview

OpenCastor is a universal runtime for embodied AI. It connects LLM "brains" (Gemini, GPT-4o, Claude) to robot "bodies" (Raspberry Pi, Jetson, Arduino) through a plug-and-play architecture. Configuration is driven by YAML files compliant with the [RCAN Standard](https://github.com/continuonai/rcan-spec) (Robotic Control & Automation Network).

**Version**: 0.1.0-alpha
**License**: Apache 2.0
**Python**: 3.10+

## Repository Structure

```
OpenCastor/
├── castor/                        # Main Python package
│   ├── __init__.py                # Version string (__version__)
│   ├── main.py                    # Core runtime: perception-action loop
│   ├── wizard.py                  # Interactive RCAN config generator
│   ├── dashboard.py               # Streamlit web UI (CastorDash)
│   ├── providers/                 # LLM provider adapters
│   │   ├── __init__.py            # get_provider() factory function
│   │   ├── base.py                # BaseProvider ABC + Thought class
│   │   ├── google_provider.py     # Google Gemini adapter
│   │   ├── openai_provider.py     # OpenAI GPT-4o adapter
│   │   └── anthropic_provider.py  # Anthropic Claude adapter
│   └── drivers/                   # Hardware driver implementations
│       ├── __init__.py
│       ├── base.py                # DriverBase ABC (move/stop/close)
│       ├── pca9685.py             # I2C PWM motor driver (Amazon kits)
│       └── dynamixel.py           # Robotis servo controller (Protocol 2.0)
├── config/
│   └── presets/                   # Hardware preset RCAN configs
│       ├── waveshare_alpha.rcan.yaml
│       ├── adeept_generic.rcan.yaml
│       ├── amazon_kit_generic.rcan.yaml
│       ├── sunfounder_picar.rcan.yaml
│       └── dynamixel_arm.rcan.yaml
├── scripts/
│   ├── install.sh                 # One-line installer for RPi/Linux
│   └── start_dashboard.sh         # Kiosk mode launcher for Streamlit
├── site/                          # Static landing page (Cloudflare Pages)
│   ├── index.html
│   ├── _headers
│   └── _redirects
├── .github/
│   ├── workflows/
│   │   └── validate_rcan.yml      # CI: RCAN schema validation
│   └── scripts/
│       └── validate_rcan.py       # Schema validator script
├── Dockerfile                     # Python 3.10-slim container
├── docker-compose.yml             # Local orchestration with device mounts
├── requirements.txt               # Python dependencies
├── wrangler.toml                  # Cloudflare Pages config
├── demo_logs.py                   # Cinematic terminal demo
└── README.md
```

## Architecture

The codebase follows a three-layer architecture metaphor:

```
[ Gemini / GPT-4o / Claude ]     <-- The Brain (Provider Layer)
            |
      [ RCAN Config ]            <-- The Spinal Cord (Validation)
            |
  [ Dynamixel / PCA9685 ]        <-- The Nervous System (Drivers)
            |
      [ Your Robot ]              <-- The Body
```

### Core Abstractions

- **`Thought`** (`castor/providers/base.py`): Hardware-agnostic representation of an AI reasoning step. Contains `raw_text` (LLM output) and `action` (parsed JSON command dict).
- **`BaseProvider`** (`castor/providers/base.py`): ABC that all LLM adapters implement. Key method: `think(image_bytes, instruction) -> Thought`.
- **`DriverBase`** (`castor/drivers/base.py`): ABC that all hardware drivers implement. Key methods: `move()`, `stop()`, `close()`.
- **`get_provider(config)`** (`castor/providers/__init__.py`): Factory function that instantiates the correct provider based on the `provider` key in RCAN config.
- **`get_driver(config)`** (`castor/main.py`): Factory function that instantiates the correct driver based on the `protocol` key in RCAN config.

### Perception-Action Loop (`castor/main.py`)

The main runtime runs a continuous OODA loop:
1. **OBSERVE** - Capture camera frame via OpenCV
2. **ORIENT & DECIDE** - Send frame + instruction to LLM provider
3. **ACT** - Translate `Thought.action` into motor commands
4. **TELEMETRY** - Check latency against configurable budget

### Provider Pattern

All providers follow the same contract:
- Constructor takes RCAN `agent` config dict
- API key resolved from environment variable first, then config fallback
- `think()` encodes image as base64 (OpenAI/Anthropic) or raw bytes (Google)
- LLM is prompted via `_build_system_prompt()` to output strict JSON only
- Response parsed via `_clean_json()` helper that strips markdown fences

### Driver Pattern

All drivers gracefully degrade:
- Hardware libraries are imported in try/except blocks
- If hardware SDK is missing, driver runs in **mock mode** (logs actions, no physical output)
- `HAS_PCA9685` / `HAS_DYNAMIXEL` module-level booleans track availability

## Key Conventions

### Configuration (RCAN)

- All robot configs use the `.rcan.yaml` extension
- Configs follow the [RCAN Spec schema](https://github.com/continuonai/rcan-spec)
- Required top-level keys: `rcan_version`, `metadata`, `agent`, `physics`, `drivers`, `network`
- Presets live in `config/presets/`
- The wizard (`castor/wizard.py`) generates new configs interactively

### Code Style

- **PEP 8** with 4-space indentation, snake_case for functions/variables
- Type hints used in base classes and function signatures
- Docstrings on key classes and methods
- Lazy imports for hardware libraries (import inside constructor or try/except at module level)
- Structured logging via Python's `logging` module with per-module loggers named `OpenCastor.<Module>`

### Environment Variables

| Variable | Purpose |
|---|---|
| `GOOGLE_API_KEY` | Google Gemini API authentication |
| `OPENAI_API_KEY` | OpenAI API authentication |
| `ANTHROPIC_API_KEY` | Anthropic Claude API authentication |
| `DYNAMIXEL_PORT` | Override default serial port for Dynamixel |
| `RCAN_SPEC_PATH` | Path to RCAN schema (set in Docker) |
| `PYTHONUNBUFFERED` | Set to `1` in Docker for streaming logs |

### Entry Points

| Command | Purpose |
|---|---|
| `python -m castor.main --config <file>` | Run the robot runtime |
| `python -m castor.main --config <file> --simulate` | Run without hardware |
| `python -m castor.wizard` | Interactive config generator |
| `streamlit run castor/dashboard.py` | Launch CastorDash web UI |
| `docker-compose up` | Run containerized runtime |

## Dependencies

Organized by subsystem (see `requirements.txt`):

- **The Brain**: `google-generativeai`, `openai`, `anthropic`
- **The Body**: `dynamixel-sdk`, `pyserial`
- **The Eyes**: `opencv-python-headless`
- **Config/Validation**: `pyyaml`, `jsonschema`, `requests`
- **Dashboard**: `streamlit`, `SpeechRecognition`, `gTTS`
- **CLI**: `rich`

Hardware-specific (not in requirements.txt, installed on RPi only):
- `adafruit-circuitpython-pca9685`, `adafruit-circuitpython-motor`, `busio`, `board`

## Development Workflow

### Setup

```bash
git clone https://github.com/continuonai/OpenCastor.git
cd OpenCastor
pip install -r requirements.txt
```

### Running Locally (No Hardware)

```bash
python -m castor.main --config config/presets/waveshare_alpha.rcan.yaml --simulate
```

### Docker

```bash
docker-compose up --build
```

The Docker container runs in privileged mode to access `/dev/ttyUSB0` (serial) and `/dev/video0` (camera).

### Validating RCAN Configs

Locally:
```bash
pip install jsonschema pyyaml
python3 .github/scripts/validate_rcan.py --schema <path-to-rcan-schema>/rcan.schema.json --dir .
```

This runs automatically in CI on any push or PR that modifies `*.rcan.yaml` files or anything under `config/`.

## CI/CD

### GitHub Actions

**RCAN Spec Validation** (`.github/workflows/validate_rcan.yml`):
- **Triggers**: Push/PR touching `*.rcan.yaml`, `*.rcan.yml`, or `config/**`
- **Steps**: Checks out the external `continuonai/rcan-spec` repo, installs `jsonschema` + `pyyaml`, validates all `.rcan.yaml` files against the RCAN JSON Schema
- **Python version**: 3.10

### Static Site

The `site/` directory deploys to Cloudflare Pages via `wrangler.toml` (project name: `opencastor`).

## Testing

There is currently no automated test suite (no pytest, unittest, or tox). Validation is handled through:
- RCAN schema validation in CI
- Mock mode in drivers for simulation without hardware
- Manual testing via the `--simulate` flag and the dashboard

## Adding New Components

### Adding a New LLM Provider

1. Create `castor/providers/<name>_provider.py`
2. Subclass `BaseProvider` from `castor/providers/base.py`
3. Implement `__init__(self, config)` - resolve API key from env then config
4. Implement `think(self, image_bytes, instruction) -> Thought`
5. Register in the factory at `castor/providers/__init__.py` (`get_provider()`)
6. Import the new class in `castor/providers/__init__.py`

### Adding a New Hardware Driver

1. Create `castor/drivers/<name>.py`
2. Subclass `DriverBase` from `castor/drivers/base.py`
3. Implement `move()`, `stop()`, `close()`
4. Use try/except for hardware SDK imports with a `HAS_<NAME>` fallback boolean
5. Add the protocol mapping in `get_driver()` in `castor/main.py`

### Adding a New Hardware Preset

1. Create `config/presets/<name>.rcan.yaml`
2. Follow the RCAN schema structure (see existing presets for reference)
3. CI will automatically validate the file on push

## Safety Considerations

- The system prompt forces LLMs to output strict JSON only (no freeform text)
- Driver values are clamped to safe ranges (e.g., Dynamixel: 0-4095 ticks)
- `safety_stop: true` in RCAN config enables emergency stop capability
- Latency budgets are configurable per agent (`latency_budget_ms`)
- The dashboard provides an EMERGENCY STOP button
- All drivers support graceful shutdown via `close()` in the finally block

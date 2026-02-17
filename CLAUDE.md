# CLAUDE.md - OpenCastor Development Guide

## Project Overview

OpenCastor is a universal runtime for embodied AI. It connects LLM "brains" (Gemini, GPT-4.1, Claude) to robot "bodies" (Raspberry Pi, Jetson, Arduino) through a plug-and-play architecture, and exposes them to messaging platforms (WhatsApp, Telegram, Discord, Slack) for remote control. Configuration is driven by YAML files compliant with the [RCAN Standard](https://rcan.dev/spec/).

**Version**: 2026.2.17.3
**License**: Apache 2.0
**Python**: 3.10+

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

## Repository Structure

```
OpenCastor/
├── castor/                        # Main Python package
│   ├── __init__.py                # Version string (__version__)
│   ├── cli.py                     # Unified CLI entry point (castor command)
│   ├── main.py                    # Core runtime: perception-action loop
│   ├── api.py                     # FastAPI gateway server
│   ├── auth.py                    # Unified auth manager (providers + channels)
│   ├── wizard.py                  # Interactive setup wizard
│   ├── dashboard.py               # Streamlit web UI (CastorDash)
│   ├── providers/                 # LLM provider adapters
│   │   ├── __init__.py            # get_provider() factory function
│   │   ├── base.py                # BaseProvider ABC + Thought class
│   │   ├── google_provider.py     # Google Gemini adapter
│   │   ├── openai_provider.py     # OpenAI GPT-4.1 adapter
│   │   └── anthropic_provider.py  # Anthropic Claude adapter
│   ├── drivers/                   # Hardware driver implementations
│   │   ├── __init__.py
│   │   ├── base.py                # DriverBase ABC (move/stop/close)
│   │   ├── pca9685.py             # I2C PWM motor driver (Amazon kits)
│   │   └── dynamixel.py           # Robotis servo controller (Protocol 2.0)
│   └── channels/                  # Messaging channel integrations
│       ├── __init__.py            # Channel registry + create_channel() factory
│       ├── base.py                # BaseChannel ABC
│       ├── whatsapp.py            # Re-export (default: neonize QR code)
│       ├── whatsapp_neonize.py    # WhatsApp via neonize (QR code scan)
│       ├── whatsapp_twilio.py     # WhatsApp via Twilio (legacy)
│       ├── telegram_channel.py    # Telegram Bot (long-polling)
│       ├── discord_channel.py     # Discord Bot
│       └── slack_channel.py       # Slack Bot (Socket Mode)
├── config/
│   └── presets/                   # Hardware preset RCAN configs
│       ├── waveshare_alpha.rcan.yaml
│       ├── adeept_generic.rcan.yaml
│       ├── amazon_kit_generic.rcan.yaml
│       ├── sunfounder_picar.rcan.yaml
│       └── dynamixel_arm.rcan.yaml
├── tests/                         # Test directory
├── scripts/
│   ├── install.sh                 # One-line installer for RPi/Linux
│   └── start_dashboard.sh         # Kiosk mode launcher
├── site/                          # Static landing page (Cloudflare Pages)
├── .github/
│   ├── workflows/validate_rcan.yml
│   └── scripts/validate_rcan.py
├── .env.example                   # Environment variable template
├── pyproject.toml                 # Python packaging (pip install -e .)
├── requirements.txt               # Python dependencies
├── Dockerfile                     # Container with health check
├── docker-compose.yml             # Gateway + runtime + dashboard services
├── CONTRIBUTING.md                # How to add providers/drivers/channels
├── wrangler.toml                  # Cloudflare Pages config
├── demo_logs.py                   # Cinematic terminal demo
└── README.md

```

## Architecture

```
[ WhatsApp / Telegram / Discord / Slack ]   <-- Messaging Channels
                    |
            [ API Gateway ]                  <-- FastAPI (castor/api.py)
                    |
      [ Gemini / GPT-4.1 / Claude ]           <-- The Brain (Provider Layer)
                    |
              [ RCAN Config ]                <-- The Spinal Cord (Validation)
                    |
        [ Dynamixel / PCA9685 ]              <-- The Nervous System (Drivers)
                    |
              [ Your Robot ]                 <-- The Body
```

### Core Abstractions

- **`Thought`** (`castor/providers/base.py`): Hardware-agnostic AI reasoning step. Contains `raw_text` and `action` (parsed JSON dict).
- **`BaseProvider`** (`castor/providers/base.py`): ABC for LLM adapters. Key method: `think(image_bytes, instruction) -> Thought`.
- **`DriverBase`** (`castor/drivers/base.py`): ABC for hardware drivers. Methods: `move()`, `stop()`, `close()`.
- **`BaseChannel`** (`castor/channels/base.py`): ABC for messaging integrations. Methods: `start()`, `stop()`, `send_message()`.
- **Factory functions**: `get_provider()` (providers), `get_driver()` (main.py), `create_channel()` (channels).

### Authentication (`castor/auth.py`)

Credentials are resolved in priority order:
1. **Environment variable** (e.g. `GOOGLE_API_KEY`)
2. **`.env` file** (loaded via python-dotenv)
3. **RCAN config fallback** (e.g. `config["api_key"]`)

Key functions:
- `resolve_provider_key(provider, config)` - Get API key for a provider
- `resolve_channel_credentials(channel, config)` - Get all creds for a channel
- `list_available_providers()` / `list_available_channels()` - Status maps
- `check_provider_ready()` / `check_channel_ready()` - Readiness booleans

### API Gateway (`castor/api.py`)

FastAPI server providing:
- `GET /health` - Health check (used by Docker HEALTHCHECK)
- `GET /api/status` - Runtime status, active providers/channels
- `POST /api/command` - Send instruction to brain, receive action
- `POST /api/action` - Direct motor command (bypass brain)
- `POST /api/stop` - Emergency stop
- `GET /api/whatsapp/status` - WhatsApp (neonize) connection status
- `POST /webhooks/whatsapp` - Twilio WhatsApp incoming webhook (legacy)
- `POST /webhooks/slack` - Slack Events API fallback

Protected by optional `OPENCASTOR_API_TOKEN` bearer auth.

### Channel System (`castor/channels/`)

All channels follow the same pattern:
- Constructor takes config dict + `on_message` callback
- SDKs are lazily imported (graceful degradation if not installed)
- `handle_message()` forwards to the brain and returns the reply
- Each channel is an optional dependency: `pip install opencastor[whatsapp]`

| Channel | SDK | Auth Env Vars |
|---------|-----|---------------|
| WhatsApp | `neonize` | None (QR code scan) |
| WhatsApp (Twilio) | `twilio` | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_NUMBER` |
| Telegram | `python-telegram-bot` | `TELEGRAM_BOT_TOKEN` |
| Discord | `discord.py` | `DISCORD_BOT_TOKEN` |
| Slack | `slack-bolt` | `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` |

### Perception-Action Loop (`castor/main.py`)

Continuous OODA loop:
1. **OBSERVE** - Capture camera frame via OpenCV
2. **ORIENT & DECIDE** - Send frame + instruction to LLM provider
3. **ACT** - Translate `Thought.action` into motor commands
4. **TELEMETRY** - Check latency against configurable budget

### Provider Pattern

- Constructor resolves API key from env first, then config
- `think()` encodes image as base64 (OpenAI/Anthropic) or raw bytes (Google)
- System prompt forces strict JSON output only
- `_clean_json()` strips markdown fences from responses

### Driver Pattern

- Hardware SDKs imported in try/except with module-level `HAS_<NAME>` boolean
- Drivers degrade to mock mode when SDK is missing (log actions, no physical output)
- Values clamped to safe physical ranges

## CLI Commands

```bash
castor run      --config robot.rcan.yaml             # Perception-action loop
castor run      --config robot.rcan.yaml --simulate  # Without hardware
castor gateway  --config robot.rcan.yaml             # API gateway + channels
castor wizard                                         # Interactive setup
castor dashboard                                      # Streamlit web UI
castor status                                         # Provider/channel readiness
```

Also available as Python modules:
```bash
python -m castor.main --config robot.rcan.yaml
python -m castor.api --config robot.rcan.yaml
python -m castor.wizard
streamlit run castor/dashboard.py
```

## Environment Variables

Copy `.env.example` to `.env` and fill in what you need.

### AI Providers
| Variable | Provider |
|---|---|
| `GOOGLE_API_KEY` | Google Gemini |
| `OPENAI_API_KEY` | OpenAI GPT-4.1 |
| `ANTHROPIC_API_KEY` | Anthropic Claude |
| `OPENROUTER_API_KEY` | OpenRouter (multi-model) |
| `OLLAMA_BASE_URL` | Local Ollama (no key needed) |

### Messaging Channels
| Variable | Channel |
|---|---|
| *(none -- QR code scan)* | WhatsApp |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_NUMBER` | WhatsApp (Twilio, legacy) |
| `TELEGRAM_BOT_TOKEN` | Telegram |
| `DISCORD_BOT_TOKEN` | Discord |
| `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | Slack |

### Gateway & Runtime
| Variable | Purpose |
|---|---|
| `OPENCASTOR_API_TOKEN` | Bearer token for API auth (generate: `openssl rand -hex 32`) |
| `OPENCASTOR_API_HOST` | Bind address (default: 127.0.0.1) |
| `OPENCASTOR_API_PORT` | Port (default: 8000) |
| `OPENCASTOR_CONFIG` | Config file path |
| `DYNAMIXEL_PORT` | Serial port override |
| `CAMERA_INDEX` | Camera device (default: 0) |
| `LOG_LEVEL` | Logging level |

## Dependencies

### Core (always installed via `requirements.txt`)
- **Brain**: `google-generativeai`, `openai`, `anthropic`
- **Body**: `dynamixel-sdk`, `pyserial`
- **Eyes**: `opencv-python-headless`
- **Config**: `pyyaml`, `jsonschema`, `requests`
- **Gateway**: `fastapi`, `uvicorn`, `python-dotenv`, `httpx`
- **Dashboard**: `streamlit`, `SpeechRecognition`, `gTTS`
- **CLI**: `rich`

### Optional (install as extras via pyproject.toml)
```bash
pip install opencastor[whatsapp]        # neonize (QR code scan)
pip install opencastor[whatsapp-twilio] # twilio (legacy)
pip install opencastor[telegram]        # python-telegram-bot
pip install opencastor[discord]         # discord.py
pip install opencastor[slack]           # slack-bolt
pip install opencastor[channels]        # All of the above
pip install opencastor[dev]             # pytest, ruff
```

Hardware-specific (installed on RPi only):
- `adafruit-circuitpython-pca9685`, `adafruit-circuitpython-motor`, `busio`, `board`

## Configuration (RCAN)

- All robot configs use the `.rcan.yaml` extension
- Configs follow the [RCAN Spec schema](https://rcan.dev/spec/)
- Required top-level keys: `rcan_version`, `metadata`, `agent`, `physics`, `drivers`, `network`, `rcan_protocol`
- Presets live in `config/presets/`
- The wizard (`castor wizard`) generates new configs interactively and saves API keys to `.env`

## Docker

```bash
# Gateway only (API + channels, no hardware)
docker compose up

# Gateway + hardware runtime
docker compose --profile hardware up

# Gateway + Streamlit dashboard
docker compose --profile dashboard up

# Everything
docker compose --profile hardware --profile dashboard up
```

The `docker-compose.yml` uses `env_file: .env` so secrets stay out of the compose file.

## CI/CD

**RCAN Spec Validation** (`.github/workflows/validate_rcan.yml`):
- Triggers on push/PR touching `*.rcan.yaml` or `config/**`
- Validates all config files against the RCAN JSON Schema
- Python 3.10

**Static Site**: `site/` deploys to Cloudflare Pages via `wrangler.toml`.

## Code Style

- **PEP 8** with 100-char line length (enforced by Ruff)
- **snake_case** for functions/variables
- **Type hints** on public method signatures
- **Docstrings** on classes and non-trivial methods
- **Lazy imports** for optional SDKs (hardware libraries, channel SDKs)
- **Structured logging**: `logging.getLogger("OpenCastor.<Module>")`
- **Linting**: `ruff check castor/` / `ruff format castor/`

## Testing

Tests go in `tests/`, mirroring the `castor/` package structure.

```bash
pip install -e ".[dev]"
pytest tests/
```

RCAN schema validation runs in CI automatically.

## Adding New Components

### New AI Provider
1. Create `castor/providers/<name>_provider.py`, subclass `BaseProvider`
2. Implement `__init__` (resolve key from env then config) and `think()`
3. Register in `castor/providers/__init__.py` (`get_provider()`)
4. Add env var mapping to `castor/auth.py` `PROVIDER_AUTH_MAP`
5. Add SDK to `pyproject.toml` and `requirements.txt`
6. Add env var to `.env.example`

### New Hardware Driver
1. Create `castor/drivers/<name>.py`, subclass `DriverBase`
2. Implement `move()`, `stop()`, `close()` with mock fallback
3. Register in `get_driver()` in `castor/main.py`
4. Add SDK to `pyproject.toml` and `requirements.txt`

### New Messaging Channel
1. Create `castor/channels/<name>.py`, subclass `BaseChannel`
2. Implement `start()`, `stop()`, `send_message()`
3. Register in `castor/channels/__init__.py`
4. Add env vars to `castor/auth.py` `CHANNEL_AUTH_MAP` and `.env.example`
5. Add SDK to `pyproject.toml` optional dependencies
6. Add webhook endpoint to `castor/api.py` if needed

### New Hardware Preset
1. Create `config/presets/<name>.rcan.yaml`
2. Follow the RCAN schema structure (see existing presets)
3. CI validates automatically on push

See `CONTRIBUTING.md` for detailed examples and templates.

## Safety Considerations

- System prompt forces LLMs to output strict JSON only
- Driver values clamped to safe ranges (e.g., Dynamixel: 0-4095 ticks)
- `safety_stop: true` in RCAN config enables emergency stop
- Configurable latency budgets (`latency_budget_ms`)
- Emergency stop via dashboard button, `POST /api/stop`, or channels
- Optional bearer-token auth on the API gateway (`OPENCASTOR_API_TOKEN`)
- `.env` file in `.gitignore` -- secrets never committed
- Drivers gracefully shut down via `close()` in finally blocks

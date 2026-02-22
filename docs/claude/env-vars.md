# OpenCastor Environment Variables & Dependencies

Copy `.env.example` to `.env` and fill in what you need.

## AI Providers

| Variable | Provider | Notes |
|----------|----------|-------|
| `GOOGLE_API_KEY` | Google Gemini | |
| `OPENAI_API_KEY` | OpenAI GPT-4.1 | Also used for OpenRouter |
| `ANTHROPIC_API_KEY` | Anthropic Claude | |
| `OPENROUTER_API_KEY` | OpenRouter (multi-model) | |
| `OLLAMA_BASE_URL` | Local Ollama | No key needed; default `http://localhost:11434` |
| `GOOGLE_AUTH_MODE=adc` | Google ADC | Application Default Credentials |
| `HF_AUTH_MODE=cli` | HuggingFace | CLI auth (`huggingface-cli login`) |
| `VERTEX_PROJECT` | Vertex AI | GCP project ID |
| `VERTEX_LOCATION` | Vertex AI | Default: `us-central1` |
| `VERTEX_MODEL` | Vertex AI | Default: `gemini-1.5-pro-002` |

## Messaging Channels

| Variable | Channel |
|----------|---------|
| *(none — QR code scan)* | WhatsApp (neonize) |
| `TWILIO_ACCOUNT_SID` | WhatsApp Twilio |
| `TWILIO_AUTH_TOKEN` | WhatsApp Twilio |
| `TWILIO_WHATSAPP_NUMBER` | WhatsApp Twilio |
| `TELEGRAM_BOT_TOKEN` | Telegram |
| `DISCORD_BOT_TOKEN` | Discord |
| `SLACK_BOT_TOKEN` | Slack |
| `SLACK_APP_TOKEN` | Slack (Socket Mode) |
| `SLACK_SIGNING_SECRET` | Slack (webhook verification) |
| `MQTT_BROKER_HOST` | MQTT |
| `MQTT_USERNAME` | MQTT |
| `MQTT_PASSWORD` | MQTT |
| `HA_LONG_LIVED_TOKEN` | Home Assistant |

## Gateway & Runtime

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENCASTOR_API_TOKEN` | None | Static bearer token (`openssl rand -hex 32`) |
| `OPENCASTOR_JWT_SECRET` | None | RCAN JWT signing secret |
| `JWT_SECRET` | None | Multi-user JWT signing (checked first; fallback to API_TOKEN) |
| `OPENCASTOR_USERS` | None | `user:pass:role,user2:pass2:role2` (SHA-256 passwords) |
| `OPENCASTOR_CORS_ORIGINS` | `*` | Comma-separated allowed CORS origins (restrict in prod) |
| `OPENCASTOR_API_HOST` | `127.0.0.1` | Bind address |
| `OPENCASTOR_API_PORT` | `8000` | Port |
| `OPENCASTOR_COMMAND_RATE` | `5` | Max `/api/command` calls/sec/IP |
| `OPENCASTOR_WEBHOOK_RATE` | `10` | Max webhook calls/min/sender |
| `OPENCASTOR_MAX_STREAMS` | `3` | Max concurrent MJPEG clients |
| `OPENCASTOR_CONFIG` | `robot.rcan.yaml` | Config file path |
| `OPENCASTOR_MEMORY_DIR` | — | Memory persistence directory |

## Storage & Database

| Variable | Default | Purpose |
|----------|---------|---------|
| `CASTOR_MEMORY_DB` | `~/.castor/memory.db` | SQLite episode memory database |
| `CASTOR_USAGE_DB` | `~/.castor/usage.db` | SQLite token/cost tracking |
| `CASTOR_HUB_URL` | GitHub raw `config/hub_index.json` | Override hub preset index URL |
| `CASTOR_SWARM_CONFIG` | `config/swarm.yaml` | Swarm node registry path |

## Voice & Audio

| Variable | Default | Purpose |
|----------|---------|---------|
| `CASTOR_VOICE_ENGINE` | auto | Override voice engine: `whisper`, `google`, `local` |
| `SDL_AUDIODRIVER` | — | Override SDL audio driver (e.g., `alsa` for RPi USB speaker) |
| `AUDIODEV` | — | ALSA device override for gTTS/pygame (e.g., `plughw:2,0`) |

## Hardware

| Variable | Default | Purpose |
|----------|---------|---------|
| `DYNAMIXEL_PORT` | — | Serial port override (e.g., `/dev/ttyUSB0`) |
| `CAMERA_INDEX` | `0` | Primary camera device index |

## Logging

| Variable | Default | Purpose |
|----------|---------|---------|
| `LOG_LEVEL` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## Dependencies

### Core (always installed)

| Category | Packages |
|----------|---------|
| Brain | `google-generativeai`, `openai`, `anthropic` |
| Body | `dynamixel-sdk`, `pyserial` |
| Eyes | `opencv-python-headless` |
| Config | `pyyaml`, `jsonschema`, `requests` |
| Gateway | `fastapi`, `uvicorn`, `python-dotenv`, `httpx` |
| Auth | `PyJWT` |
| Dashboard | `streamlit`, `SpeechRecognition`, `gTTS` |
| CLI | `rich` |

### Optional Extras (pip install opencastor[...])

| Extra | Packages | Use Case |
|-------|---------|---------|
| `rpi` | adafruit-circuitpython-pca9685, picamera2, neonize | Raspberry Pi full stack |
| `whatsapp` | `neonize==0.3.10` | WhatsApp QR code scan |
| `whatsapp-twilio` | `twilio` | WhatsApp via Twilio (legacy) |
| `telegram` | `python-telegram-bot>=21.0` | Telegram Bot |
| `discord` | `discord.py>=2.3.0` | Discord Bot |
| `slack` | `slack-bolt>=1.18.0` | Slack Socket Mode |
| `mqtt` | `paho-mqtt>=2.0.0` | MQTT broker |
| `channels` | All messaging SDKs + mqtt | All messaging channels |
| `rcan` | `zeroconf` | mDNS discovery (PyJWT is now core) |
| `dynamixel` | `dynamixel-sdk>=3.7.31` | Dynamixel servos |
| `vertex` | `google-genai>=1.0.0` | Google Vertex AI |
| `homeassistant` | `aiohttp>=3.9.0` | Home Assistant channel |
| `ros2` | `rclpy` | ROS2 bridge driver (install via ROS2 distro) |
| `webrtc` | `aiortc>=1.6.0` | WebRTC streaming |
| `all` | Everything above | Full installation |
| `dev` | `pytest`, `pytest-asyncio`, `ruff`, `qrcode` | Development tools |

### Hardware-specific (RPi only, not in PyPI)
- `adafruit-circuitpython-pca9685`
- `adafruit-circuitpython-motor`
- `busio`, `board`
- `picamera2`

### Neonize Version Pin
**Always use `neonize==0.3.10`**. Versions 0.3.14+ require `protobuf>=6.32.1` which conflicts with system `protobuf 5.x`.

Fix: `pip install "neonize==0.3.10" -q`

---

## RPi5 Hardware Setup Notes

- GPIO I2C must be enabled: add `dtparam=i2c_arm=on` to `/boot/firmware/config.txt`, then reboot. Confirms as `/dev/i2c-1`.
- PCA9685 requires external power before i2cdetect shows `0x40`.
- OAK-D: `pip install depthai==3.3.0` + `sudo udevadm control --reload-rules`
- USB speaker ALSA routing: `~/.asoundrc` with `defaults.pcm.card 2`; set `SDL_AUDIODRIVER=alsa` + `AUDIODEV=plughw:2,0`

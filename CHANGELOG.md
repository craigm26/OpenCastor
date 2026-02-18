# Changelog

All notable changes to OpenCastor are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [CalVer](https://calver.org/) versioning: `YYYY.M.DD.PATCH`.

## [2026.2.18.10] - 2026-02-18

### Added
- **WhatsApp setup flow** — wizard now verifies neonize is installed (auto-installs if missing), checks for existing session, explains QR pairing flow, and optionally starts a live QR pairing session right from the wizard
- **Telegram bot verification** — wizard collects bot token and verifies it by calling Telegram's `getMe` API, showing bot name and username on success
- **Generic channel setup** — all channels now get proper credential collection and validation

## [2026.2.18.9] - 2026-02-18

### Changed
- **Credentials moved to `~/.opencastor/`** — `.env` vars now written to `~/.opencastor/env` (0600 perms) alongside `anthropic-token` and `wizard-state.yaml`. Local `.env` still written for backward compat.
- **Uninstaller redesigned** — removes install dir but keeps `~/.opencastor/` by default. Asks user: "[1] Keep credentials (recommended)" or "[2] Delete everything". Migrates legacy `.env` to `~/.opencastor/env` during uninstall.
- **Auth loads `~/.opencastor/env` first** — `load_dotenv_if_available()` reads the safe env file before local `.env`, without overriding already-set vars.

## [2026.2.18.8] - 2026-02-18

### Added
- **AI accelerator detection** — health check now detects Hailo AI Hat (PCIe + /dev/hailo*), Google Coral TPU, Intel Movidius/MyriadX (OAK-D), and Nvidia Jetson
- **Auth module knows about token store** — `check_provider_ready("anthropic")` now checks `~/.opencastor/anthropic-token`, fixing the "no key set" false positive in post-wizard health check

### Fixed
- Post-wizard health check no longer says "FAIL: Provider key (anthropic) no key set" when setup-token is stored
- Removed stale `ANTHROPIC_AUTH_MODE=oauth` check from auth module

## [2026.2.18.7] - 2026-02-18

### Fixed
- **Installer version synced** — `install.sh` and `install.ps1` now show correct version (were stuck at v2026.2.17.20)

## [2026.2.18.6] - 2026-02-18

### Added
- **Deep hardware discovery** — startup health check now enumerates:
  - USB devices (via `lsusb`) — shows what's connected at each port
  - I2C devices (via `i2cdetect`) — identifies PCA9685, IMUs, sensors, OLEDs by address
  - SPI bus availability
  - Serial ports (UART, USB-serial adapters like Arduino/ESP32)
  - Loaded kernel drivers (I2C, SPI, PWM, V4L2, USB-serial, audio)

### Changed
- **Anthropic model fetch no longer uses API** — setup-tokens return 401 on `/v1/models`. Now parses model IDs from the public docs page (no auth needed). Falls back to static list if docs unreachable.

## [2026.2.18.5] - 2026-02-18

### Fixed
- **Token priority fix** — OpenCastor stored token (`~/.opencastor/anthropic-token`) now takes priority over `ANTHROPIC_API_KEY` env var and `.env` file. Prevents using OpenClaw's stale API key instead of the setup-token you just saved.
- **Stop importing OpenClaw's Anthropic key** — wizard no longer auto-detects `ANTHROPIC_API_KEY` from OpenClaw config (other provider keys like Google/OpenAI are still imported). This prevents the token sink problem.
- **Wizard detects existing setup-token** — on re-run, if `~/.opencastor/anthropic-token` exists, offers to reuse it instead of asking for a new one.
- **Health check shows correct auth source** — now reports "setup-token stored" when using token store, not "ANTHROPIC_API_KEY set" from the wrong source.

## [2026.2.18.4] - 2026-02-18

### Added
- **Startup health check** — `castor run` now performs a full system health check at boot: Python version, package version, dependencies, config validation, AI provider auth, camera, GPIO, I2C, speaker, disk space, memory, CPU temperature. Prints a formatted health card with ✅⚠️❌ status.
- **Wizard state memory** — wizard remembers previous selections (project name, provider, model) and shows them as defaults on re-run. Saved to `~/.opencastor/wizard-state.yaml`.
- **Dynamic version** — `__version__` now reads from installed package metadata via `importlib.metadata` instead of a hardcoded string. Falls back to current version if not pip-installed.
- 21 new tests (1286 total)

### Fixed
- Wizard version display was stuck on old version due to hardcoded `__version__` in `__init__.py`

## [2026.2.18.3] - 2026-02-18

### Fixed
- Lint error: extraneous f-string prefix in cli.py (F541)

## [2026.2.18.2] - 2026-02-18

### Added
- **Dynamic model lists** — Anthropic and OpenAI model selection now fetches live from their APIs, showing the 3 latest models with an option to expand the full list
- Falls back gracefully to built-in static list if API is unreachable or no key is available
- 6 new tests for dynamic model fetching (1271 total)

## [2026.2.18.1] - 2026-02-18

### Changed
- **Separate token store** — OpenCastor now stores Anthropic tokens at `~/.opencastor/anthropic-token`, NOT in Claude CLI's credentials. Prevents the "token sink" problem where sharing tokens between OpenCastor/OpenClaw/Claude CLI causes mutual invalidation.
- **`castor login anthropic`** — option [1] now runs `claude setup-token` directly to generate a fresh token for OpenCastor
- Wizard setup-token flow saves to `~/.opencastor/` instead of `.env`
- Token file has 0600 permissions for security
- 8 new tests (1265 total)

## [2026.2.17.21] - 2026-02-17

### Added
- **Anthropic setup-token auth** — use your Claude Max/Pro subscription instead of pay-per-token API keys
- **`castor login anthropic`** (alias: `castor login claude`) — interactive setup-token or API key auth
- **Auto-read Claude CLI credentials** — reads setup-token from `~/.claude/.credentials.json` as fallback
- Wizard now recommends setup-token as option [1] over API key
- 7 new Anthropic auth tests (1264 total)

## [2026.2.17.20] - 2026-02-17

### Added
- **Wizard redesign** — QuickStart now has distinct steps: Provider → Auth → Primary Model → Secondary Models → Messaging
- **Provider-specific auth flows** — Anthropic (Max/Pro OAuth or API key), Google (ADC via `gcloud` or API key), HuggingFace (`huggingface-cli login` or paste token), OpenAI (API key), Ollama (connection check)
- **Primary model selection** — curated model list per provider with recommendations and descriptions
- **Secondary models** — optional specialized models (Gemini Robotics ER 1.5, GPT-4o vision, custom) with cross-provider auth
- **Uninstall script** — `curl -sL opencastor.com/uninstall | bash`
- 21 new wizard tests (1244 total)

## [2026.2.17.17] - 2026-02-17

### Added
- **"Start your robot now?"** — wizard offers to launch `castor run` immediately after setup completes

### Fixed
- **Post-install instructions** — simplified Quick Start to `cd && source venv/bin/activate && castor run` (castor requires venv active)

## [2026.2.17.19] - 2026-02-17

### Added
- **Wizard redesign** — QuickStart now has distinct steps: Provider → Auth → Primary Model → Secondary Models → Messaging
- **Provider-specific auth flows** — Anthropic (Max/Pro OAuth or API key), Google (ADC via `gcloud` or API key), HuggingFace (`huggingface-cli login` or paste token), OpenAI (API key), Ollama (connection check)
- **Primary model selection** — curated model list per provider with recommendations and descriptions
- **Secondary models** — optional specialized models (Gemini Robotics ER 1.5, GPT-4o vision, custom) with cross-provider auth
- 21 new wizard tests (1244 total)

## [2026.2.17.18] - 2026-02-17

### Added
- **Claude Max/Pro plan support** — wizard offers OAuth sign-in as option 1 when choosing Anthropic. Auto-detects Claude CLI, installs if needed, runs `claude login` for browser-based auth. Falls back to API key gracefully.
- **Uninstall script** — `curl -sL opencastor.com/uninstall | bash`

## [2026.2.17.16] - 2026-02-17

### Added
- **QuickStart now includes provider + messaging choice** — users pick their AI provider (Anthropic, Google, OpenAI, HuggingFace, Ollama) and optionally connect WhatsApp or Telegram, all in the streamlined QuickStart flow

### Fixed
- **RCAN schema** — added `created_at` to metadata schema (was causing validation error)

## [2026.2.17.15] - 2026-02-17

### Fixed
- **Installer** — wizard stdin redirected to `/dev/tty` for `curl | bash` piped installs (wizard reads user input properly instead of script lines)
- **Post-install messaging** — clear "Useful Commands" section showing `castor wizard`, `castor --help`, `castor status`, `castor doctor`, `castor dashboard`; explicit tip that wizard can be re-run anytime

## [2026.2.17.14] - 2026-02-17

### Fixed
- **Installer** — wizard runs with `--accept-risk` (skips safety prompt, goes straight to config), no longer swallows wizard output, properly reports exit code
- **Wizard version** — now displays correct dynamic version via f-string

## [2026.2.17.13] - 2026-02-17

### Fixed
- **neonize version pin** — `>=1.0.0` → `>=0.3.10` (1.0 doesn't exist)
- **Installer resilience** — `[rpi]` extras failure falls back to core install instead of aborting

## [2026.2.17.12] - 2026-02-17

### Fixed
- **Installer** — `libatlas-base-dev` detection uses `apt-cache policy` (handles Bookworm "no candidate" correctly), `DEBIAN_FRONTEND=noninteractive` suppresses kernel upgrade dialogs, detached HEAD handled in `git pull`
- **`python -m castor`** — Added `__main__.py` so the package is runnable as a module
- **Install verification** — `install-check.sh` tries `castor` binary before `python -m castor` fallback

## [2026.2.17.11] - 2026-02-17

### Added
- **Cross-platform installer** — `install.sh` supports macOS (Homebrew), Fedora (dnf), Arch (pacman), Alpine (apk) alongside Debian/Ubuntu/RPi. New `install.ps1` for native Windows PowerShell. Post-install verification scripts (`install-check.sh`, `install-check.ps1`). CI matrix testing on ubuntu/macos/windows.
- **Safety Protocol Engine** (`castor/safety/protocol.py`) — 10 configurable rules adapted from Protocol 66, YAML config overrides, `castor safety rules` CLI
- **Continuous sensor monitoring** (`castor/safety/monitor.py`) — CPU temp, memory, disk, CPU load with background thread, auto e-stop after 3 consecutive criticals, `/proc/sensors` in virtual FS, `castor monitor --watch` CLI
- **Ollama provider improvements** — model cache with TTL, auto-pull, model aliases, remote host profiles via `OLLAMA_HOST`, configurable timeouts, helpful error messages

### Changed
- **BREAKING: RCAN role alignment** — `ADMIN` → `OWNER`, `OPERATOR` → `LEASEE` per RCAN spec. Backward compatibility layer accepts old names with deprecation warning.
- **Cross-platform Python** — platform markers on RPi deps (`; sys_platform == 'linux'`), `[core]`/`[all]` extras groups, conditional imports for hardware modules, cross-platform TTS/crontab/service commands

### Fixed
- **Installer** — friendly skip for `libatlas-base-dev` on Bookworm/RPi5, default config fallback (`robot.rcan.yaml`) when wizard is skipped
- **Safety module polish** — wrapped integration points in try/except, fixed CLI syntax error, cleaned imports, reformatted files
- **Website** — shrunk oversized wizard-creates icons, fixed mobile nav hamburger menu cutoff

## [2026.2.17.10] - 2026-02-17

### Added
- **Anti-subversion module** (`castor/safety/anti_subversion.py`) — prompt injection defense with 15 regex patterns, forbidden path detection, anomaly rate-spike flagging, wired into SafetyLayer and BaseProvider
- **Work authorization** (`castor/safety/authorization.py`) — work order lifecycle for destructive actions (request → approve → execute/revoke), role-gated approval, self-approval prevention, auto-expiry, destructive action detection for GPIO/motor paths
- **Physical bounds enforcement** (`castor/safety/bounds.py`) — workspace sphere/box/forbidden zones, per-joint position/velocity/torque limits, force limits (50N default, 10N human-proximity), pre-built configs for differential_drive/arm/arm_mobile
- **Tamper-evident audit log** — SHA-256 hash chain on every audit entry, `castor audit --verify` CLI, backward-compatible with existing logs
- **Safety state telemetry** (`castor/safety/state.py`) — `SafetyStateSnapshot` with composite health score exposed at `/proc/safety`
- **Recipe submission issue template** (`.github/ISSUE_TEMPLATE/recipe-submission.yml`)
- **`castor hub share --submit`** — auto-fork, branch, and PR via `gh` CLI

### Fixed
- **RCAN Safety Invariants 4 & 5** — `check_role_rate_limit()` and `check_session_timeout()` now enforced in all SafetyLayer public methods (read/write/append/ls/stat/mkdir)
- **E-stop authorization** — `clear_estop()` requires auth code via `OPENCASTOR_ESTOP_AUTH` env var when set

### Changed
- **PyPI publishing** — Trusted Publisher (OIDC) with API token fallback, all actions pinned to SHA, scoped permissions, concurrency groups, timeouts, twine check

## [2026.2.17.9] - 2026-02-17

### Added
- **Ollama provider** — run local LLMs with zero API keys
  - Text generation and vision support (LLaVA, BakLLaVA, Moondream, etc.)
  - Streaming token output via `/api/chat`
  - Model listing and pulling via Ollama API
  - `castor login ollama` — test connection, configure host, list available models
  - Proper `OllamaConnectionError` with helpful "ollama serve" message
  - Auto-detection of vision-capable models

## [2026.2.17.8] - 2026-02-17

### Added
- **Community Hub** — browse, share, and install tested robot configs
  - `castor hub browse` — list recipes with category/difficulty/provider filters
  - `castor hub search` — full-text search across all recipes
  - `castor hub show` — view recipe details and README
  - `castor hub install` — copy a recipe config to your project
  - `castor hub share` — interactive wizard to package and scrub your config
  - `castor hub categories` — list all categories and difficulty levels
- **PII scrubbing engine** — automatically removes API keys, emails, phone numbers, public IPs, home paths, and secrets from shared configs
- **2 seed recipes** — PiCar-X Home Patrol (beginner/home) and Farm Scout Crop Inspector (intermediate/agriculture)
- **Hub website page** at opencastor.com/hub with category browser and recipe cards
- Hub link added to site navigation across all pages
- 17 new tests for hub (PII scrubbing, packaging, listing, filtering)

## [2026.2.17.7] - 2026-02-17

### Added
- **Hugging Face provider** — access 1M+ models via the Inference API
  - Text-generation and vision-language models (LLaVA, Qwen-VL, etc.)
  - Supports Inference Endpoints for dedicated deployments
  - Auto-detects vision-capable models
- **`castor login` CLI command** — authenticate with Hugging Face
  - Interactive token prompt with secure input
  - `--list-models` flag to discover trending models by task
  - Saves token to both `~/.cache/huggingface/` and local `.env`
- `huggingface-hub` added as core dependency
- Hugging Face option added to setup wizard (option 5)
- 10 new tests for HF provider and login CLI

### Changed
- Provider count: 4 → 5 (website, docs, stats updated)
- Ollama moved from wizard option 5 → 6

## [2026.2.17.6] - 2026-02-17

### Fixed
- Removed deprecated `License :: OSI Approved` classifier (PEP 639 compliance) — newer setuptools rejected it when `license` expression was already set
- Ran `ruff format` across all 73 source and test files to pass CI formatting check
- Added `python-multipart>=0.0.7` as explicit dependency — required by FastAPI for `request.form()`, was failing on Python 3.10/3.11 in CI
- Replaced invalid PyPI classifier `Topic :: Scientific/Engineering :: Robotics` with valid `Artificial Intelligence` classifier
- Synced package version in `pyproject.toml` with git tag

## [2026.2.17.5] - 2026-02-17

### Added
- `py.typed` marker for PEP 561 type hint support
- `__all__` exports to core modules (providers, drivers, channels, root)
- Return type annotations (`-> None`) on all 41 CLI command handlers
- Type hints and docstrings on `DriverBase` abstract methods (move, stop, close)
- Signal handling (SIGTERM/SIGINT) in API gateway for graceful shutdown
- CLI commands reference table in CONTRIBUTING.md
- Comprehensive test suites: CLI, API endpoints, drivers, channels
- Dependabot config for Python dependencies (already existed for GitHub Actions)

### Fixed
- `castor schedule` command not dispatching due to `--command` arg shadowing subparser `dest="command"`

### Changed
- `DriverBase.move()` now has explicit `linear: float, angular: float` signature
- CONTRIBUTING.md now documents all 41 CLI commands and how to add new ones
- API gateway version updated to 2026.2.17.5

## [2026.2.17.4] - 2026-02-17

### Added
- 41-command CLI with grouped help (`castor --help`)
- `castor doctor` -- system health checks
- `castor fix` -- auto-repair common issues with backup-before-repair
- `castor demo` -- simulated perception-action loop (no hardware/API keys)
- `castor quickstart` -- one-command setup (wizard + demo)
- `castor configure` -- interactive post-wizard config editor
- `castor shell` / `castor repl` -- interactive command shells
- `castor record` / `castor replay` -- session recording and playback
- `castor benchmark` -- perception-action loop performance profiling
- `castor lint` -- deep config validation beyond JSON schema
- `castor learn` -- interactive 7-lesson tutorial
- `castor test` -- pytest wrapper for running test suite
- `castor diff` -- structured RCAN config comparison
- `castor profile` -- named config profile management
- `castor plugins` -- extensible plugin hook system (`~/.opencastor/plugins/`)
- `castor audit` -- append-only event audit log viewer
- `castor approvals` -- approval queue for dangerous motor commands
- `castor schedule` -- cron-like task scheduling
- `castor search` -- semantic search over operational logs
- `castor network` -- Tailscale integration and network status
- `castor fleet` -- multi-robot fleet management via mDNS
- `castor export` -- config bundle export with secrets auto-redacted
- `castor watch` -- live Rich telemetry dashboard
- `castor logs` -- structured colored log viewer with filtering
- `castor backup` / `castor restore` -- config and credential backup
- `castor migrate` -- RCAN config version migration
- `castor upgrade` -- self-update with health check
- `castor update-check` -- PyPI version check with cache
- `castor install-service` -- systemd service unit generation
- `castor privacy` -- sensor access privacy policy viewer
- `castor calibrate` -- interactive servo/motor calibration
- `castor test-hardware` -- individual motor/servo testing
- Safety: watchdog timer (auto-stop on brain timeout)
- Safety: geofence (operating radius limit with dead reckoning)
- Safety: approval gate (queue dangerous commands for human review)
- Safety: privacy policy (default-deny for camera, audio, location)
- Safety: battery monitor with low-voltage emergency stop
- Safety: crash recovery with automatic crash reports
- Contextual error messages with fix suggestions
- Plugin system with startup/shutdown/action/error hooks
- Shell completions via argcomplete
- SECURITY.md with vulnerability disclosure policy
- CHANGELOG.md
- pytest-cov integration for code coverage
- Pre-commit hooks (ruff, ruff-format, secrets scanning)

### Fixed
- CI workflows: `actions/checkout@v6` -> `@v4`, `actions/setup-python@v6` -> `@v5`
- Dockerfile: added non-root user, updated to `bookworm` base
- docker-compose: added `depends_on`, log rotation limits
- Moved `argcomplete` from dev to core dependencies

### Changed
- README updated with all 41 CLI commands (was 6)
- Architecture diagram now includes API gateway layer
- Ruff rules expanded: added bugbear (B), bandit security (S), pyupgrade (UP)
- CORS in api.py now configurable via `OPENCASTOR_CORS_ORIGINS` env var
- PyPI classifiers updated (License, OS, Environment, Robotics topic)

## [2026.2.17.3] - 2026-02-17

### Added
- Initial public release
- Provider adapters: Google Gemini, OpenAI GPT-4.1, Anthropic Claude
- Hardware drivers: PCA9685 (I2C PWM), Dynamixel (Protocol 2.0)
- Messaging channels: WhatsApp (neonize), Telegram, Discord, Slack
- FastAPI gateway with REST API and webhook endpoints
- Streamlit dashboard (CastorDash)
- RCAN Standard compliance with JSON Schema validation
- Virtual filesystem with RBAC and safety layers
- RCAN Protocol: JWT auth, mDNS discovery, message routing, capability registry
- Interactive setup wizard
- Docker and docker-compose support
- One-line installer script for Raspberry Pi
- Cloudflare Pages static site

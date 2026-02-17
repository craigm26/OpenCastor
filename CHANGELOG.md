# Changelog

All notable changes to OpenCastor are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [CalVer](https://calver.org/) versioning: `YYYY.M.DD.PATCH`.

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

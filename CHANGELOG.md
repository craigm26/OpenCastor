# Changelog

All notable changes to OpenCastor are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions use date-based scheme: `YYYY.MM.DD.patch`.

---

## [2026.3.7.0] — 2026-03-06
### What's New — "Whole Solution" Release
The complete RCAN robot safety stack is now production-ready:

#### Safety & Accountability
- **Streaming inference loop** (`StreamingInferenceLoop`) — live perception at up to 10 FPS
- **Confidence gates** — auto-block actions below configurable thresholds
- **HiTL gates** — human-in-the-loop approval for critical actions
- **Thought log** — full AI reasoning audit trail with JSONL persistence
- **Commitment chain** — XDG-compliant HMAC-chained action ledger

#### Distributed Registry (RCAN §17)
- **`node_resolver.py`** — `NodeResolver` resolves delegated RRNs via `rcan-py` `NodeClient`; follows delegation chain automatically
- **`node_broadcaster.py`** — `NodeBroadcaster` pushes robot record updates to the root rcan.dev registry via `POST /api/v1/sync`
- **Verification tiers** — `castor inspect` now displays robot verification badge (⬜🟡🔵✅) pulled from live registry
- **SDK compat pre-register check** — `castor register` calls `check_rcan_compliance_version()` before submitting; aborts with clear message if spec version mismatch
- **`rcan-py >= 0.2.0`** is now a core dependency (NodeClient, RRN delegated format, rcan-validate node)

#### Compliance
- `castor compliance` — generate structured compliance reports (text/JSON)
- `castor doctor` — 13-point system health check including:
  - RCAN config present check
  - RCAN compliance level (L1/L2/L3) via `check_compliance()`
  - RCAN registry reachability (`check_rcan_registry_reachable()`)
  - Commitment chain integrity check
- RCAN v1.2 compatibility matrix check (`check_rcan_compliance_version()`)

#### Developer Experience
- `castor update` — in-place self-update
- `castor logs` — stream/tail commitment log entries
- `castor benchmark` — measure inference latency
- `castor register` — register robot at rcan.dev (interactive + programmatic)
- Web wizard at port 8765 (`castor wizard --web`)
- Episode memory replay (`castor memory replay`)

#### Quality
- 6,248+ tests across Python 3.10/3.11/3.12/3.13
- Full ruff lint + format compliance
- RCAN SDK integration tests (rcan-py + rcan-validate)
- Node resolver + broadcaster unit tests (mock urllib)

---

## [2026.3.6.0] - 2026-03-06

### Added
- **Ed25519 message signing** — auto-generated keypair at `~/.opencastor/signing_key.pem`; every outbound action signed when `agent.signing.enabled: true`
- **Fleet group policies** — `fleet.groups` in RCAN YAML; deep-merge config resolution; `castor fleet list|resolve|status` subcommands
- **Multi-provider failover** — `agent.fallbacks[]`; `ProviderFailoverChain` with per-error-type triggering
- **Web wizard** — `castor wizard --web` launches browser-based setup at `localhost:8765`; zero extra deps; hardware, provider, API key, channel, registration steps
- **Episode replay** — `castor memory replay --since YYYY-MM-DD [--dry-run]`; skips already-indexed episodes; custom consolidation fn injection
- **`castor inspect`** — unified registry, config, gateway, commitment chain, and compliance view
- **`castor compliance`** — L1/L2/L3 RCAN conformance table with `--json` output
- **`castor register`** — one-click rcan.dev robot registration from CLI or wizard
- **`castor fleet`** — list, resolve, status subcommands
- **`castor memory replay`** — episode replay subcommand
- **`castor fit`** — llmfit model fit analysis via `castor/llmfit_helper.py`
- **Commitment chain** — thread-safe HMAC-SHA256 chained audit log sealed on every `_execute_action()` call; persisted to `.opencastor-commitments.jsonl`
- **rcan Python SDK** — `rcan>=0.1.0` core dependency (PyPI); all imports behind try/except; `castor/rcan/sdk_bridge.py` protocol adapter
- **RCAN message endpoint** — `POST /rcan/message` bridges spec v1.2 `RCANMessage` format
- **Prometheus metrics** — `record_safety_block`, `record_commitment`, `record_failover`, `record_confidence_gate` added to `castor/metrics.py`
- **`castor update`** [planned #456] — self-update command (coming next release)
- **CHANGELOG.md** — this file

### Fixed
- `castor/memory/` package shadowing `castor/memory.py`; resolved by absorbing `EpisodeMemory` into the package (`castor/memory/episode.py`)
- 19 test collection errors eliminated; 6,087 tests now collected cleanly
- All ruff lint errors resolved (unused imports, shadowed builtins, duplicate `cmd_fleet`, inline compound statements)
- Web wizard `--web-port` default aligned to `8765` (was `8080`)
- `pyproject.toml` version field synced with release tag (`2026.3.6.0`)

### Changed
- `castor/memory/` is now a package (was a single module); `from castor.memory import EpisodeMemory` still works
- Install script (`scripts/install.sh`) highlights `castor register` with colored rcan.dev identity pitch

---

## [2026.3.3.0] - 2026-03-03

### Added
- **AI Accountability Layer** (RCAN §16): `confidence_gate.py`, `hitl_gate.py`, `thought_log.py`
- **RCAN v1.2 compliance**: `AUTHORIZE`/`PENDING_AUTH` states, `rcan_version: "1.2.0"`
- **SBOM**: CycloneDX JSON generated on release, EO 14028 compliant
- **SECURITY.md**: physical safety 14-day patch SLA, CVE disclosure process

### Fixed
- `metrics.py` `sorted()` key=str fix

### Tests
- 5,989 tests passing (Python 3.10/3.11/3.12, CI green)

---

## [2026.2.20.12] - 2026-02-20

### Added
- Auto-Start Daemon + Offline Fallback
- WhatsApp group policy fix: `group_policy` evaluated before self-chat guard
- neonize arm64 rebuild (whatsmeow 2025-12-05 → 2026-02-19)
- `BaseProvider.build_messaging_prompt()` — canonical messaging pre-prompt for all surfaces
- Vision guard fix: `_capture_live_frame()` rejects null-padding frames
- Bob gateway runs via nohup + PID management

### Tests
- 2,233 tests (Python 3.10/3.11/3.12, CI green)

---

## [2026.2.20.0] - 2026-02-19

### Added
- Sisyphus Loop (PM→Dev→QA→Apply continuous improvement)
- ALMA consolidation integration
- `castor improve` CLI subcommand
- `castor scan` peripheral auto-detection
- Prompt caching
- Hardware-detection-wins boot override
- Gemini 3 Flash Agentic Vision
- Plug-and-play reframing
- `castor dashboard` TUI (tmux multi-pane monitor)
- Full brand kit: SVG icon, lockup, 11 PNGs, 4 variants

### Tests
- 2,233 tests

---

## [2026.2.19.0] - 2026-02-18

### Added
- Tiered brain architecture (fast/planner separation)
- Hailo-8 NPU integration
- OAK-D depth camera support
- 8 AI provider support (Anthropic, OpenAI, Google, HuggingFace, Ollama, etc.)
- RCAN v1.1 addressing

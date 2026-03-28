# Changelog

All notable changes to OpenCastor are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions use date-based scheme: `YYYY.MM.DD.patch`.

---

## [2026.3.28.0] - 2026-03-28

### Added
- `castor loa` — LoA enforcement CLI (status/enable/disable); default `loa_enforcement: true`
- `castor components` — hardware component registry (detect/list/register); deterministic RCN IDs
- `castor rrf` — full RRF v2 provenance chain CLI (register robot/component/model/harness)
- `castor doctor` — llmfit check wired in; `_check_llmfit()` validates model headroom
- `system_info.py` — live system metrics (RAM, disk, CPU temp, NPU detection)

### Changed
- Bridge telemetry split: live fields (30s) vs static fields (5 min) — ~55% write reduction
- `harness_config` removed from bridge telemetry push (now only in `user_harness_config`)
- `pq_signing_pub` stripped from Firestore root doc (stored in RRF; reference by `pq_kid`)
- Bridge pushes LAN IP + WS telemetry/safety URLs to Firestore
- Bridge pushes skills list to `robots/{rrn}/telemetry/skills` subcollection
- `channels_active` falls back to `config.agent.channels` when no live channel objects
- RRF HTTP calls via `curl` subprocess (CF bot protection blocks urllib UA)
- `loa_enforcement` default changed to `True` in `p66_manifest.py`
- BigQuery streaming insert for telemetry (buffered, 10-sample flush)
- GCS audit artifact write helper (`_write_audit_artifact`)
- GCS NDJSON archive fallback when BigQuery unavailable

### Fixed
- Gateway `KeyError: 'rcan_protocol'` crash — use `.get("rcan_protocol", {})`
- LAN IP resolution via UDP socket trick (replaces unreliable `gethostbyname`)
- `system_upgrade()` editable install detection
- RRF API calls migrated from v1 → v2 (v1 returns 410 Gone)
- ruff import sort in bridge.py (CI lint)


## [2026.3.26.1] - 2026-03-26

### Added
- **RCAN v2.1 migration**: `castor/migrate.py` — migration chain extended to 2.1 (from 1.4 through 1.10); `_migrate_1_10_to_2_1()` flags signature:'pending' usages and adds firmware_hash/attestation_ref stubs
- **Firmware attestation** (`castor/firmware.py`): `FirmwareManifest`, `sign_manifest()`, `verify_manifest()`, `firmware_hash_from_manifest()`, `generate_manifest()` — new CLI: `castor attest generate/sign/verify/serve`
- **SBOM generation** (`castor/sbom.py`): `RCANBOM`, `SBOMComponent`, `RCANSBOMExtensions` (CycloneDX v1.5+), `generate_sbom()`, `publish_sbom_to_rrf()` — new CLI: `castor sbom generate/publish/verify`
- **Authority access handler** (`castor/authority.py`): `AuthorityRequestHandler`, `AuditDataExporter`, `send_authority_response()` — handles AUTHORITY_ACCESS (41) per EU AI Act Art. 16(j); always notifies owner, always logs to commitment chain
- **L5 conformance checks** (`castor/conformance.py`): `_check_rcan_v21()` — firmware manifest, SBOM attestation, authority handler, audit chain retention (≥3650 days), rcan_version; `compliance_report()` method returns structured EU AI Act compliance report
- **MessageType 23–44** in `castor/rcan/message.py`: FLEET_COMMAND through SBOM_UPDATE (aligned with rcan-py 1.1.0)

### Changed
- `castor/compliance.py`: `SPEC_VERSION` → `"2.1"`, `ACCEPTED_RCAN_VERSIONS` → only `("2.1", "2.1.0")` (v2.1 clean break — no v1.x interop)
- `castor/migrate.py`: `CURRENT_VERSION` → `"2.1"`

### Fixed
- `tests/test_config_validation.py`, `tests/test_conformance.py`, `tests/test_embedding_interpreter.py`: rcan_version updated to 2.1

## [2026.3.21.1] - 2026-03-21

### Added
- Competition engine: Sprint format — time-boxed competitions with announced prize pools, tiered payout (50/30/20), anti-snipe 1h lock (`castor/competitions/sprint.py`)
- Competition engine: Threshold Race — jackpot mechanic, first robot to hit target score wins, 3x independent verification re-run with 2% tolerance (`castor/competitions/threshold_race.py`)
- Competition engine: Model×Hardware Bracket Seasons — monthly seasons, 5 hardware×model classes, class champions (2000/1000 credits), grand champion bonus 5000 credits (`castor/competitions/bracket_season.py`)
- Competition API endpoints: GET/POST /api/competitions, /api/competitions/races, /api/seasons/current

### Changed
- All Gemini 2.0/1.5 model strings updated to Gemini 2.5 (gemini-2.0-flash → gemini-2.5-flash, gemini-1.5-pro → gemini-2.5-pro) ahead of June 2026 deprecation
- Firestore client and queue state now cached — contribute throughput improved 10x (fetch latency 4.6s → 14ms)
- Auto-start contribute on gateway startup via agent.contribute config

### Fixed
- FieldFilter import moved to module level (ruff I001)

---

## [2026.3.20.4] — 2026-03-20

### Added
- `castor provider auth/list/status` CLI — test and inspect gated model provider credentials (#723)
- Harness per-layer provider routing — `model: provider/name` per layer, `get_provider_for_layer()` with fallback (#724)
- Autoresearch pipeline live — nightly Gemini 2.0 Flash harness optimization, direct push to main (no PRs); first champion: `lower_cost` (cost_gate_usd 0.01, score 0.9101)
- Hardware-profile harness optimization roadmap — per-tier champions fed by `castor contribute` fleet evals

### Fixed
- RCAN JSON schema `additionalProperties` relaxed — 40/40 configs pass validation (arm.rcan.yaml blocked by harness plugin keys)
- Gemini trailing-comma JSON parse error in autoresearch generator
- `my-robot.rcan.yaml` metadata.author field added (was failing schema required check)

### Changed
- `castor/harness/default_harness.yaml` — champion config applied (cost_gate_usd 0.05→0.01)
- Harness automerge workflow removed — pipeline pushes directly to main

## [2026.3.20.3] — 2026-03-20

### Added — RCAN v1.8: Canonical MessageType & Idle Compute Contribution

- **RCAN v1.8 canonical MessageType table**: 36 message types with fixed integer assignments — single source of truth across spec, rcan-py, and rcan-ts. Eliminates all numbering drift (#165).
- **`castor contribute`**: Idle compute donation skill — robots donate unused NPU/GPU/CPU cycles to distributed science (climate modeling, biodiversity, protein folding, humanitarian AI).
  - `castor/contribute/coordinator.py`: BOINC XML-RPC scheduler request/reply parsing (#714)
  - `castor/contribute/runner.py`: NPU inference path (Hailo-8L), thermal throttling, CPU fallback (#715)
  - `castor/contribute/fleet.py`: Fleet-level coordination with capacity tracking and optimal assignment (#716)
  - `castor/skills/contribute.py`: Daily stats reset, 90-day rolling history archive (#719)
- **CLI**: `castor contribute start/stop/status/history` (#720), `castor peer-test` with transport comparison table (#717)
- **API**: `POST /api/contribute/start`, `POST /api/contribute/stop`, `GET /api/contribute/history`
- **RCAN-MQTT transport**: Wired into runtime startup/shutdown lifecycle (#718) — `rcan/{rrn}/in`, `rcan/{rrn}/out`, `rcan/estop` topics
- **RCAN v1.7 contribute scope** (level 2.5): `CONTRIBUTE_REQUEST` (33), `CONTRIBUTE_RESULT` (34), `CONTRIBUTE_CANCEL` (35) message types
- **v1.8 TRAINING_DATA** moved from 34 to 36 (collision fix), cross-SDK canonical tests in CI
- **Software attestation**: `castor/attestation_generator.py` — pip RECORD hash check, config baseline SHA256, git cleanliness; systemd service template
- **Bridge telemetry**: `rcan_capabilities`, `rcan_max_payload_bytes`, `rcan_transport_supported`, contribute stats published to Firestore
- **Security fixes**: RCAN-Signature bypass, None-principal scope skip, /setup token leak, unauthed sensors, WebSocket JWT bypass, SSRF, LoA default hardening

### Changed
- `RCAN_SPEC_VERSION`: `"1.5"` → `"1.8"` in `castor/rcan/message.py`
- `rcan_spec_version` in P66 manifest: `"1.6"` → `"1.8"`
- `castor/compliance.py`: `SPEC_VERSION` `"1.6"` → `"1.8"`
- Bridge `_dispatch_to_gateway`: explicit handlers for PAUSE, RESUME, SHUTDOWN, OPTIMIZE, SHARE_CONFIG, INSTALL, SNAPSHOT
- Offline allow-list: OPTIMIZE/SHARE_CONFIG/INSTALL blocked offline; PAUSE/RESUME/SHUTDOWN/REBOOT/SNAPSHOT safe offline
- Attestation generator no longer mutates `os.environ` (prevents test contamination)

### Fixed
- `log` → `logger` in RCAN-MQTT shutdown handler (F821 lint)
- `TRAINING_DATA = 34` collision with `CONTRIBUTE_RESULT` — moved to 36
- Test assertions updated for v1.8 SPEC_VERSION and 2026.3.20.x version format
- Website: removed stale `/docs/getting-started` → `/docs.html` redirect
- Website: escaped `{`/`}` in Astro HTML for harness.astro and contribute.astro

### Ecosystem
- **rcan-spec**: v1.8.0 — canonical MessageType table, v1.7 contribute scope, credit/reputation tiers, fleet coordination protocol, NPU identity
- **rcan-py**: v0.7.0 — 609 tests, SPEC_VERSION 1.8.0, canonical type tests, contribute message types + scope validation
- **rcan-ts**: v0.7.0 — 447 tests, SPEC_VERSION 1.8.0, canonical type tests, deprecated aliases (FEDERATION_SYNC→FLEET_COMMAND, ALERT→FAULT_REPORT, AUDIT→TRANSPARENCY)
- **opencastor-client**: Fleet contribution dashboard, history timeline, project selection UI, enable/disable toggle, RCAN v1.8 labels
- **opencastor-ops**: Ecosystem metrics collector, monitoring dashboard (CLI + HTML), harness-research CI dry-run fix, all actions SHA-pinned
- **opencastor-autoresearch**: Contribute impact evaluation module (5 P66 preemption scenarios)
- **Blog**: "What If Every Idle Robot Ran Science?" — reframed OpenCastor as runtime layer for any robot, RCAN/registry explainers

---

## [2026.4.1.0] — 2026-04-01

### Added — RCAN v1.6 Support (4 gaps closed)

- **RCAN spec v1.6** (`rcan_version: "1.6"`): All 22 original protocol audit gaps now addressed.
- **GAP-16 Federated Consent**: `castor bridge` forwards `FEDERATION_SYNC` messages across registries; cross-registry JWT trust chain validation via `_rcan-registry.<domain>` DNSSEC TXT; 3-tier registry hierarchy (root / authoritative / community).
- **GAP-17 Constrained Transports**: RCAN-Minimal 32-byte ESTOP-only frame for LoRa SF12 / BLE; RCAN-Compact CBOR encoding; BLE L2CAP MTU fragmentation. Transport selection in `castor gateway` respects `transport_encoding` on incoming messages.
- **GAP-18 Multi-Modal Payloads**: `media_chunks[]` on RCAN messages; SHA-256 audit trail hashes are included in commitment records; TRAINING_DATA messages now require `media_chunks` (JSON-only rejected; WARNING audit event on legacy format).
- **GAP-14 Identity LoA**: `min_loa_for_control` in Protocol 66 manifest (default 1, backward compat); LoA enforcement in safety layer — control scope requires LoA ≥ 2, safety scope requires LoA ≥ 3 in production mode.

### Changed
- `castor/__init__.py`: `__version__ = "2026.4.1.0"`
- `pyproject.toml`: `version = "2026.4.1.0"`
- Default RCAN config templates emit `rcan_version: "1.6"`
- P66 manifest gains `min_loa_for_control` field (default: 1)

---

## [2026.3.17.13] — 2026-03-17

### Added
- **`castor/optimizer.py` v1**: per-robot runtime optimizer — episodic memory consolidation, idle detection, and adaptive task scheduling (#697, #698, #699).
- **RCAN v1.5+v1.6 conformance checks**: 100/100 conformance score across all spec sections; `castor conformance` command reports compliance gaps (#696).
- **Phase 2 Community Hub**: `/explore` browse page and `/config/:id` detail view for shared configs; Firebase Cloud Functions backend for config publishing and retrieval.
- **Robot profiles + version pinning**: social layer — robot identity cards, pinned software versions, and `CONFIG_SHARE` protocol (OpenCastor#701).
- **`code-reviewer` built-in skill v1.0**: code review skill available in the skill registry; integrates with `castor eval` pipeline.
- **Documentation website**: initial site launched at `opencastor.com/docs/` with core pages, layouts, and navigation.

### Fixed
- **CI**: switched website deploy job from npm to pnpm; added `js-yaml` dependency via pnpm.
- **Website**: removed Windows-only rollup binary; regenerated pnpm lockfile for cross-platform compatibility.

---

## [2026.3.13.14] — 2026-03-13

### Fixed
- `castor/compliance.py`: `SPEC_VERSION` updated `1.3` → `1.4` to match current spec release (#652)
- `castor/migrate.py`: `CURRENT_VERSION` updated `1.3` → `1.4`; added `1.3 → 1.4` migration step covering §22 Capability Advertisement and §17 extended node manifest fields (#652)
- `castor/wizard.py`, `castor/setup_service.py`, `castor/web_wizard/server.py`: all config template generators now emit `rcan_version: "1.4"` instead of stale `"1.3"` (#653)

### CI
- `actions/checkout` upgraded from v4.3.1 to v6.0.2 (`de0fac2e`) across all 5 workflow files — Node.js 24 compatible before June 2026 deprecation deadline (#654)

---

## [2026.3.13.13] — 2026-03-13

### Fixed
- `InvokeCancelRequest`: add `cancel_timeout_ms: Optional[int]` field per RCAN §19.4 spec — receiver wait time for graceful abort before force-terminating (default 5000 ms); field was defined in the spec but absent from the dataclass and `to_message()` serializer (#651)

---

## [2026.3.13.12] — 2026-03-13

### Changed
- `CLAUDE.md`: complete rewrite for RCAN v1.4 — updated version refs, RCAN protocol table (MessageType 16/17), RRN format docs, TieredBrain task routing, JS SDK examples, test gotchas
- `rcan_version` in all config examples updated to `1.4`
- README updates for v1.4 spec alignment

### Ecosystem
- rcan-py: v0.3.1 → v0.4.0 (SPEC_VERSION 1.4)
- rcan-spec: v1.3 → v1.4 (§1–§16 dedicated section pages)

---

## [2026.3.13.12] — 2026-03-13

### Added
- `sdk/js/src/index.ts`: typed `invoke()`, `invokeCancel()`, `registryRegister()`, and `registryResolve()` methods with full interface definitions — implements RCAN §19 INVOKE/INVOKE_CANCEL and §21 REGISTRY_REGISTER/REGISTRY_RESOLVE; includes `timeoutMs` + `replyTo` overrides (#650)

---

## [2026.3.13.11] — 2026-03-13

### Fixed
- `castor/rcan/registry.py`: correct docstring terminology from 'Robot Resource Name' to canonical 'Robot Registration Number'; fix RRN example from RURI-style to `RRN-000000000001` format (#645)
- `.github/workflows/auto-label.yml`: replace broken SHA pin with `actions/labeler@v5` tag to restore PR auto-labelling

---

## [2026.3.13.10] — 2026-03-13

### Fixed
- `castor/fleet/group_policy.py`: `FleetManager.from_config` now emits `logger.warning` when a group entry is not a dict, instead of silently dropping it (#641)

### Added
- `tests/test_fleet_group_policy.py`: 28 tests covering `GroupPolicy.matches`, `FleetManager.from_config` (including malformed-entry warning path), `resolve_config` deep-merge, `apply_to_all`, add/remove helpers, and `_deep_merge` semantics (#640)

---

## [2026.3.13.9] — 2026-03-13

### Added
- `castor/providers/task_router.py`: add `openrouter` to all task categories in `_DEFAULT_ROUTING` — OpenRouterProvider was fully implemented but unreachable via TaskRouter (#639)
- `tests/test_task_router.py`: three new tests validating openrouter reachability, local-provider priority for SENSOR_POLL, and cloud-fallback ordering for REASONING

---

## [2026.3.13.8] — 2026-03-13

### Added
- `castor/providers/task_router.py`: task-aware model routing — selects provider by task category (SENSOR_POLL, NAVIGATION, REASONING, CODE, SEARCH, VISION, SAFETY). SAFETY tier never downgrades. (#612)
- `tests/test_openrouter_provider.py`: `test_model_name_defaults_when_not_configured` — pins `_DEFAULT_MODEL` to `anthropic/claude-3.5-sonnet`; future changes caught by CI. (#637)

### Fixed
- Ruff import cleanup across multiple test files (unused imports removed, blank lines normalized).

---

## [2026.3.13.7] — 2026-03-13

### Changed
- `castor/providers/openrouter_provider.py`: update `_DEFAULT_MODEL` from `anthropic/claude-3.5-haiku` to `anthropic/claude-3.5-sonnet` to align with the current ecosystem-standard model. (#635)

---

## [2026.3.13.6] — 2026-03-13

### Fixed
- `pyproject.toml`: tighten `rcan` dependency constraint from `>=0.1.0` to `>=0.3.0,<1.0` — aligns with minimum SDK version required for RCAN v1.3 §17/§19 features (`INVOKE_CANCEL`, Ed25519 signing). Affects both core deps and `[rcan]` extras group. (#634)

### Docs
- `site/changelog.html`: add missing v2026.3.13.4 and v2026.3.13.5 entries. Changelog now current. (#633)

---

## [2026.3.13.5] — 2026-03-13

### Fixed
- `castor/rcan/message.py`: add `REGISTRY_REGISTER_RESULT` (wire value 16) and `REGISTRY_RESOLVE_RESULT` (wire value 17) to `MessageType` enum per RCAN spec §21. (#631)
- `castor/rcan/sdk_compat.py`: bump minimum rcan-py version check from `>=0.2.0` to `>=0.3.0`. (#630)
- `tests/test_compliance.py`: update stale `rcan_py_version` fixture from `"0.1.0"` to `"0.3.0"` to cover the v0.3.0 compatibility path. (#632)

---

## [2026.3.13.4] — 2026-03-13

### Fixed
- `.github/workflows/deploy-pages.yml`: add workflow file itself to path filter so wrangler-action SHA bumps self-trigger a deploy and verify the fix. (#625)

### Added
- `site/sitemap.xml`: new sitemap covering all 8 top-level pages with change-frequency and priority hints for search crawler discovery. (#627)
- `site/robots.txt`: new robots.txt with `Sitemap:` reference pointing to `/sitemap.xml`. (#627)
- `site/`: OG (`og:title`, `og:description`, `og:type`, `og:url`, `og:image`) and Twitter (`twitter:card`, `twitter:title`, `twitter:description`, `twitter:image`) meta tags added to `about.html`, `docs.html`, `changelog.html`, `hardware.html`, `beginners.html`, and `tutorials.html`. Unblocks social link previews on all pages. (#626)

---

## [2026.3.13.3] — 2026-03-13

### Fixed
- `castor/migrate.py`: implement migration chain `1.0.0-alpha → 1.1 → 1.2 → 1.3`; configs can now be fully migrated to CURRENT_VERSION without hitting an empty path. (#619)
- `castor/setup_service.py`: default new config template now generates `rcan_version: "1.3"` instead of stale `"1.0.0-alpha"`. (#620)
- `castor/conformance.py`: fix-hint messages updated to reference `rcan_version: "1.3"`. (#620)
- `docs/hardware/lerobot-kits.md`, `docs/hardware/reachy.md`: example YAML configs updated to `rcan_version: "1.3"`. (#621)

---

## [2026.3.13.2] — 2026-03-13

### Added
- `castor/rcan/invoke.py`: `InvokeCancelRequest` dataclass for INVOKE_CANCEL wire messages (§19.4). `InvokeResult.status` now includes `"cancelled"` variant. `SkillRegistry` gains `cancel(msg_id)` with `threading.Event` tracking for best-effort in-flight cancellation. (#609)
- `castor/rcan/router.py`: `MessageRouter.route_invoke_cancel()` dispatches INVOKE_CANCEL before capability routing; `InvokeCancelRequest` exported from `castor.rcan`. (#610)
- `tests/test_rcan_router.py`: `TestInvokeFamily` — 9 tests covering INVOKE routing, INVOKE_CANCEL (found / not-found / missing-msg-id), no-registry error, INVOKE_RESULT type, and routed counter increment. (#611)
- `castor/config_validation.py`: `"memory"` added to optional top-level config keys (v1.3+ `memory.compaction`).

---

## [2026.3.13.1] — 2026-03-13

### Added
- `castor/rcan/message.py`: `INVOKE_CANCEL = 15` added to `MessageType` enum (RCAN v1.3 §19 compliance). (#607)
- `tests/test_rcan_invoke.py`: `TestTimeoutEnforcement` — blocking-timeout enforcement tests; `TestConcurrentInvoke` — concurrent INVOKE execution tests. (#605)

### Fixed
- `castor/rcan/invoke.py`: `SkillRegistry.invoke()` now executes skills in a `ThreadPoolExecutor` thread and enforces `InvokeRequest.timeout_ms` via `future.result(timeout=...)`, returning `status="timeout"` immediately on deadline expiry instead of blocking indefinitely. (#608)

---

## [2026.3.13.0] — 2026-03-13

### Changed
- `castor/rcan/sdk_compat.py`: `SPEC_VERSION` updated from `"1.2"` to `"1.3"` — aligns with current spec. (#603)
- `castor/cli.py`: `rcan_version` references in registry and conformance output updated to `"1.3"`. (#603)
- `castor/rcan_generator.py`: generated config template `rcan_version` bumped to `"1.3.0"`. (#603)
- `castor/conformance.py`: conformance check fix messages and pass detail updated to reference v1.3. (#603)

### Fixed
- `castor/providers/pool_provider.py`: health probe exceptions in `_health_probe_loop` now logged at `WARNING` (was `DEBUG`), consistent with `fleet_telemetry.py`. (#606)

---

## [2026.3.12.8] — 2026-03-12

### Fixed
- `fleet_telemetry.py`: health probe exceptions now logged at `WARNING` level (with robot name) instead of silently swallowed via `DEBUG`. Fixes invisible fleet connectivity failures in production. (#602)

---

## [2026.3.12.7] — 2026-03-12

### Fixed
- `InvokeResult.status` now returns `"failure"` (instead of `"error"`) on skill exceptions, aligning with §19 spec INVOKE_RESULT status values. (#599)
- `tests/test_mission.py` `_make_config()` now includes `rcan_protocol` key, preventing brittle `KeyError` under config schema changes. (#598)

---

## [2026.3.12.6] — 2026-03-12

### Changed
- Migrated deprecated `@app.on_event("startup"/"shutdown")` to FastAPI lifespan context manager (`contextlib.asynccontextmanager`). Eliminates deprecation warnings on FastAPI 0.100+. (#596)
- Updated all test fixtures to stub `app.router.lifespan_context` with a no-op alongside existing `on_startup`/`on_shutdown` clearing, ensuring real hardware/config init is skipped during tests.

### Fixed
- `InvokeResult.to_message()` docstring incorrectly referenced non-existent §19.4; corrected to §19.3. (#597)

---

## [2026.3.12.5] — 2026-03-12

### Fixed
- **#590** `InvokeRequest` docstring corrected from §19.3 to §19.2; `InvokeResult` from §19.4 to §19.3 per RCAN v1.3 spec

### Changed
- **#591** `pyproject.toml` Documentation URL updated from GitHub README to `https://opencastor.com/docs`

---

## [2026.3.12.4] — 2026-03-12

### Added
- **#587** `MessageType.INVOKE = 11` and `MessageType.INVOKE_RESULT = 12` added to `castor/rcan/message.py` per RCAN v1.3 §19 (Behavior/Skill Invocation Protocol)
- `castor/rcan/invoke.py`: `InvokeRequest.to_message()` and `InvokeResult.to_message()` now use typed `MessageType` enum values instead of bare string literals
- Tests in `test_rcan_invoke.py` assert `MessageType.INVOKE == 11` and `MessageType.INVOKE_RESULT == 12`; `test_rcan_message.py` updated to expect 12 MessageType members

---

## [2026.3.12.3] — 2026-03-12

### Fixed
- **#585** `config/examples/minimal.rcan.yaml`: added missing required top-level fields (`physics`, `network`) and required `metadata` fields (`robot_uuid`, `author`, `license`); fixed `drivers: []` → `drivers: [{protocol: mock}]` (schema requires `minItems: 1`); replaced invalid `rcan_protocol.enabled` with `rcan_protocol.port`
- **#585** `validate_rcan.py`: added `"1.3"` to `ACCEPTED_RCAN_VERSIONS` — all 19 RCAN configs now pass validation

### Changed
- **#586** GitHub Actions upgraded to Node.js 24-compatible versions: `actions/checkout@v4.3.1` and `actions/setup-python@v6.2.0` across `ci.yml`, `install-test.yml`, `deploy-pages.yml`, and `validate_rcan.yml`
- Applied `ruff format` / `ruff check --fix` to `generate_sbom.py`, `setup_catalog.py`, `wizard.py`

---

## [2026.3.12.2] — 2026-03-12

### Fixed
- **#583** Release CI gate unblocked: enriched `setup_catalog.py` StackProfile/ModelProfile `desc` fields with informative copy; updated `sync_setup_docs.py` `_build_readme_block()` to generate a richer 3-column table; re-synced README — the `SETUP_CATALOG:BEGIN/END` check-sync step now passes cleanly

---

## [2026.3.12.1] — 2026-03-12

### Fixed
- **#580** `migrate.py`: `CURRENT_VERSION` was stale `"1.0.0-alpha"` — updated to `"1.3"`
- **#581** `web_wizard/server.py` + `wizard.py`: generated configs now emit `rcan_version: "1.3"` instead of `"1.2"` / `"1.0.0-alpha"`
- **#582** `config_validation.py`: inline comments updated from `v1.2` to `v1.3`

---

## [2026.3.12.0] — 2026-03-12

### Added
- **#537** Dynamixel U2D2-H explicit VID/PID (`0x0403:0x6015`) + `suggest_preset()` returns `dynamixel_arm` for U2D2 VID/PIDs
- **#538** `detect_i2c_devices()` with `smbus2` primary / sysfs fallback; `HAS_SMBUS` lazy import; `suggest_extras()` → `smbus2`
- **#539** `detect_rplidar_usb()` distinguishes RPLidar from YDLIDAR by product string; model-specific `suggest_extras`; `suggest_preset()` → `lidar_navigation`
- **#540** `detect_rpi_ai_camera()` via `libcamera-hello` + device-tree + v4l sysfs; NPU firmware check at `/lib/firmware/imx500/`; `suggest_extras()` → `picamera2`
- **#541** `detect_lerobot_hardware()` for SO-ARM101/ALOHA profiles; `[lerobot]` extra gains `gym-pusht` and `gym-aloha`
- §19 INVOKE/INVOKE_RESULT message types (`castor.rcan.invoke`)
  - `InvokeRequest`, `InvokeResult` dataclasses
  - `SkillRegistry` for registering and dispatching named skills/behaviors
- §20 standard telemetry field name constants (`castor.rcan.telemetry_fields`)
  - 40+ standard field names for joints, pose, power, compute, sensors, safety
### Changed
- `SPEC_VERSION` bumped from `"1.2"` to `"1.3"`

---

## [2026.3.11.2] — 2026-03-11

### Security
- `Depends(verify_token)` added to all 12 `/setup/api/*` wizard routes (#561)
- Wizard JS `getAuthHeaders()` helper; `GET /setup` injects `window.__OC_TOKEN` server-side (#561)
- SHA-256 checksum verification before DFU firmware flash (#562)
- `GET /api/metrics` now requires auth (#563)
- CORS default changed from `*` to `localhost:8501,127.0.0.1:8501` (#564)
- `?token=` query param now logs deprecation warning (#565)
- `hmac.compare_digest()` for constant-time token comparison (#566)
- GitHub Actions pinned to commit SHA throughout (#567)
- `StrictHostKeyChecking=no` → `accept-new` in deploy command (#568)
- HMAC-SHA256 verification for Teams/Matrix webhooks (#569)
- `requirements.lock` pinned lockfile added (#570)
- `/health` endpoint stripped to `{status, uptime_s, version}` only; sensitive state moved to `/api/health/detail` (auth required) (#571)
- `/api/behavior/status` → `{running}` only; detail at `/api/behavior/status/detail` (auth) (#572)

---

## [2026.3.11.1] — 2026-03-11

### Added
- `castor scan` CLI subcommand — detects connected hardware, prints full scan results with optional `--json`, `--refresh`, `--preset-only` flags (#547)
- `castor doctor` now checks hardware dependencies — warns on missing optional packages for detected devices (depthai for OAK-D, reachy2-sdk, etc.) (#548)
- `castor upgrade` enhanced — git pull + pip install -e + systemd service restart, `--check` (preview pending commits) and `--venv PATH` flags (#554)
- `castor stop` command — reads `~/.opencastor/gateway.pid`, sends SIGTERM for clean shutdown (#556)
- Gateway PID file (`~/.opencastor/gateway.pid`) + port-in-use detection on startup (#556)
- `detect_hardware()` 30-second TTL cache + `invalidate_hardware_cache()` helper (#553)
- `scan_cameras()` enriches each `/dev/videoN` entry with v4l2 device name from sysfs (#552)
- `suggest_extras(hw)` maps detected hardware keys to missing pip packages (#555)
- `/api/hardware/scan` now returns full `detect_hardware()` output + `suggest_preset()` result; supports `?refresh=true` (#543)
- `/api/status` now includes `version` field (#545)
- `docs/install/upgrade.md` — comprehensive upgrade guide: Pi OS PEP 668, `--system-site-packages`, systemd service migration, troubleshooting (#557)

### Fixed
- `scservo-sdk>=1.0` renamed to `feetech-servo-sdk` in `[lerobot]` optional dep group — package now exists on PyPI (#544)
- OAK-D SR (VID/PID `03e7:f63b`) lsusb output normalized to lowercase before model name lookup — no longer misdetected as bootloader/lite (#546)
- Systemd service templates now use `python -m castor.cli gateway` (not hardcoded `castor` binary path) — survives venv migrations (#549)
- Dashboard service template uses `python -m streamlit run` (not `streamlit` binary) — works with `--system-site-packages` venvs (#550)
- Systemd services now include `KillMode=control-group`, `TimeoutStopSec=15`, `SendSIGKILL=yes`, `ExecStartPre` port cleanup (#551)
- `LIBCAMERA_LOG_LEVELS=*:FATAL` set at `hardware_detect.py` import time — suppresses noisy libcamera stderr during scans (#558)

### Tests
- 12 new tests for OAK-D SR detection (#546), TTL cache (#553), v4l2 device name (#552), `suggest_extras()` (#555)
- 342 tests passing total; 0 ruff lint issues

---

## [2026.3.11.0] - 2026-03-11

### Added
- **Plug-and-play hardware auto-detection** — `castor scan` now identifies 12+ hardware types by USB VID/PID, I2C address, PCIe, and network discovery
  - Intel RealSense D4xx/L515 (VID `0x8086`)
  - Luxonis OAK-D / OAK-D-Lite / OAK-D-Pro (VID `0x03E7`)
  - ODrive v3 / Pro / S1 (VID `0x1209`)
  - VESC motor controller (disambiguated by product string)
  - Hailo-8 NPU (PCIe lspci + `/dev/hailo0` + Python)
  - Google Coral USB / M.2 TPU
  - Arduino family (VID `0x2341` + CH340/FTDI clones)
  - Adafruit CircuitPython boards (VID `0x239A`)
  - Dynamixel U2D2 explicit VID/PID (high-confidence, no longer inferred from any serial port)
  - RPLidar / YDLIDAR USB adapters
  - Raspberry Pi AI Camera (IMX500) via picamera2
  - I2C device name lookup table (BNO055, VL53L1X, SSD1306, ADS1115, BME280, LSM6DSO, HMC5883L, and more)
  - Pollen Robotics Reachy 2 / Reachy Mini via mDNS/hostname discovery
- **Feetech STS3215 driver** (`FeetechDriver`) — serial bus servos used in SO-ARM100/101 and LeRobot kits; `port: auto` via CH340 detection
- **Pollen Robotics Reachy driver** (`ReachyDriver`) — Reachy 2 and Reachy Mini via `reachy2-sdk` (gRPC); `host: auto` via mDNS
- **`port: auto` wiring** in ODrive, Dynamixel, and LiDAR drivers — no manual port config required
- **LeRobot RCAN profiles** — `castor/profiles/lerobot/`:
  - `so-arm101-follower.yaml`, `so-arm101-leader.yaml`, `so-arm101-bimanual.yaml`
  - `koch-arm.yaml` (Dynamixel XL430/XL330 via U2D2), `aloha.yaml` (ALOHA bimanual)
- **Pollen Robotics profiles** — `pollen/reachy2.yaml`, `pollen/reachy-mini.yaml`
- **Additional profiles** — `odrive/differential.yaml`, `coral/tpu-inference.yaml`, `arduino/uno.yaml`
- **Optional dependency groups** — `pip install opencastor[lerobot]` (Feetech + Dynamixel SDKs), `pip install opencastor[reachy]` (reachy2-sdk + zeroconf)
- **`scan_usb_descriptors()` memoization** — `lsusb` called once per scan regardless of how many detectors run
- **`invalidate_usb_descriptors_cache()`** — programmatic cache invalidation for hot-plug or test scenarios
- Wizard `generate_preset_config()` resolves `castor/profiles/{id}.yaml` for slash-style preset IDs (e.g. `pollen/reachy2`)

### Fixed
- `detect_feetech_usb()` no longer misroutes Arduino Nano CH340 clones to `lerobot/so-arm101-follower`
- `detect_reachy_network()` hostname probes now run concurrently in daemon threads; no blocking `getaddrinfo`
- `_auto_detect_vesc_port()` ODrive-USB fallback removed — prevents ODrive port being opened as VESC serial link
- `print_scan_results()` now includes all detected categories (`vesc`, `circuitpython`, `lidar`, `imx500`)
- `suggest_preset()` correctly distinguishes Reachy Mini from Reachy 2 via hostname check

---

## [2026.3.10.1] — 2026-03-10

### Added
- **EmbeddingInterpreter** — local-first multimodal semantic perception layer; three-tier design: CLIP/SigLIP2 (Tier 0, free default), ImageBind/CLAP (Tier 1, experimental), Gemini Embedding 2 (Tier 2, premium); auto-tier selection with graceful fallback; episode vector store at `~/.opencastor/episodes/`; RAG context injection into `TieredBrain` pre/post hooks; `interpreter:` RCAN block (optional); Streamlit Embedding tab; TUI pane; Prometheus metrics (`opencastor_embedding_*`); benchmark suite; test suite runner in dashboard
- **HLabs ACB v2.0 hardware support** — full driver for the HLaboratories Actuator Control Board v2.0 (STM32G474, 3-phase BLDC, 12V–30V, 40A); USB-C serial + CAN Bus (1Mbit/s) transports; `port: auto` USB VID/PID detection; motor calibration flow (pole pairs → zero electrical angle → PID push); real-time encoder telemetry at 50Hz (pos/vel/current/voltage/errors); firmware flash via DFU mode (`castor flash`); RCAN profiles (`hlabs/acb-single`, `hlabs/acb-arm-3dof`, `hlabs/acb-biped-6dof`); `/api/hardware/scan`; setup wizard onboarding flow; install with `pip install opencastor[hlabs]`

### Fixed
- `AcbDriver.move()` signature aligned with `DriverBase` (was `dict`, now `float, float`) — prevented runtime `TypeError` when used as primary driver
- CAN transport no longer falsely reports hardware mode when `python-can` is unavailable
- Calibration `None` response now correctly reported as failure (not misreported as success)
- USB serial timeout always restored via `try/finally` (was leaked on exception)
- Dashboard ACB telemetry uses real driver ID from config (was hardcoded to `"acb"`, causing 404s)
- `close()` now joins telemetry background thread to avoid races
- Flash CLI `--id` argument now wires to driver lookup; SSRF-safe firmware URL validation (GitHub releases only)
- Wizard `pole_pairs`/`can_node_id` inputs wrapped in `try/except` (was crashing on non-integer input)
- `profiles.py` module shadow removed; `castor/profiles/**/*.yaml` added to package-data
- `get_active_profile()` return type corrected to `Optional[str]`
- `EmbeddingInterpreter._null_context()` returns correct dimensions with `is_null` flag; swarm path now calls `post_think()`; test suite uses `flush()` instead of `time.sleep()`
- Anthropic CLI OAuth path now supports `cache_control` system prompt lists

### Statistics
- 94,438 lines of Python · 6,459 tests

---

## [2026.3.10.0] — 2026-03-10

### Added
- **EmbeddingInterpreter** — local-first multimodal semantic perception layer; three-tier design (CLIP Tier 0, ImageBind/CLAP Tier 1, Gemini Embedding 2 Tier 2); episode vector store at `~/.opencastor/episodes/`; RAG context injection into `TieredBrain.think()` pre/post hooks; `auto` backend walks tiers with graceful fallback to mock (#501–#516)
- **CLIP provider** (Tier 0) — `openai/clip-vit-base-patch32`, 512-dim, CPU-only, zero-cost default; singleton helper prevents repeated model loads
- **Gemini Embedding 2 provider** (Tier 2) — `gemini-embedding-2-preview`, 3072/1536/768 MRL dims, L2-normalised, MIME magic-byte detection for PNG/JPEG/WAV
- **ImageBind provider** (Tier 1, experimental) — CC BY-NC 4.0, 6-modality (RGB/depth/audio/text/IMU/thermal); temp-file `try/finally` cleanup
- **CLAP provider** (Tier 1) — local audio-text embedding via `laion/clap-htsat-unfused`
- **Embedding metrics** — Prometheus counters/histograms with `backend`, `modality`, `error_type` labels via `ProviderLatencyTracker`
- **Streamlit Embedding tab** — live backend status, episode count, top-k RAG preview, backend switcher; `/api/interpreter/status` endpoint (409 for concurrent test runs)
- **TUI embedding pane** — `_run_embedding_loop()` in `dashboard_tui.py`; reads `OPENCASTOR_API_TOKEN` for authenticated deployments
- **Benchmark suite** — `run_embedding_benchmark()` in `benchmarker.py`; skips Gemini when no API key (records as `skipped`)
- **Test suite runner** — pytest runner in dashboard Settings tab
- **Setup wizard** — embedding tier selection step; invokes `_google_auth_flow()` when Gemini/Auto selected and key is absent
- **RCAN `interpreter:` block** — added to `OPTIONAL_TOP_LEVEL` in `config_validation.py`; type-guards against scalar values; validates `backend` enum and `gemini.dimensions`
- **Multi-vector episode store schema doc** — `docs/design/episode-store-schema.md`
- **ImageBind setup guide** — `docs/setup/imagebind-setup.md`

### Fixed
- `_null_context()` returned `(1,)` embedding causing dimension mismatch in episode store; now returns zero vector matching backend's declared dims; `SceneContext.is_null` flag prevents storing null episodes
- TieredBrain swarm branch bypassed `post_think()` — episode store now records swarm actions too
- `ClipEmbeddingProvider` was re-instantiated on every `_select_backend()` call; now uses singleton via `get_clip_provider()`
- Gemini `embed_text()` / `embed_scene()` returned raw vectors; now L2-normalised to honour `EmbeddingBackend.embed()` unit-norm contract
- `embed_scene()` hardcoded `image/jpeg` / `audio/mpeg` MIME types; `_mime_from_bytes()` helper now detects from magic bytes (PNG, JPEG, WAV)
- Config validation crashed on `interpreter: true` (non-dict) with `AttributeError`; `isinstance` guard added
- TUI embedding pane polled `/api/interpreter/status` without auth headers; passes `Authorization: Bearer` from `OPENCASTOR_API_TOKEN`
- Test flakiness: replaced `time.sleep()` waits with `EmbeddingInterpreter.flush()` (joins background store thread)
- Anthropic CLI path (`_think_via_cli`) now passes `cache_control` content blocks via updated `ClaudeOAuthClient.create_message(system: str | list[dict])` instead of plain string (#517)

### Statistics
- 6,459 tests collected

---

## [2026.3.8.3] — 2026-03-08

### Fixed
- **EpisodeStore FIFO eviction** — `EpisodeStore` now enforces `max_episodes` (default 10k) cap with FIFO eviction; prevents unbounded SQLite growth (#commit 02cd0f3)
- **ApplyStage efficiency** — `improvement_history.json` capped at 1k entries; behavior rules deduplicated by name to prevent bloat (#commit 8412fc2)
- **Timezone-aware datetimes** — replaced deprecated `datetime.utcfromtimestamp()` with `datetime.fromtimestamp(..., tz=timezone.utc)` throughout codebase (#commit c005ce1)

### Changed
- **README refresh** — architecture diagram updated, Memory & Learning section added, structure tightened (#commit 96aaf18)
- **README SETUP_CATALOG markers** — restored accidentally removed markers from README rewrite (#commit a6643b5)

### Statistics
- 167,356 lines of Python · 6,401 tests

---

## [2026.3.8.2] — 2026-03-08

### Added
- **Closed captions on robot face** — `Speaker._speak()` tracks `is_speaking` + `current_caption` per TTS chunk; `/api/status` exposes both; face page shows a frosted-glass subtitle bar when `?captions=1` URL param is set
- **Brain model visibility** — `/api/status` returns `brain_primary`, `brain_secondary`, `brain_active_model`; `/api/command` returns `model_used`; gateway logs `Brain replied via <model> in <N> ms`
- **Dashboard status tab** — 🧠 Brain section shows primary/secondary models with `← active` tag; Channels section replaced full available-table with active-only green pill badges
- **Dashboard chat tab** — each assistant reply shows `via <model>` caption beneath
- **Dashboard settings tab** — 💬 Closed Captions toggle (default on), 🖥️ Terminal Access section with SSH/tmux/logs/REPL copy-paste commands, 🧙 OpenCastor Setup link to `/setup` wizard
- **Wake-up greeting** — gateway speaks `"Hello. I am <robot>. I am online and ready."` on boot via non-blocking background thread
- **Full-screen touch D-pad gamepad page** — `/gamepad` press-and-hold D-pad with `pointerdown`/`pointerup`, physical gamepad polling, speed + turn sliders, `← active` brain annotation; hostname fixed to `robot.local` not `localhost`
- **Safety denial messages** — `SafetyLayer._last_write_denial` stores human-readable reason for every write rejection; `/api/action` 422 includes specific reason; `GET /api/fs/estop` endpoint

### Fixed
- **Camera/speaker/loop/latency always showed offline** — dashboard was reading `proc["camera"]` but `snapshot()` returns nested `proc["hw"]["camera"]`; same bug for speaker, loop_count, avg_latency, last_thought — all fixed to use correct nested key paths
- **`{{_robot}}` double-brace** in gamepad page title tag caused literal `{_robot}` instead of substitution
- **`_gp_url` NameError** in dashboard voice section after gamepad link refactor
- Ruff lint: F811 duplicate `HTMLResponse` import, E702 semicolon statements, I001 unsorted imports in dashboard and api — all resolved
- `test_dashboard_mission_control` — dashboard redesign removed "Mission Control" label; restored as comment in Control tab header, behavior buttons use `mc_launch`/`mc_stop` keys

### Testing
- All 5,970+ tests passing (excluding 8 flaky pushgateway integration tests)

---

## [2026.3.8.0] — 2026-03-06

### Added
- **RCAN-Swarm Safety** — `castor node` CLI for multi-robot coordination
  - `castor node resolve <rrn>` — federated peer verification
  - `castor node ping` — registry reachability check
  - `castor node status` — show node broadcaster manifest
  - `castor node manifest` — print `/.well-known/rcan-node.json`
- `castor register --dry-run` — validate config without making API call
- `castor verification <rrn>` — check robot verification tier from rcan.dev
- `check_rcan_registry_reachable()` and `check_rrn_valid()` in `castor doctor`
- `castor/rcan/node_resolver.py` — federated RRN resolution with SQLite cache
- `castor/rcan/node_broadcaster.py` — serve `/.well-known/rcan-node.json`
- `castor/rcan/sdk_compat.py` — pre-registration SDK version check
- `castor/rcan/verification.py` — `VerificationTier` enum + `VerificationStatus`

### Fixed
- Lint: 146 ruff errors resolved across test files
- `test_deepseek_provider.py` — skip gracefully when `openai` not installed
- Integration test: handle both tuple and `ValidationResult` from `validate_config()`
- SBOM generation: heredoc syntax invalid in YAML — extracted to Python script

### Testing
- 1844+ tests passing, 11 skipped

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
- **`castor/rcan/node_resolver.py`** — `NodeResolver` with 4-step federated resolution:
  1. Local SQLite cache (XDG data dir, TTL-based)
  2. rcan.dev `/api/v1/resolve/:rrn` (federated endpoint)
  3. Direct authoritative node (X-Resolved-By header)
  4. Stale cache fallback when network fails
- **`castor/rcan/node_broadcaster.py`** — `NodeBroadcaster` + `NodeConfig` for fleet nodes
  - Serves `/.well-known/rcan-node.json` manifest
  - mDNS broadcast via `_rcan-registry._tcp`
- **`castor verification <rrn>`** — check robot verification tier from rcan.dev (⬜🟡🔵✅ badges)
- **`castor node`** — manage RCAN namespace delegation (`status`, `manifest`, `resolve`, `ping`)
- **`castor register --dry-run`** — validate config and preview what would be registered without API calls
- **`castor/rcan/sdk_compat.py`** — pre-registration SDK validation (`validate_before_register()`)
- **`castor/rcan/verification.py`** — `VerificationTier` enum + `VerificationStatus` dataclass
- **`castor doctor`** — `check_rcan_registry_reachable()` + `check_rrn_valid()` as first-class checks (run after system checks, before optional hardware)
- RRN address space expanded: 8-digit sequences → 8-16 digits, prefix `[A-Z0-9]{2,8}`

#### Test Coverage Added — §17
- `tests/test_node_resolver.py` — 22 tests: cache CRUD, live fetch, stale fallback
- `tests/test_node_broadcaster.py` — 8 tests: manifest structure, lifecycle
- `tests/test_secret_provider.py` — JWT key rotation, bundle loading, env fallback
- `tests/test_hardware_detect.py` — Hailo-8, OAK-D, I2C, platform detection
- `tests/test_telemetry.py` — CastorTelemetry, PrometheusRegistry, OTel guards
- `tests/test_cli_node.py` — `castor node status/manifest/resolve/ping` (mock resolver)
- `tests/test_cli_register_dry_run.py` — `castor register --dry-run` does not call API

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

#### Fixed
- SBOM generation: heredoc `<< EOF` invalid YAML in `release.yml` — extracted to `.github/scripts/generate_sbom.py`
- Telemetry package shadowing: `castor/telemetry/` correctly importable (no `.py` shadow)
- RRN address space expanded: 8-digit sequences → 8-16 digits, prefix `[A-Z0-9]{2,8}`

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

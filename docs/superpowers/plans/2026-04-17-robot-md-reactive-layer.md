# Plan: `robot.md` Reactive Layer — Feasibility + New Repository

## Context

OpenCastor currently describes a robot via a machine-readable YAML config (e.g. `bob.rcan.yaml`) plus a human-oriented `CLAUDE.md` that orients Claude Code to the project. Bringing a robot online today requires the planner to cross-reference several files (YAML config, P66 manifest, harness, driver metadata) to understand what the robot *is* and what it *can do*.

The goal of this initiative is a **single self-describing file — `robot.md` — that serves as the reactive layer** for a Claude Code instance running on the robot itself. `robot.md` combines:

- **Machine-readable RCAN values** (YAML frontmatter) — RRN, RURI, drivers, DoF, safety limits, skills — everything the RRF registry and tooling need.
- **Human/LLM-readable prose body** — capabilities narrative, DoF descriptions, safety rationale, task-routing guidance — the planner (Claude Opus 4.7) reads this at session start like it reads `CLAUDE.md` today.

One file. The robot "knows itself" — its hardware, its DoF, its skills, its safety gates — and the planner handles all the heavy lifting on top of that declaration.

**Feasibility: confirmed.** The pattern is a direct extrapolation of two existing OpenCastor assets:
1. `CLAUDE.md` at repo root (read at session start, declares architecture + conventions)
2. `bob.rcan.yaml` / `examples/bob-reference.rcan.yaml` (machine-readable robot declaration, RCAN v2.1/v2.2)

`robot.md` = YAML frontmatter (bob.rcan.yaml) + prose body (CLAUDE.md style), in a single file, in a dedicated lightweight repo.

### RCAN 3.0 gap (file separately)

Per user direction: **we target post-3.0 RCAN**. Grep of `/home/user/OpenCastor` shows the highest version present is **RCAN v2.1** in `castor/compliance.py` (`SPEC_VERSION = "2.1"`, `ACCEPTED_RCAN_VERSIONS = ("2.1", "2.1.0")`), with v2.2 fields only drafted in `examples/bob-reference.rcan.yaml` (post-quantum `ml-dsa-65` signing, ISO 42001, federation peer RRNs, dual-brain). **There is no RCAN 3.0 implementation in OpenCastor.**

**Action (out of scope for this plan):** file separate GitHub issues on `craigm26/opencastor` to track:
- Bump `compliance.py` SPEC_VERSION / `ACCEPTED_RCAN_VERSIONS` to 2.2.
- Track RCAN 3.0 drafting (check https://rcan.dev/spec/ for current status).
- Add `config/examples/bob-v22.rcan.yaml` as an officially accepted (not example-only) config.

For this initiative, the robot.md schema will be **v2.2 baseline with a `rcan_version` field** that can advance to 3.0 when the spec ships. Every field is annotated in the schema with its introducing spec section.

## Scope of the new GitHub repository

**Proposed name:** `robot-md` (or `opencastor-robot-md`) under `craigm26/`.

**Contents (four deliverables, all in one repo):**

| # | Path | Purpose |
|---|------|---------|
| 1 | `template/robot.md` | Canonical template with YAML frontmatter + prose sections. Filled in for Bob as the worked example. |
| 2 | `schema/robot.schema.json` | JSON Schema for the frontmatter block. Versioned; referenced from `rcan_version` field. |
| 3 | `cli/robot-md` (Python) | Validator CLI: `robot-md validate robot.md` (schema + RCAN conformance), `robot-md lint`, `robot-md render` (strip prose, emit pure YAML for runtime tooling). |
| 4 | `cli/robot-md register` | RRF registration helper: parses frontmatter, POSTs to the RRF endpoint (default `https://robot-registry-foundation.pages.dev`), writes the assigned RRN back into the file. |
| 5 | `hooks/session-start.sh` + `hooks/README.md` | Claude Code SessionStart hook that users drop into `.claude/settings.json`. Reads `robot.md`, feeds it as session context. |
| 6 | `examples/bob.robot.md` | Bob's full file as the reference implementation (mirrors `examples/bob-reference.rcan.yaml` from OpenCastor). |
| 7 | `README.md` | Quickstart: "Your robot in one file." |

**Explicitly out of scope for this repo:** runtime driver code, FastAPI gateway, safety enforcement. This repo is a **spec + tooling** repo, not a runtime. OpenCastor's gateway remains the runtime; `robot.md` is a data format + hook.

## robot.md structure (the format itself)

```markdown
---
# ========================
# YAML Frontmatter — RCAN machine-readable block
# ========================
rcan_version: "2.2"              # bumped to "3.0" when spec ships
schema: https://github.com/craigm26/robot-md/schema/v1/robot.schema.json

metadata:
  robot_name: bob
  rrn: RRN-000000000003          # RRF-assigned; empty until `robot-md register`
  rrn_uri: rrn://craigm26/robot/opencastor-rpi5-hailo-soarm101/bob-001
  ruri: rcan://robot.local:8001/bob
  manufacturer: craigm26
  model: opencastor-rpi5-hailo-soarm101
  version: 2026.4.17.0
  license: Apache-2.0
  public_key_path: ~/.opencastor/pq_signing.pub

physics:
  type: arm+camera                # arm | differential | ackermann | arm+camera | ...
  dof: 6                          # SO-ARM101 joints
  kinematics:                     # per-joint declarations (RCAN §X kinematics)
    - id: shoulder_pan
      axis: z
      limits_deg: [-180, 180]
      length_mm: 60
    # ... 5 more joints

drivers:
  - id: arm_servos
    protocol: feetech
    port: /dev/ttyUSB0
    baud_rate: 1000000
    model: STS3215
    count: 6
  - id: camera
    protocol: depthai
    model: OAK-D

brain:
  planning:                       # the "heavy lifting" planner
    provider: anthropic
    model: claude-opus-4-7
    confidence_gate: 0.60
  reactive:                       # optional low-latency layer
    provider: local
    model: openvla-7b
  task_routing:                   # RCAN §16 categories → which brain
    sensor_poll:  fast_only
    safety:       planner_always
    navigation:   periodic
    reasoning:    planner
    vision:       planner
    code:         planner

capabilities:                     # RCAN §19 skills this robot exposes
  - nav.go_to
  - arm.pick
  - arm.place
  - vision.describe
  - status.report

safety:                           # P66 manifest (summary; full rules in manifest block)
  p66_enabled: true
  loa_enforcement: true
  max_linear_velocity_ms: 0.5
  max_joint_velocity_dps: 180
  payload_kg: 0.5
  estop:
    hardware: false
    software: true
    response_ms: 100
  workspace_bounds_m: [2.0, 2.0, 1.5]
  hitl_gates:
    - scope: destructive
      require_auth: true

network:
  mdns: true
  rrf_endpoint: https://robot-registry-foundation.pages.dev
  port: 8001
  signing_alg: ml-dsa-65          # RCAN v2.2 post-quantum
  transports: [http, mqtt]

compliance:
  iso_42001: { self_assessed: true, level: 5 }
  eu_ai_act: { audit_retention_days: 3650 }
---

# Bob — Robot Reactive Layer

> This file is Bob's brain. The planner (Claude Opus 4.7) reads it at session start.
> Machine values live in the frontmatter above. Human/planner context lives below.

## Identity

Bob is a Raspberry Pi 5 + Hailo-8 NPU workstation robot with a 6-DOF SO-ARM101
follower arm and a Luxonis OAK-D stereo camera. RRN `RRN-000000000003`.

## What Bob Can Do

- **Navigate**: Bob is stationary; "navigate" means arm-based reach. The planner
  must translate `nav.go_to(x, y, z)` into joint-space commands via inverse
  kinematics (DoF=6, see frontmatter `physics.kinematics`).
- **Manipulate**: pick / place payloads up to **0.5 kg**. Gripper is servo 6.
- **See**: OAK-D provides RGB + depth. `vision.describe` returns a caption +
  depth-annotated bounding boxes.
- **Report status**: health check of all 6 servos + camera + battery.

## Safety Gates You Must Respect

1. **All arm motion** routes through `SafetyLayer`; bounds enforced per-joint.
2. **Destructive actions** (e.g. `arm.place` onto an unknown surface) require
   human-in-the-loop approval. Do not bypass.
3. **SENSOR_POLL** tasks never escalate to the planner (token budget guard).
4. **SAFETY** tasks always use the planner, never the reactive brain.

## Task Routing Narrative

The planner handles: reasoning, safety, vision captioning, code generation,
navigation planning. The reactive brain (if available) handles: low-latency
sensor polling, servo watchdog, immediate-stop triggers.

## Extension Points

- New skills: register in the OpenCastor `SkillRegistry` and add the name to
  `capabilities` in the frontmatter.
- New drivers: add to `drivers[]` in frontmatter; the gateway reads this.
- Task-routing overrides: edit `brain.task_routing`; see RCAN §16.

## References

- RCAN spec: https://rcan.dev/spec/
- Robot Registry Foundation: https://robotregistryfoundation.org/
- OpenCastor repo: https://github.com/craigm26/opencastor
```

## Why a single file works (feasibility argument)

1. **Claude Code already consumes markdown as reactive context** — `CLAUDE.md` at `/home/user/OpenCastor/CLAUDE.md` does exactly this today: declares architecture, abstractions, file paths, conventions. The planner reads it at session start.
2. **YAML frontmatter is a standard pattern** (Jekyll, Astro, MDX, Hugo) — every markdown parser and every YAML parser handles this cleanly. `python-frontmatter` (PyPI) is a one-line read.
3. **The data set is bounded** — the comprehensive inventory in our exploration shows ~10 top-level blocks (metadata, physics, drivers, brain, capabilities, safety, network, compliance, etc.). All fit in one scannable file.
4. **RRF registry lookup works via URI** — consumers that `GET` the file over HTTPS parse frontmatter for machine values and can optionally render the prose body for LLM peers doing federation discovery.

## Critical files / references (for implementation phase)

From OpenCastor (source of truth for field names and semantics):

| File | Why it matters |
|------|----------------|
| `castor/rcan/registry.py:171-210` | `RegistryMessage`, RRN validation, metadata block shape |
| `castor/safety/p66_manifest.py:41-343` | 21 safety rules → frontmatter `safety` block |
| `castor/rcan/invoke.py:26-309` | `SkillRegistry` / capability names format |
| `castor/providers/task_router.py:11-79` | `TaskCategory` enum → `brain.task_routing` keys |
| `castor/drivers/base.py:27-167` | `DriverBase` → driver block schema |
| `examples/bob-reference.rcan.yaml:1-118` | Closest existing sibling; we mirror its structure |
| `config/presets/so_arm101_leader.rcan.yaml:40-190` | DoF / kinematics block template |
| `castor/harness/default_harness.yaml:1-167` | Harness values to optionally embed as `brain.harness_ref` |
| `CLAUDE.md` (repo root) | Prose-section style / tone reference |

Reused: `python-frontmatter` (parse), `jsonschema` (validate), `httpx` (RRF POST), `rich` (CLI UX). No new libraries invented.

## Verification plan (end-to-end test)

1. **Schema round-trip**: `robot-md validate examples/bob.robot.md` returns 0 and prints a capability summary.
2. **Render/extract**: `robot-md render examples/bob.robot.md > bob.rcan.yaml`; the output passes `castor validate-config` (OpenCastor's existing validator) cleanly.
3. **RRF registration dry-run**: `robot-md register --dry-run` POSTs the expected payload to a local mock RRF endpoint; returns a stub RRN and rewrites the file in place.
4. **SessionStart hook**: install the hook in a scratch `.claude/settings.json`, launch `claude` in a directory containing `robot.md`, confirm the planner acknowledges the robot's name, DoF, and capabilities in its first turn.
5. **Planner integration smoke**: on the Bob robot itself, run `claude` with the hook installed, ask "what can you do right now?" — verify the response cites the frontmatter capabilities and respects the HiTL gate (refuses `arm.place` without approval prompt).
6. **Negative test**: delete a required field (`metadata.rrn_uri`); validator exits non-zero with a pointer to the missing field and its RCAN section.

## Out of scope / separate issues to file on OpenCastor

1. **RCAN 3.0 tracking** — spec version not present in repo; current max is v2.1 (`compliance.py`).
2. **Bump `compliance.py` to v2.2** — examples/bob-reference.rcan.yaml uses v2.2 fields that aren't in `ACCEPTED_RCAN_VERSIONS`.
3. **Gateway support for `robot.md` as drop-in config** — future work; for now, users can `robot-md render` to YAML and feed that to the gateway.

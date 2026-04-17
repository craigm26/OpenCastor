# `robot-md` — Repository Design Spec

> **⚠️ Historical. Migration landed 2026-04-17 evening.** The repo URL references throughout this doc point at `craigm26/robot-md`, which has been migrated to `continuonai/robot-md` (public spec/CLI) + `continuonai/robot-md-private` (outreach proposal). The "private at creation, flip to public later" visibility note in this doc is superseded by [`2026-04-17-robot-md-strategy.md`](./2026-04-17-robot-md-strategy.md) — read that for current truth. Body below preserved as-shipped for history.

**Status:** Draft for approval
**Date:** 2026-04-17
**Author:** craigm26
**Supersedes (in part):** `docs/superpowers/plans/2026-04-17-robot-md-reactive-layer.md` — that plan was written when OpenCastor was partially v2.2; this spec baselines on RCAN 3.0 now that the ecosystem is aligned.

---

## One-liner

**`ROBOT.md` is to a robot what `CLAUDE.md` is to a codebase.** A single self-describing file — machine-readable YAML frontmatter plus human/LLM-readable prose — that lets any Claude surface (Code, Desktop, Mobile) understand what a robot is, what it can do, and the safety envelope it operates under.

## Why this belongs in its own repo

- **Spec + tooling, not runtime.** OpenCastor is the runtime. RCAN is the wire protocol. RRF is the registry. `robot-md` is the declaration format that glues these into a thing Claude can read in one shot. Keeping it in a dedicated repo lets it evolve on its own cadence and (critically) makes it easy for Anthropic to adopt without pulling in OpenCastor dependencies.
- **Vendor-neutral surface.** The repo lives at `craigm26/robot-md` today; if Anthropic adopts the convention, it can live at `Anthropic/robot-md` tomorrow with a simple transfer. If the community fragments, `continuonai/robot-md` is also viable. Naming that doesn't lock in a sponsor is deliberate.
- **Domain is already live.** `robotmd.dev` (acquired 2026-04-17 via Cloudflare) will host the spec site + demo. The repo is what the site points at.

## Naming (locked)

| Thing | Name | Rationale |
|---|---|---|
| File | `ROBOT.md` | Uppercase, parallel to `CLAUDE.md`. Same cognitive load, same convention. |
| Repo | `robot-md` (kebab) | Matches `rcan-py`, `rcan-ts`, `rcan-spec` convention. |
| Domain | `robotmd.dev` | No hyphen — standard TLD convention. |
| PyPI package | `robot-md` | Matches repo. |
| CLI | `robot-md` (kebab command) | One verb, clear. |

## Repository layout (v0.1 target)

```
robot-md/
├── README.md                          # Elevator pitch + 60-second quickstart
├── LICENSE                            # Apache 2.0
├── CHANGELOG.md
├── CODE_OF_CONDUCT.md
├── CONTRIBUTING.md
│
├── ROBOT.md                           # ROBOT.md describing THIS repo (meta/dogfood)
│
├── spec/
│   ├── robot-md-v1.md                 # The format spec — authoritative doc
│   └── rationale.md                   # Why this format, design decisions
│
├── schema/
│   └── v1/
│       └── robot.schema.json          # JSON Schema for the YAML frontmatter
│
├── examples/
│   ├── bob.ROBOT.md                   # Full worked example (OpenCastor Bob)
│   ├── minimal.ROBOT.md               # Smallest valid ROBOT.md
│   ├── so-arm101.ROBOT.md             # SO-ARM101 preset (no brain hardware)
│   └── turtlebot4.ROBOT.md            # Wheeled robot example
│
├── cli/                               # Python package (installable as `robot-md`)
│   ├── pyproject.toml
│   ├── README.md
│   ├── src/
│   │   └── robot_md/
│   │       ├── __init__.py
│   │       ├── __main__.py            # `python -m robot_md`
│   │       ├── parser.py              # YAML frontmatter + markdown split
│   │       ├── validate.py            # `robot-md validate`
│   │       ├── render.py              # `robot-md render` → YAML for runtime
│   │       ├── register.py            # `robot-md register` → RRF POST
│   │       └── context.py             # `robot-md context` → text for hook output
│   └── tests/
│       ├── test_parser.py
│       ├── test_validate.py
│       ├── test_render.py
│       ├── test_register.py
│       └── fixtures/
│           ├── valid/*.ROBOT.md
│           └── invalid/*.ROBOT.md
│
├── integrations/
│   ├── claude-code/
│   │   ├── session-start.sh           # SessionStart hook (bash)
│   │   ├── settings.template.json     # Drop-in for .claude/settings.json
│   │   └── README.md                  # Install instructions
│   ├── claude-desktop/
│   │   └── README.md                  # MCP server approach (code lands in v0.2)
│   └── claude-mobile/
│       └── README.md                  # URL/web-bridge approach (code lands in v0.2)
│
├── proposal/
│   └── anthropic-adoption-proposal.md # The pitch deck in markdown
│
└── .github/
    ├── workflows/
    │   ├── ci.yml                     # ruff + pytest + schema self-validation
    │   └── release.yml                # tag → PyPI publish
    ├── ISSUE_TEMPLATE/
    │   ├── bug_report.yml
    │   └── feature_request.yml
    └── PULL_REQUEST_TEMPLATE.md
```

## Scope — what ships when

### v0.1 (TODAY)

1. ✅ `spec/robot-md-v1.md` — authoritative format spec
2. ✅ `schema/v1/robot.schema.json` — JSON Schema for frontmatter
3. ✅ `examples/bob.ROBOT.md` + `minimal.ROBOT.md` + `so-arm101.ROBOT.md` + `turtlebot4.ROBOT.md`
4. ✅ `cli/` — `robot-md validate | render | context` subcommands (NOT `register` yet)
5. ✅ `integrations/claude-code/session-start.sh` + README — working hook
6. ✅ `integrations/claude-desktop/README.md` + `claude-mobile/README.md` — documented approaches, no code
7. ✅ `README.md` — 60-second pitch + install + try-it
8. ✅ `proposal/anthropic-adoption-proposal.md` — draft Anthropic pitch
9. ✅ CI: lint + test + schema self-validation
10. ✅ `ROBOT.md` at the repo root dogfooding the format (robot = "this repo")

### v0.2 (this week)

- `robot-md register` — RRF integration (posts to `https://robot-registry-foundation.pages.dev`)
- `integrations/claude-desktop/mcp-server/` — working MCP server that exposes ROBOT.md as MCP resources
- `integrations/claude-mobile/bridge/` — optional Cloudflare Worker that exposes ROBOT.md at a URL for mobile Claude
- TypeScript port (`@opencastor/robot-md` on npm) — mirrors Python CLI

### v1.0 (after Anthropic dialogue)

- Formal spec freeze
- Conformance test suite (pass/fail ROBOT.md corpus)
- Versioned schema migration path (v1 → v2 when breaking changes land)
- Multi-language bindings (Rust? Go?) if demand

## The `ROBOT.md` format (v1 baseline)

YAML frontmatter delimited by `---`, followed by markdown prose.

### Frontmatter — required blocks

```yaml
---
rcan_version: "3.0"                    # Baseline on RCAN 3.0 — matches current OpenCastor
schema: https://robotmd.dev/schema/v1/robot.schema.json

metadata:
  robot_name: bob
  rrn: RRN-000000000001                # RRF-assigned; empty string until registered
  rrn_uri: rrn://craigm26/robot/opencastor-rpi5-hailo-soarm101/bob-001
  ruri: rcan://robot.local:8001/bob
  manufacturer: craigm26
  model: opencastor-rpi5-hailo-soarm101
  version: 2026.4.17.0
  license: Apache-2.0

physics:
  type: arm+camera                     # arm | wheeled | tracked | legged | arm+camera | ...
  dof: 6
  kinematics:                          # per-joint (RCAN §X)
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

brain:
  planning:
    provider: anthropic
    model: claude-opus-4-7
    confidence_gate: 0.60
  task_routing:
    sensor_poll: fast_only
    safety: planner_always

capabilities:
  - arm.pick
  - arm.place
  - vision.describe
  - status.report

safety:
  p66_enabled: true
  loa_enforcement: true
  max_joint_velocity_dps: 180
  payload_kg: 0.5
  estop:
    hardware: false
    software: true
    response_ms: 100
  hitl_gates:
    - scope: destructive
      require_auth: true

network:
  rrf_endpoint: https://robotregistryfoundation.org
  port: 8001
  signing_alg: pqc-hybrid-v1           # RCAN 3.0 L2+
  transports: [http, mqtt]

compliance:
  fria_ref: null                       # populate via `castor fria generate` (RCAN 3.0 §22, §27)
  iso_42001: { self_assessed: true, level: 5 }
  eu_ai_act: { audit_retention_days: 3650 }
---
```

### Frontmatter — optional blocks

- `federation:` — peer RRNs for multi-robot fleets
- `extensions:` — vendor-specific hooks (namespaced: `x-opencastor.harness`, `x-boston-dynamics.spot`)

### Markdown body — required sections

1. **`# <Robot Name>`** — H1 with the robot's display name
2. **`## Identity`** — 1-2 paragraphs: what is this robot, where does it live, who owns it
3. **`## What <Name> Can Do`** — capabilities narrative (parallels the `capabilities:` frontmatter block but in prose)
4. **`## Safety Gates`** — which actions require human-in-the-loop, which bounds enforced, how E-stop works
5. **`## Task Routing`** — how the planner delegates (parallels `brain.task_routing`)

### Markdown body — optional sections

- **`## Extension Points`** — how to add new skills/drivers
- **`## References`** — links to RCAN spec, RRF entry, runtime docs, physical robot URLs

### Minimum viable ROBOT.md

```yaml
---
rcan_version: "3.0"
metadata:
  robot_name: my-robot
physics:
  type: wheeled
  dof: 2
capabilities:
  - nav.move
safety:
  estop: { software: true, response_ms: 200 }
---

# my-robot

## Identity
Minimum robot for testing.

## What my-robot Can Do
- Drive forward/backward/turn.

## Safety Gates
- Stops on software E-stop within 200ms.
```

That's the floor. Everything else is progressive disclosure.

## Integration surfaces

### Claude Code (v0.1 — shipping today)

```
# install
curl -fsSL https://robotmd.dev/install | bash

# or clone + link
git clone https://github.com/craigm26/robot-md.git ~/robot-md
ln -s ~/robot-md/integrations/claude-code/session-start.sh ~/.claude/hooks/robot-md.sh

# add to .claude/settings.json:
{
  "hooks": {
    "SessionStart": [{ "command": "~/.claude/hooks/robot-md.sh" }]
  }
}
```

The hook reads `./ROBOT.md` in the session's cwd, runs `robot-md context ROBOT.md` (emits a clean text block), and feeds it to the session. Claude now has the full robot context at session start, exactly like `CLAUDE.md` gives codebase context.

### Claude Desktop (v0.1 documented, v0.2 shipping)

Approach: an MCP server named `robot-md-mcp` that:
- Watches a configured ROBOT.md path (or URL)
- Exposes MCP **resources** for the frontmatter blocks (capabilities, safety, drivers)
- Exposes MCP **tools** for robot-md CLI verbs (validate, render, context)
- Optionally exposes MCP tools that dispatch to the robot's RCAN gateway (invoke skill, query status) — this is the bridge that makes Claude Desktop "talk to the robot"

v0.1 ships a README with "coming soon" + the MCP server spec so early adopters can implement it themselves.

### Claude Mobile / iOS (v0.1 documented, v0.2 shipping)

Constraint: Claude Mobile has no file-system access, no tool calls to local code, no MCP. It has: the chat, URL fetching, and Artifacts.

Approach: **URL-based ROBOT.md delivery.**
- Operator hosts their robot's ROBOT.md at a stable public URL (e.g., `https://robotmd.dev/r/bob` via a Cloudflare Worker, or their own domain)
- User in Claude Mobile pastes the URL: "Here is my robot: https://robotmd.dev/r/bob — what can I ask you to do?"
- Claude fetches the URL, parses frontmatter + body, and reasons over the robot
- To actually invoke skills, the ROBOT.md's `network.rrf_endpoint` provides a discovery path; Claude can relay commands through a thin `/invoke` HTTP bridge

v0.1 ships a README describing this pattern + a sample Cloudflare Worker template. v0.2 ships the worker itself hosted at `robotmd.dev/r/<rrn>`.

## CLI surface (v0.1)

```
robot-md validate PATH        # → exit 0 if ROBOT.md is schema-valid and RCAN-conformant
robot-md render PATH          # → strip prose, emit pure YAML to stdout (runtime tool feeds OpenCastor)
robot-md context PATH         # → emit clean text block suitable for Claude session context
robot-md --version            # → 0.1.0
robot-md --help
```

Exit codes:
- 0: OK
- 1: File not found / parse error
- 2: Schema violation (JSON Schema)
- 3: RCAN conformance violation (e.g., `signing_alg: ed25519` under `rcan_version: 3.0`)
- 4: Missing required markdown section

`robot-md register` is deliberately deferred to v0.2 to keep v0.1 dependency-light (no httpx required for the core validator).

## Dependencies

| Package | Why | Version constraint |
|---|---|---|
| `python-frontmatter` | Parse YAML frontmatter + markdown body | `>=1.0` |
| `jsonschema` | Validate against `robot.schema.json` | `>=4.0` |
| `pyyaml` | YAML parsing (already pulled by frontmatter) | `>=6.0` |
| `rich` | CLI output (colored validation results) | `>=13.0` |
| `typer` | CLI framework | `>=0.9` |

**Not** added in v0.1: `httpx` (needed for v0.2's register), `mcp` (v0.2), `pytest-asyncio` (not needed — CLI is sync).

## Testing strategy

- **Unit**: each module (`parser`, `validate`, `render`, `context`) has focused tests with fixtures from `cli/tests/fixtures/`
- **Conformance corpus**: `cli/tests/fixtures/valid/*.ROBOT.md` → every file must pass `robot-md validate`; `cli/tests/fixtures/invalid/*.ROBOT.md` → every file must fail with the expected error code
- **Schema self-test**: a CI step validates the JSON Schema itself against `https://json-schema.org/draft/2020-12/schema`
- **Example integrity**: every file in `examples/` must pass `robot-md validate` in CI
- **Round-trip**: `robot-md render examples/bob.ROBOT.md` → parse as YAML → validate against OpenCastor's `castor.compliance.is_accepted_version` (verified via a separate CI job that installs `opencastor` from PyPI)

## Versioning

- **Schema**: URL-versioned (`/schema/v1/...`, `/schema/v2/...`). Breaking changes bump the path; both served indefinitely for old ROBOT.md files.
- **CLI**: semver, starts at `0.1.0`. v1.0.0 only after Anthropic feedback locks the format.
- **RCAN pass-through**: the ROBOT.md `rcan_version` field uses RCAN spec's own semver. ROBOT.md v1 supports `rcan_version: "3.0"` (and future 3.x minors via the forward-compat rule in `castor.compliance.is_accepted_version`).

## Relationship to sibling projects (crisp ascii diagram)

```
              ┌─────────────────────────────┐
              │   ROBOT.md (this repo)      │
              │   "what this robot IS"      │
              │   YAML + prose, one file    │
              └────┬────────────────────┬───┘
                   │ reads              │ renders to
                   ▼                    ▼
        ┌──────────────────┐   ┌──────────────────┐
        │  Claude surface  │   │ OpenCastor gateway│
        │ Code / Desktop / │   │   (runtime)       │
        │   Mobile         │   └────────┬──────────┘
        └──────────────────┘            │
                                        │ signs + sends RCAN messages
                                        ▼
                          ┌─────────────────────────┐
                          │      RCAN protocol      │
                          │       rcan.dev          │
                          │ wire format for R-to-R  │
                          └───────────┬─────────────┘
                                      │ registers at
                                      ▼
                          ┌─────────────────────────┐
                          │            RRF          │
                          │ robotregistryfoundation.│
                          │           org           │
                          │   who is this robot?    │
                          └─────────────────────────┘
```

**Invariant**: each of the four components is independently useful. You can:
- Use ROBOT.md without OpenCastor (just for Claude context)
- Use OpenCastor without ROBOT.md (YAML-only config, current path)
- Use RCAN without RRF (local-only deployment)
- Use RRF without RCAN (for indexing robots that speak other protocols)

Composition is the point. Integration is the value.

## Success criteria — TODAY

1. Repo exists at `github.com/craigm26/robot-md`, pushed with v0.1 tag
2. `pip install robot-md` works (PyPI publish)
3. `robot-md validate examples/bob.ROBOT.md` returns exit 0 with a green validation summary
4. `cat examples/bob.ROBOT.md | robot-md context -` emits a Claude-ready context block
5. Claude Code SessionStart hook works: in a scratch dir with a copied ROBOT.md, launching `claude` produces a first-turn response that cites the robot's name + DoF + one capability
6. README passes the 60-second test: a dev who's never heard of this reads the top of the README and can explain what ROBOT.md is + try it
7. `proposal/anthropic-adoption-proposal.md` is ready to shop to at least one Anthropic contact

## Success criteria — Anthropic adoption (v1.0 goal)

- `ROBOT.md` is referenced in Claude Code docs as a first-class pattern alongside `CLAUDE.md`
- Claude Desktop ships the `robot-md-mcp` server as a default (or Anthropic-endorsed) integration
- At least 10 non-`craigm26` robots in the wild have a public ROBOT.md
- At least 2 robotics companies cite ROBOT.md as their config format

## Out of scope (do NOT creep)

- ❌ Robot runtime code (OpenCastor does this)
- ❌ Registry implementation (RRF does this)
- ❌ Wire protocol definition (RCAN does this)
- ❌ Skill implementation framework (OpenCastor `SkillRegistry` does this)
- ❌ A web dashboard for managing ROBOT.md files (separate repo if needed)
- ❌ A hardware abstraction layer (OpenCastor `DriverBase` does this)

## Governance (v0.1 reality)

- **Owner**: `craigm26` initially
- **License**: Apache 2.0
- **Contribution bar (v0.1)**: small, quality PRs welcomed; major format changes require a design doc PR before any schema/code PR
- **Path to Anthropic**: if adopted, repo transfers to `Anthropic/robot-md`; craigm26 retains committer status. If not adopted, continues under `craigm26/robot-md` or `continuonai/robot-md`.

## Operational plan for TODAY

1. User approves this spec
2. Invoke writing-plans skill → produce `2026-04-17-robot-md-v0.1-implementation-plan.md`
3. Execute plan via subagent-driven development (same pattern as Phase 1)
4. Push to `github.com/craigm26/robot-md`
5. Publish `robot-md==0.1.0` to PyPI
6. Deploy robotmd.dev landing page (done via Claude design tool + Cloudflare Pages — separate later session per user direction)

## Open questions for user review

1. **Proposal authorship**: should `proposal/anthropic-adoption-proposal.md` go out under `craigm26` personally, or `ContinuonAI` as the org?
2. **Register CLI in v0.1?** Currently deferred to v0.2. If Anthropic wants a working end-to-end demo day-of, bringing it forward means adding `httpx` + an RRF integration test. Small but non-trivial.
3. **PyPI publishing** — can we use the existing `continuonai` or `craigm26` PyPI account, or do we need a fresh one for `robot-md`?
4. **TypeScript port in v0.1?** Currently v0.2. Claude Desktop MCP servers are commonly TS, so an early TS port might unblock the MCP story. Open question about scope vs. timeline.

---

**Next step after approval:** invoke `superpowers:writing-plans` to produce the implementation plan.

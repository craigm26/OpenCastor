# Two-Tier Autoresearch Architecture

OpenCastor operates two distinct but complementary autoresearch loops, each targeting a different scope of improvement.

## Overview

```
┌─────────────────────────────────────────────────────┐
│  Tier 1: Per-Robot Optimizer (castor/optimizer.py)  │
│  Scope: THIS robot's runtime config + memory        │
│  When: Nightly 3am, during idle hours               │
│  What: Context budget, skill tuning, max_iters      │
│  Risk: Low — config changes only, backup/restore    │
└────────────────────┬────────────────────────────────┘
                     │  trajectory data
                     ▼
┌─────────────────────────────────────────────────────┐
│  Tier 2: Codebase Autoresearch (opencastor-autores.) │
│  Scope: OpenCastor codebase (all robots benefit)    │
│  When: Nightly 12am–6am via cron                    │
│  What: Algorithm improvements, new capabilities     │
│  Risk: Medium — code patches, PR/review required    │
└─────────────────────────────────────────────────────┘
```

## Tier 1: Per-Robot Runtime Optimizer

**Module:** `castor/optimizer.py`, `castor/idle.py`, `castor/memory/consolidator.py`

**What it does:** Reads the robot's trajectory database and makes small, conservative improvements to its local RCAN config during idle hours (3am–8am by default).

**Optimization targets:**
- `context_budget` — adjust based on actual token usage (reduce if only 30% used)
- `max_iterations` — raise if hitting cap frequently, lower if tasks are simple
- `skill_trigger_tuning` — flag skills with low precision (advisory, no auto-edit)
- `tool_pruning` — flag unused tools in the past 7 days
- `memory_consolidation` — dedup episodes, archive stale ones, boost high-value ones

**Safety invariants (non-negotiable):**
- NEVER modifies: `safety`, `auth`, `p66`, `motor`, `estop`, `hardware` config keys
- MAX 3 changes per pass
- MIN 5% metric improvement before applying any change
- Backup config before every pass; restore on error
- Abort mid-pass if `IdleGuard` detects new activity
- Only runs when robot is genuinely idle (5-min inactivity window, battery > 20%, outside business hours)

**How to enable:**
```bash
castor optimize --schedule     # install 3am cron job
castor optimize --dry-run      # preview proposed changes
castor optimize --report       # show last pass results
```

## Tier 2: Codebase Autoresearch

**Repo:** `craigm26/opencastor-autoresearch`

**What it does:** Runs nightly LLM-assisted research against the OpenCastor codebase, proposing patches to improve code quality, algorithms, and capabilities.

**Tracks:**
- Track A: Correctness — find and fix bugs in existing code
- Track B: Performance — algorithmic improvements (latency, memory)
- Track C: Safety — harden safety-critical code paths
- Track D: Skill eval — evaluate and improve built-in skills
- Track E: Harness tests — expand P66 invariant test coverage
- Track F: Trajectory mining — extract learnings from trajectory DB

**Architecture:**
```
opencastor-autoresearch/run_agent.py
  ├── Draft model: gemma3:4b (Ollama, local)
  │     Proposes a small code change (one function at a time)
  │
  ├── Reviewer: Gemini 2.0 Flash (Google ADC)
  │     Scores proposal on: correctness, safety, improvement delta
  │     Accepts if score ≥ KEEP_THRESHOLD (default: 0.7)
  │
  └── Results: results.tsv (local), ~/.config/opencastor/trajectories.db (Track F)
```

**Dual-robot mode (planned — issue #3):**
```
Bob (drafter) ──RCAN──► Alex (reviewer)
       └── peer-coordinate skill bridges the draft/review loop
```

## Separation of Concerns

| | Tier 1 (Per-Robot) | Tier 2 (Codebase) |
|---|---|---|
| Scope | Single robot | All robots |
| Artefact | `*.rcan.yaml` config | Python source code |
| Risk | Low (config, reversible) | Medium (code, needs tests) |
| Review | Automatic (metric-gated) | Automatic + CI gate |
| Cadence | Nightly idle hours | Nightly 12am–6am |
| Output | Config diff + history | PR or patch file |
| Trajectory data | Reads from robot's DB | Writes to robot's DB (Track F) |

## Data Flow

```
Robot activity
     │
     ▼
TrajectoryLogger → trajectories.db
     │                    │
     │          ┌─────────┘
     │          ▼
     │    Tier 1 Optimizer
     │    (config tuning)
     │
     └──► Tier 2 Autoresearch (Track F)
          (code improvement proposals)
```

## Future: Dual-Robot Autoresearch

Issue [opencastor-autoresearch#3](https://github.com/craigm26/opencastor-autoresearch/issues/3) plans to extend this to a dual-robot loop:

- **Bob** (Pi 4, no arm) acts as the drafter — proposes changes using its larger context window
- **Alex** (Pi 5, arm) acts as the reviewer — validates safety for physical-layer code
- Communication via RCAN `peer-coordinate` skill (scope: chat, not control)
- Alex's review focuses on: "would this change affect the arm or physical safety layer?"

This extends the two-tier architecture to a **three-tier** system:
1. Per-robot config optimizer (Tier 1)
2. Single-robot codebase autoresearch (Tier 2)
3. Multi-robot collaborative autoresearch (Tier 3)

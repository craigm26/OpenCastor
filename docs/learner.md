# Learner / Sisyphus Operator Manual

The Sisyphus loop is OpenCastor's self-improvement system. After each task
episode, it automatically analyses failures, generates code patches, verifies
them, and applies improvements — turning runtime mistakes into better behaviour
over time.

Source: `castor/learner/`

## How It Works

```
Episode (recorded task run)
        │
        ▼
┌──────────────────┐
│  PM Stage        │  Analyse failure, identify root cause, write spec
│  (pm_stage.py)   │  Uses: Layer 3 brain (Claude / GPT / Gemini)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Dev Stage       │  Generate minimal code patch
│  (dev_stage.py)  │  Uses: Layer 3 brain
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  QA Stage        │  Run tests against patched code
│  (qa_stage.py)   │  Uses: pytest in subprocess
└────────┬─────────┘
         │  patch passes tests?
         ▼
┌──────────────────┐
│  Apply Stage     │  Write patch to disk, record rollback snapshot
│  (apply_stage.py)│  Auto-rollback if runtime regression detected
└──────────────────┘
         │
         ▼
┌──────────────────┐
│  ALMA            │  Consolidate learnings across multiple episodes
│  (alma.py)       │  Builds a long-term improvement knowledge base
└──────────────────┘
```

**Disabled by default.** Opt in explicitly — the loop never runs without
your knowledge.

## Enabling the Loop

### Via wizard (recommended)

```bash
castor wizard
# Select: "Self-improvement" → "Enable Sisyphus loop"
```

### Via RCAN config

```yaml
learner:
  enabled: true
  min_episodes_before_improve: 3   # Wait for N episodes before first run
  max_patch_size_lines: 50         # Refuse patches larger than this
  auto_apply: config_only          # config_only | behavior | manual
  rollback_on_regression: true     # Auto-rollback if test pass rate drops
```

### Via CLI

```bash
castor improve --enable
castor improve --status        # Check current state
castor improve --pause         # Pause without disabling
castor improve --resume
```

## CLI Reference

```bash
# Trigger improvement manually from the last N episodes
castor improve --episodes 5

# Analyse without applying (dry run)
castor improve --dry-run

# View improvement history
castor improve --status

# Show the last generated patch
castor improve --show-patch

# Roll back the most recent auto-patch
castor improve --rollback

# Roll back a specific patch by ID
castor improve --rollback --id abc12345

# Roll back all auto-patches (restore to pre-Sisyphus state)
castor improve --rollback --all
```

## Auto-Apply Modes

| Mode | What it can change | Requires manual review |
|---|---|---|
| `manual` | Nothing — shows patch for your review | Always |
| `config_only` | RCAN config values only (timeouts, thresholds) | Never for config |
| `behavior` | Provider logic, action parsing, config + drivers | Never |

**Recommendation:** Start with `config_only` until you are comfortable with
the system, then graduate to `behavior`.

## Reading a PM Stage Report

After each improvement cycle, a report is written to
`~/.opencastor/learner/reports/<episode-id>.md`:

```
# PM Analysis: Episode 2026-02-21T14:30:00

## Failure Summary
The robot failed to turn left on command 3/5 times.

## Root Cause
`_clean_json()` strips the outer braces of nested action dicts,
causing `Thought.action` to be None when `params` key is present.

## Proposed Fix
Update `BaseProvider._clean_json()` to use brace-counting instead
of `str.rfind("{")`.

## Affected File
castor/providers/base.py : _clean_json()

## Confidence
0.87 (high)
```

If `confidence` is below the configured threshold (default: 0.7), the patch
is flagged for manual review even if `auto_apply` is set to `behavior`.

## Tuning the Loop

### `min_confidence_to_apply`

The minimum PM confidence score before a patch is auto-applied.

```yaml
learner:
  min_confidence_to_apply: 0.8   # default: 0.7
```

### `min_episodes_before_improve`

Run at least N episodes before the first improvement cycle. Useful to
accumulate enough failure signal before patching.

```yaml
learner:
  min_episodes_before_improve: 5   # default: 3
```

### `max_patch_size_lines`

Refuse patches that modify more than N lines. Keeps changes small and safe.

```yaml
learner:
  max_patch_size_lines: 30   # default: 50
```

### Restricting patch scope

You can limit which files the Dev stage is allowed to change:

```yaml
learner:
  allowed_patch_paths:
    - "castor/providers/"
    - "castor/drivers/"
  forbidden_patch_paths:
    - "castor/safety/"      # Safety modules are always protected
    - "castor/auth.py"
```

**Note:** `castor/safety/` is always forbidden regardless of config.

## ALMA — Cross-Episode Learning

`alma.py` consolidates patterns from multiple episodes into a persistent
knowledge base at `~/.opencastor/learner/alma.json`.

ALMA does not generate patches. It:
- Tracks which failure types recur most often
- Adjusts PM stage confidence scoring over time
- Provides context to the Dev stage for better patch quality

```bash
# View ALMA's current knowledge summary
castor improve --alma-status

# Reset ALMA (if you want to start fresh)
castor improve --alma-reset
```

## Rollback

### Automatic rollback

If `rollback_on_regression: true` (default), the Apply stage monitors the
test pass rate after applying a patch. If it drops below the pre-patch
baseline, the patch is reverted automatically.

```
[Apply] Patch applied. Running tests...
[Apply] Tests: 2304/2311 passing (was 2311/2311) — REGRESSION DETECTED
[Apply] Rolling back patch abc12345...
[Apply] Rollback complete. Tests: 2311/2311 passing.
```

### Manual rollback

```bash
# Roll back the last patch
castor improve --rollback

# Roll back a specific patch (ID from `castor improve --status`)
castor improve --rollback --id abc12345

# Rollback using git (if automated rollback fails)
git log --oneline castor/providers/base.py   # find the pre-patch commit
git checkout <commit-hash> -- castor/providers/base.py
pip install -e .
```

## Safety Restrictions

The Sisyphus loop **cannot** modify:

- `castor/safety/` (anti-subversion, bounds, authorization, monitor)
- `castor/auth.py`
- `castor/registry.py`
- Any file outside the `castor/` package
- Tests that cover the safety module

These restrictions are enforced by the Apply stage and cannot be overridden
via config.

## Cost Estimates

The PM and Dev stages each call the Layer 3 brain once per improvement cycle.

| Brain | Cost per cycle (approx.) | Notes |
|---|---|---|
| Ollama / llama.cpp | $0 | Slow, lower quality patches |
| Claude Haiku / GPT-4.1 Mini | ~$0.002 | Good balance |
| Claude Opus 4.6 | ~$0.05 | Best patch quality |
| Gemini 2.5 Flash | ~$0.001 | Fast, reasonable quality |

With `min_episodes_before_improve: 5`, a typical robot running 10 tasks/day
triggers ~2 improvement cycles/day → ~$0.10/day with Claude Opus.

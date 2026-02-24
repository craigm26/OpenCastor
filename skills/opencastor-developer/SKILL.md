# OpenCastor Developer Workflow Skill

## Purpose
Production workflow for engineers validating API behavior, RCAN quality, and operator-facing dashboard tooling before merge or release.

## When to use this skill
Use this skill when a user asks for:
- **API checks**: validating REST/API service availability and endpoint behavior.
- **RCAN lint/migrate tasks**: enforcing config quality or moving configs to a newer spec version.
- **Dashboard usage**: launching/observing the terminal dashboard for debugging and demos.

## Preconditions
1. Confirm the target config file and environment (local/dev/staging).
2. Confirm whether checks should be non-destructive or can update files.
3. Ensure required services/dependencies are running.

## Developer command sequence

### 1) API readiness and status
```bash
castor status --config robot.rcan.yaml
castor doctor --config robot.rcan.yaml
```

### 2) RCAN quality gates
```bash
castor validate --config robot.rcan.yaml
castor lint --config robot.rcan.yaml
```

### 3) RCAN migration workflow (only when upgrading spec)
```bash
castor migrate --config robot.rcan.yaml
castor validate --config robot.rcan.yaml
castor lint --config robot.rcan.yaml
```

### 4) Dashboard workflow
```bash
castor dashboard --config robot.rcan.yaml
castor watch --config robot.rcan.yaml
```
Use `dashboard` for operator view and `watch` for detailed live telemetry during debugging.

## Guardrails
- Never run migration on production configs without a backup/branch.
- Always re-run `validate` and `lint` immediately after `migrate`.
- Do not perform actuator/motion actions from debugging sessions without explicit user confirmation.
- If API checks fail, prioritize diagnostics and config correctness before feature debugging.

## Recovery flow for common failures

### A) API checks failing (`status`/`doctor`)
1. Verify environment variables and service endpoints.
2. Restart local gateway/runtime.
3. Re-run `castor status` and `castor doctor`.
4. If still failing, isolate by testing with minimal config profile.

### B) `castor lint` or `castor validate` fails
1. Fix schema errors first (`validate` output).
2. Fix logical/config integrity findings (`lint` output).
3. Re-run both until clean.

### C) `castor migrate` introduces regressions
1. Compare pre/post config (git diff or `castor diff`).
2. Revert migration if safety-critical fields changed unexpectedly.
3. Apply targeted edits and repeat migrate + validate + lint cycle.

### D) Dashboard not reflecting expected state
1. Confirm runtime is active and pointed to the intended config.
2. Cross-check with `castor watch` and log output.
3. If mismatch persists, capture logs and file a reproducible bug report with command history.

## Exit criteria
- API checks are healthy.
- RCAN config passes validate + lint.
- Any migrations are verified and regression-checked.
- Dashboard and watch telemetry match expected runtime behavior.

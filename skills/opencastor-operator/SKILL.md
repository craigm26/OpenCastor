# OpenCastor Operator Skill

## Purpose
Production runbook for robot operators handling live operations, diagnostics, and config health checks.

## When to use this skill
Use this skill when a user asks for any of the following:
- **Robot operations**: startup readiness, runtime observation, or incident response during missions.
- **Diagnostics**: health investigations, provider/channel readiness checks, or post-failure triage.
- **Config checks**: RCAN conformance verification before deployment.

## Preconditions
1. Confirm the target config path (default: `robot.rcan.yaml`).
2. Confirm whether this is a real robot or simulation environment.
3. Ensure operator has authority to run diagnostics and view logs.

## Standard command sequence (run in order)
Use this exact baseline sequence unless the user requests a different order.

```bash
castor status --config robot.rcan.yaml
castor doctor --config robot.rcan.yaml
castor validate --config robot.rcan.yaml
castor watch --config robot.rcan.yaml
```

### Sequence intent
1. `castor status` verifies provider/channel readiness first.
2. `castor doctor` runs deeper system diagnostics.
3. `castor validate` ensures RCAN schema compliance before continued operation.
4. `castor watch` provides live telemetry while monitoring recovery.

## Guardrails
- **Never issue unsafe motion commands without explicit user confirmation.**
- Treat all movement, actuator, and hazardous tool actions as approval-gated.
- If safety state is unclear, stop and request confirmation of safe physical conditions.
- Prefer read-only diagnostics (`status`, `doctor`, `validate`, `watch`) before any write/action commands.
- If emergency-stop is active, do not suggest bypassing it; follow standard safety reset procedure only.

## Recovery flow for common failures

### A) `castor status` reports provider/channel not ready
1. Re-check credentials and connectivity (`.env`, API keys, channel tokens).
2. Run:
   ```bash
   castor doctor --config robot.rcan.yaml
   ```
3. If unresolved, switch to fallback provider/channel profile and re-run `castor status`.

### B) `castor doctor` reports hardware/peripheral failure
1. Verify power, bus connections, and device permissions.
2. Restart affected subsystem/service.
3. Re-run:
   ```bash
   castor doctor --config robot.rcan.yaml
   castor status --config robot.rcan.yaml
   ```
4. If still failing, keep system in safe mode and escalate with diagnostics output.

### C) `castor validate` fails RCAN checks
1. Inspect reported schema/field errors.
2. Correct config values and version fields.
3. Re-run:
   ```bash
   castor validate --config robot.rcan.yaml
   castor doctor --config robot.rcan.yaml
   ```
4. Resume operations only after validation passes.

### D) `castor watch` shows unstable telemetry / repeated faults
1. Capture fault window and related logs.
2. Execute `castor doctor --config robot.rcan.yaml` for fresh diagnostics.
3. If motion instability is present, stop motion and require explicit operator confirmation before any resume.
4. Apply config or hardware fixes, then verify with full baseline command sequence.

## Exit criteria
- `status`, `doctor`, and `validate` complete successfully.
- Live telemetry in `watch` is stable for the agreed observation window.
- Any high-risk actions remain unexecuted unless explicitly approved.

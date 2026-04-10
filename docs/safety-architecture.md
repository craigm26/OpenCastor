# Safety Architecture — OpenCastor Implementation Reference

**Spec:** [rcan.dev/safety](https://rcan.dev/safety)  
**Version:** OpenCastor 2026.3.x  
**Related:** [rcan.dev/compliance/frameworks](https://rcan.dev/compliance/frameworks), [EU AI Act mapping](https://github.com/continuonai/rcan-spec/blob/master/docs/compliance/eu-ai-act-mapping.md)

This document maps OpenCastor's safety implementation to the RCAN protocol provisions described on [rcan.dev/safety](https://rcan.dev/safety). For the protocol specification, read the rcan.dev page first. This document covers: where the code lives, how to configure it, and how to verify it is working.

---

## Safety Module Map

| Protocol Provision | RCAN Spec | OpenCastor File | Key Class/Function |
|---|---|---|---|
| Confidence gating | §16.2 | `castor/fs/safety.py` | `ConfidenceGateSafety.check()` |
| HiTL gates | §16.3 | `castor/rcan/rbac.py` | `RBACManager.require_hitl()` |
| Audit chain | §16.1 / §6 | `castor/audit.py` | `AuditChain.append()` |
| AI block logging | §16.1 | `castor/brain/robot_context.py` | `dispatch_command()` |
| ML-DSA-65 signing | §9 / §1.6 | `castor/rcan/message_signing.py` | `sign_message_dict()` |
| RBAC enforcement | §2 | `castor/rcan/rbac.py` | `RBACManager.check_scope()` |
| Geofencing | §GEOFENCE | `castor/fs/safety.py` | `GeofenceSafety.check_position()` |
| Operational memory | robot-memory.md | `castor/brain/memory_schema.py` | `RobotMemory.load()`, `.filter_eligible()` |
| Context injection | §16.4 | `castor/brain/robot_context.py` | `build_context()` |
| Nightly memory loop | autoDream | `castor/brain/autodream.py` | `AutoDream.run()` |
| Watchdog | §6 | `castor/watchdog.py` | `Watchdog.start()` |
| Privacy policy | §PRIVACY | `castor/privacy.py` | `PrivacyPolicy.check_scope()` |

---

## Confidence Gating (`castor/fs/safety.py`)

Confidence gates are declared in `rcan-config.yaml` under `agent.confidence_gates`. The `ConfidenceGateSafety` class reads these at startup and evaluates them before any command is dispatched.

```yaml
# rcan-config.yaml
agent:
  confidence_gates:
    NAVIGATE: 0.85
    MANIPULATE: 0.90
    CAMERA_STREAM: 0.70
    ESTOP: 0.50
```

When the model's reported confidence for a proposed action falls below the configured threshold, the gate blocks dispatch. An audit record with `outcome: "blocked"` and `block_reason: "confidence_gate"` is written to the audit chain.

**To verify gating is configured:**
```bash
castor validate --category safety --json | python3 -c "
import sys, json
results = json.load(sys.stdin)
for r in results:
    if r['check_id'] == 'safety.confidence_gates_configured':
        print(r['check_id'], '—', r['status'], '—', r['detail'])
"
# Expected: safety.confidence_gates_configured — pass — brain.confidence_gates is configured (RCAN §16.2)
```

---

## HiTL Gates (`castor/rcan/rbac.py`)

HiTL gates are declared under `agent.hitl_gates`. When a gated action is attempted, the runtime emits a `PENDING_AUTH` status message and blocks dispatch. The action waits until a signed `AUTHORIZE` message arrives from a principal with `OWNER` or higher role.

```yaml
agent:
  hitl_gates:
    - scope: MANIPULATE
      reason: "Physical contact with environment"
    - scope: NAVIGATE
      location_class: human_proximate
```

If no authorization arrives within `hitl_timeout_s` (default: 300), the action is cancelled with `HITL_TIMEOUT`. The AI agent has no code path to bypass this gate.

**To verify local safety invariant (structural enforcement):**
```bash
castor validate --category safety --json | python3 -c "
import sys, json
results = json.load(sys.stdin)
for r in results:
    if r['check_id'] == 'safety.local_safety_wins':
        print(r['check_id'], '—', r['status'], '—', r['detail'])
"
# Expected: safety.local_safety_wins — pass — safety.local_safety_wins=true (RCAN §6 invariant satisfied)
```

---

## Audit Chain (`castor/audit.py`)

`AuditChain.append(record)` writes an audit record to the append-only chain file. Each record includes:

- `msg_id`, `type`, `ruri`, `principal`, `scope`, `timestamp_ms`, `outcome`
- `ai_block`: `model_provider`, `model_id`, `inference_confidence`, `inference_latency_ms`, `thought_id`, `escalated`
- `chain_prev`: SHA-256 hash of the previous record
- `chain_hash`: SHA-256 hash of the current record (including `chain_prev`)

Verify chain integrity:

```bash
castor audit verify
# Expected output: "Audit chain: N records, integrity: OK"
```

If any record has been modified, the hash mismatch is reported with the record index and message ID.

---

## ML-DSA-65 Signing (`castor/rcan/message_signing.py`)

Every outbound RCAN message is signed with the robot's ML-DSA-65 private key. Key generation at first startup:

```bash
castor keys generate --algorithm mldsa65
# Writes ~/.opencastor/identity.key and identity.pub
```

Verify message signing is active:
```bash
castor validate --category rcan_v15 --json | python3 -c "
import sys, json
results = json.load(sys.stdin)
for r in results:
    if r['check_id'] == 'rcan_v15.message_signing':
        print(r['check_id'], '—', r['status'])
"
# Expected: rcan_v15.message_signing — pass
```

---

## Operational Memory (`castor/brain/memory_schema.py`)

`RobotMemory.load(path)` reads `robot-memory.md`, parses the YAML frontmatter, and applies confidence decay at read time:

```python
days_elapsed = (now - entry.last_reinforced) / 86400
entry.confidence = max(0.0, entry.confidence - DECAY_RATE * days_elapsed)
# DECAY_RATE default: 0.05/day
```

`filter_eligible()` returns entries with `confidence >= 0.30` and `type != "resolved"`, sorted by confidence descending.

Context injection format (from `build_context()` in `castor/brain/robot_context.py`):
- `confidence >= 0.80` → 🔴 prefix (high confidence, recent evidence)
- `0.50 ≤ confidence < 0.80` → 🟡 prefix
- `0.30 ≤ confidence < 0.50` → 🟢 prefix

---

## Protocol 66 Conformance

Verify the full Protocol 66 safety rules suite:

```bash
castor validate --category safety --json | python3 -c "
import sys, json
results = json.load(sys.stdin)
for r in results:
    if r['check_id'] == 'safety.p66_conformance':
        print(r['check_id'], '—', r['status'], '—', r['detail'])
"
```

Run the complete safety conformance check:

```bash
castor validate --category safety
# All safety.* checks must pass for RCAN L2 conformance
```

Use `--strict` to treat warnings as failures. Use `--json` for machine-readable output.

---

## Related Issues

- craigm26/OpenCastor#857 — AI output watermarking implementation (§16.5)
- craigm26/OpenCastor#858 — `castor fria generate` CLI (§19)
- craigm26/OpenCastor#859 — Safety subsystem benchmarks

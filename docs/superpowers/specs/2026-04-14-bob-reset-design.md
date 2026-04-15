# Bob Full Reset — Design Spec
**Date:** 2026-04-14
**Status:** Approved
**Scope:** Full wipe of Bob (RRN-000000000001 / RRN-000000000002) and fresh re-registration at RCAN 3.0, L5 conformance

---

## Context

Bob's `castor bridge` was stopped, leaving `status.online: false` in Firestore. The existing config (`rcan_version: 1.4`) is significantly behind the current spec (v3.0). Hardware has changed from a 4-wheel Amazon rover kit to a SO-ARM101 6-DOF follower arm. A full reset is preferable to patching.

**Approach chosen:** Generate fresh config here + manual deletion/registration commands (no scripts).

---

## What Gets Deleted

| System | Target | Method |
|--------|--------|--------|
| rcan.dev | RRN-000000000002 | `DELETE /api/v1/robots/RRN-000000000002` with saved API key |
| Firestore | `robots/RRN-000000000001` + all subcollections | `firebase firestore:delete --recursive` |
| Local Pi | `~/bob.rcan.yaml`, `~/.opencastor/` | `rm` |

---

## New Hardware Profile

| Component | Value |
|-----------|-------|
| SBC | Raspberry Pi 5 16GB |
| NPU | Hailo-8 |
| Arm | SO-ARM101 6-DOF follower (Feetech STS3215 × 6) |
| Serial | `/dev/ttyUSB0`, 1 Mbaud |
| Removed | PCA9685 (rover motors — no longer present) |

---

## New bob.rcan.yaml

```yaml
rcan_version: '3.0'

metadata:
  robot_name: bob
  robot_uuid: # generate with: python3 -c "import uuid; print(uuid.uuid4())"
  rrn: RRN-TBD          # fill after rcan.dev registration
  rrn_uri: rrn://craigm26/robot/opencastor-rpi5-hailo-soarm101/bob-001
  rcan_uri: rcan://robot.local:8000/bob
  manufacturer: craigm26
  model: opencastor-rpi5-hailo-soarm101
  version: '1.0'
  author: craigm26
  license: Apache-2.0
  tags:
    - arm
    - follower
    - so-arm101
    - hailo
    - rpi5
  description: >
    Bob — Raspberry Pi 5 16GB + Hailo-8 NPU + SO-ARM101 6-DOF follower arm.
    Feetech STS3215 servos. RCAN v3.0 compliant, EU AI Act Annex III.

cloud:
  firebase_uid: REPLACE_WITH_YOUR_FIREBASE_UID
  firebase_project: opencastor
  fria_ref: https://rcan.dev/api/v1/robots/RRN-TBD/fria  # fill after registration
  owner: craigm26

agent:
  provider: anthropic
  model: claude-sonnet-4-6
  vision_enabled: true
  latency_budget_ms: 300
  safety_stop: true
  confidence_gates:
    - scope: control
      min_confidence: 0.75
      on_fail: block
    - scope: safety
      min_confidence: 0.9
      on_fail: block

physics:
  type: arm
  dof: 6
  role: follower
  kinematics:
    - id: shoulder_pan
      length_mm: 50
      mass_g: 180
      axis: z
      limits_deg: [-180, 180]
    - id: shoulder_lift
      length_mm: 130
      mass_g: 220
      axis: y
      limits_deg: [-120, 120]
    - id: elbow_flex
      length_mm: 125
      mass_g: 180
      axis: y
      limits_deg: [-120, 120]
    - id: wrist_flex
      length_mm: 60
      mass_g: 100
      axis: y
      limits_deg: [-90, 90]
    - id: wrist_roll
      length_mm: 40
      mass_g: 80
      axis: z
      limits_deg: [-180, 180]
    - id: gripper
      length_mm: 60
      mass_g: 120
      axis: y
      limits_deg: [0, 90]

drivers:
  - id: servo_1
    link_id: shoulder_pan
    protocol: feetech
    model: STS3215
    port: /dev/ttyUSB0
    baud_rate: 1000000
    hardware_id: 1
    max_velocity_dps: 180
    torque_limit_pct: 60
  - id: servo_2
    link_id: shoulder_lift
    protocol: feetech
    model: STS3215
    port: /dev/ttyUSB0
    baud_rate: 1000000
    hardware_id: 2
    max_velocity_dps: 120
    torque_limit_pct: 80
  - id: servo_3
    link_id: elbow_flex
    protocol: feetech
    model: STS3215
    port: /dev/ttyUSB0
    baud_rate: 1000000
    hardware_id: 3
    max_velocity_dps: 150
    torque_limit_pct: 70
  - id: servo_4
    link_id: wrist_flex
    protocol: feetech
    model: STS3215
    port: /dev/ttyUSB0
    baud_rate: 1000000
    hardware_id: 4
    max_velocity_dps: 200
    torque_limit_pct: 60
  - id: servo_5
    link_id: wrist_roll
    protocol: feetech
    model: STS3215
    port: /dev/ttyUSB0
    baud_rate: 1000000
    hardware_id: 5
    max_velocity_dps: 200
    torque_limit_pct: 60
  - id: servo_6
    link_id: gripper
    protocol: feetech
    model: STS3215
    port: /dev/ttyUSB0
    baud_rate: 1000000
    hardware_id: 6
    max_velocity_dps: 180
    torque_limit_pct: 50

connection:
  type: serial
  port: /dev/ttyUSB0
  baud_rate: 1000000
  reconnect_interval_ms: 500

network:
  telemetry_stream: true
  sim_to_real_sync: false
  allow_remote_override: false

rcan_protocol:
  port: 8000
  capabilities:
    - status
    - arm
    - teleop
    - chat
    - vision
  enable_mdns: false
  enable_jwt: false

tiered_brain:
  fast_provider: anthropic
  fast_model: claude-haiku-4-5-20251001
  slow_provider: anthropic
  slow_model: claude-sonnet-4-6
  planner_interval: 10

agent_roster:
  - id: arm_specialist
    provider: anthropic
    model: claude-sonnet-4-6
    scope: control
  - id: chat_specialist
    provider: anthropic
    model: claude-sonnet-4-6
    scope: chat

safety:
  local_safety_wins: true
  emergency_stop_distance: 0.0
  estop_bypass_rate_limit: true
  payload_kg: 0.25
  max_joint_velocity_dps: 200
  watchdog:
    timeout_s: 10

hardware_safety:
  physical_estop: false
  hardware_watchdog_mcu: false

p66:
  enabled: true
  loa_enforcement: true
  consent_required: true
  manifest:
    estop_qos_bypass: true

consent:
  required: true
  mode: explicit
  scope_threshold: control

# ── RCAN v1.5 ──────────────────────────────────────────────────────────────
security:
  replay_protection:
    enabled: true
    window_s: 30
  signing:
    enabled: true
    algorithm: ML-DSA-65      # v3.0 mandatory — Ed25519-only rejected

loa:
  enforcement: true
  min_loa_for_control: 1

human_identity:
  loa_required: 1

# ── RCAN v1.6 ──────────────────────────────────────────────────────────────
transport:
  supported:
    - http
    - compact
    - minimal
  constrained_enabled: false

multimodal:
  enabled: true
  max_chunk_bytes: 1048576

offline:
  enabled: false

r2ram:
  scopes:
    - discover
    - status
    - chat
    - control
    - safety

federation:
  enabled: true
  consent_bridge: false

# ── RCAN v2.1 / v2.2 ───────────────────────────────────────────────────────
attestation:
  firmware_hash: sha256:pending    # run: sha256sum /usr/local/bin/castor | awk '{print "sha256:"$1}'
  sbom_ref: https://rcan.dev/robots/RRN-TBD/.well-known/rcan-sbom.json
  authority_handler_enabled: true
  audit_retention_days: 3650

# ── RCAN v3.0 — EU AI Act §23–§26 ─────────────────────────────────────────
eu_ai_act:
  annex_iii_basis: safety_component
  fria_submitted: false
  safety_benchmark:
    enabled: true
    protocol: rcan-safety-benchmark-v1
  instructions_for_use:
    language: en
    url: https://github.com/craigm26/OpenCastor/blob/main/docs/bob-instructions.md
  post_market_monitoring:
    enabled: true
    interval_days: 90
  eu_register:
    submitted: false

cameras:
  main:
    type: picamera2
    resolution: [1920, 1080]
    fps: 30

reactive:
  hailo_vision: true
  hailo_confidence: 0.4
  fallback_provider: anthropic

geofence:
  enabled: false
  boundary:
    type: none
```

---

## Placeholder Checklist

Before running the bridge, fill in these values:

| Placeholder | Where | How to get it |
|-------------|-------|---------------|
| `metadata.robot_uuid` | `bob.rcan.yaml` | `python3 -c "import uuid; print(uuid.uuid4())"` |
| `metadata.rrn` | `bob.rcan.yaml` | Returned by rcan.dev POST `/api/v1/robots` |
| `cloud.fria_ref` | `bob.rcan.yaml` | `https://rcan.dev/api/v1/robots/<new-rrn>/fria` |
| `attestation.sbom_ref` | `bob.rcan.yaml` | `https://rcan.dev/robots/<new-rrn>/.well-known/rcan-sbom.json` |
| `attestation.firmware_hash` | `bob.rcan.yaml` | `sha256sum /usr/local/bin/castor \| awk '{print "sha256:"$1}'` |
| `cloud.firebase_uid` | `bob.rcan.yaml` | Firebase console → Authentication → your user row |

---

## Re-registration Steps

### 1. Register on rcan.dev
```bash
curl -s -X POST https://rcan.dev/api/v1/robots \
  -H "Content-Type: application/json" \
  -d '{
    "manufacturer": "craigm26",
    "model": "opencastor-rpi5-hailo-soarm101",
    "version": "1.0",
    "device_id": "bob-001",
    "description": "Pi 5 + Hailo-8 + SO-ARM101 follower arm. RCAN 3.0.",
    "contact_email": "YOUR_EMAIL"
  }'
# Save the returned rrn and api_key — api_key shown ONCE, never retrievable again
# Recommended: echo "RCAN_API_KEY=<key>" >> ~/OpenCastor/.env
```

### 2. Fill placeholders
Update `bob.rcan.yaml` with the new RRN and all values from the checklist above.

### 3. Submit FRIA
```bash
curl -s -X POST https://rcan.dev/api/v1/robots/<new-rrn>/fria \
  -H "Authorization: Bearer <new-api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "risk_level": "limited",
    "annex_iii_basis": "safety_component",
    "intended_use": "SO-ARM101 follower arm teleoperation and autonomous pick-and-place via OpenCastor runtime",
    "deployment_context": "research_lab",
    "human_oversight": true,
    "technical_documentation": "https://github.com/craigm26/OpenCastor"
  }'
# On success, set eu_ai_act.fria_submitted: true in the config
```

### 4. Start services on Bob's Pi
```bash
# Copy config
scp bob.rcan.yaml USER@robot.local:~/opencastor/bob.rcan.yaml

# On Bob's Pi:
castor gateway --config ~/opencastor/bob.rcan.yaml &
castor bridge --config ~/opencastor/bob.rcan.yaml
```

The bridge's first run writes a fresh Firestore document at `robots/<new-rrn>` with `status.online: true`. Bob appears in the Flutter app within 30 seconds.

---

## Success Criteria

- [ ] rcan.dev shows new RRN with `verification_tier: community`
- [ ] FRIA document accepted (`sig_verified: true`)
- [ ] Firestore has fresh `robots/<new-rrn>` with `status.online: true`
- [ ] Bob appears as "Online" in the Flutter app
- [ ] `castor validate --config bob.rcan.yaml` passes all checks
- [ ] `rcan_version` in Firestore doc shows `3.0` (requires bridge update — follow-on task)

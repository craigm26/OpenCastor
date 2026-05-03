# RCAN Integration Guide

OpenCastor is a productized open-core [RCAN protocol](https://rcan.dev/compatibility) runtime — gateway-as-kernel plus drivers, fleet management, cloud bridge, and commercial support. RCAN-the-protocol is implementation-independent; OpenCastor is the most fully-documented runtime that speaks it.

This document covers the key integration points: how OpenCastor maps to RCAN concepts,
and how to use the protocol's advanced features — including §19 INVOKE and §20 telemetry.

---

## Protocol Sections Implemented

| Section | Topic | Module |
|---------|-------|--------|
| §7 | Confidence Gate | `castor/confidence_gate.py` |
| §8 | Human-in-the-Loop (HiTL) Gate | `castor/hitl_gate.py` |
| §9 | Message Signing (Ed25519) | `castor/rcan/message_signing.py` |
| §10 | Registry Auto-Registration | `castor/rcan/registry.py` |
| §12 | Federation Protocol | `castor/rcan/node_resolver.py` |
| §16 | Commitment Chain (AuditChain) | `castor/rcan/commitment_chain.py` |
| §17 | Distributed Registry Node Protocol | `castor/rcan/node_broadcaster.py` |
| §18 | Capability Advertisement | `castor/rcan/capabilities.py` |
| §19 | Behavior/Skill Invocation | `castor/rcan/invoke.py` |
| §20 | Telemetry Field Registry | `castor/rcan/telemetry_fields.py` |
| §21 | Robot Registry Integration | `castor/rcan/registry.py` |

---

## §19 — Behavior/Skill Invocation (INVOKE)

RCAN §19 defines a structured RPC-like protocol for triggering named behaviors or
skills on a robot runtime and receiving structured results.

### Message Types

- `INVOKE` — trigger a named skill on a remote or local robot
- `INVOKE_RESULT` — response containing status, output, and error details
- `INVOKE_CANCEL` — cancel a pending invocation by `invoke_id`

### Quick Start

```python
from castor.rcan.invoke import InvokeRequest, InvokeDispatcher

# Register a skill handler
dispatcher = InvokeDispatcher()

@dispatcher.register("nav.go_to")
def handle_go_to(params: dict) -> dict:
    x, y = params["x"], params["y"]
    # ... navigate to (x, y) ...
    return {"reached": True, "position": {"x": x, "y": y}}

# Dispatch an incoming INVOKE message
req = InvokeRequest(skill="nav.go_to", params={"x": 1.5, "y": 0.0})
result = dispatcher.dispatch(req)
# result.status == "ok", result.output == {"reached": True, ...}
```

### Wire Format

```json
{
  "type": "INVOKE",
  "msg_id": "<uuid>",
  "skill": "nav.go_to",
  "params": {"x": 1.5, "y": 0.0},
  "timeout_ms": 5000
}
```

```json
{
  "type": "INVOKE_RESULT",
  "invoke_id": "<same-uuid>",
  "status": "ok",
  "output": {"reached": true, "position": {"x": 1.5, "y": 0.0}},
  "error": null,
  "duration_ms": 312
}
```

### Parallel Invocations

Use `castor.rcan.parallel_invoke` to fire multiple skills concurrently:

```python
from castor.rcan.parallel_invoke import parallel_invoke

results = parallel_invoke(dispatcher, [
    InvokeRequest(skill="nav.go_to", params={"x": 1.0, "y": 0.0}),
    InvokeRequest(skill="arm.home", params={}),
])
```

### Skill Naming Conventions

Skills follow a `<subsystem>.<action>` dot-notation pattern:

| Skill | Description |
|-------|-------------|
| `nav.go_to` | Navigate to (x, y) |
| `nav.stop` | Stop all navigation |
| `arm.pick` | Pick object at pose |
| `arm.home` | Return arm to home position |
| `camera.capture` | Capture a frame |
| `behavior.<name>` | Run a named behavior chain |

### Timeout and Cancellation

```python
# Invoke with a 3-second timeout
req = InvokeRequest(skill="arm.pick", params={"pose": [0, 0, 0.5]}, timeout_ms=3000)

# Cancel a pending invoke
from castor.rcan.invoke import InvokeCancel
cancel = InvokeCancel(invoke_id=req.invoke_id)
dispatcher.cancel(cancel)
```

Full spec: [rcan.dev/spec/section-19/](https://rcan.dev/spec/section-19/)

---

## §20 — Telemetry Field Registry

RCAN §20 defines a standard set of telemetry field names to ensure cross-runtime
compatibility. Use these constants when emitting telemetry — any consumer that
understands RCAN §20 will be able to parse your robot's data.

### Using the Constants

```python
from castor.rcan import telemetry_fields as tf

telemetry_frame = {
    tf.BATTERY_PERCENT:   87.3,
    tf.CPU_PERCENT:       14.2,
    tf.LINEAR_VELOCITY:   0.35,
    tf.ANGULAR_VELOCITY:  0.02,
    tf.ESTOP_ACTIVE:      False,
}
```

### Standard Field Groups

#### Joint Fields
| Constant | Wire Name | Type | Unit |
|----------|-----------|------|------|
| `JOINT_POSITION` | `joint_position` | float \| list[float] | radians |
| `JOINT_VELOCITY` | `joint_velocity` | float \| list[float] | rad/s |
| `JOINT_EFFORT` | `joint_effort` | float \| list[float] | Nm |
| `JOINT_TEMPERATURE` | `joint_temperature` | float \| list[float] | °C |
| `JOINT_NAMES` | `joint_names` | list[str] | — |

#### Mobile Robot State
| Constant | Wire Name | Type | Unit |
|----------|-----------|------|------|
| `LINEAR_VELOCITY` | `linear_velocity` | float | m/s |
| `ANGULAR_VELOCITY` | `angular_velocity` | float | rad/s |
| `POSITION_X` / `Y` / `Z` | `position_x/y/z` | float | meters |
| `ORIENTATION_W/X/Y/Z` | `orientation_w/x/y/z` | float | quaternion |

#### Power
| Constant | Wire Name | Type | Unit |
|----------|-----------|------|------|
| `BATTERY_VOLTAGE` | `battery_voltage` | float | V |
| `BATTERY_CURRENT` | `battery_current` | float | A |
| `BATTERY_PERCENT` | `battery_percent` | float | 0–100 |
| `BATTERY_CHARGING` | `battery_charging` | bool | — |

#### Compute
| Constant | Wire Name | Type | Unit |
|----------|-----------|------|------|
| `CPU_PERCENT` | `cpu_percent` | float | 0–100 |
| `MEMORY_PERCENT` | `memory_percent` | float | 0–100 |
| `GPU_PERCENT` | `gpu_percent` | float | 0–100 |
| `NPU_PERCENT` | `npu_percent` | float | 0–100 |
| `TEMPERATURE_CPU` | `temperature_cpu` | float | °C |

#### Safety
| Constant | Wire Name | Type |
|----------|-----------|------|
| `ESTOP_ACTIVE` | `estop_active` | bool |
| `SAFETY_ZONE_BREACH` | `safety_zone_breach` | bool |

### WebSocket Telemetry Stream

OpenCastor's `WS /ws/telemetry` endpoint emits §20-compatible frames at 5 Hz:

```json
{
  "battery_percent": 87.3,
  "cpu_percent": 14.2,
  "linear_velocity": 0.35,
  "estop_active": false,
  "timestamp": "2026-03-13T12:00:00Z"
}
```

### Conformance

OpenCastor passes the RCAN §20 telemetry field conformance tests:

```bash
castor conformance --level L3
# § 20  telemetry-fields  PASS
```

Full spec: [rcan.dev/spec/section-20/](https://rcan.dev/spec/section-20/)

---

## Related Docs

- [subsystems.md](claude/subsystems.md) — Provider pattern and RCAN config
- [api-reference.md](claude/api-reference.md) — REST/WS API including telemetry endpoint
- [RCAN specification](https://rcan.dev/spec/) — Full protocol specification (see also [live compatibility matrix](https://rcan.dev/compatibility))
- [Robot Registry Foundation](https://robotregistryfoundation.org) — RRN registry

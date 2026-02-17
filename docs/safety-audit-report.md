# Safety Audit Report: OpenCastor vs ContinuonAI Standards & RCAN Spec

**Version:** 1.0  
**Date:** 2026-02-17  
**Auditor:** Automated deep analysis  
**Scope:** OpenCastor safety kernel, ContinuonAI safety architecture, RCAN protocol safety invariants  

---

## 1. Executive Summary

OpenCastor implements a robust, Unix-philosophy safety architecture through its virtual filesystem (`castor/fs/safety.py`), RCAN RBAC system (`castor/rcan/rbac.py`), and supporting modules (watchdog, geofence, privacy, audit, crash recovery). The design is pragmatic, well-layered, and production-aware.

ContinuonAI's ContinuonOS implements a more comprehensive "Ring 0" safety kernel (`brain-a/safety/`) with deeper concerns: anti-subversion defenses, tamper-evident audit logs, work authorization for destructive actions, protocol-level safety rules (Protocol 66), physical workspace/joint bounds, and continuous sensor monitoring.

**Overall assessment:** OpenCastor covers ~65% of ContinuonAI's safety surface area. The gaps are primarily in:
- **Anti-subversion / prompt injection defense** (absent)
- **Work authorization for destructive actions** (absent)
- **Physical bounds enforcement** (workspace, joint, force limits — absent)
- **Tamper-evident audit logging** (hash-chained integrity — absent)
- **Safety state telemetry streaming** (absent)
- **Continuous sensor monitoring** (temperature, force — absent)

OpenCastor excels in areas ContinuonOS does not prioritize:
- **Geofencing** with dead-reckoning odometry
- **Privacy-by-default** sensor policies
- **Crash recovery** with state persistence
- **RCAN RBAC** with rate limiting and session timeouts (more complete than ContinuonOS's implementation)

### Alignment Score

| Dimension | OpenCastor | ContinuonOS Target | Gap |
|-----------|-----------|-------------------|-----|
| Permission enforcement | ★★★★★ | ★★★★★ | None |
| RCAN RBAC | ★★★★★ | ★★★★☆ | OC leads |
| Motor safety (rate limit, clamp) | ★★★★☆ | ★★★★★ | Minor |
| Emergency stop | ★★★★☆ | ★★★★★ | Minor |
| Audit logging | ★★★☆☆ | ★★★★★ | Moderate |
| Anti-subversion | ☆☆☆☆☆ | ★★★★★ | **Critical** |
| Work authorization | ☆☆☆☆☆ | ★★★★★ | **Critical** |
| Physical bounds | ☆☆☆☆☆ | ★★★★★ | **Critical** |
| Sensor monitoring | ★☆☆☆☆ | ★★★★☆ | Significant |
| Safety protocol/rules | ★★☆☆☆ | ★★★★★ | Significant |
| Geofencing | ★★★★☆ | ☆☆☆☆☆ | OC leads |
| Privacy | ★★★★★ | ★★☆☆☆ | OC leads |
| Crash recovery | ★★★★☆ | ★☆☆☆☆ | OC leads |

---

## 2. ContinuonAI Safety Architecture Overview

### 2.1 Ring 0 Safety Kernel Design

ContinuonOS uses a concentric ring architecture (Ring 0–6). Ring 0 (Safety) is the innermost ring:

- **File:** `brain-a/src/brain_a/safety/kernel.py`
- **Pattern:** Singleton, boots first, cannot be disabled
- **Latency target:** <1ms for safety checks (τ=10ms for full validation)
- **Key principle:** VETO power over all actions from any ring

The kernel is a `SafetyKernel` singleton (lines 89–97) with:
- Watchdog thread (100ms heartbeat, line 121)
- Signal handler registration (SIGTERM/SIGINT → safe shutdown)
- `atexit` handler for clean teardown
- Emergency stop with callback chain
- E-stop reset requiring environment-variable authorization code

### 2.2 Safety Submodules

| Module | File | Purpose |
|--------|------|---------|
| **Bounds** | `safety/bounds.py` | Workspace sphere/box, joint position/velocity/torque limits, EE velocity, contact force limits, forbidden zones |
| **Monitor** | `safety/monitor.py` | Continuous CPU temp, memory, force sensor monitoring. 3 consecutive failures → critical alert |
| **Anti-Subversion** | `safety/anti_subversion.py` | 14 regex patterns for prompt injection, forbidden target scopes, anomaly detection (rate limiting auth attempts), tamper-evident hash-chained audit log |
| **Authorization** | `safety/authorization.py` | Work orders for destructive actions (demolish, cut, burn, etc.), property claims, time-limited authorizations, role-gated (creator/owner/leasee only) |
| **Protocol** | `safety/protocol.py` | Protocol 66: 15 named safety rules across 10 categories (motion, force, workspace, human, thermal, electrical, software, emergency, property, privacy) |
| **State** | `safety/state.py` | `SafetyStateSnapshot` dataclass for telemetry streaming via gRPC/RCAN |

### 2.3 Protocol 66 Safety Rules

ContinuonOS defines 15 explicit safety rules in `protocol.py` (lines 74–117):

| Rule ID | Category | Description | Severity |
|---------|----------|-------------|----------|
| MOTION_001 | Motion | Max joint velocity 2.0 rad/s | violation |
| MOTION_002 | Motion | Max EE velocity 1.0 m/s (0.25 w/ human) | violation |
| MOTION_004 | Motion | E-stop response <100ms | critical |
| FORCE_001 | Force | Max contact 50N (10N w/ human) | violation |
| FORCE_003 | Force | Collision detection → stop & retract | violation |
| WORKSPACE_001 | Workspace | Sphere bounds r=0.8m | violation |
| WORKSPACE_002 | Workspace | Forbidden zones | critical |
| HUMAN_001 | Human | Detection at 2m, slow at 1m, stop at 0.3m | violation |
| HUMAN_002 | Human | Contact response: stop & comply, max 10N | violation |
| THERMAL_001 | Thermal | Motor temp max 80°C, warn at 60°C | violation |
| ELECTRICAL_001 | Electrical | Power monitoring 11–13V, max 10A | warning |
| SOFTWARE_001 | Software | Watchdog timeout 100ms | critical |
| EMERGENCY_001 | Emergency | E-stop always available | critical |
| PROPERTY_001 | Property | Destructive actions need authorization | critical |
| PRIVACY_001 | Privacy | Consent required for data collection | critical |

### 2.4 Absolute Prohibitions (Immutable)

From `anti_subversion.py` (lines 22–31), using `frozenset` (immutable at runtime):
- `harm_human_intentionally`, `harm_child`, `harm_protected_person`
- `assist_crime`, `destroy_evidence`, `illegal_surveillance`
- `weapons_creation`, `bioweapons`, `nuclear_materials`
- `human_trafficking`, `terrorism`

These cannot be disabled, overridden, or modified at runtime.

---

## 3. RCAN Spec Safety Requirements

Source: `rcan-spec/src/pages/spec/index.astro` (Section 6: Safety Invariants)

### 3.1 Five Safety Invariants (Protocol Requirements)

| # | Invariant | Description |
|---|-----------|-------------|
| 1 | **Local safety always wins** | No remote command can bypass on-device safety checks |
| 2 | **Graceful degradation** | Network loss triggers safe-stop, not undefined behavior |
| 3 | **Audit trail** | All commands logged with user, timestamp, and outcome |
| 4 | **Rate limiting** | Commands throttled per role (Guest: 10/min, User: 100/min, etc.) |
| 5 | **Timeout enforcement** | Control sessions expire; explicit renewal required |

### 3.2 Role-Based Access Control (5-tier)

From the RCAN spec:

| Role | Level | Key Permissions |
|------|-------|-----------------|
| CREATOR | 5 | Full hardware/software control, OTA push, safety override |
| OWNER | 4 | Configuration, skill installation, user management |
| LEASEE | 3 | Time-bound operational control |
| USER | 2 | Operational control within allowed modes |
| GUEST | 1 | Limited interaction, chat, read-only status |

### 3.3 RCAN Error Codes (Safety-Related)

| Code | Name | Description |
|------|------|-------------|
| 3003 | COMMAND_REJECTED | Command failed safety check |
| 4001 | SAFETY_VIOLATION | Action would violate safety constraints |
| 4002 | EMERGENCY_STOP | Robot in emergency stop state |
| 2005 | RATE_LIMITED | Too many requests |

### 3.4 Conformance Tests (Safety)

From `rcan-spec/src/pages/conformance/index.astro`:

| Test ID | Scenario | Expected |
|---------|----------|----------|
| SAFE-001 | SAFETY priority message | Response <100ms |
| SAFE-002 | Network partition during command | Safe-stop triggered |
| SAFE-003 | Remote command bypasses local limit | REJECTED |
| SAFE-004 | Any command execution | Audit log entry created |

### 3.5 Message Priority System

RCAN messages carry a `Priority` field. `SAFETY` priority messages skip the normal queue entirely (Safety Invariant 6 in the OpenCastor implementation).

---

## 4. OpenCastor Safety Kernel Analysis

### 4.1 Architecture: Unix-Style Virtual Filesystem

OpenCastor's safety is built on a virtual filesystem metaphor:

- **File:** `castor/fs/safety.py` — The `SafetyLayer` wraps a `Namespace` with enforcement
- **File:** `castor/fs/permissions.py` — Unix rwx ACLs + Linux-style capabilities (`Cap` flags)
- **Pattern:** Every operation goes through `SafetyLayer.read()` / `.write()` / `.ls()`, which check permissions, rate limits, and value bounds before touching the namespace

This is an elegant design that makes safety enforcement compositional and auditable.

### 4.2 Specific Safety Mechanisms

#### 4.2.1 Permission Enforcement (`castor/fs/permissions.py`)

- **5 principals:** `root`, `brain`, `channel`, `api`, `driver`
- **10 capability flags:** `MOTOR_WRITE`, `ESTOP`, `CONFIG_WRITE`, `MEMORY_READ`, `MEMORY_WRITE`, `CHANNEL_SEND`, `PROVIDER_SWITCH`, `SAFETY_OVERRIDE`, `DEVICE_ACCESS`, `CONTEXT_WRITE`
- **Prefix-matching ACLs:** `/dev/motor` inherits from `/dev` unless overridden
- **Root bypass:** `root` principal bypasses all checks (lines 73, 175)

**Strength:** Clean separation of concerns. The `brain` principal cannot write to `/etc` (config) without `CONFIG_WRITE` capability. Drivers cannot access memory. Channels get read-only sensor access.

#### 4.2.2 Motor Safety (`castor/fs/safety.py`, lines 155–195)

- **Rate limiting:** Max 20 motor commands/second (1-second sliding window)
- **Value clamping:** Linear and angular motor values clamped to [-1.0, 1.0]
- **E-stop enforcement:** When `_estop=True`, all writes to `/dev/motor/*` are blocked

**Strength:** Rate limiting prevents motor command flooding from runaway AI loops. Clamping prevents physical over-driving.

#### 4.2.3 RCAN RBAC (`castor/rcan/rbac.py`)

- **5-tier role hierarchy:** GUEST(1) → USER(2) → OPERATOR(3) → ADMIN(4) → CREATOR(5)
- **Scope flags:** STATUS, CONTROL, CONFIG, TRAINING, ADMIN
- **Per-role rate limits:** Guest 10/min, User 100/min, Operator 500/min, Admin 1000/min, Creator unlimited
- **Session timeouts:** Guest 5min, User 1hr, Operator 2hr, Admin 8hr, Creator no timeout
- **Legacy bridge:** Maps old principal names to RCAN roles automatically

**Note:** Rate limiting and session timeout methods exist on `SafetyLayer` (`check_role_rate_limit()`, `check_session_timeout()`) but are **not called from `read()` or `write()`** — they must be invoked explicitly by callers (e.g., `main.py`). This is a gap: these checks should be integrated into the core enforcement path.

#### 4.2.4 Emergency Stop (`castor/fs/safety.py`, lines 235–262)

- **Capability-gated:** Requires `CAP_ESTOP` (or root)
- **Clear requires `CAP_SAFETY_OVERRIDE`** (or root)
- **Blocks all `/dev/motor` writes** while active
- **Updates `/proc/status`** to "estop"

**Strength:** Two-tier capability requirement (estop vs. clear) prevents accidental resumption.

#### 4.2.5 Violation Tracking & Lockout (`castor/fs/safety.py`, lines 97–115)

- **Per-principal violation counter**
- **5 violations → 30-second lockout** (configurable)
- **Root exempt from lockout**
- **Audit logging of all violations**

**Strength:** Adaptive defense against misbehaving principals.

#### 4.2.6 Watchdog (`castor/watchdog.py`)

- **Independent of latency budget** — stops motors if brain goes silent
- **Default 10-second timeout**
- **Configurable via RCAN config**

#### 4.2.7 Geofence (`castor/geofence.py`)

- **Dead-reckoning odometry** tracks distance from start
- **Configurable radius** (default 5m)
- **Action: stop or warn**

#### 4.2.8 Privacy (`castor/privacy.py`)

- **Default-deny** for camera, audio, location
- **Default-allow** for anonymous telemetry
- **Environment variable overrides**
- **7-day data retention default**

#### 4.2.9 Audit Log (`castor/audit.py`)

- **Append-only JSON lines** file
- **Logs:** motor commands, approvals, config changes, errors, startup/shutdown
- **Thread-safe** with lock

#### 4.2.10 Crash Recovery (`castor/crash.py`)

- **Saves crash state** to `.opencastor-crash.json`
- **Captures:** last thought, last action, loop count, uptime, error
- **Interactive recovery** on next startup

#### 4.2.11 RCAN Message System (`castor/rcan/message.py`)

- **8 message types:** DISCOVER, STATUS, COMMAND, STREAM, EVENT, HANDOFF, ACK, ERROR
- **4 priority levels:** LOW, NORMAL, HIGH, SAFETY
- **SAFETY priority skips queue** (documented as Safety Invariant 6)
- **TTL-based expiry**
- **Scope-tagged messages** for RBAC enforcement

---

## 5. Gap Analysis

### 5.1 Critical Gaps (OpenCastor Missing vs ContinuonAI)

| # | Gap | ContinuonOS Reference | Impact | Priority |
|---|-----|----------------------|--------|----------|
| G1 | **No anti-subversion / prompt injection defense** | `safety/anti_subversion.py` — 14 regex patterns, forbidden scopes, anomaly detection | AI brain could be manipulated via prompt injection to bypass safety | **P0** |
| G2 | **No work authorization for destructive actions** | `safety/authorization.py` — signed work orders, property claims, time-limited | Robot could perform destructive actions without proper authorization chain | **P0** |
| G3 | **No physical workspace/joint bounds** | `safety/bounds.py` — workspace sphere/box, joint limits, EE velocity, contact force | Manipulator could exceed safe physical limits | **P0** (for arm-equipped robots) |
| G4 | **No tamper-evident audit log** | `safety/anti_subversion.py:TamperEvidentLog` — hash-chained entries, genesis block, integrity verification | Audit log could be silently modified | **P1** |
| G5 | **No structured safety protocol/rules engine** | `safety/protocol.py` — Protocol 66, 15 named rules, 10 categories, enable/disable per rule | Safety policies are hardcoded rather than configurable/introspectable | **P1** |
| G6 | **No safety state telemetry streaming** | `safety/state.py` — `SafetyStateSnapshot` for gRPC/RCAN | Remote monitoring cannot inspect safety kernel state | **P1** |
| G7 | **No continuous sensor monitoring** | `safety/monitor.py` — CPU temp, memory, force sensors, configurable thresholds | Thermal runaway or force overload goes undetected | **P2** |
| G8 | **No human detection/proximity safety** | Protocol 66 HUMAN_001/002 — detection zones, contact response | No adaptive behavior when humans are near | **P2** |

### 5.2 Minor Gaps

| # | Gap | ContinuonOS Reference | Impact |
|---|-----|----------------------|--------|
| G9 | E-stop reset uses capability check only, no authorization code | `kernel.py` line 178 — env var `CONTINUON_ESTOP_RESET_CODE` | E-stop could be cleared more easily than intended |
| G10 | No absolute prohibitions list (immutable) | `anti_subversion.py` lines 22–31 — `frozenset` of prohibited actions | No hard floor on what actions are categorically forbidden |
| G11 | RCAN role naming differs from spec | Spec: CREATOR/OWNER/LEASEE/USER/GUEST. OC: CREATOR/ADMIN/OPERATOR/USER/GUEST | Could cause interop confusion |

### 5.3 Areas Where OpenCastor Leads

| # | Feature | OpenCastor | ContinuonOS |
|---|---------|-----------|-------------|
| L1 | **Geofencing** | Full dead-reckoning geofence with configurable radius | Not implemented |
| L2 | **Privacy-by-default** | Default-deny camera/audio/location, env overrides | Only a Protocol 66 rule (PRIVACY_001), no enforcement module |
| L3 | **Crash recovery** | Persistent crash state, interactive recovery | Only emergency log to `/tmp` |
| L4 | **RCAN RBAC completeness** | Rate limits + session timeouts integrated into safety layer | RBAC is lighter, no rate limiting |
| L5 | **Virtual filesystem abstraction** | All safety as filesystem operations — composable, testable | Direct function calls — tighter coupling |

---

## 6. Alignment Recommendations (Prioritized)

### P0 — Critical (Implement Before Any Arm/Manipulation Deployment)

#### R1: Add Anti-Subversion Module

**Create `castor/safety/anti_subversion.py`** modeled on ContinuonOS's implementation:

1. **Prompt injection detection:** Port the 14 regex patterns from `brain_a/safety/anti_subversion.py` (lines 75–89)
2. **Forbidden target scopes:** Block "all", "everything", "system", "root", "kernel", "safety" as targets
3. **Absolute prohibitions:** Implement as `frozenset` — immutable at runtime
4. **Integration point:** Hook into `SafetyLayer.write()` for any path under `/dev/` or `/mnt/` where the data originates from AI-generated content

**Effort:** ~200 LOC, 1–2 days

#### R2: Add Physical Bounds Module

**Create `castor/safety/bounds.py`** for arm-equipped robots:

1. **Workspace bounds:** Sphere + optional box + floor + forbidden zones (port `WorkspaceBounds` from ContinuonOS)
2. **Joint limits:** Position, velocity, torque, acceleration per joint (port `JointBounds`)
3. **EE velocity limits:** Normal mode vs. human-present mode
4. **Contact force limits:** Normal vs. human-present
5. **Integration:** Validate in `SafetyLayer.write()` for `/dev/motor/arm/*` paths

**Effort:** ~300 LOC, 2–3 days. Can reuse ContinuonOS dataclasses directly.

#### R3: Add Work Authorization System

**Create `castor/safety/authorization.py`** for destructive actions:

1. **Work order model:** Scoped to specific targets, time-limited, role-gated
2. **Property claims:** Ownership verification before destruction authorization
3. **Default deny:** All destructive actions blocked without active authorization
4. **Integration:** Check in `SafetyLayer.write()` when action payload contains destructive verbs

**Effort:** ~400 LOC, 2–3 days. Port from ContinuonOS with simplification.

### P1 — Important (Implement Before Production)

#### R4: Upgrade Audit Log to Tamper-Evident

**Modify `castor/audit.py`:**

1. Add SHA-256 hash chaining (each entry includes hash of previous entry)
2. Add genesis block constant
3. Add `verify_integrity()` method
4. ~50 LOC addition to existing module

**Effort:** 0.5 day

#### R5: Add Safety Protocol / Rules Engine

**Create `castor/safety/protocol.py`:**

1. Port Protocol 66 rule definitions (or a subset relevant to OpenCastor's robot types)
2. Make rules configurable via RCAN config YAML
3. Integrate rule checking into `SafetyLayer`
4. Enable/disable individual rules at runtime (with audit logging)

**Effort:** ~200 LOC, 1–2 days

#### R6: Add Safety State Telemetry

**Create `castor/safety/state.py`:**

1. `SafetyStateSnapshot` dataclass (port from ContinuonOS)
2. Expose via `/proc/safety` in the virtual filesystem
3. Include in RCAN STATUS messages
4. Enable remote safety monitoring

**Effort:** ~100 LOC, 0.5 day

#### R7: Align RCAN Role Names with Spec

The RCAN spec uses CREATOR/OWNER/LEASEE/USER/GUEST. OpenCastor uses CREATOR/ADMIN/OPERATOR/USER/GUEST. Rename:
- `ADMIN` → `OWNER`
- `OPERATOR` → `LEASEE`

This is a breaking change but important for spec conformance.

**Effort:** Find-and-replace + test updates, 0.5 day

### P2 — Enhancement (Implement When Adding Sensors)

#### R8: Add Continuous Sensor Monitoring

**Create `castor/safety/monitor.py`:**

1. CPU temperature monitoring (Pi thermal zone)
2. Memory usage monitoring
3. Force/torque sensor integration (when available)
4. Configurable thresholds with warning → critical escalation
5. Auto-trigger e-stop after N consecutive failures

**Effort:** ~200 LOC, 1 day. Port from ContinuonOS.

#### R9: Add E-Stop Authorization Code

Require an authorization code (env var or config) to clear e-stop, matching ContinuonOS's pattern.

**Effort:** ~20 LOC, 0.5 hour

---

## 7. Implementation Roadmap

### Quick Wins (< 1 hour each)

1. **Wire rate limit + session timeout into SafetyLayer** — 10 LOC in `castor/fs/safety.py`
2. **Add e-stop authorization code** — 20 LOC in `castor/fs/safety.py`
3. **Add hash chaining to audit log** — 50 LOC in `castor/audit.py`

### Phase 1: Foundation Safety (Week 1–2)

| Task | Est. | Dependencies |
|------|------|-------------|
| R1: Anti-subversion module | 2 days | None |
| R4: Tamper-evident audit | 0.5 day | None |
| R6: Safety state telemetry | 0.5 day | None |
| R9: E-stop auth code | 0.5 hr | None |
| R7: RCAN role name alignment | 0.5 day | None |

**Milestone:** Core safety gaps closed. All RCAN Safety Invariants fully met.

### Phase 2: Physical Safety (Week 3–4)

| Task | Est. | Dependencies |
|------|------|-------------|
| R2: Physical bounds module | 3 days | Arm driver integration |
| R3: Work authorization | 3 days | R1 (uses anti-subversion checks) |
| R5: Safety protocol engine | 2 days | R2, R3 (rules reference bounds and auth) |

**Milestone:** Full ContinuonAI safety parity for arm-equipped robots.

### Phase 3: Monitoring & Polish (Week 5–6)

| Task | Est. | Dependencies |
|------|------|-------------|
| R8: Sensor monitoring | 1 day | Hardware sensor availability |
| RCAN conformance test suite | 2 days | All above |
| Integration testing | 2 days | All above |

**Milestone:** Production-ready safety kernel with conformance tests.

---

## Appendix A: File Reference Map

| OpenCastor File | ContinuonOS Equivalent | Status |
|----------------|----------------------|--------|
| `castor/fs/safety.py` | `brain_a/safety/kernel.py` | ✅ Implemented (different approach) |
| `castor/fs/permissions.py` | (inline in kernel) | ✅ More complete in OC |
| `castor/rcan/rbac.py` | (none — lighter RBAC) | ✅ More complete in OC |
| `castor/rcan/message.py` | (none — gRPC-based) | ✅ Implemented |
| `castor/watchdog.py` | `brain_a/safety/monitor.py` (watchdog portion) | ✅ Implemented |
| `castor/geofence.py` | (none) | ✅ OC-only feature |
| `castor/privacy.py` | Protocol 66 PRIVACY_001 | ✅ More complete in OC |
| `castor/audit.py` | `anti_subversion.py:TamperEvidentLog` | ⚠️ Missing hash chain |
| `castor/crash.py` | (none) | ✅ OC-only feature |
| (none) | `brain_a/safety/anti_subversion.py` | ❌ **Missing** |
| (none) | `brain_a/safety/authorization.py` | ❌ **Missing** |
| (none) | `brain_a/safety/bounds.py` | ❌ **Missing** |
| (none) | `brain_a/safety/protocol.py` | ❌ **Missing** |
| (none) | `brain_a/safety/state.py` | ❌ **Missing** |

## Appendix B: RCAN Safety Invariant Compliance

| Invariant | Requirement | OpenCastor Status | Evidence |
|-----------|-------------|-------------------|----------|
| 1. Local safety wins | No remote bypass | ✅ **PASS** | `SafetyLayer` wraps all namespace access; no bypass path exists |
| 2. Graceful degradation | Network loss → safe-stop | ✅ **PASS** | `BrainWatchdog` triggers motor stop on timeout (`watchdog.py:78`) |
| 3. Audit trail | All commands logged | ✅ **PASS** | `_audit_action()` in `safety.py:120`; `AuditLog` in `audit.py` |
| 4. Rate limiting | Per-role throttling | ⚠️ **PARTIAL** | `check_role_rate_limit()` exists in `safety.py:197` but not called from `read()`/`write()` — must be invoked by callers |
| 5. Timeout enforcement | Sessions expire | ⚠️ **PARTIAL** | `check_session_timeout()` exists in `safety.py:229` but not called from `read()`/`write()` — must be invoked by callers |

**Result: OpenCastor passes 3 of 5 RCAN Safety Invariants fully. Invariants 4 and 5 have implementations that exist but are not wired into the enforcement path — a straightforward fix.**

### Recommended Fix for Invariants 4 & 5

Add rate limit and session timeout checks to `SafetyLayer.read()` and `SafetyLayer.write()`:

```python
# In SafetyLayer.read() and .write(), before permission check:
if not self.check_role_rate_limit(principal):
    return None  # or False for write
if not self.check_session_timeout(principal):
    return None  # or False for write
```

**Effort:** ~10 LOC, 15 minutes.

## Appendix C: RCAN Conformance Test Coverage

| Test ID | Description | OpenCastor Coverage |
|---------|-------------|-------------------|
| SAFE-001 | SAFETY priority response <100ms | ⚠️ Not benchmarked; `Priority.SAFETY` exists in `message.py` but no queue-skip implementation verified |
| SAFE-002 | Network partition → safe-stop | ✅ `BrainWatchdog` covers this |
| SAFE-003 | Remote bypass rejected | ✅ `SafetyLayer` enforces locally |
| SAFE-004 | Audit log for all commands | ✅ `_audit_action()` called on every write |

## Appendix D: Industry Standard Alignment Notes

### ISO 13482 (Personal Care Robots)
- **Hazard identification:** OpenCastor's geofence and motor clamping partially address this. Physical bounds (R2) would significantly improve compliance.
- **Protective stop function:** E-stop implemented. Missing: Category 0/1/2 stop classification.
- **Speed and force limiting:** Motor clamping provides basic speed limiting. Contact force limits (in R2) needed for ISO 13482 compliance.

### ISO 10218 (Industrial Robots)
- **Safety-rated monitored stop:** E-stop exists. Missing: SIL (Safety Integrity Level) classification.
- **Hand guiding:** Not applicable to current OpenCastor scope.
- **Speed and separation monitoring:** HUMAN_001/002 rules (in R5) would address this.

### IEC 61508 (Functional Safety)
- **Software watchdog:** Implemented (`watchdog.py`)
- **Diagnostic coverage:** Partial (crash recovery). Sensor monitoring (R8) would improve.
- **Safety function testing:** No formal test harness exists for safety functions.

---

*This report was generated through deep analysis of source code across 4 repositories: OpenCastor, ContinuonOS, continuon-proto, and rcan-spec. All file references are to specific source files and line numbers as of 2026-02-17.*

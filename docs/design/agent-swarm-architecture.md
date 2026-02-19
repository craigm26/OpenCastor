# RFC: Agent Swarm Architecture — Layer 3 for OpenCastor

**Status:** Draft Proposal  
**Author:** OpenCastor Core Team  
**Date:** 2026-02-18  
**Version:** 0.1.0  
**Target Release:** OpenCastor v2026.4.x (Phase 1)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current Architecture](#2-current-architecture)
3. [Proposed Architecture](#3-proposed-architecture)
4. [The Centerpiece: Self-Improving Loop](#4-the-centerpiece-self-improving-loop)
5. [Agent Types](#5-agent-types)
6. [Communication Protocol](#6-communication-protocol)
7. [Lifecycle Management](#7-lifecycle-management)
8. [Integration with Tiered Brain](#8-integration-with-tiered-brain)
9. [Multi-Robot Extension](#9-multi-robot-extension)
10. [Implementation Phases](#10-implementation-phases)
11. [API Design](#11-api-design)
12. [Cost Analysis](#12-cost-analysis)
13. [Open Questions](#13-open-questions)

---

## 1. Executive Summary

### What

Layer 3 adds a **multi-agent swarm** to OpenCastor: multiple specialized AI agents running concurrently on a single robot, coordinated by an orchestrator. Think of it as **"soul fragments"** — one physical body, many concurrent minds (navigator, observer, manipulator, guardian), each an expert in its domain.

### Why

Today's OpenCastor brain is a single pipeline: sense → think → act. This works for simple tasks but breaks down when a robot needs to simultaneously navigate, watch for hazards, plan a grasp, and talk to a human. A single LLM call can't do all of that well, and serializing these concerns creates unacceptable latency.

More importantly: **robots don't learn from their mistakes**. A robot that fails a grasp today will fail the same grasp tomorrow. The highest-value addition isn't more agents — it's a **self-improving loop** that watches the robot operate, diagnoses failures, proposes code/config fixes, verifies them, and applies them autonomously. This is the Sisyphus pattern from [Oh-My-OpenCode](https://github.com/nicepkg/oh-my-opencode): persistent, iterative improvement with built-in QA.

### Inspiration

| Source | Key Idea | Our Adaptation |
|--------|----------|----------------|
| **Oh-My-OpenCode** | PM→Dev→QA/QC self-improving loop, Sisyphus persistence | Learner agent: episode analysis → behavior patches → verification → apply |
| **Claude Flow** | Leader-specialist delegation, MCP tool coordination | Orchestrator delegates to specialized agents via shared protocol |
| **"Soul Fragments"** | Multiple AI personas on one entity | Navigator, Observer, Manipulator, etc. as concurrent agents |

---

## 2. Current Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    OpenCastor Brain                      │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Layer 2: Planner         (~10-15s)             │    │
│  │  Claude / deep reasoning                        │    │
│  │  Complex task decomposition, NL understanding   │    │
│  └──────────────────────┬──────────────────────────┘    │
│                         │                               │
│  ┌──────────────────────▼──────────────────────────┐    │
│  │  Layer 1: Fast Brain       (~500ms)             │    │
│  │  Gemini Flash / HF / MLX                        │    │
│  │  Perception-action loops, object detection      │    │
│  └──────────────────────┬──────────────────────────┘    │
│                         │                               │
│  ┌──────────────────────▼──────────────────────────┐    │
│  │  Layer 0: Reactive         (<1ms)               │    │
│  │  Rule-based safety, e-stop, collision avoid     │    │
│  │  ALWAYS RUNS — never preempted                  │    │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Hardware Abstraction Layer (HAL)               │    │
│  │  Motors, sensors, cameras, arms, grippers       │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

**Limitations of this architecture:**

- **Serial reasoning**: Only one "thought" at a time
- **No learning**: Identical mistakes repeat across episodes
- **No specialization**: One model prompt tries to cover navigation, manipulation, and communication
- **No persistent monitoring**: Scene understanding is discarded between ticks

---

## 3. Proposed Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      OpenCastor Brain v2                         │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  Layer 3: Agent Swarm            (concurrent, async)     │    │
│  │                                                          │    │
│  │  ┌────────────┐  ┌──────────┐  ┌────────────────────┐   │    │
│  │  │Orchestrator│──│Blackboard│──│   Learner (★)      │   │    │
│  │  │  (master)  │  │ (shared  │  │  Self-Improving    │   │    │
│  │  └─────┬──────┘  │  state)  │  │  Loop (Sisyphus)   │   │    │
│  │        │         └────┬─────┘  └────────────────────┘   │    │
│  │   ┌────┼─────┬────────┼────┬──────────┐                 │    │
│  │   ▼    ▼     ▼        ▼    ▼          ▼                 │    │
│  │  Nav  Obs  Manip   Comm  Guard    [User Agents]         │    │
│  │                                                          │    │
│  └──────────────────────────┬───────────────────────────────┘    │
│                             │                                    │
│  ┌──────────────────────────▼───────────────────────────────┐    │
│  │  Layer 2: Planner         (~10-15s)                      │    │
│  │  Individual agents may invoke Layer 2 for deep reasoning │    │
│  └──────────────────────────┬───────────────────────────────┘    │
│                             │                                    │
│  ┌──────────────────────────▼───────────────────────────────┐    │
│  │  Layer 1: Fast Brain       (~500ms)                      │    │
│  │  Agents share perception pipeline; results on blackboard │    │
│  └──────────────────────────┬───────────────────────────────┘    │
│                             │                                    │
│  ┌──────────────────────────▼───────────────────────────────┐    │
│  │  Layer 0: Reactive         (<1ms) — ALWAYS ACTIVE        │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  HAL + RCAN Network                                      │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

Key principles:

- **Layer 0 is sacred.** Nothing in the swarm can override reactive safety. Ever.
- **Agents are concurrent, not sequential.** They run as async tasks sharing a blackboard.
- **The Learner is always watching.** It's the one agent that never sleeps — it observes episodes, diagnoses failures, and proposes improvements.
- **Agents are optional.** A simple robot might run only Learner + Observer. The swarm scales to the hardware.

---

## 4. The Centerpiece: Self-Improving Loop

> *"A robot that fails a grasp and doesn't learn from it is just expensive furniture."*

The **Learner agent** is the single most valuable addition in this proposal. It implements the **Sisyphus pattern** from Oh-My-OpenCode: persistent, iterative self-improvement with built-in quality assurance.

### 4.1 The Problem

Today, if a robot:
- Fails to pick up a mug → it will fail the same way next time
- Takes a suboptimal path → it will take the same path next time
- Misunderstands a voice command → same misunderstanding next time

There is no feedback loop. No learning. No improvement.

### 4.2 The Sisyphus Loop

```
    ┌──────────────────────────────────────────────────────┐
    │                 SISYPHUS LOOP                        │
    │                                                      │
    │   ┌─────────┐    ┌─────────┐    ┌─────────┐         │
    │   │  PM     │───▶│  Dev    │───▶│  QA/QC  │──┐      │
    │   │(Analyze)│    │(Patch)  │    │(Verify) │  │      │
    │   └────▲────┘    └─────────┘    └─────────┘  │      │
    │        │                                      │      │
    │        │         ┌──────────┐                 │      │
    │        └─────────│  Apply   │◀────────────────┘      │
    │                  │ (if pass)│     ✗ → retry          │
    │                  └──────────┘       (up to N)        │
    │                                                      │
    └──────────────────────────────────────────────────────┘

    Triggered by: episode completion (success or failure)
    Cadence: after every task, or batched every N episodes
```

### 4.3 The Four Stages

#### Stage 1: PM (Project Manager) — Analyze the Episode

The PM reviews what happened. It has access to:
- Full episode log (actions taken, sensor readings, outcomes)
- The task goal and success criteria
- Historical performance on similar tasks
- Current action-selection config and behavior parameters

```python
class PMStage:
    """Analyzes episode outcomes and identifies improvement opportunities."""

    async def analyze(self, episode: Episode) -> AnalysisReport:
        report = AnalysisReport(episode_id=episode.id)

        # Score the episode
        report.outcome = episode.success  # bool
        report.duration = episode.duration
        report.efficiency = self._compute_efficiency(episode)

        # Identify failure points
        if not episode.success:
            report.failure_point = self._find_failure_moment(episode)
            report.root_cause = await self._diagnose_root_cause(
                episode, report.failure_point
            )
            # e.g. "Grasp failed because approach angle was 15° off"
            # e.g. "Navigation stalled at doorway — costmap inflation too high"

        # Identify improvement opportunities even on success
        report.suboptimalities = self._find_inefficiencies(episode)
        # e.g. "Path was 2.3m longer than optimal"
        # e.g. "Spent 4s re-detecting object that was already known"

        # Prioritize what to fix
        report.improvements = self._prioritize(
            report.root_cause, report.suboptimalities
        )

        return report
```

The PM uses **Layer 2 (Claude)** for complex root-cause analysis. For routine episodes, a local model suffices.

#### Stage 2: Dev — Generate the Fix

The Dev stage takes the PM's analysis and produces a concrete patch:

```python
class DevStage:
    """Generates behavior improvements as code patches or config changes."""

    async def generate_fix(self, report: AnalysisReport) -> Patch:
        for improvement in report.improvements:
            if improvement.type == "config_tuning":
                # Adjust a numeric parameter
                # e.g. grasp_approach_angle_offset: 0 → 15
                return ConfigPatch(
                    file="config/manipulation.yaml",
                    key=improvement.config_key,
                    old_value=improvement.current_value,
                    new_value=improvement.suggested_value,
                    rationale=improvement.rationale,
                )

            elif improvement.type == "behavior_rule":
                # Add or modify a behavior rule
                # e.g. "If object is on shelf edge, use lateral grasp"
                return BehaviorPatch(
                    layer="fast_brain",
                    rule=improvement.new_rule,
                    conditions=improvement.conditions,
                    rationale=improvement.rationale,
                )

            elif improvement.type == "code_patch":
                # Actual code modification (highest risk, most power)
                return CodePatch(
                    file=improvement.target_file,
                    diff=improvement.generated_diff,
                    rationale=improvement.rationale,
                    requires_review=True,  # Always flag for QA
                )

        return NullPatch()  # Nothing actionable
```

**Safety constraint**: Code patches always require QA verification. Config patches within known-safe bounds can be auto-applied after QA.

#### Stage 3: QA/QC — Verify Before Applying

This is the critical safety gate. The QA stage:

1. **Static analysis**: Does the patch make sense? Does it conflict with safety rules?
2. **Simulation test**: Run the original episode in simulation with the patch applied
3. **Regression check**: Run a suite of known-good episodes to ensure nothing breaks
4. **Bounds check**: Are all values within safe operating ranges?

```python
class QAStage:
    """Verifies patches before they're applied to the live robot."""

    async def verify(self, patch: Patch, episode: Episode) -> QAResult:
        checks = []

        # 1. Safety bounds
        if isinstance(patch, ConfigPatch):
            checks.append(self._check_safety_bounds(patch))

        # 2. Replay in simulation (if sim available)
        if self.sim_available:
            sim_result = await self.simulator.replay(
                episode, patches=[patch]
            )
            checks.append(QACheck(
                name="sim_replay",
                passed=sim_result.success and sim_result.improved,
                detail=f"Sim: success={sim_result.success}, "
                       f"efficiency_delta={sim_result.efficiency_delta:+.1%}",
            ))

        # 3. Regression suite
        regression = await self._run_regression_suite(patch)
        checks.append(regression)

        # 4. Human review gate (for code patches)
        if isinstance(patch, CodePatch):
            checks.append(QACheck(
                name="human_review",
                passed=False,  # Requires explicit approval
                detail="Code patch queued for human review",
            ))

        return QAResult(
            approved=all(c.passed for c in checks),
            checks=checks,
            retry_suggested=any(c.retry_hint for c in checks),
        )
```

#### Stage 4: Apply (or Retry)

```python
class ApplyStage:
    """Applies verified patches. Retries Dev→QA if verification fails."""

    MAX_RETRIES = 3

    async def apply(self, patch: Patch, qa_result: QAResult) -> bool:
        if qa_result.approved:
            await self._apply_patch(patch)
            await self._log_improvement(patch)
            return True

        if qa_result.retry_suggested and self.retry_count < self.MAX_RETRIES:
            # Feed QA feedback back to Dev for a better patch
            self.retry_count += 1
            new_patch = await self.dev.generate_fix(
                self.report,
                previous_attempt=patch,
                qa_feedback=qa_result,
            )
            new_qa = await self.qa.verify(new_patch, self.episode)
            return await self.apply(new_patch, new_qa)

        # Give up — log for human review
        await self._queue_for_human_review(patch, qa_result)
        return False
```

### 4.4 Connection to ContinuonAI ALMA

The Learner agent's episode analysis maps directly to ALMA's memory consolidation:

| ALMA Concept | Learner Equivalent |
|---|---|
| Short-term memory | Episode log (raw sensor + action trace) |
| Consolidation | PM stage: analyze, extract patterns |
| Long-term memory | Behavior rules, config deltas, learned heuristics |
| Recall | Fast Brain queries learned rules during action selection |

The Learner periodically runs an **ALMA consolidation pass**: reviewing many episodes to find cross-episode patterns, not just single-episode fixes.

```python
async def alma_consolidation(self, episodes: list[Episode]) -> list[Patch]:
    """Cross-episode pattern analysis. Runs every N episodes or daily."""
    patterns = await self.pm.find_cross_episode_patterns(episodes)
    # e.g. "Object grasps fail 80% of the time when lighting < 200 lux"
    # e.g. "Navigation is 30% faster when using A* vs RRT in open spaces"
    patches = []
    for pattern in patterns:
        patch = await self.dev.generate_fix(pattern)
        qa = await self.qa.verify_pattern_patch(patch, episodes)
        if qa.approved:
            patches.append(patch)
    return patches
```

### 4.5 What Kinds of Things Improve?

| Category | Example Improvement | Mechanism |
|---|---|---|
| **Grasp parameters** | Approach angle, grip force, pre-grasp pose | Config tuning |
| **Navigation** | Costmap inflation, planner choice per environment | Config tuning |
| **Perception** | Detection confidence thresholds, retry strategies | Config tuning |
| **Action selection** | "Use two-handed grasp for objects > 500g" | Behavior rule |
| **Recovery behaviors** | "If stuck > 5s, back up and re-plan" | Behavior rule |
| **Prompt engineering** | Improved system prompts for Layer 1/2 | Template patch |
| **Code logic** | Bug fixes, edge case handling | Code patch (human-gated) |

---

## 5. Agent Types

### 5.1 Orchestrator (Master Agent)

The conductor. Receives high-level goals, decomposes them into sub-tasks, delegates to specialist agents, and manages their results.

```
Role:        Task decomposition, delegation, conflict resolution
Model:       Layer 2 (Claude) for complex planning; local for routine dispatch
Always-on:   Yes (when swarm is active)
Delegates:   All other agents
```

**Responsibilities:**
- Parse high-level commands ("clean the kitchen") into sub-tasks
- Assign sub-tasks to appropriate agents
- Resolve conflicts (Navigator says "go left," Observer says "obstacle left")
- Manage task priority and preemption
- Report progress to human operator

### 5.2 Navigator

```
Role:        Spatial planning, path optimization, SLAM integration
Model:       Local (MLX/HF) for fast replanning; Layer 2 for novel environments
Always-on:   During locomotion tasks
Reads:       Blackboard (map, obstacles, goal)
Writes:      Blackboard (planned_path, eta, blocked_paths)
```

### 5.3 Observer

```
Role:        Persistent scene understanding, change detection, anomaly alerts
Model:       Layer 1 (Gemini Flash) for continuous VLM; local for filtering
Always-on:   Yes — the "eyes" of the swarm
Reads:       Camera feeds, depth data, Blackboard
Writes:      Blackboard (scene_graph, detected_objects, anomalies)
```

The Observer maintains a **persistent scene graph** that other agents query instead of re-processing raw vision. It implements the Oh-My-OpenCode **explore-before-act** pattern: before the robot acts, the Observer sends out 2–5 parallel "scout" queries to characterize the scene from multiple angles.

### 5.4 Manipulator

```
Role:        Arm/gripper planning, grasp strategy, force control
Model:       Local for known grasps; Layer 2 for novel objects
Always-on:   During manipulation tasks
Reads:       Blackboard (scene_graph, target_object, grasp_history)
Writes:      Blackboard (grasp_plan, arm_trajectory)
```

### 5.5 Communicator

```
Role:        Human interaction, intent parsing, status reporting
Model:       Layer 1 for fast NLU; Layer 2 for complex dialogue
Always-on:   Yes
Reads:       Audio/text input, Blackboard (robot_state)
Writes:      Blackboard (parsed_intent, pending_confirmations)
```

### 5.6 Guardian (Safety Meta-Agent)

```
Role:        Monitors other agents for unsafe plans, enforces constraints
Model:       Local rule engine + Layer 1 for edge cases
Always-on:   ALWAYS — like Layer 0 but for swarm-level decisions
Reads:       ALL blackboard writes from all agents
Writes:      Blackboard (vetoed_actions, safety_alerts)
Priority:    HIGHEST (can veto any agent except Layer 0)
```

The Guardian sits between the swarm and the hardware. It reviews every action plan before execution:

```python
class GuardianAgent(SwarmAgent):
    async def review(self, action_plan: ActionPlan) -> Verdict:
        # Hard rules (instant, no model needed)
        if action_plan.max_velocity > self.safety_limits.max_velocity:
            return Verdict.VETO("Velocity exceeds safety limit")
        if action_plan.workspace_violation:
            return Verdict.VETO("Action outside safe workspace")

        # Soft rules (model-assisted)
        if action_plan.confidence < 0.7:
            risk = await self.assess_risk(action_plan)
            if risk > self.risk_threshold:
                return Verdict.VETO(f"High risk ({risk:.0%}), low confidence")

        return Verdict.APPROVE()
```

### 5.7 Learner (Self-Improving Agent) ★

```
Role:        Episode analysis, behavior improvement, ALMA consolidation
Model:       Layer 2 (Claude) for root-cause analysis; local for metrics
Always-on:   ALWAYS — runs in background, never interferes with active tasks
Reads:       Episode logs, Blackboard (full history), config files
Writes:      Config patches, behavior rules, improvement reports
```

See [Section 4](#4-the-centerpiece-self-improving-loop) for the full design.

### 5.8 Custom / User Agents

The framework supports user-defined agents for domain-specific tasks:

```python
class PlantWaterer(SwarmAgent):
    """Example custom agent for a gardening robot."""
    name = "plant_waterer"
    triggers = ["water_plants", "check_soil"]

    async def run(self, task: Task) -> TaskResult:
        soil_moisture = await self.blackboard.read("soil_moisture")
        if soil_moisture < self.config.dry_threshold:
            return TaskResult(action="water", duration=self.config.water_seconds)
        return TaskResult(action="skip", reason="Soil sufficiently moist")
```

---

## 6. Communication Protocol

### 6.1 Shared Blackboard

The primary communication mechanism is a **typed, versioned blackboard** — a shared key-value store that all agents read from and write to.

```
┌─────────────────────────────────────────────────────┐
│                   BLACKBOARD                        │
│                                                     │
│  scene_graph:      {...}     [Observer, v42, 50ms]  │
│  planned_path:     [...]     [Navigator, v7, 1.2s]  │
│  grasp_plan:       {...}     [Manipulator, v3, 0.8s]│
│  parsed_intent:    "pick up" [Communicator, v1, 2s] │
│  safety_alerts:    []        [Guardian, v99, 10ms]  │
│  robot_state:      {...}     [HAL, v1000, 1ms]      │
│  episode_log:      [...]     [Learner, v15, async]  │
│                                                     │
│  Each entry: key → (value, writer, version, age)    │
└─────────────────────────────────────────────────────┘
```

```python
class Blackboard:
    """Thread-safe, versioned shared state for the agent swarm."""

    async def write(self, key: str, value: Any, writer: str) -> int:
        """Write a value. Returns new version number."""
        ...

    async def read(self, key: str, max_age_ms: int = None) -> BlackboardEntry:
        """Read a value. Optional staleness check."""
        ...

    async def watch(self, key: str, callback: Callable) -> Subscription:
        """Subscribe to changes on a key."""
        ...

    async def cas(self, key: str, expected_version: int, value: Any, writer: str) -> bool:
        """Compare-and-swap for conflict-free concurrent writes."""
        ...
```

**Why a blackboard over message passing?**

- Agents run at different frequencies. The Observer updates 10x/sec; the Planner updates every 15s. Message passing would require complex buffering.
- New agents can be added without modifying existing agents — they just read/write keys.
- The blackboard doubles as the episode log for the Learner.

### 6.2 Direct Messages (Supplementary)

For urgent, point-to-point communication (e.g., Guardian vetoing an action), agents can send direct messages:

```python
await self.send_message(
    to="manipulator",
    type=MessageType.VETO,
    payload={"action_id": "grasp_42", "reason": "Collision risk"},
    priority=Priority.CRITICAL,
)
```

### 6.3 RCAN Integration

The blackboard extends naturally to OpenCastor's **RCAN (Robot Communication and Networking)** layer. Remote robots can subscribe to blackboard keys across the network:

```python
# Robot A publishes its scene graph
await blackboard.write("scene_graph", graph, writer="observer", scope=Scope.NETWORK)

# Robot B subscribes to Robot A's scene graph
await rcan.subscribe("robot_a/scene_graph", callback=self.on_remote_scene)
```

---

## 7. Lifecycle Management

### 7.1 Agent Lifecycle

```
    spawn() ──▶ INITIALIZING ──▶ READY ──▶ RUNNING ──▶ IDLE
                    │                         │          │
                    ▼                         ▼          ▼
                  FAILED              SUSPENDED      shutdown()
                    │                    │               │
                    ▼                    ▼               ▼
                 restart()           resume()         STOPPED
```

### 7.2 Resource Budgets

Each agent has a resource budget to prevent runaway costs:

```yaml
# config/swarm.yaml (excerpt)
resource_budgets:
  default:
    max_cpu_percent: 15
    max_memory_mb: 256
    max_api_calls_per_minute: 10
    max_api_cost_per_hour_usd: 0.50

  overrides:
    learner:
      max_api_calls_per_minute: 5     # Runs in background, no rush
      max_api_cost_per_hour_usd: 1.00  # Worth investing in improvements
    observer:
      max_cpu_percent: 25             # Vision is CPU-heavy
      max_api_calls_per_minute: 30    # Frequent VLM calls
    guardian:
      max_cpu_percent: 5              # Must be lightweight
      max_api_calls_per_minute: 2     # Mostly rule-based
```

### 7.3 Spawn Policies

```yaml
# config/swarm.yaml (excerpt)
spawn_policy:
  # Always running
  always_on:
    - learner      # Self-improvement never stops
    - observer     # Eyes always open
    - guardian     # Safety always active

  # Spawned on demand by orchestrator
  on_demand:
    - navigator    # When locomotion needed
    - manipulator  # When manipulation needed
    - communicator # When human interaction detected

  # Started by orchestrator for complex multi-step tasks
  orchestrator:
    trigger: task_complexity > 2  # Simple tasks skip the orchestrator
```

---

## 8. Integration with Tiered Brain

The swarm sits *alongside* the existing tiers, not replacing them:

```
                    ┌───────────────────────┐
  Human command ───▶│   Communicator Agent  │
                    └──────────┬────────────┘
                               │ parsed intent
                               ▼
                    ┌───────────────────────┐
                    │    Orchestrator        │
                    │  "pick up the mug"    │
                    └──┬────────┬───────────┘
                       │        │
            ┌──────────▼─┐  ┌──▼──────────┐
            │  Observer   │  │  Navigator  │
            │ "find mug"  │  │ "go to mug" │
            └──────┬──────┘  └──────┬──────┘
                   │                │
                   ▼                ▼
            ┌─────────────────────────────┐
            │  Layer 1: Fast Brain        │  ◀── Shared perception
            │  VLM: "mug at (1.2, 0.8)"  │
            └──────────────┬──────────────┘
                           │
            ┌──────────────▼──────────────┐
            │  Layer 0: Reactive          │  ◀── ALWAYS runs
            │  Collision? E-stop? Cliff?  │
            └──────────────┬──────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │   Hardware   │
                    └──────────────┘

Meanwhile, in background:
  ┌──────────┐     ┌──────────┐
  │ Guardian  │     │ Learner  │
  │ reviewing │     │ watching │
  │ all plans │     │ episode  │
  └──────────┘     └──────────┘
```

**Critical rule**: Layer 0 reactive safety is never bypassed. The swarm generates *intentions*; Layer 0 gates *execution*. If Layer 0 says stop, everything stops, regardless of what any agent wants.

**Escalation path**:
1. Simple, routine task → Layer 1 handles it directly (no swarm)
2. Moderate task → Orchestrator + 1-2 agents
3. Complex task → Full swarm, Layer 2 reasoning
4. After every task → Learner analyzes the episode

---

## 9. Multi-Robot Extension

The same agent architecture scales from one robot to a fleet:

```
    ┌────────────────┐     ┌────────────────┐     ┌────────────────┐
    │   Robot A       │     │   Robot B       │     │   Robot C       │
    │                 │     │                 │     │                 │
    │  Local Swarm:   │     │  Local Swarm:   │     │  Local Swarm:   │
    │  - Observer     │     │  - Observer     │     │  - Observer     │
    │  - Navigator    │     │  - Manipulator  │     │  - Navigator    │
    │  - Learner      │     │  - Learner      │     │  - Learner      │
    │                 │     │                 │     │                 │
    │  Blackboard ────┼─────┼─── Blackboard ──┼─────┼── Blackboard   │
    └────────┬────────┘     └────────┬────────┘     └────────┬────────┘
             │                       │                       │
             └───────────┬───────────┘───────────────────────┘
                         │
                  ┌──────▼──────┐
                  │ Fleet       │
                  │ Orchestrator│  (runs on any robot or edge server)
                  │             │
                  │ - Task      │
                  │   allocation│
                  │ - Shared    │
                  │   learning  │  ◀── Learner patches shared across fleet
                  │ - Conflict  │
                  │   resolution│
                  └─────────────┘
```

**Shared Learning** is the killer feature at fleet scale: when Robot A's Learner discovers that a particular grasp strategy works better, that patch can propagate to Robot B and C after fleet-level QA verification.

```python
class FleetLearner:
    async def share_improvement(self, patch: Patch, source_robot: str):
        """Propagate a verified improvement across the fleet."""
        # Only share patches that passed local QA
        assert patch.qa_status == QAStatus.APPROVED

        for robot in self.fleet.robots:
            if robot.id == source_robot:
                continue
            # Each robot's QA must also approve (different hardware, etc.)
            await robot.rcan.send(
                topic="learner/incoming_patch",
                payload=patch.serialize(),
            )
```

---

## 10. Implementation Phases

### Phase 1: Self-Improving Loop (★ Highest Priority)

**Target: v2026.4.x**

The killer feature ships first. A single robot that gets better every day is worth more than a swarm that doesn't learn.

**Deliverables:**
- [ ] `SwarmAgent` base class and lifecycle management
- [ ] `Blackboard` with typed keys, versioning, watch/subscribe
- [ ] `LearnerAgent` with full PM → Dev → QA/QC → Apply pipeline
- [ ] Episode logging infrastructure (action traces, sensor snapshots, outcomes)
- [ ] Config patching system with safety bounds
- [ ] Behavior rule engine (if/then rules that Layer 1 evaluates)
- [ ] Simulation replay for QA verification (optional, degrades gracefully)
- [ ] ALMA consolidation pass (cross-episode pattern analysis)
- [ ] CLI: `opencastor learner status`, `opencastor learner history`, `opencastor learner review`
- [ ] Dashboard: improvement history, pending reviews, performance trends

**Success criteria:**
- Robot measurably improves at a repeated task over 50 episodes
- No unsafe patches applied (QA catches 100% of out-of-bounds values)
- Human can review and revert any auto-applied change

### Phase 2: Observer + Navigator (Basic Parallel Agents)

**Target: v2026.6.x**

**Deliverables:**
- [ ] `ObserverAgent` — persistent scene graph, change detection
- [ ] `NavigatorAgent` — path planning integration, replanning on obstacle
- [ ] Explore-before-act pattern (parallel scout queries)
- [ ] `GuardianAgent` — safety review of agent-proposed actions
- [ ] Agent-to-agent direct messaging
- [ ] Resource budget enforcement

**Success criteria:**
- Observer maintains scene graph that Navigator and Manipulator query
- Navigation tasks complete faster with persistent scene understanding
- Guardian successfully vetoes at least one unsafe plan in testing

### Phase 3: Full Agent Roster + Orchestrator

**Target: v2026.9.x**

**Deliverables:**
- [ ] `OrchestratorAgent` — task decomposition, delegation
- [ ] `ManipulatorAgent` — grasp planning, arm trajectory
- [ ] `CommunicatorAgent` — NLU, dialogue management
- [ ] Custom agent SDK and plugin system
- [ ] Full blackboard persistence and replay
- [ ] CLI: `opencastor swarm status`, `opencastor swarm spawn <agent>`, `opencastor swarm kill <agent>`

**Success criteria:**
- "Pick up the mug and bring it to me" works end-to-end with agent coordination
- Custom agent can be written in < 50 lines of Python

### Phase 4: Multi-Robot Swarm

**Target: v2027.1.x**

**Deliverables:**
- [ ] Fleet Orchestrator
- [ ] Cross-robot blackboard sync via RCAN
- [ ] Shared learning (patch propagation with fleet-level QA)
- [ ] Fleet-level task allocation
- [ ] Multi-robot SLAM fusion

**Success criteria:**
- Two robots coordinate on a task neither could do alone
- Learning transfers: improvement on Robot A benefits Robot B

---

## 11. API Design

### 11.1 Python API

```python
from opencastor.swarm import SwarmAgent, Blackboard, Orchestrator
from opencastor.swarm.learner import LearnerAgent, Episode

# --- Defining a custom agent ---

class MyAgent(SwarmAgent):
    name = "my_agent"
    description = "Does something useful"
    required_capabilities = ["camera"]  # Won't spawn if robot lacks camera

    async def on_task(self, task: Task) -> TaskResult:
        """Called when the orchestrator assigns a task."""
        scene = await self.blackboard.read("scene_graph")
        # ... do work ...
        return TaskResult(success=True, data={"found": True})

    async def on_tick(self):
        """Called every tick for always-on agents."""
        ...

    async def on_shutdown(self):
        """Cleanup."""
        ...


# --- Launching the swarm ---

from opencastor import Robot

robot = Robot("config/robot.yaml")
swarm = robot.swarm  # Configured via swarm section in YAML

# Manual control
await swarm.spawn("observer")
await swarm.spawn("navigator")
status = await swarm.status()
# SwarmStatus(agents=[AgentStatus(name="observer", state="running"), ...])

await swarm.kill("navigator")

# --- Querying the Learner ---

learner = swarm.get_agent("learner")
history = await learner.get_improvement_history(last_n=10)
for improvement in history:
    print(f"{improvement.date}: {improvement.description}")
    print(f"  Impact: {improvement.performance_delta:+.1%}")
    print(f"  Status: {improvement.status}")

# Manually trigger analysis
await learner.analyze_episode(episode_id="ep_042")

# Review pending patches
pending = await learner.get_pending_patches()
for patch in pending:
    print(patch.diff)
    await learner.approve_patch(patch.id)  # or .reject_patch()
```

### 11.2 Configuration (YAML)

```yaml
# config/swarm.yaml

swarm:
  enabled: true
  log_dir: "data/swarm_logs"

  blackboard:
    backend: "memory"       # "memory" | "redis" | "sqlite"
    max_entries: 10000
    persistence: true       # Save blackboard to disk on shutdown
    persistence_path: "data/blackboard.db"

  agents:
    learner:
      enabled: true
      always_on: true
      model: "claude-sonnet"           # PM analysis model
      dev_model: "claude-sonnet"       # Patch generation model
      qa_simulation: true              # Use sim for QA (if available)
      auto_apply_config: true          # Auto-apply config patches that pass QA
      auto_apply_code: false           # Code patches always need human review
      consolidation_interval: 50       # ALMA pass every N episodes
      max_retries: 3                   # Sisyphus retry limit
      episode_logging:
        log_sensors: true
        log_actions: true
        log_model_calls: true
        snapshot_interval_ms: 1000     # Sensor snapshot cadence

    observer:
      enabled: true
      always_on: true
      model: "gemini-flash"
      tick_rate_hz: 5                  # Scene graph update frequency
      scout_count: 3                   # Parallel scouts for explore-before-act

    navigator:
      enabled: true
      always_on: false                 # Spawned on demand
      model: "local/mlx-nav"
      replan_on_obstacle: true

    manipulator:
      enabled: true
      always_on: false
      model: "local/mlx-grasp"
      grasp_database: "data/grasps.db"

    communicator:
      enabled: true
      always_on: true
      model: "gemini-flash"
      wake_word: "hey robot"

    guardian:
      enabled: true
      always_on: true
      model: "local/safety-check"      # Must be local — no latency tolerance
      veto_log: "data/guardian_vetoes.log"
      safety_bounds:
        max_velocity_ms: 1.0
        max_arm_force_n: 50
        workspace_bounds: [-2, -2, 0, 2, 2, 2]  # AABB in meters

    orchestrator:
      enabled: true
      always_on: false                 # Only for complex tasks
      model: "claude-sonnet"
      complexity_threshold: 2          # Tasks scoring below skip orchestrator

  resource_budgets:
    default:
      max_cpu_percent: 15
      max_memory_mb: 256
      max_api_calls_per_minute: 10
      max_api_cost_per_hour_usd: 0.50
    overrides:
      learner:
        max_api_cost_per_hour_usd: 1.00
      observer:
        max_cpu_percent: 25
        max_api_calls_per_minute: 30
```

### 11.3 CLI

```bash
# Swarm management
opencastor swarm status                    # Show all agents and their states
opencastor swarm spawn observer            # Manually spawn an agent
opencastor swarm kill navigator            # Kill a running agent
opencastor swarm logs observer --tail 50   # View agent logs

# Learner-specific
opencastor learner status                  # Show learning stats
opencastor learner history --last 20       # Recent improvements
opencastor learner review                  # Interactive: review pending patches
opencastor learner replay ep_042           # Re-analyze a specific episode
opencastor learner consolidate             # Manually trigger ALMA pass
opencastor learner revert patch_017        # Revert an applied patch

# Blackboard
opencastor blackboard dump                 # Print current blackboard state
opencastor blackboard watch scene_graph    # Live-stream a key
opencastor blackboard history planned_path # Version history of a key
```

---

## 12. Cost Analysis

### 12.1 Model Selection Strategy

The swarm is designed to be **cost-conscious by default**:

| Agent | Primary Model | API? | Rationale |
|-------|--------------|------|-----------|
| Guardian | Local rules + local model | **No** | Safety can't depend on network latency |
| Observer | Gemini Flash / local VLM | Minimal | High frequency, low complexity per call |
| Navigator | Local MLX model | **No** | Needs fast replanning |
| Manipulator | Local MLX model | **No** | Needs fast grasp evaluation |
| Communicator | Gemini Flash | Yes | NLU needs capability, but calls are infrequent |
| Orchestrator | Claude Sonnet | Yes | Complex reasoning, but only for complex tasks |
| Learner (PM) | Claude Sonnet | Yes | Root-cause analysis needs strong reasoning |
| Learner (Dev) | Claude Sonnet | Yes | Patch generation needs code capability |
| Learner (QA) | Local + sim | Minimal | Mostly rule-based and simulation |

### 12.2 Estimated Costs

Assuming a moderately active home robot (50 tasks/day):

| Component | Calls/Day | Avg Tokens | Cost/Day (est.) |
|-----------|-----------|------------|-----------------|
| Orchestrator | 20 | 2K in + 1K out | $0.18 |
| Communicator | 50 | 500 in + 200 out | $0.08 |
| Learner PM | 50 | 3K in + 1K out | $0.30 |
| Learner Dev | 25 | 2K in + 1K out | $0.15 |
| Observer (API) | 100 | 1K in + 200 out | $0.10 |
| **Total** | | | **~$0.81/day** |

With aggressive local model usage and API only for complex cases: **~$0.30–0.50/day**.

### 12.3 Cost Controls

- Resource budgets (Section 7.2) enforce hard caps
- Agents fall back to local models when API budget exhausted
- Learner batches episodes and runs consolidation during off-peak hours
- Simple tasks skip the orchestrator entirely

---

## 13. Open Questions

These are areas where we need community input and further design work:

### Consensus Mechanisms
- When Navigator and Observer disagree (e.g., "path is clear" vs "I see an obstacle"), who wins?
- **Current proposal**: Guardian arbitrates, defaulting to the more conservative option
- **Alternative**: Weighted voting based on agent confidence scores
- **Needs**: Real-world testing to tune the mechanism

### Failure Modes
- What happens when the Learner's "improvements" make things worse over time? (Gradient drift)
- **Current proposal**: Regression suite in QA; periodic full revert-to-baseline test
- **Needs**: Long-duration testing (weeks of continuous operation)

### Security
- Can a compromised agent poison the blackboard?
- **Current proposal**: Guardian validates all blackboard writes; agents have write permissions only on their own keys
- **Needs**: Formal threat model

### Determinism
- Swarm behavior is inherently non-deterministic (async agents, LLM randomness)
- How do we test and debug?
- **Current proposal**: Full episode recording + replay with seed pinning
- **Needs**: Tooling for deterministic replay

### Agent Priorities During Resource Contention
- When CPU/memory is tight, which agents get preempted?
- **Current proposal**: Guardian and Layer 0 never preempted; Learner preempted first (it's background work); Observer and Navigator share remaining budget
- **Needs**: Priority scheduler implementation

### Human-in-the-Loop Granularity
- How much should humans be in the loop for the Learner?
- **Current proposal**: Config tuning auto-applies; behavior rules notify; code patches require approval
- **Needs**: User studies to find the right balance between autonomy and oversight

---

## Contributing

This is a living proposal. We want your input:

- **Discord**: `#agent-swarm` channel
- **GitHub Issues**: Tag with `layer-3-swarm`
- **PRs**: Start with the `SwarmAgent` base class and `Blackboard` — those are the foundation everything else builds on

Priority areas for contributors:
1. **Episode logging format** — what does a good episode trace look like?
2. **QA simulation harness** — connecting the Learner's QA stage to Gazebo/Isaac Sim
3. **Local model benchmarks** — which MLX/HF models work best for each agent role?
4. **Behavior rule engine** — DSL for if/then rules that Layer 1 can evaluate at 500ms

---

*"The measure of intelligence is the ability to change." — Albert Einstein*

*A robot that improves itself is not just a tool. It's a partner that grows with you.*

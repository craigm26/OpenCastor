# Agent Orchestration Operator Manual

OpenCastor includes a multi-agent orchestration system that runs alongside the
perception-action loop. Agents add higher-level reasoning, specialization, and
coordination on top of the core tiered brain.

## Overview

**Agents vs direct provider calls**

| | Provider call | Agent |
|---|---|---|
| Scope | Single request → response | Persistent task with memory |
| Lifecycle | Stateless, per frame | Spawned, runs until goal met |
| Decision source | Tiered brain | Specialist reasoning + state |
| Example | "what action to take now?" | "patrol this room until done" |

**Architecture layers**

```
[ Messaging Channels ]
        │
[ API Gateway ]
        │
[ Tiered Brain (reactive → fast → planner) ]
        │
[ AgentRegistry ]   ←── agents register here
    ├── ObserverAgent
    ├── NavigatorAgent
    └── SpecialistAgents (Scout, Responder, Dock, Manipulator, TaskPlanner)
        │
[ SharedState ]     ←── agents share observations and goals
```

Agent source: `castor/agents/`, `castor/specialists/`

## Enabling Agents

### Via RCAN config

```yaml
agent:
  provider: anthropic
  model: claude-opus-4-6

# Enable specific agents
agents:
  observer:
    enabled: true
  navigator:
    enabled: true
  specialists:
    scout:
      enabled: true
      grid_size: 0.5          # metres per grid cell
    responder:
      enabled: true
      priority_keywords:      # messages that skip the queue
        - "stop"
        - "emergency"
        - "help"
    dock:
      enabled: false          # requires known charger position
      charger_position: [1.2, 0.8]   # [x, y] in metres
    manipulator:
      enabled: false          # requires gripper hardware
      gripper_channel: 15     # PCA9685 channel
    task_planner:
      enabled: true
      max_steps: 10
```

### Via CLI flag

```bash
castor run --config robot.rcan.yaml --agents observer,navigator,scout
```

### Via environment variable

```bash
OPENCASTOR_AGENTS=observer,scout castor run --config robot.rcan.yaml
```

## Specialist Reference

### ObserverAgent (`castor/agents/observer.py`)

Continuously captures and annotates the visual field. Feeds structured
observations into `SharedState` for other agents to consume.

| Config key | Default | Description |
|---|---|---|
| `agents.observer.enabled` | `false` | Enable the observer |
| `agents.observer.frame_interval` | `1.0` | Seconds between observations |

### NavigatorAgent (`castor/agents/navigator.py`)

Executes movement goals using waypoint tracking. Receives destination from
`SharedState.goal` and issues motor commands until arrival.

| Config key | Default | Description |
|---|---|---|
| `agents.navigator.enabled` | `false` | Enable the navigator |
| `agents.navigator.arrival_threshold_m` | `0.1` | Stop when within this distance |

### ScoutAgent (`castor/specialists/scout.py`)

Performs systematic area exploration using a grid-based sweep pattern.
Marks explored cells in `SharedState.map`.

```bash
# Trigger a scout mission via channel message
"scout the living room"
```

| Config key | Default | Description |
|---|---|---|
| `agents.specialists.scout.enabled` | `false` | Enable scout |
| `agents.specialists.scout.grid_size` | `0.5` | Grid cell size in metres |

### ResponderAgent (`castor/specialists/responder.py`)

Handles urgent channel messages with priority queue bypass. Any message
containing a `priority_keyword` is routed here before the main queue.

| Config key | Default | Description |
|---|---|---|
| `agents.specialists.responder.enabled` | `true` | Enable responder |
| `agents.specialists.responder.priority_keywords` | `["stop","emergency"]` | Trigger words |

### DockAgent (`castor/specialists/dock.py`)

Navigates to the charging station when battery is low or on command.
Requires the charger's position to be configured.

```bash
# Trigger manually
"go to charger"
"dock now"
```

| Config key | Required | Description |
|---|---|---|
| `agents.specialists.dock.enabled` | — | Enable dock |
| `agents.specialists.dock.charger_position` | ✅ | `[x, y]` in metres |

### ManipulatorAgent (`castor/specialists/manipulator.py`)

Controls a gripper or robotic arm for pick-and-place tasks. Requires a
PCA9685 channel configured for the servo.

| Config key | Required | Description |
|---|---|---|
| `agents.specialists.manipulator.enabled` | — | Enable manipulator |
| `agents.specialists.manipulator.gripper_channel` | ✅ | PCA9685 channel (0–15) |

### TaskPlannerAgent (`castor/specialists/task_planner.py`)

Decomposes multi-step natural language goals into ordered sub-tasks and
delegates them to other agents. Uses the Layer 3 planner brain (cloud LLM).

```bash
# Example: complex goal via WhatsApp
"Go to the kitchen, pick up the ball, bring it to the living room"
# TaskPlanner breaks this into: navigate → pick up → navigate → place
```

| Config key | Default | Description |
|---|---|---|
| `agents.specialists.task_planner.enabled` | `false` | Enable planner |
| `agents.specialists.task_planner.max_steps` | `10` | Maximum sub-tasks per goal |

## Monitoring Agents

### CLI

```bash
# List all active agents and their status
castor agents

# Pause a specific agent
castor agents pause scout

# Resume a paused agent
castor agents resume scout

# Kill an agent (it will restart if auto_restart is enabled)
castor agents kill task_planner
```

### Streamlit Dashboard

The CastorDash dashboard (`castor dashboard`) shows an **Agent Activity** panel
with live status, current goal, and recent actions for each active agent.

### Log output

Agent events use structured logging under `OpenCastor.Agent.<Name>`:

```
2026-02-21 14:32:01 INFO  OpenCastor.Agent.Scout  Starting grid sweep (16 cells)
2026-02-21 14:32:08 INFO  OpenCastor.Agent.Scout  Cell (0,0) explored
2026-02-21 14:32:15 WARN  OpenCastor.Agent.Scout  Obstacle at (1,0) — replanning
```

Filter with:

```bash
castor logs --filter Agent
```

## Writing a Custom Agent

1. Create `castor/agents/my_agent.py`:

```python
from castor.agents.base import BaseAgent
from castor.agents.shared_state import SharedState

class MyAgent(BaseAgent):
    name = "my_agent"

    def __init__(self, config: dict, state: SharedState):
        super().__init__(config, state)
        self._enabled = config.get("agents", {}).get("my_agent", {}).get("enabled", False)

    async def run(self):
        """Main agent loop. Override this method."""
        while self._running:
            obs = self.state.latest_observation
            if obs:
                # Do something with the observation
                self.state.set_goal("target_x", obs.get("object_x"))
            await self._sleep(1.0)
```

2. Register in `castor/registry.py`:

```python
from castor.agents.my_agent import MyAgent
registry.register_agent("my_agent", MyAgent)
```

3. Enable in RCAN config:

```yaml
agents:
  my_agent:
    enabled: true
```

See `castor/agents/base.py` for the full `BaseAgent` API.

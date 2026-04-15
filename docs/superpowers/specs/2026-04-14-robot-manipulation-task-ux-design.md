# Robot Manipulation Task UX Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable a user to type a natural-language pick-and-place instruction in the Flutter app chat, see a live visual plan of what the robot is about to do, and watch real-time step-by-step progress as the arm executes.

**Architecture:** Natural language → bridge intent detector → `/api/arm/pick_place` → Firestore task doc (written per phase) → Flutter `TaskProgressCard` (live stream). A robot-level `task_execution: ask | automatic` toggle controls whether the user must confirm before the arm moves.

**Tech Stack:** Python (FastAPI gateway, bridge), Firebase Firestore, Flutter/Dart (Riverpod), RCAN YAML config

---

## 1. RCAN Config Addition

Add `task_execution` field to the robot config schema and `bob.rcan.yaml`:

```yaml
task_execution: ask   # ask | automatic (default: ask)
```

- `ask`: bridge creates task doc with `status: pending_confirmation`; waits for Flutter to write `confirmed: true` before dispatching to gateway
- `automatic`: bridge dispatches to gateway immediately; task doc is created with `status: running`

Bridge reads this from `config.get("task_execution", "ask")`.

---

## 2. Firestore Task Document Schema

Collection path: `robots/{rrn}/tasks/{task_id}`

```json
{
  "task_id": "abc123",
  "cmd_id": "c97c88ee",
  "type": "pick_place",
  "target": "red lego brick",
  "destination": "bowl",
  "status": "pending_confirmation | running | complete | failed | cancelled",
  "phase": "SCAN | APPROACH | GRASP | PLACE",
  "frame_b64": "<base64 JPEG from detection>",
  "detected_objects": ["red lego brick", "bowl"],
  "error": null,
  "created_at": "<ISO8601>",
  "updated_at": "<ISO8601>",
  "confirmed": false
}
```

- `frame_b64` is written once at SCAN phase; subsequent phase updates omit it to save bandwidth
- `detected_objects` is populated from `/api/detection/latest` before dispatching pick_place
- Phase updates only write `phase`, `status`, `updated_at` (partial update via Firestore `.update()`)

---

## 3. Gateway Changes (`castor/api.py`)

### 3a. `_PickPlaceRequest` — add `task_id` and Firestore writer

```python
class _PickPlaceRequest(BaseModel):
    target: str = "red lego brick"
    destination: str = "bowl"
    max_vision_steps: int = 4
    task_id: Optional[str] = None        # NEW: Firestore task doc to update
    firebase_project: Optional[str] = None  # NEW: project for task doc writes
```

### 3b. Phase progress writes

In `arm_pick_place`, after each `_vision_plan` call succeeds and before `_exec`:

```python
_write_task_phase(task_id, firebase_project, phase="APPROACH", status="running")
# ... exec ...
_write_task_phase(task_id, firebase_project, phase="APPROACH", status="complete")
```

`_write_task_phase` is a best-effort helper (never raises; swallows Firestore errors).

### 3c. SCAN phase

Before the APPROACH plan, capture detection frame and write the initial task state:

```python
_write_task_scan(task_id, firebase_project, frame_b64, detected_objects)
```

This writes `phase: SCAN, status: running, frame_b64: ..., detected_objects: [...]`.

---

## 4. Bridge Changes (`castor/cloud/bridge.py`)

### 4a. Intent detector

```python
import re

_PICK_PLACE_RE = re.compile(
    r"(?:pick|grab|take|get)\s+(?P<target>.+?)\s+"
    r"(?:into|in|to|onto|and\s+place\s+(?:it\s+)?(?:into|in))\s+"
    r"(?P<destination>.+)",
    re.IGNORECASE,
)

def _detect_pick_place_intent(instruction: str) -> tuple[str, str] | None:
    """Return (target, destination) if instruction is a pick-and-place, else None."""
    m = _PICK_PLACE_RE.search(instruction.strip())
    if m:
        return m.group("target").strip(), m.group("destination").strip()
    return None
```

### 4b. Routing in `_dispatch_to_gateway`

Before dispatching to `/cap/chat`, check intent:

```python
pick_place = _detect_pick_place_intent(instruction)
if pick_place:
    target, destination = pick_place
    return self._dispatch_pick_place(target, destination, doc)
```

### 4c. `_dispatch_pick_place`

```python
def _dispatch_pick_place(self, target: str, destination: str, doc: dict) -> dict:
    task_id = str(uuid.uuid4())[:8]
    task_execution = self._config.get("task_execution", "ask")

    # Write initial task doc to Firestore
    self._write_task_doc(task_id, target, destination, doc,
                         status="pending_confirmation" if task_execution == "ask" else "running")

    if task_execution == "ask":
        # Poll Firestore for confirmed=true (max 120s, poll every 2s)
        if not self._wait_for_confirmation(task_id, timeout_s=120):
            self._update_task_doc(task_id, {"status": "cancelled", "error": "user_timeout"})
            return {"status": "cancelled"}

    # Dispatch to gateway pick_place endpoint
    resp = httpx.post(
        f"{self.gateway_url}/api/arm/pick_place",
        json={"target": target, "destination": destination,
              "task_id": task_id,
              "firebase_project": self.firebase_project},
        headers={"Authorization": f"Bearer {self.gateway_token}"},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()
```

### 4d. `_wait_for_confirmation`

Uses a `threading.Event` + Firestore `on_snapshot` listener on `robots/{rrn}/tasks/{task_id}`. The listener sets the event when `confirmed == true`. The main thread waits on the event with the given timeout, then detaches the listener. Returns `True` if confirmed within timeout, `False` otherwise. This avoids busy-polling.

### 4e. Bridge reads `task_execution` from config

```python
self._config = config  # full config dict already available on self
```

---

## 5. Flutter Changes (`opencastor-client`)

### 5a. `RobotCommand` model — add `task_id`

In `lib/data/models/command.dart`:

```dart
final String? taskId;  // links to robots/{rrn}/tasks/{taskId}
```

Populated from Firestore field `task_id` when present.

### 5b. `TaskDoc` model — `lib/data/models/task_doc.dart`

```dart
class TaskDoc {
  final String taskId;
  final String type;          // "pick_place"
  final String target;
  final String destination;
  final String status;        // pending_confirmation | running | complete | failed | cancelled
  final String phase;         // SCAN | APPROACH | GRASP | PLACE
  final String? frameB64;     // base64 JPEG, present on SCAN
  final List<String> detectedObjects;
  final String? error;
  final bool confirmed;

  // fromFirestore factory, copyWith, etc.
}
```

### 5c. `taskDocProvider` — `lib/data/repositories/task_doc_repository.dart`

```dart
final taskDocProvider = StreamProvider.family<TaskDoc?, ({String rrn, String taskId})>(
  (ref, args) => FirebaseFirestore.instance
    .collection('robots')
    .doc(args.rrn)
    .collection('tasks')
    .doc(args.taskId)
    .snapshots()
    .map((s) => s.exists ? TaskDoc.fromFirestore(s) : null),
);
```

### 5d. `TaskProgressCard` widget — `lib/ui/widgets/task_progress_card.dart`

Renders inline in the chat list when `command.taskId != null`:

```
┌─────────────────────────────────┐
│ 🦾 Pick & Place                 │
│ red lego brick → bowl           │
│                                 │
│ [scene snapshot if available]   │
│ Detected: red lego brick, bowl  │
│                                 │
│ ✓ SCAN                          │
│ ▶ APPROACH  (spinning indicator)│
│ ○ GRASP                         │
│ ○ PLACE                         │
│                                 │
│ [Run ▶]  ← only in ask mode     │
└─────────────────────────────────┘
```

- Phase states: `○` pending, `▶` running (with `CircularProgressIndicator`), `✓` complete, `✗` failed
- `[Run ▶]` button writes `confirmed: true` to the task doc in Firestore
- `[Run ▶]` hidden when `status != pending_confirmation`
- Scene snapshot decoded from `frameB64` and displayed as a rounded `Image.memory`
- Card uses `Card` + `ListTile` + `Column` — no custom painting

### 5e. `chat_bubble.dart` — render `TaskProgressCard` when `taskId` present

```dart
if (command.taskId != null)
  TaskProgressCard(rrn: rrn, taskId: command.taskId!)
else
  // existing bubble rendering
```

### 5f. Robot settings screen — `task_execution` toggle

In robot settings (or LAN settings card), add:

```dart
SwitchListTile(
  title: const Text('Task execution'),
  subtitle: Text(taskExecution == 'ask' ? 'Ask before executing' : 'Automatic'),
  value: taskExecution == 'automatic',
  onChanged: (v) => _updateTaskExecution(v ? 'automatic' : 'ask'),
),
```

Writes to Firestore `robots/{rrn}` field `task_execution`. Bridge reads it on startup (already reads robot doc for firebase_uid etc.).

---

## 6. Error Handling

| Condition | Gateway behaviour | Bridge behaviour | Flutter card |
|-----------|------------------|------------------|--------------|
| No arm driver | 503 | writes `status: failed, error: no_arm` | "No arm available" chip |
| Detection empty | Continues (brain tries) | writes `detected_objects: []` | "Nothing detected — run anyway?" (ask mode) |
| Phase timeout (30s) | Returns partial log | bridge writes `status: failed, error: timeout, phase: X` | Shows failed phase chip |
| ESTOP received | Arm halts immediately | writes `status: cancelled` | "Stopped" banner on card |
| Firestore write fails | Continues arm execution | logs warning, swallows error | Card may not update (best-effort) |
| User doesn't confirm (120s) | Not called | writes `status: cancelled, error: user_timeout` | Card shows "Timed out" |

---

## 7. Testing

- **Unit**: `_detect_pick_place_intent` correctly parses: "pick the red lego brick into the bowl", "grab lego and place it in the bowl", "take the cube to the container"
- **Unit**: `_detect_pick_place_intent` returns None for: "move forward", "what do you see", "hi"
- **Unit**: bridge routes pick-and-place intent to `_dispatch_pick_place`, not `/cap/chat`
- **Unit**: `_wait_for_confirmation` returns True on confirmed, False on timeout
- **Integration**: `arm_pick_place` writes correct task phase sequence to Firestore (mock Firestore client)
- **Flutter widget test**: `TaskProgressCard` renders all 4 phase states correctly from mock stream
- **Flutter widget test**: `[Run ▶]` button visible only when `status == pending_confirmation`

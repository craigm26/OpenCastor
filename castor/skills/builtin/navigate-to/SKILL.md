---
name: navigate-to
description: >
  Use when the user asks the robot to move, go somewhere, drive to a location,
  come closer, back up, or navigate to a named place or person. Triggers on
  "go to", "move to", "drive to", "navigate to", "come here", "back up",
  "turn around", "follow me".
version: "1.0"
requires:
  - control
  - drive
consent: required
tools:
  - get_camera_frame
  - get_distance
  - get_telemetry
  - move
max_iterations: 8
---

# Navigate To Skill

Move the robot to a target location safely using sensor feedback.

## Protocol 66 — MANDATORY

**You MUST request user consent before ANY movement.**
Do NOT call `move()` until the user has explicitly confirmed ("yes", "confirm", "proceed", "go ahead").
If consent is not yet granted: describe the planned movement and ask for confirmation. Stop there.

## Steps

### 1. Pre-flight check
- Call `get_distance()` — if distance < 0.3m, report obstacle and abort
- Call `get_telemetry()` — check battery > 10%, motors healthy
- If any check fails: report the issue and do NOT proceed

### 2. Request consent
Describe the intended movement clearly:
"I plan to [move forward X metres / turn left / navigate to table]. Shall I proceed?"
Wait for explicit confirmation. Do NOT continue without it.

### 3. Execute (after consent)
- Move in increments: 0.3–0.5m steps maximum
- After each step: call `get_distance()` to check for new obstacles
- If obstacle detected (< 0.4m): stop immediately, report position
- Continue until target reached or obstacle prevents further movement

### 4. Confirm arrival
Report final position and any obstacles encountered.

## Safety rules (always enforced)
- NEVER move if distance < 0.3m in direction of travel
- NEVER exceed speed 0.3 m/s unless explicitly asked
- ALWAYS stop on any sensor error — do not assume path is clear
- If unsure about surroundings: ask for camera confirm before moving

## Example

User: "Go to the table"
→ `get_distance()` → 1.2m clear
→ "I plan to drive forward approximately 0.8 metres toward the table. Shall I proceed?"
→ User: "yes"
→ `move(linear=0.3)` → check → `move(linear=0.3)` → check → arrive → report

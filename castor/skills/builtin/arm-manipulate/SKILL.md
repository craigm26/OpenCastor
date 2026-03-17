---
name: arm-manipulate
description: >
  Use when the user asks the robot to pick up, grab, grasp, place, move, hand
  over, or manipulate a physical object using the robotic arm or gripper.
  Triggers on "pick up", "grab", "pick", "place", "put X on Y", "hand me",
  "move the X", "get the X", "assemble", "sort".
version: "1.0"
requires:
  - control
  - vision
  - gripper
consent: required
tools:
  - get_camera_frame
  - get_distance
  - move
  - grip
max_iterations: 12
---

# Arm Manipulate Skill

Pick up, place, or manipulate objects using the robotic arm and gripper.

## Protocol 66 — MANDATORY

**You MUST request user consent before ANY arm or gripper movement.**
Do NOT call `move()` or `grip()` until the user has explicitly confirmed.
If consent is not yet granted: describe the plan and ask. Stop there.

## Steps

### 1. Visual confirmation
- Call `get_camera_frame()` — locate the target object in the scene
- If object not clearly visible: ask user to adjust placement or camera angle
- Call `get_distance()` — confirm target is within arm reach (typically < 0.6m)

### 2. Pre-grasp assessment
Describe what you see and your intended approach:
"I can see [object description] approximately [distance]m away. I plan to [approach + grasp plan]. Shall I proceed?"

### 3. Request consent — WAIT for "yes" / "confirm" / "go ahead"

### 4. Approach (after consent)
- `move()` to pre-grasp position (slow, incremental)
- `get_distance()` check — stop if < 0.05m from object unexpectedly

### 5. Grasp
- `grip("open")` — open gripper fully
- `move()` to grasp position (slow)
- `grip("close")` — close gripper
- `get_camera_frame()` — visual confirmation of grasp

### 6. Transport
- Lift / move to target position
- `move()` incrementally, checking clearance

### 7. Release
- Position above target location
- `grip("open")` — release object
- `move()` retreat to safe position
- Confirm completion

## Safety rules
- Maximum gripper force: use default — never override force limits
- Always visual-confirm before closing gripper (avoid crushing objects)
- If grasp fails (camera shows object not held): stop and report
- Never move arm at high speed near humans

## Example

User: "Pick up the red Lego brick"
→ `get_camera_frame()` → see brick at 0.4m
→ `get_distance()` → 0.38m clear
→ "I can see a red 2x4 Lego brick about 0.4m away on the table. I plan to approach, open the gripper, grasp it, and lift it. Shall I proceed?"
→ User: "yes"
→ `grip("open")` → `move(approach)` → `grip("close")` → `get_camera_frame()` → confirm → report

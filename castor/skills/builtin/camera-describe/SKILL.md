---
name: camera-describe
description: >
  Use when the user asks what the robot sees, requests a photo or snapshot,
  wants a description of the surroundings, asks about objects in view, or
  says "look at X", "what's in front of you", "describe your environment".
version: "1.0"
requires:
  - vision
consent: none
tools:
  - get_camera_frame
  - get_distance
max_iterations: 2
---

# Camera Describe Skill

Capture and describe what the robot's camera sees.

## Steps

1. Call `get_camera_frame()` to capture a JPEG snapshot
2. If depth sensor available, call `get_distance()` to get nearest obstacle distance
3. Describe the scene naturally: objects, colours, layout, notable features
4. Include distance context: "The nearest object is approximately X metres away"
5. Note any obstacles within 1 metre as a safety observation

## If camera unavailable

Return: "My camera is not currently available. I can describe my sensor readings instead if helpful."

## Guidelines

- Be specific about what you see — avoid vague descriptions like "some objects"
- Mention spatial relationships: "to the left", "in the centre", "in the background"
- If asked about a specific object, focus your description on finding it
- Do not fabricate visual content if the frame is blank or unavailable

## Example

User: "What do you see?"
→ `get_camera_frame()` → `get_distance()`
→ "I can see a wooden table in the foreground with several Lego bricks scattered on it. The nearest object is approximately 0.4 metres away. In the background there's a white wall."

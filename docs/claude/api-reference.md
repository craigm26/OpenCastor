# OpenCastor API Reference

Complete reference for all endpoints in `castor/api.py`.

## Authentication

Auth layers checked in order:
1. **Multi-user JWT** — `JWT_SECRET` + `OPENCASTOR_USERS` env var (`castor/auth_jwt.py`)
2. **RCAN JWT** — `OPENCASTOR_JWT_SECRET`
3. **Static bearer** — `OPENCASTOR_API_TOKEN`
4. **Open** — no auth required

Roles: `admin(3) > operator(2) > viewer(1)`
- Viewers get 403 on `POST /api/command`
- Operators get 403 on `POST /api/config/reload`

Error responses use `{"error": "...", "code": "HTTP_NNN", "status": NNN}` (not `{"detail": "..."}`).

---

## Health & Status

### GET /health
Docker HEALTHCHECK endpoint. Returns uptime, brain status, driver status, active channels.

### GET /api/status
Full runtime status including active providers and channels.

Response now includes:
- `brain_primary` — `{provider, model}` from RCAN config
- `brain_secondary` — list of `{provider, model, tags}` secondary models
- `brain_active_model` — model name string currently handling requests
- `speaking` — `true` while TTS audio is playing
- `caption` — current TTS sentence being spoken (empty when silent)
- `channels_active` — list of active channel names

### GET /api/fs/estop
Returns `{estopped: bool, proc_status: str, last_denial: str}`.
`last_denial` is a human-readable reason for the most recent safety layer write rejection.

---

## Command & Control

### POST /api/command
Send a natural language instruction to the brain. Rate limited: 5 req/s/IP.

Request:
```json
{"instruction": "go forward slowly"}
```
Response:
```json
{
  "raw_text": "Moving forward at low speed.",
  "action": {"type": "move", "linear": 0.3, "angular": 0.0},
  "model_used": "gemini-2.5-flash"
}
```
`model_used` identifies which provider/model generated the response. Also logs `Brain replied via <model> in <N> ms`.

### POST /api/command/stream
NDJSON streaming of LLM tokens. Uses `think_stream()`; falls back to `think()` if streaming unavailable.
Rate limited: 5 req/s/IP.

### POST /api/action
Direct motor command, bypasses the brain/LLM entirely.

Request:
```json
{"action": {"speed": 0.5, "direction": "left"}}
```

### POST /api/stop
Emergency stop. Immediately halts all motors.

### POST /api/estop/clear
Clear the emergency stop state. Requires `CAP_SAFETY_OVERRIDE` or `SAFETY_OVERRIDE` capability.

---

## Driver

### GET /api/driver/health
Driver health check. Returns 503 if no driver is configured.

Response:
```json
{"ok": true, "mode": "hardware", "error": null, "driver_type": "PCA9685Driver"}
```

---

## Learner / Sisyphus

### GET /api/learner/stats
Sisyphus loop statistics. Returns `{"available": false}` when not running.

Response:
```json
{
  "available": true,
  "episodes_analyzed": 42,
  "patches_applied": 7,
  "avg_duration_ms": 1234.5,
  "last_run": "2026-02-22T10:00:00Z"
}
```

### GET /api/learner/episodes
Recent episodes from EpisodeStore. Query param: `?limit=N` (max 100).

### POST /api/learner/episode
Submit a new episode. Query param: `?run_improvement=true` to trigger improvement loop.

Request:
```json
{"observation": "...", "action": {...}, "outcome": "success", "reward": 1.0}
```

---

## Command History

### GET /api/command/history
Last N instruction→thought→action pairs from a ring buffer (maxlen=50).
Query param: `?limit=N`

---

## Virtual Filesystem

### POST /api/fs/read
Read a VFS path.

Request: `{"path": "/etc/config/robot_name"}`

### POST /api/fs/write
Write to a VFS path. Requires appropriate capability.

Request: `{"path": "/dev/motor/speed", "value": 0.5}`

### GET /api/fs/ls
Directory listing. Query param: `?path=/dev`

### GET /api/fs/tree
Full tree view from a path. Query param: `?path=/`

### GET /api/fs/proc
Runtime introspection snapshot (read-only `/proc` equivalent).

### GET /api/fs/memory
Query memory stores. Query param: `?tier=episodic|semantic|procedural`

### GET /api/fs/permissions
Dump the full permission table (ACLs and capabilities).

---

## Authentication & Security

### POST /api/auth/token
Issue a RCAN JWT token.

Request: `{"principal": "operator1", "role": "operator", "scopes": ["motor_write"]}`

### GET /api/auth/whoami
Return the authenticated principal's identity.

### GET /api/audit
Audit log of work orders, approvals, and denials.

### GET /api/rbac
RBAC roles and principals table.

---

## Streaming

### GET /api/stream/mjpeg
MJPEG live camera stream. Max 3 concurrent clients (`OPENCASTOR_MAX_STREAMS`).
Query param: `?camera=id` for multi-camera setups.

### POST /api/stream/webrtc/offer
WebRTC SDP offer/answer exchange via aiortc. ICE config from RCAN `network.ice_servers`.
Falls back to MJPEG if aiortc not installed.

---

## Metrics & Runtime Control

### GET /api/metrics
Prometheus text format metrics (counters, gauges, histograms via `MetricsRegistry`).
Stdlib-only implementation — no prometheus_client dependency.

### POST /api/runtime/pause
Pause the perception-action loop. Sets VFS `/proc/paused` flag.

### POST /api/runtime/resume
Resume the perception-action loop.

### GET /api/runtime/status
Loop running/paused state and loop count.

Response:
```json
{"running": true, "paused": false, "loop_count": 1234}
```

### POST /api/config/reload
Hot-reload `robot.rcan.yaml` without restarting the gateway. Requires admin role.

### GET /api/provider/health
Brain provider health check.

Response:
```json
{"ok": true, "latency_ms": 234.5, "error": null, "usage_stats": {...}}
```

---

## Episode Memory

### GET /api/memory/episodes
Recent episodes from SQLite store. Query param: `?limit=N` (max 100).

### GET /api/memory/export
Export all episodes as JSONL download.

### DELETE /api/memory/episodes
Clear all episode memory.

### POST /api/memory/replay/{id}
Replay a stored episode through the active driver.

---

## Usage Tracking

### GET /api/usage
Token/cost summary from UsageTracker. Returns today's and all-time usage per provider.

Response:
```json
{
  "today": {"google": {"tokens": 12000, "cost_usd": 0.024}},
  "all_time": {"google": {"tokens": 450000, "cost_usd": 0.90}}
}
```

---

## Depth / Vision (OAK-D)

### GET /api/depth/frame
JPEG image with JET colormap depth overlay (45% opacity).
Returns `{"available": false}` if no depth sensor connected.

### GET /api/depth/obstacles
Obstacle zone distances.

Response:
```json
{"left_cm": 45.2, "center_cm": 12.1, "right_cm": 67.8, "nearest_cm": 12.1}
```

---

## Real-time Telemetry

### WS /ws/telemetry
WebSocket, 5 Hz JSON push.
Auth: `?token=<bearer_token>` query parameter.

Payload:
```json
{
  "loop_latency_ms": 234.5,
  "battery_v": 11.8,
  "provider": "google",
  "obstacles": {"nearest_cm": 25.0}
}
```

---

## Voice

### POST /api/voice/listen
Trigger one STT capture via `Listener`. Returns transcribed text or error.

Response: `{"text": "go forward"}` or `{"error": "No speech detected"}`

### POST /api/audio/transcribe
Multipart upload of audio file for transcription.

Response:
```json
{"text": "turn left", "engine": "whisper", "duration_ms": 450}
```
Returns 503 if no voice engine available, 422 on invalid audio format.

---

## Navigation

### POST /api/nav/waypoint
Dead-reckoning navigation move via `WaypointNav`. Returns immediately with a `job_id`; poll `/api/nav/status` for completion.

Request:
```json
{"distance_m": 1.5, "heading_deg": 90.0, "speed": 0.6}
```

**Brain-triggered nav**: When the AI brain produces `{"type":"nav_waypoint","distance_m":float,"heading_deg":float}` in its action JSON (e.g. from a "move forward 1 inch" WhatsApp command), `_execute_action()` in `api.py` also dispatches to `WaypointNav` via a daemon thread — same dead-reckoning logic, no REST round-trip needed.

### GET /api/nav/status
Current navigation job status.

Response:
```json
{"running": true, "job_id": "nav-001", "distance_m": 1.5, "heading_deg": 90.0}
```

---

## Behaviors

### POST /api/behavior/run
Start a named YAML behavior sequence.

Request:
```json
{"behavior_file": "behaviors/patrol.yaml", "behavior_name": "patrol_loop"}
```

### POST /api/behavior/stop
Stop the currently running behavior.

### GET /api/behavior/status
Current behavior status.

Response:
```json
{"running": true, "current_step": 3, "behavior_name": "patrol_loop"}
```

---

## Fleet Management

### GET /api/fleet
List all discovered robots. Discovers via mDNS `_rcan._tcp`.

Response:
```json
{"robots": [{"ruri": "rcan://opencastor.alex.a1b2", "name": "alex", "ip": "192.168.68.91", "status": "online", "last_seen": "..."}]}
```

### POST /api/fleet/{ruri}/command
Proxy a command to a remote robot via RCAN bearer token.

### GET /api/fleet/{ruri}/status
Proxy a status fetch from a remote robot.

---

## Guardian

### POST /api/guardian/report
Submit a safety report from a GuardianAgent. Used internally by the multi-agent framework.

---

## Multi-user JWT Auth

### POST /auth/token
Exchange credentials for a JWT token.

Request: `{"username": "operator1", "password": "sha256_hash"}`

Response:
```json
{"access_token": "eyJ...", "token_type": "bearer", "role": "operator"}
```

### GET /auth/me
Return the current JWT user's identity.

Response: `{"username": "operator1", "role": "operator"}`

---

## Web Setup Wizard

### GET /setup
Serve the web-based configuration wizard UI (HTML page).

### POST /setup/api/session/start
Start a setup-v3 resumable session. Returns `session_id`, stage, device probe, and timeline.

### GET /setup/api/session/{session_id}
Fetch current session state (selections, stage, checks, timeline).

### POST /setup/api/session/{session_id}/select
Persist stage selections.

Request example:
```json
{"stage":"stack","values":{"stack_id":"ollama_universal_local","provider":"ollama"}}
```

### POST /setup/api/session/{session_id}/resume
Resume a previously interrupted session.

### GET /setup/api/catalog
Return setup catalog data shared by CLI/web flows:
- provider list + order
- model/profile menus
- curated stack profiles
- hardware presets
- detected device info
- Apple profile presets

<!-- SETUP_CATALOG:BEGIN -->
- Stack IDs: `apple_native`, `mlx_local_vision`, `ollama_universal_local`
- Apple profile IDs: `apple-balanced`, `apple-creative`, `apple-tagging`
<!-- SETUP_CATALOG:END -->

Response highlights:
```json
{
  "stack_profiles": [
    {"id":"apple_native","provider":"apple","model_profile_id":"apple-balanced"},
    {"id":"mlx_local_vision","provider":"mlx","model_profile_id":"mlx-community/Qwen2.5-VL-7B-Instruct-4bit"},
    {"id":"ollama_universal_local","provider":"ollama","model_profile_id":"llava:13b"}
  ]
}
```

### POST /setup/api/preflight
Run provider preflight checks before config generation.

Request example:
```json
{"provider":"apple","model_profile":"apple-balanced","auto_install":true}
```

Response includes:
- `ok`
- `reason`
- `issues`
- `actions`
- `checks`
- `fallback_stacks`
- `auto_install` (when attempted)

### POST /setup/api/remediate
Execute one remediation action (explicit consent required for command execution actions).

Request example:
```json
{"remediation_id":"install_apple_sdk","consent":true,"session_id":"..."}
```

### POST /setup/api/verify-config
Dry-run verification gate before saving config:
- provider init + `health_check`
- driver viability checks
- channel credential sanity

Request example:
```json
{"robot_name":"MyRobot","provider":"apple","model":"apple-balanced","preset":"rpi_rc_car","allow_warnings":false}
```

### POST /setup/api/generate-config
Generate and save RCAN config from setup selections.

Request example:
```json
{"robot_name":"MyRobot","provider":"apple","model":"apple-balanced","preset":"rpi_rc_car","stack_id":"apple_native"}
```

Notes:
- `stack_id` is optional metadata from setup-v2 stack selection.
- For Apple, `model` should be one of the Apple profile IDs.
- Endpoint writes both config file and provider key env var (when key-backed provider + `api_key` provided).

### POST /setup/api/test-provider
Compatibility endpoint for provider health checks.
- Apple path: runs preflight and returns normalized reason when unavailable.
- Key-backed providers: probes via provider `health_check()`.

### POST /setup/api/save-config
Compatibility endpoint to write raw RCAN YAML and `.env` values.
Internally routes through setup-v2 save helpers for consistency.

### GET /setup/api/metrics
Return aggregated local setup reliability metrics:
- first-run success rate
- median time-to-remediation
- fallback success rate
- abandonment rate
- top reason codes

---

## Webhooks (Messaging Channels)

### POST /webhooks/whatsapp
Twilio WhatsApp webhook. Rate limited: 10 req/min/sender.

### POST /webhooks/slack
Slack Events API webhook. Rate limited: 10 req/min/sender.

---

## IMU (Inertial Measurement Unit)

### GET /api/imu/reading
Read accelerometer, gyroscope, magnetometer, and temperature from IMU sensor.
Response: `{accel_g: {x,y,z}, gyro_dps: {x,y,z}, mag_uT, temp_c, mode}`

### GET /api/imu/health
IMU driver health check. Response: `{ok, mode: "hardware"|"mock", error}`

---

## LiDAR

### GET /api/lidar/scan
Full 360° scan. Response: `[{angle_deg, distance_mm, quality}, ...]`

### GET /api/lidar/obstacles
4-sector obstacle map. Response: `{min_distance_mm, sectors: {front,right,back,left}}`

### GET /api/lidar/health
LiDAR driver health check.

---

## Reactive Obstacle Avoidance

### GET /api/avoidance/status
Current avoidance state. Response: `{active, mode, estop_zone_mm, slow_zone_mm, slow_factor}`

### POST /api/avoidance/configure
Update avoidance parameters. Body: `{estop_zone_mm?, slow_zone_mm?, slow_factor?}`

---

## LLM Response Cache

### GET /api/cache/stats
Cache statistics. Response: `{hits, misses, entries, hit_rate_pct, enabled, max_age_s, max_size}`

### POST /api/cache/clear
Delete all cached entries. Response: `{deleted}`

### POST /api/cache/enable
Re-enable cache for this session.

### POST /api/cache/disable
Bypass cache for this session.

---

## Point Cloud

### GET /api/depth/pointcloud
Current point cloud as JSON array of `{x, y, z}` points.

### GET /api/depth/pointcloud.ply
Current point cloud as a PLY file download.

### GET /api/depth/pointcloud/stats
Point cloud statistics: `{point_count, bounds, density}`

---

## Object Detection

### GET /api/detection/frame
Current camera frame with detection overlays (JPEG).

### GET /api/detection/latest
Latest detection results. Response: `{detections: [{class, confidence, bbox}]}`

### POST /api/detection/configure
Update detection parameters. Body: `{confidence_threshold?, model?}`

---

## Simulation Bridge

### GET /api/sim/formats
Supported simulation formats. Response: `{formats: ["mujoco", "gazebo", "webots"]}`

### POST /api/sim/export
Export the active RCAN config to a simulation format. Body: `{format: "mujoco"|"gazebo"|"webots"}`

### POST /api/sim/import
Import sim config into RCAN. Body: `{format, content}`

### GET /api/sim/config
Current sim bridge configuration.

---

## Fine-Tune Export

### GET /api/finetune/export
Export episode memory as JSONL for fine-tuning. Query params: `limit` (default 500), `provider` (`openai`|`anthropic`).

---

## Personality

### GET /api/personality
Current active personality profile. Response: `{name, description, system_prompt_prefix}`

### POST /api/personality/set
Set a personality profile. Body: `{name}` — e.g. `friendly`, `military`, `scientist`, `child`, `pirate`, `chef`.

---

## Workspace

### GET /api/workspace/list
List all robot workspaces.

### POST /api/workspace/create
Create a new isolated workspace. Body: `{name, rcan_path?}`

### POST /api/workspace/switch
Switch the active workspace. Body: `{name}`

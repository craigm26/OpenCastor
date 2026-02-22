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
{"raw_text": "Moving forward at low speed.", "action": {"speed": 0.3, "direction": "forward"}}
```

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
Dead-reckoning navigation move via `WaypointNav`.

Request:
```json
{"distance_m": 1.5, "heading_deg": 90.0, "speed": 0.6}
```

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

### POST /setup/api/test-provider
Test an API key before saving.

### POST /setup/api/save-config
Write the RCAN config and `.env` file based on wizard form submission.

---

## Webhooks (Messaging Channels)

### POST /webhooks/whatsapp
Twilio WhatsApp webhook. Rate limited: 10 req/min/sender.

### POST /webhooks/slack
Slack Events API webhook. Rate limited: 10 req/min/sender.

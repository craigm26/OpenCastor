# PRD: Dashboard UX Overhaul — Controls, Camera, Voice & iOS Fix

## Self-Clarification

1. **Problem/Goal:** The dashboard control page has dead-weight UI (gamepad button), a broken camera feed for the OAK depth camera, non-functional voice/STT/wake word despite a physical USB mic being connected, and the whole dashboard doesn't load on iOS Safari. All of these degrade the daily UX for interacting with the robot.

2. **Core Functionality:**
   - Remove gamepad button, make movement controls look and feel polished
   - Restore live camera feed from the connected USB3 OAK depth camera; show it in status
   - Make server-side STT, push-to-talk, and wake word work automatically with the physical USB mic on boot — no config needed
   - Fix iOS Safari so the dashboard loads and controls work (same as Chrome)

3. **Scope/Boundaries:** Do NOT rewrite the Streamlit app framework. Do NOT add WebRTC or new streaming protocols. Do NOT change the gamepad page itself (just remove the link to it). Do NOT add new API endpoints unless required for mic auto-detection.

4. **Success Criteria:**
   - iOS Safari opens the dashboard and movement controls respond
   - OAK camera feed visible on control page; status row shows camera model/mode
   - Clicking "Push to Talk" or "Server Mic" captures audio and returns transcript
   - Wake word fires within 3 seconds of saying "hey [robot name]" after cold boot
   - No gamepad button on control page

5. **Constraints:** Raspberry Pi 5 hardware (arm64), Python 3.13, USB 2.0 mic already physically connected, OAK depth camera connected via USB3. No HTTPS (HTTP only on LAN). Server-side mic only (browser mic not required). Must pass existing test suite (`pytest tests/`), ruff clean.

---

## Introduction

The OpenCastor dashboard control page has accumulated friction: a gamepad shortcut button that takes up prime space, suboptimal movement button styling, a broken live camera feed (OAK camera not detected at startup), STT/push-to-talk/wake word that silently fail due to missing mic auto-detection, and a complete load failure on iOS Safari. This PRD fixes all five areas to deliver a smooth, always-ready interaction experience.

---

## Goals

- Remove the "Open Gamepad Controller" button from the control tab entirely
- Improve visual design and touch target size of robot movement D-pad buttons
- Auto-detect and use the connected USB 2.0 microphone at gateway startup for STT and wake word
- Restore live MJPEG feed from the OAK depth camera on the control page
- Show camera model/mode in the status sensor row
- Fix iOS Safari rendering so the dashboard loads and controls are fully usable
- Wake word detection starts automatically on boot without any manual API call

---

## Tasks

### T-001: Remove Gamepad Button & Improve Movement Controls
**Description:** In `castor/dashboard.py`, remove the "🎮 Open Gamepad Controller →" button from the control tab. Then improve the D-pad buttons: larger touch targets (min 72px), clearer directional icons, stronger visual feedback on press (color + scale), and better spacing/layout on mobile.

**Acceptance Criteria:**
- [ ] "Open Gamepad Controller" button no longer appears anywhere in the control tab
- [ ] D-pad buttons have `min-height: 72px; min-width: 72px`
- [ ] Active (pressed) state shows distinct background color change (not just opacity)
- [ ] Buttons use larger Unicode arrows or SVG icons (not just ▲▼◀▶)
- [ ] Speed and turn sliders remain functional
- [ ] E-STOP button unchanged
- [ ] `pytest tests/` passes, `ruff check castor/` clean
- [ ] Verify in browser: control tab renders correctly on desktop and mobile viewport

---

### T-002: Fix OAK Depth Camera Feed & Status Display
**Description:** The OAK-4 Pro depth camera is connected via USB3 but isn't detected. In `castor/camera.py`, ensure the OAK camera initialisation path is tried before the generic USB fallback and that failure is logged clearly. In `castor/api.py`, check that `/api/stream/mjpeg` actually starts a capture thread when the OAK pipeline initialises. In the dashboard control tab, ensure the `<img src="/api/stream/mjpeg">` URL is constructed correctly for the network hostname (not localhost). In `castor/api.py` `/api/status`, add `camera_model` and `camera_mode` fields. In `castor/dashboard.py` status sensor row, display these fields.

**Acceptance Criteria:**
- [ ] On gateway startup, logs show OAK camera detected (or clear error if absent, falling back to USB index 0)
- [ ] `/api/stream/mjpeg` returns frames when OAK camera is connected
- [ ] Camera feed `<img>` in control tab uses the correct network hostname (not `localhost` / `127.0.0.1`) so it works from any browser on the LAN
- [ ] `/api/status` response includes `camera_model` (e.g. `"OAK-4 Pro"` or `"USB 0"`) and `camera_mode` (e.g. `"rgb"`, `"depth_overlay"`)
- [ ] Status tab sensor row shows camera model and mode (not just `live`/`offline`)
- [ ] `pytest tests/` passes, `ruff check castor/` clean
- [ ] Verify in browser: camera feed visible in control tab from a different device on the LAN

---

### T-003: Auto-Detect USB Microphone for STT & Push-to-Talk
**Description:** STT and push-to-talk silently fail because the server-side listener doesn't auto-detect the USB mic. In `castor/voice.py` and wherever the listener is initialised in `castor/api.py`, add USB mic auto-detection: enumerate PyAudio/sounddevice devices at startup, pick the first non-default USB input device (device name contains "USB" or "usb"), set it as the active input device. Log which device was selected. Also add a `GET /api/voice/devices` endpoint that returns detected audio input devices so the dashboard can surface errors. If STT initialisation fails, log the error clearly rather than silently skipping.

**Acceptance Criteria:**
- [ ] On gateway startup with USB mic connected, logs show `"Audio input: <device name> (index N)"`
- [ ] `POST /api/voice/listen` returns a transcript (not an error) when USB mic is connected
- [ ] `GET /api/voice/devices` returns list of `{index, name, default}` audio input devices
- [ ] Dashboard push-to-talk button shows transcript in toast notification after speaking
- [ ] Dashboard "Server Mic (STT)" button works the same way
- [ ] If no mic found, `POST /api/voice/listen` returns `{"error": "no audio input device", "code": "HTTP_503"}` (not silent 500)
- [ ] `pytest tests/` passes, `ruff check castor/` clean
- [ ] Verify on device: press PTT, speak, receive transcript

---

### T-004: Auto-Start Wake Word on Gateway Boot
**Description:** Wake word detection currently requires a manual `POST /api/hotword/start` call. In `castor/api.py` startup (`lifespan` or `@app.on_event("startup")`), after the main gateway initialisation, automatically call hotword start if `audio.wake_word_enabled: true` in the RCAN config (or if `CASTOR_HOTWORD` env var is set). If the USB mic is not yet detected at startup, retry hotword init once after a 3-second delay. Log hotword engine and wake phrase on start.

**Acceptance Criteria:**
- [ ] After `castor gateway` starts (with USB mic connected), `GET /api/hotword/status` returns `{active: true}` within 5 seconds — no manual API call needed
- [ ] Gateway logs show `"Wake word active: '<phrase>' via <engine>"` at startup
- [ ] If mic unavailable at boot, a single retry occurs after 3s; logs show outcome either way
- [ ] `GET /api/hotword/status` still returns correct status after auto-start
- [ ] `pytest tests/` passes, `ruff check castor/` clean

---

### T-005: Fix iOS Safari Compatibility
**Description:** The dashboard fails to load on iOS Safari. Primary causes identified:
1. Streamlit's WebSocket handshake can stall on iOS Safari if `server.maxUploadSize` or CORS headers are not set
2. The MJPEG `<img>` stream works as a standard image tag on Safari but the token auth URL param format may differ
3. Some CSS properties used in the dashboard HTML components need `-webkit-` prefixes for Safari
4. `window.parent.location` iframe access may be blocked by Safari's ITP, preventing the hostname resolution for the camera URL

Fix:
- In the embedded HTML camera component, use `window.location.hostname` as primary hostname source (not `window.parent.location`) — add a robust multi-fallback chain
- Add `-webkit-overflow-scrolling: touch` and any missing `-webkit-` prefixes in dashboard HTML component CSS
- Ensure all dashboard `st.components.v1.html()` blocks have `scrolling="no"` or appropriate height to avoid double-scrollbar on iOS
- Test and document that `castor gateway` must be started with `--host 0.0.0.0` (already the case) and that the dashboard Streamlit server must also bind to `0.0.0.0`

**Acceptance Criteria:**
- [ ] Dashboard at `http://<alex-ip>:8501` loads fully on iOS Safari (page renders, tabs are clickable)
- [ ] Movement D-pad buttons respond to touch on iOS Safari
- [ ] Camera feed `<img>` visible on iOS Safari (or shows "No camera signal" fallback gracefully — not a broken image)
- [ ] No JavaScript console errors related to cross-origin `window.parent` access
- [ ] `pytest tests/` passes, `ruff check castor/` clean
- [ ] Verify on device: iOS Safari, navigate to dashboard, tap control tab, tap movement button

---

## Functional Requirements

- **FR-1:** The control tab MUST NOT contain any button or link to `/gamepad`
- **FR-2:** D-pad movement buttons MUST have a minimum touch target of 72×72px
- **FR-3:** On gateway startup, the system MUST enumerate audio input devices and select the first available USB microphone
- **FR-4:** `POST /api/voice/listen` MUST return a transcript when a USB mic is connected, without any additional configuration
- **FR-5:** Wake word detection MUST start automatically on gateway boot if `CASTOR_HOTWORD` is set or `audio.wake_word_enabled: true` in RCAN config
- **FR-6:** The camera MJPEG feed in the control tab MUST be addressable by the LAN IP (not localhost) so remote browsers can load it
- **FR-7:** `/api/status` MUST include `camera_model` and `camera_mode` fields
- **FR-8:** The dashboard MUST load and be interactive on iOS Safari 16+
- **FR-9:** All audio device errors MUST produce clear log messages and structured API error responses (not silent failures)

---

## Non-Goals

- No WebRTC, no HTTPS/TLS setup
- No browser-side microphone capture (server-side only)
- No changes to the `/gamepad` page itself
- No new AI providers or driver changes
- No mobile-native app
- No Porcupine or paid wake word engine — use existing engine (keyword matching or vosk)

---

## Technical Considerations

- **Audio stack**: PyAudio or sounddevice; device enumeration via `pyaudio.PyAudio().get_device_info_by_index(i)` or `sounddevice.query_devices()`
- **OAK camera**: `depthai==3.3.0` already installed; init check should catch `RuntimeError` from depthai if camera is absent
- **iOS Safari WebSocket**: Streamlit 1.x handles this natively; check that no custom headers are blocking the upgrade
- **Hostname in embedded HTML**: `window.location.hostname` is always same-origin and accessible in Safari; use it as primary, fall back to hardcoded `alex.local` only as last resort
- **Wake word retry**: Use `asyncio.create_task` with `asyncio.sleep(3)` for the retry — don't block startup

---

## Success Metrics

- Dashboard opens within 3 seconds on iOS Safari (same as Chrome)
- Push-to-talk round trip (speech → transcript displayed) under 5 seconds
- Wake word detection fires within 3 seconds of utterance
- Camera feed visible within 2 seconds of page load
- Zero gamepad button occurrences in control tab HTML output

---

## Open Questions

- Does the OAK camera need a specific depthai pipeline config in the RCAN file, or should camera.py auto-detect it?
- Is the current wake word engine (non-Porcupine) accurate enough for "hey alex" at conversational distance, or should vosk be the default?
- Should `GET /api/voice/devices` be authenticated (token required) or open?

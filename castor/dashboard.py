"""
CastorDash — single-page telemetry dashboard for OpenCastor.

Mirrors the terminal watch layout:
  • Header bar : robot · brain · driver · channels · uptime
  • Left column: live MJPEG camera feed + command input
  • Right column: status/telemetry · driver · channels · learner stats
  • Bottom row : recent command history

Run with: streamlit run castor/dashboard.py
"""

import os
import sys
import time

# Prevent castor/watchdog.py from shadowing the watchdog package when
# Streamlit adds the script directory (castor/) to sys.path.
_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path = [p for p in sys.path if os.path.normpath(p) != os.path.normpath(_this_dir)]

import requests as _req
import streamlit as st

# ── page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CastorDash · Bob",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={},
)

# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
  /* dark background */
  .stApp { background-color: #0d1117; color: #e6edf3; }

  /* metric cards */
  [data-testid="stMetric"] {
    background: #161b22;
    border-radius: 8px;
    padding: 8px 12px;
    border: 1px solid #30363d;
  }
  [data-testid="stMetricValue"] { font-size: 1.1rem !important; }

  /* header status bar */
  .status-bar {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 8px 16px;
    margin-bottom: 12px;
    font-family: monospace;
    font-size: 0.9rem;
  }

  /* panel titles */
  .panel-title {
    color: #8b949e;
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-bottom: 4px;
  }

  /* emergency stop */
  div[data-testid="stButton"] button[kind="primary"] {
    background-color: #da3633 !important;
    border-color: #da3633 !important;
    font-weight: 700;
    width: 100%;
  }

  /* hide streamlit branding */
  #MainMenu, footer, header { visibility: hidden; }

  /* compact dataframe */
  [data-testid="stDataFrame"] { font-size: 0.8rem; }
</style>
""",
    unsafe_allow_html=True,
)

# ── session state ──────────────────────────────────────────────────────────────
_DEFAULTS = {
    "gateway_url": os.getenv("OPENCASTOR_GATEWAY_URL", "http://127.0.0.1:8000"),
    "api_token":   os.getenv("OPENCASTOR_API_TOKEN", ""),
    "messages":    [],
    "voice_mode":  False,
    "voice_speak_replies": True,
    "last_refresh": 0.0,
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

GW  = st.session_state.gateway_url
TOK = st.session_state.api_token
HDR = {"Authorization": f"Bearer {TOK}"} if TOK else {}

# ── API helpers ────────────────────────────────────────────────────────────────

def _get(path: str, timeout: float = 2.0) -> dict:
    try:
        r = _req.get(f"{GW}{path}", headers=HDR, timeout=timeout)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def _fmt_uptime(s) -> str:
    try:
        s = int(float(s))
    except Exception:
        return "—"
    h, rem = divmod(s, 3600)
    m, sc  = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sc:02d}" if h else f"{m:02d}:{sc:02d}"


def _dot_html(ok, true_col="#3fb950", false_col="#f85149", none_col="#6e7681") -> str:
    color = true_col if ok is True else (false_col if ok is False else none_col)
    return f'<span style="color:{color};font-size:0.9em;">●</span>'

# ── fetch all data once per render ────────────────────────────────────────────
health  = _get("/health")
status  = _get("/api/status")
proc    = _get("/api/fs/proc")
driver  = _get("/api/driver/health")
learner = _get("/api/learner/stats")
hist    = _get("/api/command/history?limit=8")

robot_name  = status.get("robot_name", health.get("robot_name", "Bob"))
uptime      = health.get("uptime_s", 0)
brain_ok    = health.get("brain")
driver_ok   = health.get("driver")
channels_active = status.get("channels_active", health.get("channels", []))
cam_ok      = str(proc.get("camera", "")).lower() in ("online", "true", "ok")
loop_count  = proc.get("loop_count", 0)
avg_lat     = proc.get("avg_latency_ms", 0)
lat_color   = "#3fb950" if avg_lat < 300 else "#d29922" if avg_lat < 1000 else "#f85149"

# ── HEADER STATUS BAR ─────────────────────────────────────────────────────────
ch_html = " &nbsp;·&nbsp; ".join(
    f'<span style="color:#58a6ff">{c}</span>' for c in channels_active
) if channels_active else '<span style="color:#6e7681">no channels</span>'

st.markdown(
    f"""
<div class="status-bar">
  🤖 &nbsp;<strong>{robot_name}</strong>
  &nbsp;&nbsp;&nbsp;
  {_dot_html(brain_ok)} brain&nbsp;<strong>{"online" if brain_ok else "offline"}</strong>
  &nbsp;&nbsp;
  {_dot_html(driver_ok, "#3fb950", "#d29922")} driver&nbsp;<strong>{"online" if driver_ok else "mock"}</strong>
  &nbsp;&nbsp;&nbsp;
  📡 &nbsp;{ch_html}
  &nbsp;&nbsp;&nbsp;&nbsp;
  <span style="color:#6e7681">↑ {_fmt_uptime(uptime)}</span>
  &nbsp;&nbsp;
  {_dot_html(cam_ok)} camera&nbsp;<strong>{"live" if cam_ok else "offline"}</strong>
</div>
""",
    unsafe_allow_html=True,
)

# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Settings")
    st.session_state.gateway_url = st.text_input(
        "Gateway URL", value=st.session_state.gateway_url
    )
    st.session_state.api_token = st.text_input(
        "API Token", value=st.session_state.api_token, type="password"
    )
    refresh_s = st.slider("Auto-refresh (s)", 1, 10, 3)
    st.divider()

    # Emergency Stop — always prominent
    st.markdown("### 🛑 Emergency Stop")
    if st.button("EMERGENCY STOP", type="primary", use_container_width=True):
        try:
            _req.post(f"{GW}/api/stop", headers=HDR, timeout=3)
            st.warning("⚠️ Motors disengaged!")
        except Exception as e:
            st.error(f"E-stop failed: {e}")

    st.divider()
    st.markdown("### 🎤 Voice Mode")
    st.session_state.voice_mode = st.toggle(
        "Continuous Voice",
        value=st.session_state.voice_mode,
    )
    if st.session_state.voice_mode:
        st.session_state.voice_speak_replies = st.checkbox(
            "Speak replies", value=st.session_state.voice_speak_replies
        )
        st.components.v1.html(
            """
<script>
function castorStartVoice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { alert('Voice not supported — use Chrome/Edge'); return; }
  const r = new SR();
  r.lang = 'en-US';
  r.onresult = e => window.parent.postMessage(
    {type:'streamlit:setComponentValue', value: e.results[0][0].transcript}, '*');
  r.start();
}
</script>
<button onclick="castorStartVoice()"
  style="padding:8px 16px;border-radius:20px;background:#238636;color:white;
         border:none;cursor:pointer;width:100%;font-size:13px;">
  🎤 Browser Mic
</button>""",
            height=48,
        )

# ── MAIN BODY ─────────────────────────────────────────────────────────────────
left_col, right_col = st.columns([3, 2], gap="medium")

# ═══════════════════════════════════════════════════════════════════
# LEFT COLUMN — camera feed + command input
# ═══════════════════════════════════════════════════════════════════
with left_col:

    # ── Live camera ──────────────────────────────────────────────
    st.markdown('<p class="panel-title">📷 Live Camera — OAK-D USB3 · 640×480 @ 30fps</p>',
                unsafe_allow_html=True)

    _mjpeg_base = f"{GW}/api/stream/mjpeg"
    _mjpeg_url  = f"{_mjpeg_base}?token={TOK}" if TOK else _mjpeg_base

    # Embed MJPEG via HTML img tag (token in URL so browser can load it)
    cam_border = "#3fb950" if cam_ok else "#f85149"
    st.components.v1.html(
        f"""
<div style="background:#0d1117;border:2px solid {cam_border};border-radius:8px;
            overflow:hidden;aspect-ratio:4/3;max-height:420px;position:relative;">
  <img id="cam"
       src="{_mjpeg_url}"
       style="width:100%;height:100%;object-fit:cover;display:block;"
       onerror="document.getElementById('cam-err').style.display='flex';
                this.style.display='none';" />
  <div id="cam-err"
       style="display:none;position:absolute;inset:0;align-items:center;
              justify-content:center;flex-direction:column;color:#8b949e;
              font-family:monospace;font-size:0.85rem;background:#0d1117;">
    <div style="font-size:2rem;margin-bottom:8px;">📷</div>
    <div>No camera signal</div>
    <div style="margin-top:4px;font-size:0.7rem;color:#6e7681;">{_mjpeg_base}</div>
  </div>
</div>
<div style="margin-top:4px;font-family:monospace;font-size:0.7rem;color:#6e7681;">
  Stream: <a href="{_mjpeg_url}" target="_blank" style="color:#58a6ff;">{_mjpeg_base}</a>
</div>
""",
        height=440,
    )

    st.divider()

    # ── Command input ─────────────────────────────────────────────
    st.markdown('<p class="panel-title">💬 Command</p>', unsafe_allow_html=True)

    # Voice button (server-side mic)
    if st.button("🎤 Speak"):
        try:
            import speech_recognition as sr
            recognizer = sr.Recognizer()
            with sr.Microphone() as source:
                st.toast("Listening…", icon="🎤")
                audio = recognizer.listen(source, timeout=8, phrase_time_limit=30)
                text = recognizer.recognize_google(audio)
                st.session_state["voice_input"] = text
                if st.session_state.voice_mode:
                    st.toast(f"Heard: {text[:60]}", icon="✅")
        except Exception as e:
            st.toast(f"Voice: {e}", icon="❌")

    prompt = st.chat_input("Type a command…")
    user_text = prompt or st.session_state.pop("voice_input", None)

    # Chat history (compact)
    msg_container = st.container(height=180)
    with msg_container:
        for m in st.session_state.messages[-6:]:
            with st.chat_message(m["role"]):
                st.markdown(m["content"])

    if user_text:
        st.session_state.messages.append({"role": "user", "content": user_text})
        with st.spinner("Thinking…"):
            try:
                r = _req.post(
                    f"{GW}/api/command",
                    json={"instruction": user_text},
                    headers=HDR,
                    timeout=30,
                )
                reply = r.json().get("raw_text", str(r.json())) if r.ok else f"[{r.status_code}]"
            except Exception as e:
                reply = f"[error] {e}"

        st.session_state.messages.append({"role": "assistant", "content": reply})

        # Browser speech synthesis in voice mode
        if st.session_state.voice_mode and st.session_state.voice_speak_replies:
            safe = reply.replace("\\", "\\\\").replace("`", "\\`").replace('"', '\\"')
            st.components.v1.html(
                f"<script>(()=>{{const u=new SpeechSynthesisUtterance(`{safe}`);"
                "u.lang='en-US';window.speechSynthesis.cancel();window.speechSynthesis.speak(u);}})();</script>",
                height=0,
            )
        st.rerun()

# ═══════════════════════════════════════════════════════════════════
# RIGHT COLUMN — status panels (mirrors terminal watch)
# ═══════════════════════════════════════════════════════════════════
with right_col:

    # ── Status & Telemetry ────────────────────────────────────────
    st.markdown('<p class="panel-title">⚡ Status & Telemetry</p>', unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    c1.metric("Uptime",   _fmt_uptime(uptime))
    c2.metric("Loops",    str(loop_count))
    c1.metric("Latency",  f"{avg_lat:.0f} ms" if avg_lat else "—")
    c2.metric("Camera",   "live ●" if cam_ok else "offline ○")
    speaker_ok = str(proc.get("speaker", "")).lower() in ("online", "true", "ok")
    c1.metric("Speaker",  "online" if speaker_ok else "offline")

    last_thought = str(proc.get("last_thought") or "")
    if last_thought:
        st.caption(f"💭 {last_thought[:80]}{'…' if len(last_thought) > 80 else ''}")

    st.divider()

    # ── Driver ───────────────────────────────────────────────────
    st.markdown('<p class="panel-title">🦾 Driver</p>', unsafe_allow_html=True)
    drv_ok   = driver.get("ok")
    drv_mode = driver.get("mode", "?")
    drv_type = driver.get("driver_type", "PCA9685")
    drv_err  = driver.get("error", "")

    dc1, dc2 = st.columns(2)
    dc1.metric("Mode",  drv_mode.capitalize() if drv_mode else "—")
    dc2.metric("Type",  drv_type or "—")
    if drv_err:
        st.caption(f"ℹ️ {drv_err[:64]}")

    st.divider()

    # ── Channels ─────────────────────────────────────────────────
    st.markdown('<p class="panel-title">📡 Channels</p>', unsafe_allow_html=True)
    ch_avail  = status.get("channels_available", {})
    ch_active = set(channels_active)

    if ch_avail:
        ch_rows = []
        for ch_name, avail in sorted(ch_avail.items()):
            is_active = ch_name in ch_active
            dot = "🟢" if is_active else ("🟡" if avail else "⚫")
            state = "active" if is_active else ("ready" if avail else "unavail")
            ch_rows.append({"Channel": ch_name, "Status": state, "": dot})
        import pandas as pd
        st.dataframe(
            pd.DataFrame(ch_rows),
            hide_index=True,
            use_container_width=True,
            height=min(150, 36 + 36 * len(ch_rows)),
        )
    else:
        st.caption("No channel data")

    st.divider()

    # ── Learner stats ─────────────────────────────────────────────
    st.markdown('<p class="panel-title">🧠 Learner (Sisyphus)</p>', unsafe_allow_html=True)
    if learner.get("available"):
        lc1, lc2 = st.columns(2)
        lc1.metric("Episodes",  learner.get("episodes_analyzed", 0))
        lc2.metric("Applied",   learner.get("improvements_applied", 0))
        lc1.metric("Rejected",  learner.get("improvements_rejected", 0))
        avg_dur = learner.get("avg_duration_ms")
        lc2.metric("Avg cycle", f"{avg_dur:.0f} ms" if avg_dur else "—")
    else:
        st.caption("No learner data yet — run a few commands first")

    st.divider()

    # ── Offline fallback ─────────────────────────────────────────
    fb = status.get("offline_fallback", {})
    if fb.get("enabled"):
        st.markdown('<p class="panel-title">🔌 Offline Fallback</p>', unsafe_allow_html=True)
        fc1, fc2 = st.columns(2)
        fc1.metric("Using fallback", "Yes" if fb.get("using_fallback") else "No")
        fc2.metric("Provider", fb.get("fallback_provider", "—"))

# ── BOTTOM — command history ──────────────────────────────────────────────────
st.divider()
st.markdown('<p class="panel-title">🕒 Recent Commands</p>', unsafe_allow_html=True)

history_entries = hist.get("history", [])
if history_entries:
    import pandas as pd
    rows = []
    for e in reversed(history_entries):
        ts = e.get("ts", "")
        hhmm = ts[11:16] if len(ts) > 15 else ts[:5]
        instr = str(e.get("instruction", ""))[:48]
        action = str(e.get("action") or e.get("raw_text") or "")[:64]
        rows.append({"Time": hhmm, "Command": instr, "Response / Action": action})
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
        height=min(240, 36 + 36 * len(rows)),
    )
else:
    st.caption("No commands yet — type one above")

# ── AUTO-REFRESH ──────────────────────────────────────────────────────────────
time.sleep(refresh_s)
st.rerun()

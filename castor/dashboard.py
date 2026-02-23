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

import requests as _req  # noqa: E402
import streamlit as st  # noqa: E402

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

  /* ── enhanced metric cards ── */
  [data-testid="stMetric"] {
    background: #161b22 !important;
    border-radius: 8px !important;
    padding: 12px 16px !important;
    border: 1px solid #21262d !important;
    border-left: 3px solid #58a6ff !important;
    transition: border-left-color 0.3s;
  }
  [data-testid="stMetricValue"] { font-size: 1.05rem !important; font-weight: 600 !important; }
  [data-testid="stMetricLabel"] { font-size: 0.7rem !important; color: #8b949e !important; text-transform: uppercase; letter-spacing: 0.06em; }

  /* ── glowing status dots ── */
  .status-bar { padding: 10px 18px !important; }
  .dot-green  { display:inline-block; width:10px; height:10px; border-radius:50%;
                background:#3fb950; box-shadow: 0 0 6px #3fb950; margin-right:4px; }
  .dot-red    { display:inline-block; width:10px; height:10px; border-radius:50%;
                background:#f85149; box-shadow: 0 0 6px #f85149; margin-right:4px; }
  .dot-yellow { display:inline-block; width:10px; height:10px; border-radius:50%;
                background:#d29922; box-shadow: 0 0 5px #d29922; margin-right:4px; }
  .dot-grey   { display:inline-block; width:10px; height:10px; border-radius:50%;
                background:#6e7681; margin-right:4px; }

  /* ── camera offline pulsing border ── */
  @keyframes cam-pulse {
    0%   { border-color: #f85149; box-shadow: 0 0 0px #f85149; }
    50%  { border-color: #ff7b72; box-shadow: 0 0 8px #f85149; }
    100% { border-color: #f85149; box-shadow: 0 0 0px #f85149; }
  }
  .cam-offline { animation: cam-pulse 2s ease-in-out infinite; }

  /* ── command panel background ── */
  .command-panel {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 10px;
    padding: 14px 16px;
    margin-top: 8px;
  }

  /* ── log viewer terminal feel ── */
  .log-terminal {
    background: #0d1117 !important;
    border: 1px solid #21262d !important;
    border-radius: 8px;
    padding: 10px 14px;
    font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace !important;
    font-size: 0.72rem !important;
    color: #3fb950 !important;
    overflow-y: auto;
    max-height: 260px;
  }

  /* ── section divider accent ── */
  .section-header {
    color: #e6edf3;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    border-left: 3px solid #58a6ff;
    padding-left: 8px;
    margin: 12px 0 6px 0;
  }
  .section-header.green { border-left-color: #3fb950; }
  .section-header.orange { border-left-color: #d29922; }
  .section-header.red { border-left-color: #f85149; }
</style>
""",
    unsafe_allow_html=True,
)

# ── session state ──────────────────────────────────────────────────────────────
_DEFAULTS = {
    "gateway_url": os.getenv("OPENCASTOR_GATEWAY_URL", "http://127.0.0.1:8000"),
    "api_token": os.getenv("OPENCASTOR_API_TOKEN", ""),
    "messages": [],
    "voice_mode": False,
    "voice_speak_replies": True,
    "last_refresh": 0.0,
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

GW = st.session_state.gateway_url


def _hdr() -> dict:
    """Build auth header from current session state (evaluated on every rerun)."""
    tok = st.session_state.api_token
    return {"Authorization": f"Bearer {tok}"} if tok else {}


# ── API helpers ────────────────────────────────────────────────────────────────


def _get(path: str, timeout: float = 2.0) -> dict:
    try:
        r = _req.get(f"{GW}{path}", headers=_hdr(), timeout=timeout)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def _fmt_uptime(s) -> str:
    try:
        s = int(float(s))
    except Exception:
        return "—"
    h, rem = divmod(s, 3600)
    m, sc = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sc:02d}" if h else f"{m:02d}:{sc:02d}"


def _dot_html(ok, true_col="#3fb950", false_col="#f85149", none_col="#6e7681") -> str:
    color = true_col if ok is True else (false_col if ok is False else none_col)
    return f'<span style="color:{color};font-size:0.9em;">●</span>'


# ── fetch all data once per render ────────────────────────────────────────────
health = _get("/health")
status = _get("/api/status")
proc = _get("/api/fs/proc")
driver = _get("/api/driver/health")
learner = _get("/api/learner/stats")
hist = _get("/api/command/history?limit=8")
episodes = _get("/api/memory/episodes?limit=20")
usage = _get("/api/usage")

robot_name = status.get("robot_name", health.get("robot_name", "Bob"))
uptime = health.get("uptime_s", 0)
brain_ok = health.get("brain")
driver_ok = health.get("driver")
channels_active = status.get("channels_active", health.get("channels", []))
cam_ok = str(proc.get("camera", "")).lower() in ("online", "true", "ok")
loop_count = proc.get("loop_count", 0)
avg_lat = proc.get("avg_latency_ms", 0)
lat_color = "#3fb950" if avg_lat < 300 else "#d29922" if avg_lat < 1000 else "#f85149"

# ── HEADER STATUS BAR ─────────────────────────────────────────────────────────
ch_html = (
    " &nbsp;·&nbsp; ".join(f'<span style="color:#58a6ff">{c}</span>' for c in channels_active)
    if channels_active
    else '<span style="color:#6e7681">no channels</span>'
)

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
    st.session_state.gateway_url = st.text_input("Gateway URL", value=st.session_state.gateway_url)
    st.session_state.api_token = st.text_input(
        "API Token", value=st.session_state.api_token, type="password"
    )
    refresh_s = st.slider("Auto-refresh (s)", 1, 10, 3)
    st.divider()

    # Emergency Stop — always prominent
    st.markdown("### 🛑 Emergency Stop")
    if st.button("EMERGENCY STOP", type="primary", use_container_width=True):
        try:
            _req.post(f"{GW}/api/stop", headers=_hdr(), timeout=3)
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
    st.markdown(
        '<p class="section-header green">📷 Live Camera — OAK-D USB3 · 640×480 @ 30fps</p>',
        unsafe_allow_html=True,
    )

    _mjpeg_base = f"{GW}/api/stream/mjpeg"
    _tok = st.session_state.api_token
    _mjpeg_url = f"{_mjpeg_base}?token={_tok}" if _tok else _mjpeg_base

    # Embed MJPEG via HTML img tag.
    # IMPORTANT: Replace 127.0.0.1/localhost with window.location.hostname via JS
    # so remote browsers (not on the Pi) can reach the stream.
    cam_border_cls = "cam-offline" if not cam_ok else ""
    cam_border_style = "border:2px solid #3fb950;" if cam_ok else "border:2px solid #f85149;"
    _gw_port = GW.split(":")[-1] if ":" in GW else "8000"
    _tok_js = _tok.replace('"', '\\"') if _tok else ""
    st.components.v1.html(
        f"""
<style>
@keyframes cam-pulse {{
  0%   {{ border-color:#f85149; box-shadow:0 0 0px #f85149; }}
  50%  {{ border-color:#ff7b72; box-shadow:0 0 8px #f85149; }}
  100% {{ border-color:#f85149; box-shadow:0 0 0px #f85149; }}
}}
.cam-wrap {{ animation: {'cam-pulse 2s ease-in-out infinite' if not cam_ok else 'none'}; }}
</style>
<div class="cam-wrap" style="background:#0d1117;{cam_border_style}border-radius:8px;
            overflow:hidden;aspect-ratio:4/3;max-height:420px;position:relative;">
  <img id="cam" src=""
       style="width:100%;height:100%;object-fit:cover;display:block;"
       onerror="document.getElementById('cam-err').style.display='flex';
                this.style.display='none';" />
  <div id="cam-err"
       style="display:none;position:absolute;inset:0;align-items:center;
              justify-content:center;flex-direction:column;color:#8b949e;
              font-family:monospace;font-size:0.85rem;background:#0d1117;">
    <div style="font-size:2rem;margin-bottom:8px;">📷</div>
    <div>No camera signal</div>
    <div id="cam-url-err" style="margin-top:4px;font-size:0.7rem;color:#6e7681;"></div>
  </div>
</div>
<div style="margin-top:4px;font-family:monospace;font-size:0.7rem;color:#6e7681;">
  Stream: <a id="cam-link" href="#" target="_blank" style="color:#58a6ff;">resolving…</a>
</div>
<script>
(function() {{
  var tok = "{_tok_js}";
  var port = "{_gw_port}";
  var host = window.location.hostname;
  var proto = window.location.protocol;
  var base = proto + "//" + host + ":" + port + "/api/stream/mjpeg";
  var url = tok ? base + "?token=" + encodeURIComponent(tok) : base;
  var img = document.getElementById("cam");
  var link = document.getElementById("cam-link");
  var errUrl = document.getElementById("cam-url-err");
  if (img)  {{ img.src = url; }}
  if (link) {{ link.href = url; link.textContent = base; }}
  if (errUrl) {{ errUrl.textContent = base; }}
}})();
</script>
""",
        height=460,
    )

    # ── Depth obstacle badges (Issue #117) ──────────────────────
    _depth_obs = _get("/api/depth/obstacles")
    if _depth_obs.get("available"):
        st.markdown('<p class="panel-title">📏 Obstacle Distances</p>', unsafe_allow_html=True)
        _do_l, _do_c, _do_r = st.columns(3)
        _do_l.metric(
            "Left", f"{_depth_obs['left_cm']:.0f} cm" if _depth_obs.get("left_cm") else "—"
        )
        _do_c.metric(
            "Center", f"{_depth_obs['center_cm']:.0f} cm" if _depth_obs.get("center_cm") else "—"
        )
        _do_r.metric(
            "Right", f"{_depth_obs['right_cm']:.0f} cm" if _depth_obs.get("right_cm") else "—"
        )

    st.divider()

    # ── Command input ─────────────────────────────────────────────
    st.markdown('<p class="section-header">💬 Command</p>', unsafe_allow_html=True)

    # Voice button (server-side mic via local STT)
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

    # Push-to-Talk button — delegates mic capture to the gateway STT endpoint
    if st.button("🎙️ Push to Talk", help="Uses gateway /api/voice/listen (STT via server mic)"):
        try:
            with st.spinner("Listening…"):
                resp = _req.post(
                    f"{GW}/api/voice/listen",
                    headers=_hdr(),
                    timeout=20,
                )
            if resp.ok:
                data = resp.json()
                transcript = data.get("transcript", "")
                thought = data.get("thought") or {}
                st.toast(f"Heard: {transcript[:80]}", icon="🎙️")
                if thought.get("raw_text"):
                    st.toast(f"Reply: {thought['raw_text'][:80]}", icon="🤖")
                if transcript:
                    st.session_state["voice_input"] = transcript
            else:
                err = resp.json().get("detail", resp.text)
                st.toast(f"PTT error: {err}", icon="❌")
        except Exception as _ptt_exc:
            st.toast(f"PTT: {_ptt_exc}", icon="❌")

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
                    headers=_hdr(),
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

    # ── Live Logs ─────────────────────────────────────────────────
    st.divider()
    st.markdown(
        '<p class="section-header green">📋 Live Logs</p>',
        unsafe_allow_html=True,
    )
    with st.expander(f"Gateway log — last 50 lines (refreshes every {refresh_s}s)", expanded=True):
        import subprocess as _subprocess
        from pathlib import Path as _Path

        _log_lines: list = []
        _log_source = ""
        # 1. Try journalctl (systemd --user service)
        try:
            _jctl = _subprocess.run(
                [
                    "journalctl",
                    "--user",
                    "-u",
                    "opencastor.service",
                    "-n",
                    "50",
                    "--no-pager",
                    "--output=short",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if _jctl.returncode == 0 and _jctl.stdout.strip():
                _log_lines = _jctl.stdout.splitlines()
                _log_source = "journalctl --user -u opencastor.service"
        except Exception:
            pass
        # 2. Fallback: /tmp log files
        if not _log_lines:
            for _log_candidate in [
                "/tmp/alex_gateway.log",
                "/tmp/bob_gateway.log",
                "/tmp/castor_gateway.log",
            ]:
                try:
                    _raw = _Path(_log_candidate).read_text(errors="replace")
                    _log_lines = _raw.splitlines()[-50:]
                    _log_source = _log_candidate
                    break
                except Exception:
                    continue
        if _log_lines:
            st.caption(f"Source: {_log_source}")
            st.code("\n".join(_log_lines), language=None)
        else:
            st.caption("No gateway log found — start the gateway or check systemd service")

# ═══════════════════════════════════════════════════════════════════
# RIGHT COLUMN — status panels (mirrors terminal watch)
# ═══════════════════════════════════════════════════════════════════
with right_col:
    # ── Status & Telemetry ────────────────────────────────────────
    st.markdown('<p class="section-header">⚡ Status & Telemetry</p>', unsafe_allow_html=True)

    speaker_ok = str(proc.get("speaker", "")).lower() in ("online", "true", "ok")
    _today = (usage.get("daily") or [{}])[-1] if usage.get("daily") else {}
    _today_tokens = _today.get("total_tokens", 0)
    _today_cost = _today.get("cost_usd", 0.0)

    c1, c2, c3 = st.columns(3)
    c1.metric("Uptime", _fmt_uptime(uptime))
    c2.metric("Loops", str(loop_count))
    c3.metric("Latency", f"{avg_lat:.0f} ms" if avg_lat else "—")
    c1.metric("Camera", "live ●" if cam_ok else "offline ○")
    c2.metric("Speaker", "online" if speaker_ok else "offline")
    c3.metric("Tokens", f"{_today_tokens:,}" if _today_tokens else "0")

    last_thought = str(proc.get("last_thought") or "")
    if last_thought:
        st.caption(f"💭 {last_thought[:80]}{'…' if len(last_thought) > 80 else ''}")

    st.divider()

    # ── Driver + Battery (side by side) ─────────────────────────
    _drv_bat_col, _bat_col = st.columns(2)

    with _drv_bat_col:
        st.markdown('<p class="section-header orange">🦾 Driver</p>', unsafe_allow_html=True)
        drv_ok = driver.get("ok")
        drv_mode = driver.get("mode", "?")
        drv_type = driver.get("driver_type", "PCA9685")
        drv_err = driver.get("error", "")
        dc1, dc2 = st.columns(2)
        dc1.metric("Mode", drv_mode.capitalize() if drv_mode else "—")
        dc2.metric("Type", (drv_type or "—").replace("PCA9685RC", "RC").replace("Driver", ""))
        if drv_err:
            st.caption(f"ℹ️ {drv_err[:52]}")

    _bat = _get("/api/battery/latest")
    with _bat_col:
        st.markdown('<p class="section-header green">🔋 Battery</p>', unsafe_allow_html=True)
        if _bat.get("available", False) or _bat.get("voltage_v") is not None:
            _bv = _bat.get("voltage_v")
            _bc = _bat.get("current_ma")
            _bp = _bat.get("power_mw")
            _bat1, _bat2 = st.columns(2)
            _bat1.metric("Voltage", f"{_bv:.1f}V" if _bv is not None else "—")
            _bat2.metric("Current", f"{_bc:.0f}mA" if _bc is not None else "—")
            st.metric("Power", f"{_bp:.0f}mW" if _bp is not None else "—")
        else:
            st.caption("No sensor")

    st.divider()

    # ── Channels ─────────────────────────────────────────────────
    st.markdown('<p class="section-header">📡 Channels</p>', unsafe_allow_html=True)
    ch_avail = status.get("channels_available", {})
    ch_active = set(channels_active)

    if ch_avail:
        ch_rows = []
        # Sort: active first (🟢), then ready (🟡), then unavail (⚫); alpha within group
        _order = {"active": 0, "ready": 1, "unavail": 2}
        for ch_name, avail in sorted(ch_avail.items()):
            is_active = ch_name in ch_active
            dot = "🟢" if is_active else ("🟡" if avail else "⚫")
            ch_status = "active" if is_active else ("ready" if avail else "unavail")
            ch_rows.append(
                {"Channel": ch_name, "Status": ch_status, "": dot, "_ord": _order[ch_status]}
            )
        ch_rows.sort(key=lambda r: (r["_ord"], r["Channel"]))
        for r in ch_rows:
            del r["_ord"]
        import pandas as pd

        st.dataframe(
            pd.DataFrame(ch_rows),
            hide_index=True,
            use_container_width=True,
            height=min(250, 36 + 36 * len(ch_rows)),
        )
    else:
        st.caption("No channel data")

    st.divider()

    # ── Learner + Cache (side by side) ───────────────────────────
    _lrn_col, _cch_col = st.columns(2)

    with _lrn_col:
        st.markdown('<p class="section-header">🧠 Learner</p>', unsafe_allow_html=True)
        if learner.get("available"):
            lc1, lc2 = st.columns(2)
            lc1.metric("Episodes", learner.get("episodes_analyzed", 0))
            lc2.metric("Applied", learner.get("improvements_applied", 0))
            lc1.metric("Rejected", learner.get("improvements_rejected", 0))
            avg_dur = learner.get("avg_duration_ms")
            lc2.metric("Avg cycle", f"{avg_dur:.0f}ms" if avg_dur else "—")
        else:
            st.caption("No data yet")

    _cs = _get("/api/cache/stats")
    with _cch_col:
        st.markdown('<p class="section-header">⚡ Cache</p>', unsafe_allow_html=True)
        if _cs.get("entries") is not None:
            _cc1, _cc2 = st.columns(2)
            _cc1.metric("Hit rate", f"{_cs.get('hit_rate_pct', 0):.1f}%")
            _cc2.metric("Entries", _cs.get("entries", 0))
        else:
            st.caption("No data")

    st.divider()

    # ── Offline fallback ─────────────────────────────────────────
    fb = status.get("offline_fallback", {})
    if fb.get("enabled"):
        st.markdown('<p class="section-header">🔌 Offline Fallback</p>', unsafe_allow_html=True)
        fc1, fc2 = st.columns(2)
        fc1.metric("Using fallback", "Yes" if fb.get("using_fallback") else "No")
        fc2.metric("Provider", fb.get("fallback_provider", "—"))

    # ── Object Detection ─────────────────────────────────────────
    _det = _get("/api/detection/latest")
    if _det.get("detections") is not None:
        st.markdown('<p class="section-header">👁 Detection</p>', unsafe_allow_html=True)
        _dets = _det.get("detections", [])
        _dlat = _det.get("latency_ms", 0)
        _dmode = _det.get("mode", "mock")
        st.caption(f"Mode: {_dmode} · {_dlat:.0f}ms · {len(_dets)} objects")
        if _dets:
            for _d in _dets[:5]:
                _conf = _d.get("confidence", 0)
                _cls = _d.get("class", "?")
                _color = "🟢" if _conf > 0.7 else "🟡"
                st.write(f"{_color} **{_cls}** ({_conf:.0%})")
        else:
            st.caption("None detected")

# ── BOTTOM — command history ──────────────────────────────────────────────────
st.divider()
st.markdown('<p class="section-header">🕒 Recent Commands</p>', unsafe_allow_html=True)

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

# ── EPISODE HISTORY ──────────────────────────────────────────────────────────
st.divider()
with st.expander(
    f"🧠 Episode Memory  — {episodes.get('total', 0)} total",
    expanded=False,
):
    ep_list = episodes.get("episodes", [])
    if ep_list:
        import pandas as pd

        ep_rows = []
        for ep in ep_list:
            ts = ep.get("ts", "")
            hhmm = ts[11:19] if len(ts) > 18 else ts
            action_type = (ep.get("action") or {}).get("type", ep.get("action_type", "—"))
            ep_rows.append(
                {
                    "Time": hhmm,
                    "Instruction": str(ep.get("instruction", ""))[:48],
                    "Action": action_type,
                    "Latency (ms)": f"{ep.get('latency_ms', 0):.0f}",
                    "Outcome": ep.get("outcome", "—")[:24],
                }
            )
        # Summary table
        st.dataframe(
            pd.DataFrame(ep_rows),
            hide_index=True,
            use_container_width=True,
            height=min(300, 36 + 36 * len(ep_rows)),
        )
        # Per-episode replay buttons
        st.markdown('<p class="panel-title">Replay an episode</p>', unsafe_allow_html=True)
        for ep in ep_list:
            ep_id = ep.get("id", "")
            action_type = (ep.get("action") or {}).get("type", "—")
            ts = ep.get("ts", "")
            hhmm = ts[11:19] if len(ts) > 18 else ts
            label = f"{hhmm}  {str(ep.get('instruction', ''))[:32]}  [{action_type}]"
            if st.button("▶", key=f"replay_{ep_id}", help=f"Replay: {label}"):
                try:
                    r = _req.post(
                        f"{GW}/api/memory/replay/{ep_id}",
                        headers=_hdr(),
                        timeout=5,
                    )
                    if r.ok:
                        st.toast("Replayed ✓", icon="▶")
                    else:
                        st.toast(f"Replay failed: {r.status_code} {r.text[:80]}", icon="❌")
                except Exception as _replay_err:
                    st.toast(f"Replay error: {_replay_err}", icon="❌")
    else:
        st.caption("No episodes recorded yet — start the runtime loop to capture them")


# ── FLEET (Swarm) PANEL ───────────────────────────────────────────────────────
st.divider()
st.markdown("### 🤖 Fleet")


def _load_fleet_nodes():
    """Load nodes from config/swarm.yaml, gracefully returning [] on any error."""
    from pathlib import Path

    try:
        import yaml
    except ImportError:
        return []
    # Locate swarm.yaml relative to OPENCASTOR_CONFIG or project root
    env_cfg = os.getenv("OPENCASTOR_CONFIG")
    candidates = []
    if env_cfg:
        candidates.append(Path(env_cfg).parent / "swarm.yaml")
    # Walk up from this file to find the project root config/swarm.yaml
    _here = Path(__file__).resolve().parent.parent
    candidates.append(_here / "config" / "swarm.yaml")
    candidates.append(Path("config/swarm.yaml"))

    for c in candidates:
        if c.exists():
            try:
                with open(c) as fh:
                    data = yaml.safe_load(fh) or {}
                return data.get("nodes", [])
            except Exception:
                pass
    return []


def _query_fleet_node(node):
    """GET /health for one fleet node; return status dict (never raises)."""
    import time as _time

    host = node.get("ip") or node.get("host", "localhost")
    port = node.get("port", 8000)
    base = f"http://{host}:{port}"
    token = node.get("token", "")
    hdrs = {"Authorization": f"Bearer {token}"} if token else {}
    start = _time.monotonic()
    result = {
        "Robot": node.get("name", "?"),
        "IP": str(host),
        "Brain": False,
        "Driver": False,
        "Uptime": "—",
        "Ping (ms)": None,
        "Status": "offline",
        "_base": base,
        "_headers": hdrs,
        "_online": False,
    }
    try:
        r = _req.get(f"{base}/health", headers=hdrs, timeout=2.5)
        elapsed = (_time.monotonic() - start) * 1000.0
        result["Ping (ms)"] = round(elapsed, 1)
        if r.status_code == 200:
            d = r.json()
            result["_online"] = True
            result["Brain"] = bool(d.get("brain"))
            result["Driver"] = bool(d.get("driver"))
            # Uptime
            try:
                s = int(float(d.get("uptime_s", 0)))
                h, rem = divmod(s, 3600)
                m, sc = divmod(rem, 60)
                result["Uptime"] = f"{h:02d}:{m:02d}:{sc:02d}" if h else f"{m:02d}:{sc:02d}"
            except Exception:
                pass
            if result["Brain"] and result["Driver"]:
                result["Status"] = "🟢 healthy"
            else:
                result["Status"] = "🟡 degraded"
    except Exception:
        elapsed = (_time.monotonic() - start) * 1000.0
        result["Ping (ms)"] = round(elapsed, 1)
        result["Status"] = "⚫ offline"
    return result


_fleet_nodes = _load_fleet_nodes()

if not _fleet_nodes:
    st.caption("No fleet nodes configured — add nodes to config/swarm.yaml")
else:
    import concurrent.futures as _cf

    with _cf.ThreadPoolExecutor(max_workers=len(_fleet_nodes)) as _ex:
        _fleet_results = list(_ex.map(_query_fleet_node, _fleet_nodes))

    # Build display DataFrame (exclude internal keys)
    import pandas as pd

    _display_cols = ["Robot", "IP", "Brain", "Driver", "Uptime", "Ping (ms)", "Status"]
    _fleet_df = pd.DataFrame([{k: r[k] for k in _display_cols} for r in _fleet_results])
    # Render booleans as checkmarks for readability
    _fleet_df["Brain"] = _fleet_df["Brain"].map(lambda v: "✅" if v else "❌")
    _fleet_df["Driver"] = _fleet_df["Driver"].map(lambda v: "✅" if v else "❌")

    st.dataframe(
        _fleet_df,
        hide_index=True,
        use_container_width=True,
        height=min(300, 36 + 36 * len(_fleet_results)),
    )

    # Send to fleet
    _fleet_col1, _fleet_col2 = st.columns([4, 1])
    with _fleet_col1:
        _fleet_instruction = st.text_input(
            "Send to fleet",
            placeholder="e.g. move forward 1 meter",
            key="fleet_instruction",
            label_visibility="collapsed",
        )
    with _fleet_col2:
        _fleet_send = st.button("Send to fleet", use_container_width=True)

    if _fleet_send and _fleet_instruction:
        _active_nodes = [r for r in _fleet_results if r["_online"]]
        if not _active_nodes:
            st.warning("No nodes online — cannot send command")
        else:
            _fleet_errors = []
            for _fr in _active_nodes:
                try:
                    _resp = _req.post(
                        f"{_fr['_base']}/api/command",
                        json={"instruction": _fleet_instruction},
                        headers=_fr["_headers"],
                        timeout=10,
                    )
                    if not _resp.ok:
                        _fleet_errors.append(f"{_fr['Robot']}: HTTP {_resp.status_code}")
                except Exception as _fe:
                    _fleet_errors.append(f"{_fr['Robot']}: {_fe}")
            if _fleet_errors:
                st.error("Some nodes failed: " + "; ".join(_fleet_errors))
            else:
                st.success(f"Command sent to {len(_active_nodes)} node(s)")

    # Per-node stop buttons
    if _fleet_results:
        st.markdown('<p class="panel-title">Per-node emergency stop</p>', unsafe_allow_html=True)
        _stop_cols = st.columns(min(len(_fleet_results), 6))
        for _i, _fr in enumerate(_fleet_results):
            _name = _fr["Robot"]
            with _stop_cols[_i % len(_stop_cols)]:
                if st.button("⏹", key=f"stop_{_name}", help=f"Stop {_name}"):
                    try:
                        _req.post(
                            f"{_fr['_base']}/api/stop",
                            headers=_fr["_headers"],
                            timeout=3,
                        )
                        st.toast(f"{_name} stopped", icon="⏹")
                    except Exception as _se:
                        st.toast(f"Stop failed: {_se}", icon="❌")


# ── BEHAVIORS PANEL ───────────────────────────────────────────────────────────
st.divider()
with st.expander("🎬 Behaviors", expanded=False):
    _beh_status = _get("/api/behavior/status")
    _beh_running = _beh_status.get("running", False)
    _beh_name = _beh_status.get("name") or "—"
    _beh_job_id = _beh_status.get("job_id") or "—"

    if _beh_running:
        st.success(f"Running: **{_beh_name}**  (job {_beh_job_id[:8]})")
    else:
        st.info("No behavior running")

    _beh_path = st.text_input(
        "Behavior file path",
        value="",
        placeholder="patrol.behavior.yaml",
        key="behavior_path_input",
    )
    _bcol1, _bcol2 = st.columns(2)
    with _bcol1:
        if st.button("Run", key="behavior_run_btn", use_container_width=True):
            if _beh_path.strip():
                try:
                    _br = _req.post(
                        f"{GW}/api/behavior/run",
                        json={"path": _beh_path.strip()},
                        headers=_hdr(),
                        timeout=5,
                    )
                    if _br.ok:
                        _bd = _br.json()
                        st.toast(
                            f"Started: {_bd.get('name', '?')} (job {_bd.get('job_id', '?')[:8]})",
                            icon="▶",
                        )
                    else:
                        st.toast(f"Error {_br.status_code}: {_br.text[:80]}", icon="❌")
                except Exception as _be:
                    st.toast(f"Request failed: {_be}", icon="❌")
            else:
                st.toast("Enter a behavior file path first", icon="⚠")
    with _bcol2:
        if st.button("Stop", key="behavior_stop_btn", use_container_width=True):
            try:
                _bs = _req.post(
                    f"{GW}/api/behavior/stop",
                    headers=_hdr(),
                    timeout=5,
                )
                if _bs.ok:
                    st.toast("Behavior stopped", icon="⏹")
                else:
                    st.toast(f"Stop error {_bs.status_code}", icon="❌")
            except Exception as _bse:
                st.toast(f"Stop failed: {_bse}", icon="❌")

# ── GAMEPAD PANEL ─────────────────────────────────────────────────────────────
st.divider()
with st.expander("🎮 Gamepad / Manual Drive", expanded=False):
    st.caption(
        "Connect a USB/Bluetooth gamepad and use the left stick to drive. "
        "Commands are sent to /api/command at ~10 Hz."
    )
    _gamepad_html = f"""
<div id="gp-status" style="font-family:monospace;font-size:0.8rem;color:#8b949e;margin-bottom:8px;">
  Gamepad: <span id="gp-name">Not connected — press any button to activate</span>
</div>
<div style="display:flex;gap:12px;align-items:center;margin-bottom:6px;">
  <div style="text-align:center;">
    <div id="gp-linear" style="font-size:1.4rem;color:#3fb950;">↕ 0.00</div>
    <div style="font-size:0.7rem;color:#6e7681;">linear</div>
  </div>
  <div style="text-align:center;">
    <div id="gp-angular" style="font-size:1.4rem;color:#58a6ff;">↔ 0.00</div>
    <div style="font-size:0.7rem;color:#6e7681;">angular</div>
  </div>
  <button id="gp-estop" style="padding:6px 18px;background:#da3633;color:white;border:none;
    border-radius:6px;cursor:pointer;font-size:0.85rem;">E-STOP</button>
</div>
<script>
(function() {{
  const GW = "{GW}";
  const TOKEN = "{st.session_state.get("token", "")}";
  const headers = TOKEN ? {{"Authorization": "Bearer " + TOKEN}} : {{}};
  let interval = null;
  let gpIndex = null;

  window.addEventListener("gamepadconnected", function(e) {{
    gpIndex = e.gamepad.index;
    document.getElementById("gp-name").textContent = e.gamepad.id;
    if (!interval) interval = setInterval(loop, 100);
  }});
  window.addEventListener("gamepaddisconnected", function(e) {{
    if (e.gamepad.index === gpIndex) {{
      gpIndex = null;
      clearInterval(interval); interval = null;
      document.getElementById("gp-name").textContent = "Disconnected";
    }}
  }});

  document.getElementById("gp-estop").onclick = function() {{
    fetch(GW + "/api/stop", {{method:"POST", headers:headers}});
  }};

  function deadzone(v, dz=0.12) {{ return Math.abs(v) < dz ? 0 : v; }}

  function loop() {{
    const gp = navigator.getGamepads ? navigator.getGamepads()[gpIndex] : null;
    if (!gp) return;
    const linear = -deadzone(gp.axes[1]);
    const angular = -deadzone(gp.axes[0]);
    document.getElementById("gp-linear").textContent = "↕ " + linear.toFixed(2);
    document.getElementById("gp-angular").textContent = "↔ " + angular.toFixed(2);
    if (Math.abs(linear) > 0.01 || Math.abs(angular) > 0.01) {{
      fetch(GW + "/api/action", {{
        method: "POST",
        headers: Object.assign({{"Content-Type":"application/json"}}, headers),
        body: JSON.stringify({{linear: linear, angular: angular}})
      }}).catch(function(){{}});
    }}
  }}
}})();
</script>
"""
    st.components.v1.html(_gamepad_html, height=130)


# ── SLAM MAP PANEL ─────────────────────────────────────────────────────────────
st.divider()
with st.expander("🗺 SLAM / Nav Map", expanded=False):
    _map_data = _get("/api/nav/map/current")
    _map_available = _map_data.get("available", False)

    _mcol1, _mcol2 = st.columns([3, 1])
    with _mcol1:
        if _map_available:
            _map_width = _map_data.get("width", 0)
            _map_height = _map_data.get("height", 0)
            _map_res = _map_data.get("resolution_m", 0)
            st.caption(
                f"Map: {_map_width}×{_map_height} cells | Resolution: {_map_res * 100:.1f} cm/cell"
            )
            _cells = _map_data.get("cells")
            if _cells:
                try:
                    import numpy as _np

                    _arr = _np.array(_cells, dtype=float)
                    # Normalize: -1=unknown→grey, 0=free→white, 100=occupied→black
                    _img = _np.zeros((_arr.shape[0], _arr.shape[1], 3), dtype=_np.uint8)
                    _img[_arr < 0] = [80, 80, 80]  # unknown: grey
                    _img[_arr == 0] = [230, 230, 230]  # free: light grey
                    _img[_arr > 50] = [20, 20, 20]  # occupied: dark
                    # Mark robot pose
                    _pose = _map_data.get("robot_pose", {})
                    if _pose:
                        _rx = int(_pose.get("x", 0))
                        _ry = int(_pose.get("y", 0))
                        if 0 <= _rx < _img.shape[1] and 0 <= _ry < _img.shape[0]:
                            _img[max(0, _ry - 2) : _ry + 3, max(0, _rx - 2) : _rx + 3] = [
                                63,
                                185,
                                80,
                            ]
                    import io as _io

                    from PIL import Image as _PILImg

                    _pil = _PILImg.fromarray(_img)
                    _buf = _io.BytesIO()
                    _pil.save(_buf, format="PNG")
                    st.image(_buf.getvalue(), caption="Occupancy Map", use_container_width=True)
                except Exception as _me:
                    st.warning(f"Map render error: {_me}")
            else:
                _map_img_url = _map_data.get("image_url")
                if _map_img_url:
                    st.image(f"{GW}{_map_img_url}", caption="SLAM Map", use_container_width=True)
                else:
                    st.info("Map data received but no cells/image available.")
        else:
            st.info("SLAM map not available — start navigation or enable SLAM in your RCAN config.")

    with _mcol2:
        st.metric("Map Status", "Active" if _map_available else "Unavailable")
        _nav_status = _get("/api/nav/status")
        if _nav_status.get("running"):
            st.metric("Nav Job", _nav_status.get("job_id", "?")[:8])
            st.metric("Distance", f"{_nav_status.get('distance_m', 0):.1f} m")
        if st.button("Clear Map", key="slam_clear_btn"):
            _r = _req.post(f"{GW}/api/nav/map/clear", headers=_hdr(), timeout=5)
            st.toast("Map cleared" if _r.ok else f"Error {_r.status_code}", icon="🗑")


# ── AUTO-REFRESH ──────────────────────────────────────────────────────────────
time.sleep(refresh_s)
st.rerun()

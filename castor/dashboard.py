"""
CastorDash — mobile-first, tab-based telemetry dashboard for OpenCastor.

Tabs:  🕹️ Control · 📊 Status · 💬 Chat · 🤖 Fleet · 🔧 Builder · ⚙️ Settings

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
    page_title="CastorDash",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={},
)

# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
  /* ── base light theme ── */
  .stApp { background-color: #f5f7fa !important; color: #0d0d0d !important; }
  #MainMenu, footer, header { visibility: hidden; }

  /* ── desktop padding; tighter on mobile ── */
  .block-container { padding: 0.75rem 1.25rem 1rem !important; max-width: 100% !important; }
  @media (max-width: 768px) {
    .block-container { padding: 0.4rem 0.4rem 0.5rem !important; }
  }

  /* ── touch-friendly buttons (min 48 px tall) ── */
  [data-testid="stButton"] > button {
    min-height: 48px !important;
    font-size: 0.95rem !important;
    touch-action: manipulation;
    -webkit-tap-highlight-color: transparent;
    -webkit-appearance: none;
    border-radius: 8px !important;
    background: #ffffff !important;
    color: #0d0d0d !important;
    border: 1px solid #d0d5dd !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08) !important;
  }
  [data-testid="stChatInput"] textarea { font-size: 1rem !important; }
  [data-testid="stTextInput"] input   { font-size: 1rem !important; min-height: 44px; }

  /* ── metric cards ── */
  [data-testid="stMetric"] {
    background: #ffffff !important;
    border-radius: 8px !important;
    padding: 14px 16px !important;
    border: 1px solid #d0d5dd !important;
    border-left: 3px solid #0057ff !important;
    box-shadow: 0 1px 4px rgba(0,0,0,0.07) !important;
  }
  [data-testid="stMetricValue"] { font-size: 1.1rem !important; font-weight: 700 !important; color: #0d0d0d !important; }
  [data-testid="stMetricLabel"] {
    font-size: 0.68rem !important; color: #555f6e !important;
    text-transform: uppercase; letter-spacing: 0.06em;
  }

  /* ── section headers ── */
  .sh {
    color: #0d0d0d; font-size: 0.78rem; font-weight: 700;
    letter-spacing: 0.08em; text-transform: uppercase;
    border-left: 3px solid #0057ff; padding-left: 9px;
    margin: 10px 0 5px 0;
  }
  .sh.g { border-left-color: #007a2f; }
  .sh.o { border-left-color: #b35a00; }
  .sh.r { border-left-color: #c00000; }

  /* ── status bar ── */
  .status-bar {
    background: #ffffff; border: 1px solid #d0d5dd; border-radius: 8px;
    padding: 8px 14px; margin-bottom: 8px; font-family: monospace; font-size: 0.82rem;
    white-space: nowrap; overflow-x: auto; -webkit-overflow-scrolling: touch;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    color: #0d0d0d;
  }
  @media (max-width: 768px) { .status-bar { font-size: 0.72rem; padding: 6px 8px; } }

  /* ── status dots ── */
  .dot-g { display:inline-block;width:9px;height:9px;border-radius:50%;
            background:#007a2f;box-shadow:0 0 5px #007a2f;margin-right:3px;}
  .dot-r { display:inline-block;width:9px;height:9px;border-radius:50%;
            background:#c00000;box-shadow:0 0 5px #c00000;margin-right:3px;}
  .dot-y { display:inline-block;width:9px;height:9px;border-radius:50%;
            background:#b35a00;box-shadow:0 0 4px #b35a00;margin-right:3px;}
  .dot-x { display:inline-block;width:9px;height:9px;border-radius:50%;
            background:#6b7280;margin-right:3px;}

  /* ── sensor badges ── */
  .bw { display:inline-block;background:#e6f4ec;color:#007a2f;border:1px solid #007a2f;
        border-radius:4px;font-size:0.62rem;font-weight:700;padding:1px 6px;text-transform:uppercase;}
  .bm { display:inline-block;background:#fff3e0;color:#b35a00;border:1px solid #b35a00;
        border-radius:4px;font-size:0.62rem;font-weight:700;padding:1px 6px;text-transform:uppercase;}
  .be { display:inline-block;background:#fdecea;color:#c00000;border:1px solid #c00000;
        border-radius:4px;font-size:0.62rem;font-weight:700;padding:1px 6px;text-transform:uppercase;}
  .bx { display:inline-block;background:#f0f1f3;color:#555f6e;border:1px solid #d0d5dd;
        border-radius:4px;font-size:0.62rem;font-weight:700;padding:1px 6px;text-transform:uppercase;}

  /* ── telem row ── */
  .tr { display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:6px;
        background:#ffffff;border:1px solid #d0d5dd;margin-bottom:5px;font-size:0.82rem;
        box-shadow:0 1px 2px rgba(0,0,0,0.05);}
  .tl { flex:1;font-weight:600;color:#0d0d0d;font-size:0.8rem; }
  .tv { color:#555f6e;font-size:0.76rem; }

  /* ── log terminal (keep dark for readability) ── */
  .log-term {
    background:#111827 !important;border:1px solid #374151 !important;border-radius:8px;
    padding:8px 12px;font-family:"JetBrains Mono","Fira Code","Consolas",monospace !important;
    font-size:0.7rem !important;color:#4ade80 !important;overflow-y:auto;max-height:320px;
  }

  /* ── camera offline pulse ── */
  @keyframes cam-pulse {
    0%,100% { border-color:#c00000; box-shadow:0 0 0px #c00000; }
    50%      { border-color:#ef4444; box-shadow:0 0 8px #c00000; }
  }

  /* ── tab label sizing ── */
  [data-testid="stTabs"] button { font-size: 0.82rem; padding: 8px 10px; color: #0d0d0d !important; }
  @media (max-width: 480px) {
    [data-testid="stTabs"] button { font-size: 0.7rem; padding: 6px 6px; }
  }

  /* ── misc ── */
  [data-testid="stDataFrame"] { font-size: 0.78rem; }
  hr { margin: 0.8rem 0 !important; }

  /* ── back-to-face link ── */
  .face-back {
    display:inline-block;padding:6px 14px;background:#0057ff;color:#fff !important;
    border-radius:8px;text-decoration:none;font-size:0.82rem;font-weight:600;
    box-shadow:0 1px 4px rgba(0,87,255,0.3);
  }

  /* iOS Safari scroll fix */
  .stTabContent, [data-testid="stVerticalBlock"] {
    -webkit-overflow-scrolling: touch;
  }
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
    "_latency_history": {},
    "dp_speed": 0.7,
    "dp_turn": 0.6,
    "face_style": "friendly",
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

GW = st.session_state.gateway_url


def _hdr() -> dict:
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


def _dot(ok, tc="#007a2f", fc="#c00000") -> str:
    color = tc if ok else fc
    return f'<span style="color:{color};font-size:0.9em;">●</span>'


def _badge(mode: str) -> str:
    if mode == "hardware":
        return '<span class="bw">HW</span>'
    if mode == "mock":
        return '<span class="bm">MOCK</span>'
    if mode == "error":
        return '<span class="be">ERR</span>'
    return '<span class="bx">OFFLINE</span>'


# ── fetch all data once per render ────────────────────────────────────────────
health = _get("/health")
status = _get("/api/status")
proc = _get("/api/fs/proc")
driver = _get("/api/driver/health")
learner = _get("/api/learner/stats")
hist = _get("/api/command/history?limit=8")
episodes = _get("/api/memory/episodes?limit=20")
usage = _get("/api/usage")
_imu_raw = _get("/api/imu/latest")
_imu_orient = _get("/api/imu/orientation")
_lidar_raw = _get("/api/lidar/scan")
_bat_raw = _get("/api/battery/latest")

# ACB telemetry — try to fetch from all driver IDs found in driver health
_acb_telemetry: dict = {}
_driver_type = driver.get("driver_type", "") if driver else ""
if "AcbDriver" in _driver_type:
    # Single AcbDriver — fetch using driver_id from health check (if available)
    _acb_id = (driver.get("driver_id") or driver.get("id", "acb")) if driver else "acb"
    _telem = _get(f"/api/drivers/{_acb_id}/telemetry")
    if _telem and not _telem.get("error"):
        _acb_telemetry[_acb_id] = _telem

_imu_mode = _imu_raw.get("mode", "offline") if _imu_raw else "offline"
_lidar_mode = _lidar_raw.get("mode", "offline") if _lidar_raw else "offline"
_bat_mode = _bat_raw.get("mode", "offline") if _bat_raw else "offline"

robot_name = status.get("robot_name", health.get("robot_name", "robot"))
uptime = health.get("uptime_s", 0)
brain_ok = health.get("brain")
driver_ok = health.get("driver")
channels_active = status.get("channels_active", health.get("channels", []))
_proc_hw = proc.get("hw", {})
_proc_loop = proc.get("loop", {})
cam_ok = str(_proc_hw.get("camera", "")).lower() in ("online", "true", "ok")
loop_count = _proc_loop.get("iteration", 0)
avg_lat = _proc_loop.get("latency_ms", 0)

# ── SIDEBAR (settings + quick actions) ────────────────────────────────────────
with st.sidebar:
    theme_choice = st.radio(
        "🎨 Theme",
        ["Dark", "Light"],
        horizontal=True,
        key="theme_choice",
        help="Toggle between dark and light mode"
    )
    if theme_choice == "Light":
        st.markdown("""
        <style>
        :root { color-scheme: light; }
        .stApp { background-color: #f8f8fc !important; color: #0f0f1a !important; }
        .stSidebar { background-color: #f0f0f8 !important; }
        .stMarkdown, .stText, label { color: #0f0f1a !important; }
        </style>
        """, unsafe_allow_html=True)
    st.markdown("### ⚙️ Settings")
    st.session_state.gateway_url = st.text_input("Gateway URL", value=st.session_state.gateway_url)
    st.session_state.api_token = st.text_input(
        "API Token", value=st.session_state.api_token, type="password"
    )
    refresh_s = st.slider("Refresh (s)", 1, 10, 3)
    st.divider()
    st.markdown("### 🛑 E-STOP")
    if st.button("⏹ EMERGENCY STOP", type="primary", use_container_width=True):
        try:
            _req.post(f"{GW}/api/stop", headers=_hdr(), timeout=3)
            st.warning("Motors disengaged!")
        except Exception as _e:
            st.error(f"E-stop failed: {_e}")
    if st.button("▶ Clear Stop", use_container_width=True):
        try:
            _req.post(f"{GW}/api/estop/clear", headers=_hdr(), timeout=3)
            st.success("Stop cleared")
        except Exception as _e:
            st.error(f"Clear failed: {_e}")
    st.divider()
    st.markdown("### 🎤 Voice")
    st.session_state.voice_mode = st.toggle("Continuous voice", value=st.session_state.voice_mode)
    if st.session_state.voice_mode:
        st.session_state.voice_speak_replies = st.checkbox(
            "Speak replies", value=st.session_state.voice_speak_replies
        )

# ── back-to-face link ────────────────────────────────────────────────────────
# Use the device's actual hostname (e.g. "alex") with .local suffix for mDNS,
# so the link resolves correctly from the browser regardless of how GW is set.
import socket as _socket  # noqa: E402

_gw_port = GW.split(":")[-1].split("/")[0] if GW.count(":") >= 2 else "8000"
_hn = _socket.gethostname()
_face_host = _hn if "." in _hn else f"{_hn}.local"
_face_style_qs = st.session_state.get("face_style", "friendly")
st.markdown(
    f'<a class="face-back" href="http://{_face_host}:{_gw_port}/face?style={_face_style_qs}">← Robot Face</a>',
    unsafe_allow_html=True,
)

# ── HEADER STATUS BAR ─────────────────────────────────────────────────────────
_ch_html = (
    " · ".join(f'<span style="color:#0057ff">{c}</span>' for c in channels_active)
    if channels_active
    else '<span style="color:#6b7280">no channels</span>'
)
st.markdown(
    f"""<div class="status-bar">
  🤖 <strong>{robot_name}</strong> &nbsp;
  {_dot(brain_ok)} brain <strong>{"on" if brain_ok else "off"}</strong> &nbsp;
  {_dot(driver_ok, "#007a2f", "#b35a00")} driver <strong>{"hw" if driver_ok else "mock"}</strong>
  &nbsp; 📡 {_ch_html} &nbsp;
  <span style="color:#6b7280">⏱ {_fmt_uptime(uptime)}</span> &nbsp;
  {_dot(cam_ok)} cam <strong>{"live" if cam_ok else "off"}</strong>
</div>""",
    unsafe_allow_html=True,
)

# ── TABS ──────────────────────────────────────────────────────────────────────
_tab_ctrl, _tab_status, _tab_chat, _tab_fleet, _tab_builder, _tab_emb, _tab_settings = st.tabs(
    ["🕹️ Control", "📊 Status", "💬 Chat", "🤖 Fleet", "🔧 Builder", "🧠 Embedding", "⚙️ Settings"]
)


# ═══════════════════════════════════════════════════════════════════════════════
# 🕹️ CONTROL TAB  (Mission Control)
# ═══════════════════════════════════════════════════════════════════════════════
with _tab_ctrl:
    # E-STOP row always at top
    _estop_c, _clr_c = st.columns(2)
    with _estop_c:
        if st.button("⏹ E-STOP", type="primary", use_container_width=True, key="ctrl_estop"):
            try:
                _req.post(f"{GW}/api/stop", headers=_hdr(), timeout=3)
                st.toast("Motors stopped!", icon="⏹")
            except Exception as _e:
                st.error(str(_e))
    with _clr_c:
        if st.button("▶ Clear", use_container_width=True, key="ctrl_clear"):
            try:
                _req.post(f"{GW}/api/estop/clear", headers=_hdr(), timeout=3)
                st.toast("Stop cleared", icon="▶")
            except Exception as _e:
                st.error(str(_e))

    st.divider()

    # ── Camera + D-pad side by side on desktop, stacked on mobile ─────────────
    _cam_col, _dpad_col = st.columns([3, 2], gap="medium")

    with _cam_col:
        st.markdown('<p class="sh g">📷 Live Camera</p>', unsafe_allow_html=True)
        _tok = st.session_state.api_token
        _tok_js = _tok.replace('"', '\\"') if _tok else ""
        _gw_host = GW.replace("http://", "").replace("https://", "").split(":")[0]
        _gw_port = GW.split(":")[-1].split("/")[0] if ":" in GW else "8000"
        _gw_proto = "https:" if GW.startswith("https") else "http:"
        if _gw_host in ("127.0.0.1", "localhost", ""):
            import socket as _socket

            try:
                _gw_host = _socket.gethostbyname(_socket.gethostname())
            except Exception:
                pass
        _cam_border = "2px solid #007a2f" if cam_ok else "2px solid #c00000"
        _cam_anim = "none" if cam_ok else "cam-pulse 2s ease-in-out infinite"
        st.components.v1.html(
            f"""
<style>
/* NOTE: mirrors the global cam-pulse keyframe in the main CSS block above — keep in sync */
@keyframes cam-pulse {{
  0%,100% {{ border-color:#c00000; box-shadow:0 0 0px #c00000; }}
  50%      {{ border-color:#ef4444; box-shadow:0 0 8px #c00000; }}
}}
</style>
<div style="background:#ffffff;border:{_cam_border};border-radius:8px;overflow:hidden;
            aspect-ratio:4/3;max-height:380px;position:relative;
            animation:{_cam_anim};">
  <img id="cam" src="" style="width:100%;height:100%;object-fit:cover;display:block;"
       onerror="document.getElementById('cam-err').style.display='flex';this.style.display='none';" />
  <div id="cam-err" style="display:none;position:absolute;inset:0;align-items:center;
       justify-content:center;flex-direction:column;color:#6b7280;font-family:monospace;
       font-size:0.85rem;background:#ffffff;">
    <div style="font-size:2rem;margin-bottom:8px;">📷</div><div>No camera signal</div>
  </div>
</div>
<script>
(function(){{
  var tok="{_tok_js}", port="{_gw_port}", cfgHost="{_gw_host}", proto="{_gw_proto}";
  var host=cfgHost;
  if(host==="127.0.0.1"||host==="localhost"||host===""){{
    try{{var wh=window.location.hostname;if(wh&&wh!=="")host=wh;}}catch(e){{}}
  }}
  var base=proto+"//"+host+":"+port+"/api/stream/mjpeg";
  var url=tok?base+"?token="+encodeURIComponent(tok):base;
  var img=document.getElementById("cam");
  if(img)img.src=url;
}})();
</script>""",
            height=400,
        )

        # Depth obstacle badges
        _depth = _get("/api/depth/obstacles")
        if _depth.get("available"):
            st.markdown('<p class="sh">📏 Obstacles</p>', unsafe_allow_html=True)
            _dl, _dc, _dr = st.columns(3)
            _dl.metric(
                "Left", f"{_depth.get('left_cm', 0):.0f} cm" if _depth.get("left_cm") else "—"
            )
            _dc.metric(
                "Center", f"{_depth.get('center_cm', 0):.0f} cm" if _depth.get("center_cm") else "—"
            )
            _dr.metric(
                "Right", f"{_depth.get('right_cm', 0):.0f} cm" if _depth.get("right_cm") else "—"
            )

    with _dpad_col:
        st.markdown('<p class="sh">🕹️ Manual Drive</p>', unsafe_allow_html=True)

        _spd = st.slider("Speed", 0.1, 1.0, st.session_state.dp_speed, 0.05, key="dp_speed_sl")
        _trn = st.slider("Turn", 0.1, 1.0, st.session_state.dp_turn, 0.05, key="dp_turn_sl")
        st.session_state.dp_speed = _spd
        st.session_state.dp_turn = _trn

        # D-pad — rich HTML/JS component with hold-to-move and active feedback
        st.components.v1.html(
            f"""<body style="margin:0;padding:0;overflow:hidden;touch-action:none;">
<style>
  .dpad {{
    display:grid;
    grid-template-columns:repeat(3,1fr);
    grid-template-rows:repeat(3,1fr);
    gap:6px;
    width:100%;
    max-width:280px;
    margin:0 auto;
  }}
  .dpad-btn {{
    min-width:72px;
    min-height:72px;
    width:min(90px,22vw);
    height:min(90px,22vw);
    background:#1e293b;
    color:#f8fafc;
    border:none;
    border-radius:10px;
    font-size:1.6rem;
    cursor:pointer;
    display:flex;
    align-items:center;
    justify-content:center;
    user-select:none;
    touch-action:none;
    transition:background 0.08s,transform 0.08s;
  }}
  .dpad-btn:active, .dpad-btn.pressed {{
    background:#2563eb;
    transform:scale(0.93);
  }}
  .dpad-empty {{ visibility:hidden; }}
</style>
<div class="dpad">
  <div></div>
  <button class="dpad-btn" id="dp_fwd" title="Forward">⬆</button>
  <div></div>
  <button class="dpad-btn" id="dp_left" title="Turn left">⬅</button>
  <button class="dpad-btn" id="dp_stop" title="Stop">⏹</button>
  <button class="dpad-btn" id="dp_right" title="Turn right">➡</button>
  <div></div>
  <button class="dpad-btn" id="dp_back" title="Backward">⬇</button>
  <div></div>
</div>
<script>
(function(){{
  var spd={_spd}, trn={_trn};
  var tok="{_tok_js}", cfgHost="{_gw_host}", port="{_gw_port}", proto="{_gw_proto}";
  var host=cfgHost;
  if(host==="127.0.0.1"||host==="localhost"||host===""){{
    try{{var wh=window.location.hostname;if(wh&&wh!=="")host=wh;}}catch(e){{}}
  }}
  var base=proto+"//"+host+":"+port+"/api/action";
  function hdrs(){{
    var h={{"Content-Type":"application/json"}};
    if(tok)h["Authorization"]="Bearer "+tok;
    return h;
  }}
  var _interval=null;
  var _activeBtn=null;
  var _actions={{
    dp_fwd:  {{"type":"move","linear":spd,"angular":0.0}},
    dp_left: {{"type":"move","linear":0.0,"angular":trn}},
    dp_right:{{"type":"move","linear":0.0,"angular":-trn}},
    dp_back: {{"type":"move","linear":-spd,"angular":0.0}},
    dp_stop: null
  }};
  function postAction(body){{
    fetch(base,{{method:"POST",headers:hdrs(),body:JSON.stringify(body)}}).catch(function(){{}});
  }}
  function sendStop(){{
    postAction({{"type":"move","linear":0.0,"angular":0.0}});
  }}
  function startHold(id){{
    if(_interval)return;
    var action=_actions[id];
    if(!action){{ sendStop(); return; }}
    postAction(action);
    _interval=setInterval(function(){{postAction(action);}},150);
  }}
  function endHold(){{
    if(_interval){{clearInterval(_interval);_interval=null;}}
    if(_activeBtn){{_activeBtn.classList.remove("pressed");_activeBtn=null;}}
    sendStop();
  }}
  ["dp_fwd","dp_left","dp_stop","dp_right","dp_back"].forEach(function(id){{
    var btn=document.getElementById(id);
    if(!btn)return;
    btn.addEventListener("pointerdown",function(e){{
      e.preventDefault();
      btn.setPointerCapture(e.pointerId);
      _activeBtn=btn;
      btn.classList.add("pressed");
      startHold(id);
    }});
    btn.addEventListener("pointerup",function(e){{e.preventDefault();endHold();}});
    btn.addEventListener("pointercancel",function(e){{e.preventDefault();endHold();}});
  }});
}})();
</script>
</body>""",
            height=320,
        )

        st.divider()
        st.markdown('<p class="sh">🎤 Voice</p>', unsafe_allow_html=True)
        if st.button("🎙️ Push to Talk", use_container_width=True, key="ptt_ctrl"):
            try:
                with st.spinner("Listening…"):
                    resp = _req.post(f"{GW}/api/voice/listen", headers=_hdr(), timeout=20)
                if resp.ok:
                    data = resp.json()
                    transcript = data.get("transcript", "")
                    thought = data.get("thought") or {}
                    if transcript:
                        st.toast(f"Heard: {transcript[:60]}", icon="🎙️")
                    if thought.get("raw_text"):
                        st.toast(f"Reply: {thought['raw_text'][:60]}", icon="🤖")
                else:
                    st.toast(f"PTT error: {resp.status_code}", icon="❌")
            except Exception as _ptt_e:
                st.toast(f"PTT: {_ptt_e}", icon="❌")

        if st.button("🎤 Server Mic (STT)", use_container_width=True, key="stt_ctrl"):
            try:
                with st.spinner("Listening…"):
                    resp = _req.post(f"{GW}/api/voice/listen", headers=_hdr(), timeout=20)
                if resp.ok:
                    data = resp.json()
                    text = data.get("transcript", "")
                    if text:
                        st.session_state["voice_input"] = text
                        st.toast(f"Heard: {text[:60]}", icon="🎤")
                    else:
                        st.toast("No speech detected", icon="🎤")
                else:
                    err = resp.json().get("detail", {})
                    msg = (
                        err.get("error", str(resp.status_code))
                        if isinstance(err, dict)
                        else str(err)
                    )
                    st.toast(f"STT: {msg}", icon="❌")
            except Exception as _stt_e:
                st.toast(f"STT: {_stt_e}", icon="❌")


# ═══════════════════════════════════════════════════════════════════════════════
# 📊 STATUS TAB
# ═══════════════════════════════════════════════════════════════════════════════
with _tab_status:
    # ── Sensor badges row ──────────────────────────────────────────────────────
    _s1, _s2, _s3, _s4, _s5 = st.columns(5)
    _sensors = [
        ("🧭 IMU", _imu_mode, _imu_raw.get("model", "") if _imu_raw else ""),
        ("📡 LiDAR", _lidar_mode, "RPLidar" if _lidar_mode == "hardware" else "none"),
        (
            "🔋 Battery",
            _bat_mode,
            f"{_bat_raw.get('voltage_v', 0):.1f}V"
            if _bat_raw and _bat_raw.get("voltage_v")
            else "no sensor",
        ),
        (
            "🦾 Driver",
            driver.get("mode", "offline"),
            (driver.get("driver_type") or "—").replace("Driver", ""),
        ),
        (
            "📷 Camera",
            "hardware" if cam_ok else "offline",
            status.get("camera_model", "USB 0") if cam_ok else "no signal",
        ),
    ]
    for col, (name, mode, detail) in zip([_s1, _s2, _s3, _s4, _s5], _sensors, strict=False):
        col.markdown(
            f'<div class="tr"><div><div class="tl">{name}</div>'
            f'<div class="tv">{detail}</div></div>{_badge(mode)}</div>',
            unsafe_allow_html=True,
        )

    st.divider()

    # ── Key metrics ──────────────────────────────────────────────────────────
    speaker_ok = str(_proc_hw.get("speaker", "")).lower() in ("online", "true", "ok")
    _today = (usage.get("daily") or [{}])[-1] if usage.get("daily") else {}
    _mc1, _mc2, _mc3, _mc4, _mc5, _mc6 = st.columns(6)
    _mc1.metric("Uptime", _fmt_uptime(uptime))
    _mc2.metric("Loops", str(loop_count))
    _mc3.metric("Latency", f"{avg_lat:.0f} ms" if avg_lat else "—")
    _mc4.metric("Camera", "live ●" if cam_ok else "offline ○")
    _mc5.metric("Speaker", "online" if speaker_ok else "offline")
    _mc6.metric("Tokens", f"{_today.get('total_tokens', 0):,}")

    _lt_raw = proc.get("brain", {}).get("last_thought") or {}
    last_thought = str(_lt_raw.get("raw_text", "") if isinstance(_lt_raw, dict) else _lt_raw)
    if last_thought:
        st.caption(f"💭 {last_thought[:120]}{'…' if len(last_thought) > 120 else ''}")

    st.divider()

    # ── Driver + Battery ──────────────────────────────────────────────────────
    _drv_col, _bat_col = st.columns(2)

    with _drv_col:
        st.markdown('<p class="sh o">🦾 Driver</p>', unsafe_allow_html=True)
        _dc1, _dc2 = st.columns(2)
        _drv_mode = driver.get("mode", "?")
        _drv_type = (
            (driver.get("driver_type") or "—").replace("PCA9685RC", "RC").replace("Driver", "")
        )
        _dc1.metric("Mode", (_drv_mode or "—").capitalize())
        _dc2.metric("Type", _drv_type)
        if driver.get("error"):
            st.caption(f"ℹ️ {str(driver['error'])[:60]}")

    with _bat_col:
        st.markdown('<p class="sh g">🔋 Battery</p>', unsafe_allow_html=True)
        if _bat_mode == "mock":
            st.warning("Mock — no battery sensor", icon=None)
        elif _bat_raw and (_bat_raw.get("available") or _bat_raw.get("voltage_v") is not None):
            _bv = _bat_raw.get("voltage_v")
            _bc = _bat_raw.get("current_ma")
            _bb1, _bb2 = st.columns(2)
            _bb1.metric("Voltage", f"{_bv:.1f}V" if _bv is not None else "—")
            _bb2.metric("Current", f"{_bc:.0f}mA" if _bc is not None else "—")
            if _bv is not None:
                _bat_min = float(os.getenv("CASTOR_BAT_MIN_V", "3.0"))
                _bat_max = float(os.getenv("CASTOR_BAT_MAX_V", "4.2"))
                _bat_rng = _bat_max - _bat_min
                _bat_pct = max(0.0, min(1.0, (_bv - _bat_min) / _bat_rng)) if _bat_rng else 0.5
                _bat_ico = "🟢" if _bat_pct > 0.5 else ("🟡" if _bat_pct > 0.2 else "🔴")
                st.progress(_bat_pct, text=f"{_bat_ico} {_bat_pct:.0%}")
        else:
            st.caption("No sensor")

    st.divider()

    # ── Brain ──────────────────────────────────────────────────────────────────
    st.markdown('<p class="sh">🧠 Brain</p>', unsafe_allow_html=True)
    _bp = status.get("brain_primary") or {}
    _bs = status.get("brain_secondary") or []
    _bam = status.get("brain_active_model")
    if _bp:
        _bp_label = f"{_bp.get('provider', '?')} / {_bp.get('model', '?')}"
        _active_tag = (
            " ← active" if (_bam and _bp.get("model") and _bam.endswith(_bp["model"])) else ""
        )
        st.markdown(f"**Primary:** `{_bp_label}`{_active_tag}")
    if _bs:
        for _s in _bs:
            _s_label = f"{_s.get('provider', '?')} / {_s.get('model', '?')}"
            _s_tags = ", ".join(_s.get("tags") or [])
            _active_tag2 = (
                " ← active" if (_bam and _s.get("model") and _bam.endswith(_s["model"])) else ""
            )
            st.markdown(
                f"**Secondary:** `{_s_label}`{_active_tag2}"
                + (f" — _{_s_tags}_" if _s_tags else "")
            )
    elif not _bp:
        st.caption("No brain config")

    st.divider()

    # ── Channels ──────────────────────────────────────────────────────────────
    st.markdown('<p class="sh">📡 Channels</p>', unsafe_allow_html=True)
    _ch_active = set(channels_active)
    _CH_NAMES = {
        "whatsapp": "WhatsApp",
        "whatsapp_twilio": "WhatsApp (Twilio)",
        "telegram": "Telegram",
        "discord": "Discord",
        "slack": "Slack",
        "mqtt": "MQTT",
        "homeassistant": "Home Assistant",
    }
    if _ch_active:
        _badges = " ".join(
            f'<span style="background:#1a6e3c;color:#fff;padding:2px 10px;border-radius:12px;font-size:0.85rem;">'
            f"🟢 {_CH_NAMES.get(_cn, _cn.replace('_', ' ').title())}</span>"
            for _cn in sorted(_ch_active)
        )
        st.markdown(_badges, unsafe_allow_html=True)
    else:
        st.caption("No active channels")

    st.divider()

    # ── Learner + Cache + Fallback ─────────────────────────────────────────────
    _lc1, _lc2, _lc3 = st.columns(3)
    with _lc1:
        st.markdown('<p class="sh">🧠 Learner</p>', unsafe_allow_html=True)
        if learner.get("available"):
            _l1, _l2 = st.columns(2)
            _l1.metric("Episodes", learner.get("episodes_analyzed", 0))
            _l2.metric("Applied", learner.get("improvements_applied", 0))
        else:
            st.caption("No data yet")

    _cs = _get("/api/cache/stats")
    with _lc2:
        st.markdown('<p class="sh">⚡ Cache</p>', unsafe_allow_html=True)
        if _cs.get("entries") is not None:
            _cc1, _cc2 = st.columns(2)
            _cc1.metric("Hit rate", f"{_cs.get('hit_rate_pct', 0):.1f}%")
            _cc2.metric("Entries", _cs.get("entries", 0))
        else:
            st.caption("No data")

    with _lc3:
        st.markdown('<p class="sh">🔌 Fallback</p>', unsafe_allow_html=True)
        _fb = status.get("offline_fallback", {})
        if _fb.get("enabled"):
            st.metric("Active", "Yes" if _fb.get("using_fallback") else "No")
            st.caption(_fb.get("fallback_provider", "—"))
        else:
            st.caption("Disabled")

    st.divider()

    # ── Object detection ──────────────────────────────────────────────────────
    _det = _get("/api/detection/latest")
    if _det.get("detections") is not None:
        st.markdown('<p class="sh">👁 Detection</p>', unsafe_allow_html=True)
        _dets = _det.get("detections", [])
        st.caption(
            f"Mode: {_det.get('mode', 'mock')} · {_det.get('latency_ms', 0):.0f}ms · {len(_dets)} objects"
        )
        if _dets:
            for _d in _dets[:5]:
                _conf = _d.get("confidence", 0)
                st.write(
                    f"{'🟢' if _conf > 0.7 else '🟡'} **{_d.get('class', '?')}** ({_conf:.0%})"
                )

    st.divider()

    # ── Recent commands ────────────────────────────────────────────────────────
    st.markdown('<p class="sh">🕒 Recent Commands</p>', unsafe_allow_html=True)
    _hist_entries = hist.get("history", [])
    if _hist_entries:
        import pandas as _pd2

        _hist_rows = []
        for _e in reversed(_hist_entries):
            _ts = _e.get("ts", "")
            _hist_rows.append(
                {
                    "Time": _ts[11:16] if len(_ts) > 15 else _ts[:5],
                    "Command": str(_e.get("instruction", ""))[:40],
                    "Response": str(_e.get("action") or _e.get("raw_text") or "")[:60],
                }
            )
        st.dataframe(
            _pd2.DataFrame(_hist_rows),
            hide_index=True,
            use_container_width=True,
            height=min(220, 36 + 36 * len(_hist_rows)),
        )
    else:
        st.caption("No commands yet")

    # ── IMU orientation ────────────────────────────────────────────────────────
    if _imu_orient and not _imu_orient.get("error"):
        st.divider()
        st.markdown('<p class="sh">🧭 IMU Orientation</p>', unsafe_allow_html=True)
        _io1, _io2, _io3 = st.columns(3)
        _io1.metric("Yaw", f"{_imu_orient.get('yaw_deg', 0):.1f}°")
        _io2.metric("Pitch", f"{_imu_orient.get('pitch_deg', 0):.1f}°")
        _io3.metric("Roll", f"{_imu_orient.get('roll_deg', 0):.1f}°")

    # ── ACB Actuators (#524) ────────────────────────────────────────────────────
    if _acb_telemetry:
        st.divider()
        st.markdown('<p class="sh">⚙️ Actuators (HLabs ACB)</p>', unsafe_allow_html=True)
        for _joint_id, _tel in _acb_telemetry.items():
            _err = int(_tel.get("error_flags", 0))
            _mode_badge = "🟢" if _tel.get("control_mode") else "⚪"
            _err_badge = "🔴 ERROR" if _err else "✅"
            st.markdown(
                f"**{_joint_id}** — mode: `{_tel.get('control_mode', '?')}` "
                f"{_mode_badge}  {_err_badge}"
            )
            _ac1, _ac2, _ac3 = st.columns(3)
            _ac1.metric("Position", f"{_tel.get('pos_rad', 0.0):.3f} rad")
            _ac2.metric("Velocity", f"{_tel.get('vel_rad_s', 0.0):.3f} rad/s")
            _ac3.metric("Current", f"{_tel.get('current_a', 0.0):.2f} A")


# ═══════════════════════════════════════════════════════════════════════════════
# 💬 CHAT TAB
# ═══════════════════════════════════════════════════════════════════════════════
with _tab_chat:
    # Quick voice buttons
    _vb1, _vb2, _ = st.columns([1, 1, 3])
    with _vb1:
        if st.button("🎙️ PTT", use_container_width=True, key="ptt_chat"):
            try:
                with st.spinner("Listening…"):
                    _r = _req.post(f"{GW}/api/voice/listen", headers=_hdr(), timeout=20)
                if _r.ok:
                    _d = _r.json()
                    if _d.get("transcript"):
                        st.session_state["voice_input"] = _d["transcript"]
            except Exception as _e:
                st.toast(str(_e), icon="❌")
    with _vb2:
        if st.button("🗑 Clear chat", use_container_width=True, key="clr_chat"):
            st.session_state.messages = []
            st.rerun()

    # Chat history
    _msg_box = st.container(height=340)
    with _msg_box:
        for _m in st.session_state.messages[-20:]:
            with st.chat_message(_m["role"]):
                st.markdown(_m["content"])
                if _m["role"] == "assistant" and _m.get("model_used"):
                    st.caption(f"via {_m['model_used']}")

    # Chat input
    _prompt = st.chat_input("Type a command or question…")
    _user_text = _prompt or st.session_state.pop("voice_input", None)
    if _user_text:
        st.session_state.messages.append({"role": "user", "content": _user_text})
        with st.spinner("Thinking…"):
            try:
                _cr = _req.post(
                    f"{GW}/api/command",
                    json={"instruction": _user_text},
                    headers=_hdr(),
                    timeout=30,
                )
                _cr_json = _cr.json() if _cr.ok else {}
                _reply = (
                    _cr_json.get("raw_text", str(_cr.json())) if _cr.ok else f"[{_cr.status_code}]"
                )
                _model_used = _cr_json.get("model_used")
            except Exception as _ce:
                _reply = f"[error] {_ce}"
                _model_used = None
        st.session_state.messages.append(
            {"role": "assistant", "content": _reply, "model_used": _model_used}
        )
        if st.session_state.voice_mode and st.session_state.voice_speak_replies:
            _safe = _reply.replace("\\", "\\\\").replace("`", "\\`").replace('"', '\\"')
            st.components.v1.html(
                f"<script>(()=>{{const u=new SpeechSynthesisUtterance(`{_safe}`);"
                "u.lang='en-US';window.speechSynthesis.cancel();window.speechSynthesis.speak(u);}})();</script>",
                height=0,
            )
        st.rerun()

    st.divider()

    # Episode history
    with st.expander(f"🧠 Episode Memory — {episodes.get('total', 0)} total", expanded=False):
        _ep_list = episodes.get("episodes", [])
        if _ep_list:
            import pandas as _pd3

            _ep_rows = []
            for _ep in _ep_list:
                _ets = _ep.get("ts", "")
                _ep_rows.append(
                    {
                        "Time": _ets[11:19] if len(_ets) > 18 else _ets,
                        "Instruction": str(_ep.get("instruction", ""))[:40],
                        "Action": (_ep.get("action") or {}).get(
                            "type", _ep.get("action_type", "—")
                        ),
                        "Latency ms": f"{_ep.get('latency_ms', 0):.0f}",
                        "Outcome": _ep.get("outcome", "—")[:20],
                    }
                )
            st.dataframe(
                _pd3.DataFrame(_ep_rows),
                hide_index=True,
                use_container_width=True,
                height=min(260, 36 + 36 * len(_ep_rows)),
            )

            # Timeline bar chart
            try:
                import pandas as _pd4

                _tl = []
                for _ep in _ep_list:
                    _ets = _ep.get("ts")
                    if _ets:
                        try:
                            _epoch = float(_ets)
                        except (TypeError, ValueError):
                            import datetime as _dt

                            _epoch = _dt.datetime.fromisoformat(str(_ets)).timestamp()
                        _tl.append(
                            {
                                "ts": _epoch,
                                "action": (_ep.get("action") or {}).get("type", "?"),
                                "n": 1,
                            }
                        )
                if _tl:
                    _tl_df = _pd4.DataFrame(_tl)
                    _tl_df["min"] = _pd4.to_datetime(_tl_df["ts"], unit="s").dt.floor("1min")
                    _tl_piv = _tl_df.groupby(["min", "action"])["n"].sum().unstack(fill_value=0)
                    st.bar_chart(_tl_piv, height=140, use_container_width=True)
            except Exception:
                pass

            # Replay buttons
            st.markdown("**Replay an episode:**")
            for _ep in _ep_list[:10]:
                _ep_id = _ep.get("id", "")
                _at = (_ep.get("action") or {}).get("type", "—")
                _ets = _ep.get("ts", "")
                _lbl = f"{_ets[11:19] if len(_ets) > 18 else _ets}  {str(_ep.get('instruction', ''))[:28]}  [{_at}]"
                if st.button("▶", key=f"replay_{_ep_id}", help=f"Replay: {_lbl}"):
                    try:
                        _rr = _req.post(
                            f"{GW}/api/memory/replay/{_ep_id}", headers=_hdr(), timeout=5
                        )
                        st.toast("Replayed ✓" if _rr.ok else f"Failed: {_rr.status_code}", icon="▶")
                    except Exception as _re:
                        st.toast(str(_re), icon="❌")
        else:
            st.caption("No episodes yet — start the runtime loop to capture them")


# ═══════════════════════════════════════════════════════════════════════════════
# 🤖 FLEET TAB
# ═══════════════════════════════════════════════════════════════════════════════
with _tab_fleet:

    def _load_fleet_nodes():
        from pathlib import Path

        try:
            import yaml as _yaml
        except ImportError:
            return []
        _here = Path(__file__).resolve().parent.parent
        for c in [
            Path(os.getenv("OPENCASTOR_CONFIG", "")).parent / "swarm.yaml"
            if os.getenv("OPENCASTOR_CONFIG")
            else None,
            _here / "config" / "swarm.yaml",
            Path("config/swarm.yaml"),
        ]:
            if c and c.exists():
                try:
                    return (_yaml.safe_load(c.read_text()) or {}).get("nodes", [])
                except Exception:
                    pass
        return []

    def _query_node(node):
        import time as _t

        host = node.get("ip") or node.get("host", "localhost")
        port = node.get("port", 8000)
        base = f"http://{host}:{port}"
        tok = node.get("token", "")
        hdrs = {"Authorization": f"Bearer {tok}"} if tok else {}
        res = {
            "Robot": node.get("name", "?"),
            "IP": str(host),
            "Brain": False,
            "Driver": False,
            "Uptime": "—",
            "Ping ms": None,
            "Status": "offline",
            "_base": base,
            "_hdrs": hdrs,
            "_online": False,
        }
        t0 = _t.monotonic()
        try:
            r = _req.get(f"{base}/health", headers=hdrs, timeout=2.5)
            ms = (_t.monotonic() - t0) * 1000
            res["Ping ms"] = round(ms, 1)
            if r.status_code == 200:
                d = r.json()
                res["_online"] = True
                res["Brain"] = bool(d.get("brain"))
                res["Driver"] = bool(d.get("driver"))
                try:
                    s = int(float(d.get("uptime_s", 0)))
                    h, rem = divmod(s, 3600)
                    m, sc = divmod(rem, 60)
                    res["Uptime"] = f"{h:02d}:{m:02d}:{sc:02d}" if h else f"{m:02d}:{sc:02d}"
                except Exception:
                    pass
                res["Status"] = "🟢 healthy" if (res["Brain"] and res["Driver"]) else "🟡 degraded"
        except Exception:
            res["Ping ms"] = round((_t.monotonic() - t0) * 1000, 1)
            res["Status"] = "⚫ offline"
        return res

    _fn = _load_fleet_nodes()
    if not _fn:
        st.info("No fleet nodes configured — add nodes to config/swarm.yaml")
    else:
        import concurrent.futures as _cf

        with _cf.ThreadPoolExecutor(max_workers=len(_fn)) as _ex:
            _fr = list(_ex.map(_query_node, _fn))

        import pandas as _pd5

        _dcols = ["Robot", "IP", "Brain", "Driver", "Uptime", "Ping ms", "Status"]
        _fdf = _pd5.DataFrame([{k: r[k] for k in _dcols} for r in _fr])
        _fdf["Brain"] = _fdf["Brain"].map(lambda v: "✅" if v else "❌")
        _fdf["Driver"] = _fdf["Driver"].map(lambda v: "✅" if v else "❌")
        st.dataframe(
            _fdf, hide_index=True, use_container_width=True, height=min(280, 36 + 36 * len(_fr))
        )

        # Fleet command
        _fi1, _fi2 = st.columns([4, 1])
        with _fi1:
            _finstr = st.text_input(
                "Fleet command",
                placeholder="e.g. move forward 1 meter",
                key="fleet_instr",
                label_visibility="collapsed",
            )
        with _fi2:
            _fsend = st.button("Send", use_container_width=True, key="fleet_send")
        if _fsend and _finstr:
            _active = [r for r in _fr if r["_online"]]
            _errs = []
            for _r in _active:
                try:
                    _rr = _req.post(
                        f"{_r['_base']}/api/command",
                        json={"instruction": _finstr},
                        headers=_r["_hdrs"],
                        timeout=10,
                    )
                    if not _rr.ok:
                        _errs.append(f"{_r['Robot']}: {_rr.status_code}")
                except Exception as _fe:
                    _errs.append(f"{_r['Robot']}: {_fe}")
            st.error("Errors: " + "; ".join(_errs)) if _errs else st.success(
                f"Sent to {len(_active)} node(s)"
            )

        # Per-node stop
        if _fr:
            st.markdown('<p class="sh r">⏹ Per-node stop</p>', unsafe_allow_html=True)
            _scols = st.columns(min(len(_fr), 6))
            for _i, _r in enumerate(_fr):
                with _scols[_i % len(_scols)]:
                    if st.button(
                        f"⏹ {_r['Robot']}", key=f"stop_{_r['Robot']}", use_container_width=True
                    ):
                        try:
                            _req.post(f"{_r['_base']}/api/stop", headers=_r["_hdrs"], timeout=3)
                            st.toast(f"{_r['Robot']} stopped", icon="⏹")
                        except Exception as _se:
                            st.toast(str(_se), icon="❌")


# ═══════════════════════════════════════════════════════════════════════════════
# 🔧 BUILDER TAB
# ═══════════════════════════════════════════════════════════════════════════════
with _tab_builder:
    # ── Live Logs (expanded by default for builders) ───────────────────────────
    with st.expander("📋 Gateway Logs", expanded=True):
        import subprocess as _sp
        from pathlib import Path as _Path

        _log_lines, _log_src = [], ""
        try:
            _jctl = _sp.run(
                [
                    "journalctl",
                    "--user",
                    "-u",
                    "opencastor.service",
                    "-n",
                    "60",
                    "--no-pager",
                    "--output=short",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if _jctl.returncode == 0 and _jctl.stdout.strip():
                _log_lines = _jctl.stdout.splitlines()
                _log_src = "journalctl --user -u opencastor.service"
        except Exception:
            pass
        if not _log_lines:
            for _lp in ["/tmp/alex_gateway.log", "/tmp/bob_gateway.log", "/tmp/castor_gateway.log"]:
                try:
                    _log_lines = _Path(_lp).read_text(errors="replace").splitlines()[-60:]
                    _log_src = _lp
                    break
                except Exception:
                    continue
        if _log_lines:
            st.caption(f"Source: {_log_src}")
            st.code("\n".join(_log_lines[-60:]), language=None)
        else:
            st.caption("No logs found — check systemd service or log file paths")

    st.divider()

    # ── Driver health (key for builders) ──────────────────────────────────────
    with st.expander("🦾 Driver & Hardware", expanded=True):
        _dhc1, _dhc2, _dhc3 = st.columns(3)
        _dhc1.metric("Mode", (driver.get("mode") or "—").capitalize())
        _dhc2.metric("OK", "✅" if driver.get("ok") else "❌")
        _dhc3.metric("Type", (driver.get("driver_type") or "—").replace("Driver", ""))
        if driver.get("error"):
            st.error(driver["error"])
        # Raw action test
        st.markdown("**Test a raw move command:**")
        _tc1, _tc2, _tc3 = st.columns(3)
        _test_lin = _tc1.number_input("linear", -1.0, 1.0, 0.0, 0.1, key="test_lin")
        _test_ang = _tc2.number_input("angular", -1.0, 1.0, 0.0, 0.1, key="test_ang")
        _test_dur = _tc3.number_input("duration ms", 0, 5000, 500, 100, key="test_dur")
        if st.button("⚙️ Send Test Action", key="test_action"):
            try:
                _tr = _req.post(
                    f"{GW}/api/action",
                    json={
                        "type": "move",
                        "linear": _test_lin,
                        "angular": _test_ang,
                        "duration_ms": int(_test_dur),
                    },
                    headers=_hdr(),
                    timeout=3,
                )
                st.success(str(_tr.json())) if _tr.ok else st.error(str(_tr.json()))
            except Exception as _te:
                st.error(str(_te))

    st.divider()

    # ── Behaviors & Missions ────────────────────────────────────────────────────
    with st.expander("🎬 Behaviors & Missions", expanded=False):
        _beh = _get("/api/behavior/status")
        if _beh.get("running"):
            st.success(
                f"Running: **{_beh.get('name', '?')}**  (job {str(_beh.get('job_id', ''))[:8]})"
            )
        else:
            st.info("No behavior running")
        _bp = st.text_input(
            "Behavior / mission file", placeholder="patrol.behavior.yaml", key="beh_path"
        )
        _bb1, _bb2 = st.columns(2)
        with _bb1:
            if st.button("▶ Run", key="mc_launch", use_container_width=True):
                if _bp.strip():
                    try:
                        _br = _req.post(
                            f"{GW}/api/behavior/run",
                            json={"path": _bp.strip()},
                            headers=_hdr(),
                            timeout=5,
                        )
                        st.toast(
                            f"Started: {_br.json().get('name', '?')}"
                            if _br.ok
                            else f"Error {_br.status_code}",
                            icon="▶" if _br.ok else "❌",
                        )
                    except Exception as _be:
                        st.toast(str(_be), icon="❌")
                else:
                    st.toast("Enter a file path first", icon="⚠")
        with _bb2:
            if st.button("⏹ Stop", key="mc_stop", use_container_width=True):
                try:
                    _bs = _req.post(f"{GW}/api/behavior/stop", headers=_hdr(), timeout=5)
                    st.toast(
                        "Stopped" if _bs.ok else f"Error {_bs.status_code}",
                        icon="⏹" if _bs.ok else "❌",
                    )
                except Exception as _bse:
                    st.toast(str(_bse), icon="❌")

        # Mission history
        try:
            from castor.dashboard_memory_timeline import MemoryTimeline as _MLT

            _mlt = _MLT()
            _mo = _mlt.get_outcome_summary(window_h=24)
            _mpc = _mlt.get_latency_percentiles(window_h=24)
            _m1, _m2, _m3 = st.columns(3)
            _m1.metric("Episodes (24h)", _mo.get("total", 0))
            _m2.metric("Success rate", f"{_mo.get('ok_rate', 0) * 100:.0f}%")
            _m3.metric("p50 latency", f"{_mpc.get('p50_ms') or 0:.0f} ms")
        except Exception:
            pass

    st.divider()

    # ── Provider Health ────────────────────────────────────────────────────────
    with st.expander("🧠 Provider Health", expanded=False):
        _ph = _get("/api/pool/health")
        if not _ph or _ph.get("error"):
            st.caption("Not available — configure pool provider to enable.")
        else:
            _pc1, _pc2, _pc3 = st.columns(3)
            _pc1.metric("Strategy", _ph.get("strategy", "—"))
            _pc2.metric("Pool size", _ph.get("pool_size", 0))
            _pc3.metric("Degraded", _ph.get("degraded_count", 0))
            _ph_members = _ph.get("members", [])
            if _ph_members:
                import pandas as _pd6

                st.dataframe(
                    _pd6.DataFrame(
                        [
                            {
                                "Index": m.get("pool_index", "?"),
                                "Mode": m.get("mode", "—"),
                                "OK": "✅" if m.get("ok") else "❌",
                                "Error": str(m.get("error", ""))[:50],
                            }
                            for m in _ph_members
                        ]
                    ),
                    hide_index=True,
                    use_container_width=True,
                    height=min(180, 36 + 36 * len(_ph_members)),
                )
            # Latency sparkline
            _ema_now = (_ph.get("adaptive") or {}).get("ema_latency_ms", {})
            if _ema_now:
                _lh = st.session_state.get("_latency_history", {})
                for _k, _v in _ema_now.items():
                    _lh.setdefault(str(_k), []).append(round(float(_v), 1))
                    _lh[str(_k)] = _lh[str(_k)][-20:]
                st.session_state["_latency_history"] = _lh
                if any(len(v) > 1 for v in _lh.values()):
                    try:
                        import pandas as _pd7

                        st.line_chart(
                            _pd7.DataFrame({f"Pool[{k}]": v for k, v in _lh.items() if v}),
                            height=110,
                            use_container_width=True,
                        )
                        st.caption("EMA latency (ms) per provider — last 20 refreshes")
                    except Exception:
                        pass

    st.divider()

    # ── LiDAR Scan ────────────────────────────────────────────────────────────
    with st.expander("📡 LiDAR Scan", expanded=False):
        _ls = _lidar_raw
        if _ls.get("mode") == "mock":
            st.warning("LiDAR in mock mode — no RPLidar connected", icon=None)
        if not _ls.get("points"):
            st.caption("LiDAR not available — connect an RPLidar sensor.")
        else:
            _pts = _ls.get("points", [])
            st.caption(f"Scan: {_ls.get('timestamp', '—')} — {len(_pts)} points")
            try:
                import math

                import pandas as _pd8

                _ang = [math.radians(p.get("angle_deg", 0)) for p in _pts]
                _dst = [p.get("distance_m", 0) for p in _pts]
                _ldf = _pd8.DataFrame(
                    {
                        "x_m": [r * math.cos(a) for r, a in zip(_dst, _ang, strict=False)],
                        "y_m": [r * math.sin(a) for r, a in zip(_dst, _ang, strict=False)],
                        "dist_m": _dst,
                    }
                )
                st.scatter_chart(_ldf, x="x_m", y="y_m", size="dist_m", height=280)
            except Exception as _lde:
                st.caption(f"Plot unavailable: {_lde}")

    st.divider()

    # ── SLAM / Nav Map ─────────────────────────────────────────────────────────
    with st.expander("🗺 SLAM / Nav Map", expanded=False):
        _md = _get("/api/nav/map/current")
        _ma = _md.get("available", False)
        if not _ma:
            st.info("SLAM map not available — enable SLAM in your RCAN config.")
        else:
            st.caption(
                f"Map: {_md.get('width', 0)}×{_md.get('height', 0)} cells  "
                f"res={_md.get('resolution_m', 0) * 100:.1f} cm/cell"
            )
            _cells = _md.get("cells")
            if _cells:
                try:
                    import io as _io

                    import numpy as _np
                    from PIL import Image as _PI

                    _arr = _np.array(_cells, dtype=float)
                    _img = _np.zeros((_arr.shape[0], _arr.shape[1], 3), dtype=_np.uint8)
                    _img[_arr < 0] = [80, 80, 80]
                    _img[_arr == 0] = [230, 230, 230]
                    _img[_arr > 50] = [20, 20, 20]
                    _pose = _md.get("robot_pose", {})
                    if _pose:
                        _rx, _ry = int(_pose.get("x", 0)), int(_pose.get("y", 0))
                        if 0 <= _rx < _img.shape[1] and 0 <= _ry < _img.shape[0]:
                            _img[max(0, _ry - 2) : _ry + 3, max(0, _rx - 2) : _rx + 3] = [
                                63,
                                185,
                                80,
                            ]
                    _buf = _io.BytesIO()
                    _PI.fromarray(_img).save(_buf, format="PNG")
                    st.image(_buf.getvalue(), caption="Occupancy Map", use_container_width=True)
                except Exception as _me:
                    st.warning(f"Render error: {_me}")
        _nav = _get("/api/nav/status")
        if _nav.get("running"):
            st.metric("Nav job", _nav.get("job_id", "?")[:8])
        if st.button("🗑 Clear Map", key="slam_clear"):
            try:
                _req.post(f"{GW}/api/nav/map/clear", headers=_hdr(), timeout=5)
                st.toast("Map cleared", icon="🗑")
            except Exception as _mce:
                st.toast(str(_mce), icon="❌")

    st.divider()

    # ── Runtime controls ────────────────────────────────────────────────────────
    with st.expander("⚙️ Runtime Controls", expanded=False):
        _rc1, _rc2 = st.columns(2)
        with _rc1:
            st.markdown("**Gateway**")
            if st.button("⏸ Pause loop", key="rt_pause", use_container_width=True):
                try:
                    _req.post(f"{GW}/api/runtime/pause", headers=_hdr(), timeout=3)
                    st.toast("Paused", icon="⏸")
                except Exception as _pe:
                    st.toast(str(_pe), icon="❌")
            if st.button("▶ Resume loop", key="rt_resume", use_container_width=True):
                try:
                    _req.post(f"{GW}/api/runtime/resume", headers=_hdr(), timeout=3)
                    st.toast("Resumed", icon="▶")
                except Exception as _re:
                    st.toast(str(_re), icon="❌")
        with _rc2:
            st.markdown("**Host**")
            if st.button("↺ Reboot host", key="rt_reboot", use_container_width=True):
                try:
                    _req.post(f"{GW}/api/system/reboot", headers=_hdr(), timeout=3)
                    st.warning("Rebooting…")
                except Exception as _rbe:
                    st.error(str(_rbe))
            if st.button("⏻ Shutdown host", key="rt_shutdown", use_container_width=True):
                try:
                    _req.post(f"{GW}/api/system/shutdown", headers=_hdr(), timeout=3)
                    st.warning("Shutting down…")
                except Exception as _sde:
                    st.error(str(_sde))

# ═══════════════════════════════════════════════════════════════════════════════
# 🧠 EMBEDDING TAB
# ═══════════════════════════════════════════════════════════════════════════════
with _tab_emb:
    st.subheader("🧠 Embedding Interpreter")
    try:
        _emb_r = _req.get(f"{GW}/api/interpreter/status", headers=_hdr(), timeout=3)
        _emb_d = _emb_r.json() if _emb_r.status_code == 200 else {"enabled": False}
    except Exception:
        _emb_d = {"enabled": False}

    if not _emb_d.get("enabled"):
        st.info(
            "Embedding Interpreter is disabled. Enable it in your RCAN config:\n\n"
            "```yaml\ninterpreter:\n  enabled: true\n  backend: auto\n```"
        )
    else:
        # Status cards
        _ec1, _ec2, _ec3, _ec4 = st.columns(4)
        with _ec1:
            st.metric("Backend", _emb_d.get("backend", "—"))
        with _ec2:
            st.metric("Episodes", _emb_d.get("episode_count", 0))
        with _ec3:
            _sim = _emb_d.get("last_goal_similarity")
            st.metric("Goal Similarity", f"{_sim:.2f}" if _sim is not None else "—")
        with _ec4:
            st.metric("Escalations", _emb_d.get("escalations_session", 0))

        st.caption(
            f"Backend: {_emb_d.get('backend', '?')} · "
            f"Dims: {_emb_d.get('dimensions', '?')} · "
            f"Avg latency: {_emb_d.get('avg_latency_ms') or '—'}ms"
        )

        # Recent episodes table
        _recent = _emb_d.get("recent_episodes", [])
        if _recent:
            st.subheader("Recent Episodes")
            import pandas as _pd

            _ep_rows = []
            for ep in _recent[-10:]:
                _ep_rows.append(
                    {
                        "Timestamp": ep.get("timestamp", "")[:19].replace("T", " "),
                        "Instruction": ep.get("instruction", "")[:40],
                        "Action": ep.get("action_type", "?"),
                        "Outcome": ep.get("outcome", "?"),
                        "Similarity": round(ep.get("goal_similarity", 0), 3),
                    }
                )
            st.dataframe(_pd.DataFrame(_ep_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No episodes stored yet. Run the robot to populate the episode store.")

# ═══════════════════════════════════════════════════════════════════════════════
# ⚙️ SETTINGS TAB
# ═══════════════════════════════════════════════════════════════════════════════
with _tab_settings:
    st.subheader("⚙️ Settings")

    # ── Robot Face Style ───────────────────────────────────────────────────────
    st.markdown("#### 🤖 Robot Face Design")
    st.caption(
        "Choose the face shown on the kiosk screen. Changes take effect when you navigate back to the robot face."
    )

    _face_options = {
        "friendly": "😊 Friendly — warm blue eyes, rosy cheeks, wide smile",
        "kawaii": "🌸 Kawaii — big sparkly purple eyes, pink blush, cat mouth",
        "retro": "💾 Retro — green-on-dark pixel eyes, scanlines, terminal vibe",
    }
    _current_style = st.session_state.get("face_style", "friendly")
    _selected_style = st.radio(
        "Face style",
        options=list(_face_options.keys()),
        format_func=lambda k: _face_options[k],
        index=list(_face_options.keys()).index(_current_style),
        label_visibility="collapsed",
    )
    if _selected_style != _current_style:
        st.session_state.face_style = _selected_style
        st.rerun()

    st.divider()

    # ── Gateway & Connection ───────────────────────────────────────────────────
    st.markdown("#### 🔌 Gateway Connection")
    _new_gw = st.text_input(
        "Gateway URL",
        value=st.session_state.gateway_url,
        placeholder="http://alex.local:8000",
        key="settings_gw_input",
    )
    _new_tok = st.text_input(
        "API Token",
        value=st.session_state.api_token,
        type="password",
        placeholder="leave blank if no auth",
        key="settings_tok_input",
    )
    if st.button("💾 Save Connection", key="settings_save_conn"):
        st.session_state.gateway_url = _new_gw.strip()
        st.session_state.api_token = _new_tok.strip()
        st.success("Connection settings saved.")
        st.rerun()

    st.divider()

    # ── Terminal / tmux ────────────────────────────────────────────────────────
    st.markdown("#### 🖥️ Terminal Access")
    st.caption(
        "Quick commands to open a terminal on the robot. Run these in a shell on any machine on the same network."
    )
    import urllib.parse as _urlp

    _gw_host_t = _urlp.urlparse(GW).hostname or "alex.local"
    _ssh_user = st.text_input("SSH user", value="craigm26", key="ssh_user_input")
    _ssh_host = st.text_input("SSH host", value=_gw_host_t, key="ssh_host_input")
    _term_cmds = {
        "SSH shell": f"ssh {_ssh_user}@{_ssh_host}",
        "Attach tmux": f"ssh {_ssh_user}@{_ssh_host} -t 'tmux attach || tmux new'",
        "Gateway logs": f"ssh {_ssh_user}@{_ssh_host} -t 'journalctl --user -u opencastor -f'",
        "Python REPL": f"ssh {_ssh_user}@{_ssh_host} -t '/home/{_ssh_user}/opencastor-env/bin/python'",
        "Watch status": f"ssh {_ssh_user}@{_ssh_host} -t 'watch -n2 systemctl --user status opencastor'",
    }
    for _label, _cmd in _term_cmds.items():
        st.code(_cmd, language="bash")

    st.divider()

    # ── OpenCastor Setup Wizard ─────────────────────────────────────────────────
    st.markdown("#### 🧙 OpenCastor Setup")
    st.caption(
        "Launch the interactive setup wizard to configure your robot, API keys, hardware, and channels."
    )
    _setup_url = f"{GW}/setup"
    st.markdown(
        f'<a href="{_setup_url}" target="_blank" style="font-size:1rem;">🔗 Open Setup Wizard</a>',
        unsafe_allow_html=True,
    )

    st.divider()

    # ── Closed Captions ────────────────────────────────────────────────────────
    st.markdown("#### 💬 Closed Captions")
    st.caption("Show what the robot is saying as a subtitle bar on the face screen.")
    _cc_on = st.toggle(
        "Enable closed captions on face",
        value=st.session_state.get("cc_enabled", True),
        key="cc_toggle",
    )
    if _cc_on != st.session_state.get("cc_enabled", True):
        st.session_state.cc_enabled = _cc_on

    st.divider()

    # ── Quick-open face preview ────────────────────────────────────────────────
    st.markdown("#### 👀 Preview Face")
    import socket as _sock_s

    _gw_port_s = GW.split(":")[-1].split("/")[0] if GW.count(":") >= 2 else "8000"
    _hn_s = _sock_s.gethostname()
    _fh_s = _hn_s if "." in _hn_s else f"{_hn_s}.local"
    _cc_param = "&captions=1" if st.session_state.get("cc_enabled", True) else ""
    _face_url = f"http://{_fh_s}:{_gw_port_s}/face?style={st.session_state.face_style}{_cc_param}"
    st.markdown(
        f'<a href="{_face_url}" target="_blank" style="font-size:1rem;">'
        f"🔗 Open face in new tab ({st.session_state.face_style})</a>",
        unsafe_allow_html=True,
    )

    st.divider()

    with st.expander("🧪 Test Suite", expanded=False):
        st.caption("Run the automated test suite from the dashboard.")
        _tc1, _tc2 = st.columns(2)
        with _tc1:
            if st.button("▶ Run full test suite", key="run_full_tests"):
                try:
                    _req.post(
                        f"{GW}/api/test/run", json={"suite": "full"}, headers=_hdr(), timeout=5
                    )
                    st.toast("Test suite started!", icon="🧪")
                except Exception as _e:
                    st.error(str(_e))
        with _tc2:
            if st.button("▶ Run embedding tests", key="run_emb_tests"):
                try:
                    _req.post(
                        f"{GW}/api/test/run",
                        json={"suite": "embedding"},
                        headers=_hdr(),
                        timeout=5,
                    )
                    st.toast("Embedding tests started!", icon="🧠")
                except Exception as _e:
                    st.error(str(_e))
        try:
            _tr = _req.get(f"{GW}/api/test/status", headers=_hdr(), timeout=3)
            _trd = _tr.json() if _tr.status_code == 200 else {}
        except Exception:
            _trd = {}
        if _trd.get("running"):
            st.info("⏳ Test suite running…")
        elif _trd.get("result"):
            _res = _trd["result"]
            if _res.get("passed"):
                st.success(f"✅ Tests passed (suite: {_res.get('suite', '?')})")
            else:
                st.error(f"❌ Tests failed (suite: {_res.get('suite', '?')})")
            if _res.get("stdout"):
                st.code(_res["stdout"][-2000:], language="text")


# ── AUTO-REFRESH ───────────────────────────────────────────────────────────────
time.sleep(refresh_s)
st.rerun()

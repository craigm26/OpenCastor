"""
CastorDash - Touch-friendly HDMI/Web UI for OpenCastor.
Run with: streamlit run castor/dashboard.py
"""

import os
import sys
import time

import streamlit as st

st.set_page_config(
    page_title="CastorDash",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# --- UI STYLING ---
st.markdown(
    """
<style>
    .stApp { background-color: #0e1117; }
    div.stButton > button:first-child {
        height: 3em; width: 100%; border-radius: 20px;
        background-color: #4CAF50; color: white;
    }
</style>
""",
    unsafe_allow_html=True,
)

# --- SESSION STATE ---
if "messages" not in st.session_state:
    st.session_state.messages = []
if "brain" not in st.session_state:
    st.session_state.brain = None
if "gateway_url" not in st.session_state:
    st.session_state.gateway_url = os.getenv(
        "OPENCASTOR_GATEWAY_URL", "http://127.0.0.1:8000"
    )
if "api_token" not in st.session_state:
    st.session_state.api_token = os.getenv("OPENCASTOR_API_TOKEN", "")

# --- SIDEBAR ---
with st.sidebar:
    st.markdown("## OpenCastor")
    st.markdown("*The Body for the Gemini Brain*")
    st.divider()

    config_path = st.text_input("Config File", value="robot.rcan.yaml")

    if st.button("Initialize Brain"):
        try:
            import yaml

            from castor.providers import get_provider

            with open(config_path) as f:
                config = yaml.safe_load(f)
            st.session_state.brain = get_provider(config["agent"])
            st.success(f"Brain Online: {config['agent'].get('model', 'unknown')}")
        except Exception as e:
            st.error(f"Failed: {e}")

    st.divider()
    st.session_state.gateway_url = st.text_input(
        "Gateway URL", value=st.session_state.gateway_url
    )
    st.session_state.api_token = st.text_input(
        "API Token", value=st.session_state.api_token, type="password"
    )

    st.divider()
    if st.button("EMERGENCY STOP", type="primary"):
        try:
            import requests

            headers = {}
            if st.session_state.api_token:
                headers["Authorization"] = f"Bearer {st.session_state.api_token}"
            requests.post(
                f"{st.session_state.gateway_url}/api/stop", headers=headers, timeout=3
            )
            st.warning("Motors disengaged!")
        except Exception as e:
            st.error(f"E-stop failed: {e}")


def _gateway_headers() -> dict:
    h = {}
    if st.session_state.api_token:
        h["Authorization"] = f"Bearer {st.session_state.api_token}"
    return h


# --- TABS ---
tab_cmd, tab_fleet, tab_vision, tab_audit = st.tabs(["Command", "Fleet", "Vision", "Audit"])

# ============================================================
# TAB 1: Command
# ============================================================
with tab_cmd:
    col1, col2 = st.columns([2, 1])

    with col1:
        st.header("Command Link")

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        c1, c2 = st.columns([1, 4])
        with c1:
            if st.button("🎤 Speak"):
                try:
                    import speech_recognition as sr

                    r = sr.Recognizer()
                    with sr.Microphone() as source:
                        st.toast("Listening...", icon="🎤")
                        audio = r.listen(source, timeout=5)
                        text = r.recognize_google(audio)
                        st.session_state["voice_input"] = text
                except Exception as e:
                    st.toast(f"Voice error: {e}", icon="❌")

        prompt = st.chat_input("Type command here...")
        user_text = prompt or st.session_state.get("voice_input")

        if user_text:
            st.session_state.messages.append({"role": "user", "content": user_text})
            with st.chat_message("user"):
                st.markdown(user_text)

            with st.spinner("Thinking..."):
                if st.session_state.brain is not None:
                    thought = st.session_state.brain.think(b"", user_text, surface="dashboard")
                    reply = thought.raw_text
                else:
                    # Try gateway API
                    try:
                        import requests

                        resp = requests.post(
                            f"{st.session_state.gateway_url}/api/command",
                            json={"instruction": user_text},
                            headers=_gateway_headers(),
                            timeout=30,
                        )
                        reply = resp.json().get("raw_text", str(resp.json()))
                    except Exception as e:
                        reply = f"[Gateway error] {e}"

            st.session_state.messages.append({"role": "assistant", "content": reply})
            with st.chat_message("assistant"):
                st.markdown(reply)

            try:
                from gtts import gTTS

                tts = gTTS(text=reply[:200], lang="en")
                import tempfile

                tmp_mp3 = os.path.join(tempfile.gettempdir(), "castor_response.mp3")
                tts.save(tmp_mp3)
                if sys.platform == "darwin":
                    os.system(f"afplay {tmp_mp3} &")
                elif sys.platform == "win32":
                    os.system(f'start /b "" "{tmp_mp3}"')
                else:
                    os.system(f"mpg321 {tmp_mp3} 2>/dev/null &")
            except Exception:
                pass

            st.session_state.pop("voice_input", None)

    with col2:
        st.header("Telemetry")
        try:
            import requests

            status = requests.get(
                f"{st.session_state.gateway_url}/api/status",
                headers=_gateway_headers(),
                timeout=3,
            ).json()
            st.metric("Brain", "Online" if status.get("brain") else "Offline")
            st.metric("Driver", "Online" if status.get("driver") else "Offline")
            channels = status.get("channels_active", [])
            st.metric("Channels", len(channels))
            if channels:
                st.caption(", ".join(channels))
        except Exception:
            st.metric("Gateway", "Offline")


# ============================================================
# TAB 2: Fleet
# ============================================================
with tab_fleet:
    st.header("Fleet Management")
    st.caption("All RCAN peers discovered on the local network")

    col_f1, col_f2 = st.columns([3, 1])
    with col_f2:
        if st.button("Refresh Fleet"):
            st.rerun()

    peers = []
    try:
        import requests

        resp = requests.get(
            f"{st.session_state.gateway_url}/api/rcan/peers",
            headers=_gateway_headers(),
            timeout=5,
        )
        peers = resp.json().get("peers", [])
    except Exception as e:
        st.info(f"Could not reach gateway: {e}")

    if not peers:
        st.info(
            "No RCAN peers found. Make sure robots are running with "
            "`castor gateway` and `rcan_protocol.enable_mdns: true`."
        )
    else:
        for peer in peers:
            with st.expander(
                f"🤖 {peer.get('robot_name', 'Unknown')} — {peer.get('ruri', '?')}",
                expanded=True,
            ):
                pc1, pc2, pc3, pc4 = st.columns(4)
                pc1.metric("Model", peer.get("model", "?"))
                pc2.metric("Status", peer.get("status", "?"))
                safety = peer.get("safety_score")
                if safety is not None:
                    score_color = "🟢" if safety > 0.7 else "🟡" if safety > 0.4 else "🔴"
                    pc3.metric("Safety Score", f"{score_color} {safety:.2f}")
                pc4.metric("Latency", f"{peer.get('latency_ms', '?')} ms")

                addr = peer.get("addresses", ["?"])[0]
                port = peer.get("port", 8000)
                peer_url = f"http://{addr}:{port}"

                col_e1, col_e2 = st.columns(2)
                with col_e1:
                    if st.button(f"⛔ E-Stop {peer.get('robot_name', '')}", key=f"estop_{peer.get('ruri',addr)}"):
                        try:
                            import requests

                            requests.post(
                                f"{peer_url}/api/stop",
                                headers=_gateway_headers(),
                                timeout=3,
                            )
                            st.success("Emergency stop sent!")
                        except Exception as ex:
                            st.error(f"E-stop failed: {ex}")
                with col_e2:
                    caps = peer.get("capabilities", [])
                    st.caption(f"Caps: {', '.join(caps) if caps else 'none'}")

    # Also show safety telemetry history from local gateway
    st.divider()
    st.subheader("Local Safety Telemetry History")
    try:
        import requests

        hist_resp = requests.get(
            f"{st.session_state.gateway_url}/api/fs/proc",
            headers=_gateway_headers(),
            timeout=3,
        )
        proc_data = hist_resp.json()
        safety_score = proc_data.get("safety_score", None)
        if safety_score is not None:
            st.metric("Current Safety Score", f"{safety_score:.3f}")
    except Exception:
        st.caption("Safety telemetry unavailable (gateway offline)")


# ============================================================
# TAB 3: Vision (MJPEG Stream)
# ============================================================
with tab_vision:
    st.header("Live Camera")

    mjpeg_url = f"{st.session_state.gateway_url}/api/stream/mjpeg"
    if st.session_state.api_token:
        st.info(
            f"MJPEG stream: `{mjpeg_url}`  \n"
            "Note: Bearer token required — open in VLC or use the embed below."
        )
    else:
        st.image(mjpeg_url, caption="Live MJPEG stream", use_container_width=True)

    st.subheader("Static Frame")
    if st.button("Capture Frame"):
        try:
            import requests

            resp = requests.get(
                f"{st.session_state.gateway_url}/api/fs/read",
                json={"path": "/dev/camera"},
                headers=_gateway_headers(),
                timeout=3,
            )
            cam = resp.json().get("data", {})
            st.json(cam)
        except Exception as e:
            st.error(f"Could not fetch frame metadata: {e}")


# ============================================================
# TAB 4: Audit Log
# ============================================================
with tab_audit:
    st.header("Work Authority Audit Log")
    st.caption("Destructive action authorizations: requested, approved, denied, executed, revoked")

    if st.button("Refresh Audit Log"):
        st.rerun()

    try:
        import requests

        resp = requests.get(
            f"{st.session_state.gateway_url}/api/audit",
            headers=_gateway_headers(),
            timeout=5,
        )
        data = resp.json()
        log = data.get("audit_log", [])
        if log:
            import pandas as pd

            df = pd.DataFrame(log)
            if "timestamp" in df.columns:
                df["time"] = pd.to_datetime(df["timestamp"], unit="s").dt.strftime(
                    "%H:%M:%S"
                )
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No audit events recorded yet.")
    except Exception as e:
        st.info(f"Audit log unavailable: {e}")

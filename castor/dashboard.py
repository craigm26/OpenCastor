"""
CastorDash - Touch-friendly HDMI/Web UI for OpenCastor.
Run with: streamlit run castor/dashboard.py
"""

import os
import sys

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
    if st.button("EMERGENCY STOP", type="primary"):
        st.warning("Motors disengaged!")

# --- MAIN UI ---
col1, col2 = st.columns([2, 1])

with col1:
    st.header("Command Link")

    # Display chat history
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # Voice input
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

    # Chat input
    prompt = st.chat_input("Type command here...")
    user_text = prompt or st.session_state.get("voice_input")

    if user_text:
        st.session_state.messages.append({"role": "user", "content": user_text})
        with st.chat_message("user"):
            st.markdown(user_text)

        # Generate response
        with st.spinner("Thinking..."):
            if st.session_state.brain is not None:
                thought = st.session_state.brain.think(b"\x00" * 1024, user_text)
                reply = thought.raw_text
            else:
                reply = f"[No brain connected] Received: {user_text}"

        st.session_state.messages.append({"role": "assistant", "content": reply})
        with st.chat_message("assistant"):
            st.markdown(reply)

        # TTS output
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
    st.header("Vision")
    st.image(
        "https://placehold.co/400x300/1a1a2e/4CAF50?text=Camera+Feed",
        use_container_width=True,
    )
    st.markdown("### Telemetry")
    st.metric(label="Speed", value="0.0 m/s")
    st.metric(label="Heading", value="N/A")
    st.metric(label="Status", value="Idle")

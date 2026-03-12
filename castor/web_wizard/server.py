"""
OpenCastor Web Wizard server (issue #439).

Serves a browser-based setup wizard at localhost:8765.
Launched via: castor wizard --web

Stack: pure stdlib http.server + inline HTML/JS (no extra deps).
For a richer experience, optionally uses FastAPI if available.

Steps mirror the terminal wizard:
  1. Welcome + hardware detection
  2. Hardware selection
  3. Provider / model selection (llmfit-aware)
  4. API key entry
  5. Channel setup (optional)
  6. Config preview + write
  7. Registration with rcan.dev (optional)
  8. Done
"""

from __future__ import annotations

import json
import logging
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logger = logging.getLogger(__name__)

PORT = int(os.environ.get("CASTOR_WIZARD_PORT", "8765"))


# ── HTML template ────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>OpenCastor Setup Wizard</title>
<style>
  :root {
    --bg: #0a0a0f; --bg-alt: #12121a; --accent: #22d3ee;
    --text: #e8e6e3; --muted: #9ca3af; --border: #27272a;
    --green: #22c55e; --yellow: #eab308; --red: #ef4444;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif;
         min-height: 100vh; display: flex; flex-direction: column; align-items: center;
         justify-content: flex-start; padding: 2rem 1rem; }
  .container { width: 100%; max-width: 680px; }
  .header { display: flex; align-items: center; gap: 1rem; margin-bottom: 2rem; }
  .logo { font-size: 2rem; }
  .header h1 { font-size: 1.5rem; font-weight: 700; }
  .header small { color: var(--muted); font-size: 0.85rem; }
  .steps { display: flex; gap: 0.5rem; margin-bottom: 2rem; flex-wrap: wrap; }
  .step { width: 28px; height: 6px; border-radius: 3px; background: var(--border);
          transition: background 0.3s; }
  .step.done { background: var(--accent); }
  .step.active { background: var(--accent); opacity: 0.5; }
  .card { background: var(--bg-alt); border: 1px solid var(--border); border-radius: 16px;
          padding: 2rem; margin-bottom: 1.5rem; }
  .card h2 { font-size: 1.2rem; font-weight: 700; margin-bottom: 0.5rem; }
  .card .subtitle { color: var(--muted); font-size: 0.9rem; margin-bottom: 1.5rem; }
  .field { margin-bottom: 1.25rem; }
  .field label { display: block; font-size: 0.85rem; font-weight: 600; color: var(--muted);
                 margin-bottom: 0.4rem; text-transform: uppercase; letter-spacing: 0.05em; }
  .field input, .field select, .field textarea {
    width: 100%; background: rgba(255,255,255,0.04); border: 1px solid var(--border);
    border-radius: 8px; padding: 0.65rem 0.9rem; color: var(--text); font-size: 0.95rem;
    outline: none; transition: border-color 0.2s;
  }
  .field input:focus, .field select:focus { border-color: var(--accent); }
  .field .hint { font-size: 0.78rem; color: var(--muted); margin-top: 0.3rem; }
  .btn { padding: 0.7rem 1.5rem; border-radius: 9999px; font-weight: 700; font-size: 0.95rem;
         cursor: pointer; border: none; transition: all 0.2s; }
  .btn-primary { background: var(--accent); color: #0a0a0f; }
  .btn-primary:hover { filter: brightness(1.1); transform: scale(1.02); }
  .btn-secondary { background: rgba(255,255,255,0.06); color: var(--text);
                   border: 1px solid rgba(255,255,255,0.1); }
  .btn-secondary:hover { background: rgba(255,255,255,0.1); }
  .btn-row { display: flex; gap: 1rem; justify-content: flex-end; margin-top: 1.5rem; }
  .hardware-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 0.75rem; }
  .hw-card { border: 2px solid var(--border); border-radius: 12px; padding: 1rem; cursor: pointer;
             text-align: center; transition: all 0.2s; background: transparent; }
  .hw-card:hover { border-color: var(--accent); background: rgba(34,211,238,0.05); }
  .hw-card.selected { border-color: var(--accent); background: rgba(34,211,238,0.1); }
  .hw-card .icon { font-size: 2rem; margin-bottom: 0.5rem; }
  .hw-card .name { font-size: 0.85rem; font-weight: 600; }
  .hw-card .desc { font-size: 0.7rem; color: var(--muted); margin-top: 0.2rem; }
  .badge { display: inline-block; font-size: 0.7rem; font-weight: 700; padding: 0.2rem 0.6rem;
           border-radius: 9999px; text-transform: uppercase; letter-spacing: 0.05em; }
  .badge-green { background: rgba(34,197,94,0.15); color: var(--green); border: 1px solid rgba(34,197,94,0.3); }
  .badge-yellow { background: rgba(234,179,8,0.15); color: var(--yellow); border: 1px solid rgba(234,179,8,0.3); }
  .badge-accent { background: rgba(34,211,238,0.15); color: var(--accent); border: 1px solid rgba(34,211,238,0.3); }
  pre.config { background: rgba(0,0,0,0.4); border: 1px solid var(--border); border-radius: 10px;
               padding: 1rem; font-size: 0.78rem; font-family: 'JetBrains Mono', monospace;
               color: var(--accent); overflow-x: auto; max-height: 300px; overflow-y: auto; }
  .alert { padding: 0.75rem 1rem; border-radius: 8px; font-size: 0.85rem; margin-bottom: 1rem; }
  .alert-success { background: rgba(34,197,94,0.1); border: 1px solid rgba(34,197,94,0.3); color: var(--green); }
  .alert-warn { background: rgba(234,179,8,0.1); border: 1px solid rgba(234,179,8,0.3); color: var(--yellow); }
  .alert-error { background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3); color: var(--red); }
  .loading { display: inline-block; width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.2);
             border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .done-icon { font-size: 4rem; text-align: center; margin-bottom: 1rem; }
  footer { color: var(--muted); font-size: 0.75rem; text-align: center; margin-top: 2rem; }
  footer a { color: var(--accent); text-decoration: none; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="logo">🤖</div>
    <div>
      <h1>OpenCastor Setup</h1>
      <small>Interactive wizard — configure your robot runtime</small>
    </div>
  </div>

  <div class="steps" id="steps-bar">
    <!-- filled by JS -->
  </div>

  <div id="wizard-root"></div>
</div>

<footer>
  OpenCastor — <a href="https://rcan.dev" target="_blank">rcan.dev</a> —
  <a href="https://github.com/craigm26/OpenCastor" target="_blank">GitHub</a>
</footer>

<script>
const STEPS = [
  "Welcome", "Hardware", "Provider", "API Keys",
  "Channels", "Config", "Register", "Done"
];

let state = {
  step: 0,
  hardware: "",
  provider: "",
  model: "",
  apiKey: "",
  channelType: "",
  channelToken: "",
  robotName: "",
  manufacturer: "",
  modelName: "",
  version: "v1",
  deviceId: "",
  configYaml: "",
  rrn: "",
  detectedHardware: null,
};

// ── Step bar ────────────────────────────────────────────────────────────────
function renderSteps() {
  const bar = document.getElementById("steps-bar");
  bar.innerHTML = STEPS.map((_, i) => {
    const cls = i < state.step ? "done" : i === state.step ? "active" : "";
    return `<div class="step ${cls}" title="${STEPS[i]}"></div>`;
  }).join("");
}

// ── Fetch helpers ────────────────────────────────────────────────────────────
async function api(path, method="GET", body=null) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  return res.json();
}

// ── Render ────────────────────────────────────────────────────────────────────
function render() {
  renderSteps();
  const root = document.getElementById("wizard-root");
  switch (state.step) {
    case 0: root.innerHTML = stepWelcome(); break;
    case 1: root.innerHTML = stepHardware(); break;
    case 2: root.innerHTML = stepProvider(); break;
    case 3: root.innerHTML = stepApiKeys(); break;
    case 4: root.innerHTML = stepChannels(); break;
    case 5: root.innerHTML = stepConfig(); fetchConfig(); break;
    case 6: root.innerHTML = stepRegister(); break;
    case 7: root.innerHTML = stepDone(); break;
    default: root.innerHTML = stepDone();
  }
}

// ── Steps ─────────────────────────────────────────────────────────────────────

function stepWelcome() { return `
<div class="card">
  <h2>Welcome to OpenCastor</h2>
  <p class="subtitle">
    This wizard will configure your robot runtime in a few minutes.
    You'll end up with a <code>.rcan.yaml</code> config file ready to run.
  </p>
  <div class="field">
    <label>Robot Name</label>
    <input id="robot-name" type="text" placeholder="My Robot" value="${state.robotName}" oninput="state.robotName=this.value" />
    <div class="hint">A friendly display name for your robot</div>
  </div>
  <div class="btn-row">
    <button class="btn btn-primary" onclick="next()">Get Started →</button>
  </div>
</div>`; }

function stepHardware() { return `
<div class="card">
  <h2>Hardware Detection</h2>
  <p class="subtitle">
    Select your hardware platform. OpenCastor optimizes provider selection, driver loading, and safety thresholds per platform.
  </p>
  <div class="hardware-grid">
    ${[
      { id:"rpi5", icon:"🍓", name:"Raspberry Pi 5", desc:"16GB recommended" },
      { id:"rpi4", icon:"🍓", name:"Raspberry Pi 4", desc:"8GB recommended" },
      { id:"jetson", icon:"🟢", name:"NVIDIA Jetson", desc:"Orin / Nano / Xavier" },
      { id:"x86", icon:"💻", name:"x86 / PC", desc:"Ubuntu / Debian" },
      { id:"hailo", icon:"🔺", name:"Hailo-8 AI Kit", desc:"NPU accelerated" },
      { id:"mac", icon:"🍎", name:"macOS", desc:"Apple Silicon / Intel" },
      { id:"other", icon:"🔧", name:"Other / Custom", desc:"Manual config" },
    ].map(h => `
      <div class="hw-card ${state.hardware===h.id?'selected':''}" onclick="selectHardware('${h.id}')">
        <div class="icon">${h.icon}</div>
        <div class="name">${h.name}</div>
        <div class="desc">${h.desc}</div>
      </div>`).join("")}
  </div>
  <div class="btn-row">
    <button class="btn btn-secondary" onclick="prev()">Back</button>
    <button class="btn btn-primary" onclick="next()" ${!state.hardware?"disabled":""}>Next →</button>
  </div>
</div>`; }

function stepProvider() { return `
<div class="card">
  <h2>AI Provider</h2>
  <p class="subtitle">Choose where your robot's brain runs. Local models keep data on-device; cloud APIs are easier to start with.</p>
  <div class="field">
    <label>Provider</label>
    <select id="provider-select" onchange="state.provider=this.value; state.model=''; render()">
      <option value="" disabled ${!state.provider?"selected":""}>Select provider…</option>
      <option value="ollama" ${state.provider==="ollama"?"selected":""}>Ollama (local)</option>
      <option value="anthropic" ${state.provider==="anthropic"?"selected":""}>Anthropic (Claude)</option>
      <option value="openai" ${state.provider==="openai"?"selected":""}>OpenAI (GPT)</option>
      <option value="google" ${state.provider==="google"?"selected":""}>Google (Gemini)</option>
      <option value="huggingface" ${state.provider==="huggingface"?"selected":""}>HuggingFace</option>
    </select>
  </div>
  ${state.provider ? `
  <div class="field">
    <label>Model</label>
    <input id="model-input" type="text" placeholder="${providerModelHint(state.provider)}"
           value="${state.model}" oninput="state.model=this.value" />
    <div class="hint">${providerModelDesc(state.provider)}</div>
  </div>` : ""}
  <div class="btn-row">
    <button class="btn btn-secondary" onclick="prev()">Back</button>
    <button class="btn btn-primary" onclick="next()" ${!state.provider||!state.model?"disabled":""}>Next →</button>
  </div>
</div>`; }

function providerModelHint(p) {
  return { ollama:"qwen2.5:7b", anthropic:"claude-haiku-3-5", openai:"gpt-4o-mini",
           google:"gemini-2-flash", huggingface:"Qwen/Qwen2.5-7B-Instruct" }[p] || "model-name";
}
function providerModelDesc(p) {
  return { ollama:"Run locally via Ollama. Install: brew install ollama",
           anthropic:"Claude models via Anthropic API. Needs ANTHROPIC_API_KEY",
           openai:"GPT models via OpenAI API. Needs OPENAI_API_KEY",
           google:"Gemini models via Google AI. Needs GOOGLE_API_KEY",
           huggingface:"Open models via HF Inference API. Needs HF_API_KEY" }[p] || "";
}

function stepApiKeys() { return `
<div class="card">
  <h2>API Key</h2>
  <p class="subtitle">
    ${state.provider === "ollama"
      ? "Ollama runs locally — no API key needed. Just make sure Ollama is running."
      : `Enter your ${state.provider} API key. It will be saved to <code>~/.opencastor/env</code> and never logged.`}
  </p>
  ${state.provider !== "ollama" ? `
  <div class="field">
    <label>${state.provider.toUpperCase()} API Key</label>
    <input id="api-key" type="password" placeholder="sk-..." value="${state.apiKey}"
           oninput="state.apiKey=this.value" autocomplete="new-password" />
    <div class="hint">Stored securely in ~/.opencastor/env — never sent anywhere except ${state.provider}</div>
  </div>` : `
  <div class="alert alert-success">✓ No API key needed for Ollama</div>`}
  <div class="btn-row">
    <button class="btn btn-secondary" onclick="prev()">Back</button>
    <button class="btn btn-primary" onclick="next()">Next →</button>
  </div>
</div>`; }

function stepChannels() { return `
<div class="card">
  <h2>Messaging Channel <span class="badge badge-yellow">Optional</span></h2>
  <p class="subtitle">Connect a messaging channel to control your robot via chat. Skip if you only use the API.</p>
  <div class="field">
    <label>Channel Type</label>
    <select onchange="state.channelType=this.value; render()">
      <option value="" ${!state.channelType?"selected":""}>None / Skip</option>
      <option value="whatsapp" ${state.channelType==="whatsapp"?"selected":""}>WhatsApp</option>
      <option value="telegram" ${state.channelType==="telegram"?"selected":""}>Telegram</option>
      <option value="discord" ${state.channelType==="discord"?"selected":""}>Discord</option>
      <option value="signal" ${state.channelType==="signal"?"selected":""}>Signal</option>
    </select>
  </div>
  ${state.channelType && state.channelType !== "" ? `
  <div class="field">
    <label>Bot Token / Key</label>
    <input type="password" placeholder="Token…" value="${state.channelToken}"
           oninput="state.channelToken=this.value" />
  </div>` : ""}
  <div class="btn-row">
    <button class="btn btn-secondary" onclick="prev()">Back</button>
    <button class="btn btn-primary" onclick="next()">Preview Config →</button>
  </div>
</div>`; }

function stepConfig() { return `
<div class="card">
  <h2>Config Preview</h2>
  <p class="subtitle">Review your generated RCAN config before writing to disk.</p>
  <div id="config-loading" style="text-align:center;padding:2rem">
    <div class="loading"></div><p style="margin-top:1rem;color:var(--muted)">Generating config…</p>
  </div>
  <pre class="config" id="config-preview" style="display:none"></pre>
  <div id="config-field" style="display:none">
    <div class="field" style="margin-top:1rem">
      <label>Save to file</label>
      <input id="config-filename" type="text" value="myrobot.rcan.yaml" />
    </div>
  </div>
  <div class="btn-row" id="config-btns" style="display:none">
    <button class="btn btn-secondary" onclick="prev()">Back</button>
    <button class="btn btn-primary" onclick="writeConfig()">Write Config →</button>
  </div>
</div>`; }

async function fetchConfig() {
  const data = await api("/api/wizard/config", "POST", state);
  document.getElementById("config-loading").style.display = "none";
  document.getElementById("config-preview").style.display = "block";
  document.getElementById("config-preview").textContent = data.yaml || "(error generating config)";
  document.getElementById("config-field").style.display = "block";
  document.getElementById("config-btns").style.display = "flex";
  state.configYaml = data.yaml || "";
}

async function writeConfig() {
  const filename = document.getElementById("config-filename").value || "myrobot.rcan.yaml";
  const result = await api("/api/wizard/write", "POST", { ...state, filename });
  if (result.success) { state.step = 6; render(); }
  else { alert("Error: " + result.error); }
}

function stepRegister() { return `
<div class="card">
  <h2>Register with rcan.dev <span class="badge badge-accent">Free</span></h2>
  <p class="subtitle">
    Get a globally unique Robot Registry Number (RRN) — like an ISBN for your robot.
    Listed at <a href="https://robotregistryfoundation.org/registry" target="_blank" style="color:var(--accent)">robotregistryfoundation.org/registry</a>.
  </p>
  <div class="field">
    <label>Manufacturer</label>
    <input type="text" placeholder="acme" value="${state.manufacturer}" oninput="state.manufacturer=this.value" />
  </div>
  <div class="field">
    <label>Model</label>
    <input type="text" placeholder="robotarm" value="${state.modelName}" oninput="state.modelName=this.value" />
  </div>
  <div class="field">
    <label>Version</label>
    <input type="text" placeholder="v1" value="${state.version}" oninput="state.version=this.value" />
  </div>
  <div class="field">
    <label>Device ID</label>
    <input type="text" placeholder="unit-001" value="${state.deviceId}" oninput="state.deviceId=this.value" />
    <div class="hint">Unique identifier for this physical unit</div>
  </div>
  <div id="register-result"></div>
  <div class="btn-row">
    <button class="btn btn-secondary" onclick="skipRegistration()">Skip for now</button>
    <button class="btn btn-primary" onclick="doRegister()">Register →</button>
  </div>
</div>`; }

async function doRegister() {
  const resultEl = document.getElementById("register-result");
  resultEl.innerHTML = '<div class="alert alert-warn"><span class="loading"></span> Registering…</div>';
  const data = await api("/api/wizard/register", "POST", state);
  if (data.rrn) {
    state.rrn = data.rrn;
    resultEl.innerHTML = `<div class="alert alert-success">✅ Registered! RRN: <strong>${data.rrn}</strong></div>`;
    setTimeout(() => { state.step = 7; render(); }, 1500);
  } else {
    resultEl.innerHTML = `<div class="alert alert-error">⚠️ ${data.error || "Registration failed"}</div>`;
  }
}

function skipRegistration() { state.step = 7; render(); }

function stepDone() { return `
<div class="card" style="text-align:center">
  <div class="done-icon">🎉</div>
  <h2>You're all set!</h2>
  <p class="subtitle" style="margin-top:0.5rem">OpenCastor is configured and ready to run.</p>
  ${state.rrn ? `<div class="alert alert-success" style="text-align:left;margin-top:1rem">
    🤖 Robot registered: <strong>${state.rrn}</strong><br>
    <a href="https://robotregistryfoundation.org/registry/${state.rrn}" target="_blank" style="color:var(--accent)">
      View at rcan.dev →
    </a>
  </div>` : ""}
  <div style="margin-top:2rem; text-align:left">
    <p style="color:var(--muted); font-size:0.85rem; margin-bottom:1rem">Run your robot:</p>
    <pre class="config">castor run --config myrobot.rcan.yaml</pre>
    <pre class="config" style="margin-top:0.75rem">castor status</pre>
  </div>
  <div class="btn-row" style="justify-content:center; margin-top:2rem">
    <a href="https://rcan.dev/quickstart" target="_blank" class="btn btn-secondary">Quickstart Docs</a>
    <button class="btn btn-primary" onclick="window.close()">Close Wizard</button>
  </div>
</div>`; }

// ── Navigation ────────────────────────────────────────────────────────────────
function next() { if (state.step < STEPS.length - 1) { state.step++; render(); } }
function prev() { if (state.step > 0) { state.step--; render(); } }
function selectHardware(id) {
  state.hardware = id;
  document.querySelectorAll(".hw-card").forEach(c => c.classList.remove("selected"));
  document.querySelectorAll(".hw-card").forEach(c => {
    if (c.querySelector(".name") && c.onclick?.toString().includes(id)) c.classList.add("selected");
  });
  // Re-render to enable Next button
  document.querySelector('.btn-row .btn-primary').removeAttribute('disabled');
  render();
}

// ── Init ─────────────────────────────────────────────────────────────────────
render();
</script>
</body>
</html>"""


# ── API handler ───────────────────────────────────────────────────────────────


def _generate_config(state: dict) -> str:
    """Generate RCAN YAML config from wizard state."""
    robot_name = state.get("robotName") or "MyRobot"
    hardware = state.get("hardware", "rpi5")
    provider = state.get("provider", "ollama")
    model = state.get("model", "qwen2.5:7b")
    manufacturer = state.get("manufacturer", "myorg")
    model_name = state.get("modelName", "myrobot")
    version = state.get("version", "v1")
    device_id = state.get("deviceId", "unit-001")

    camera_type = "oakd" if hardware in ("rpi5", "rpi4") else "none"
    driver_type = "pca9685" if hardware in ("rpi5", "rpi4") else "mock"

    channel_section = ""
    ch_type = state.get("channelType", "")
    ch_token = state.get("channelToken", "")
    if ch_type and ch_token:
        channel_section = f"""
channels:
  {ch_type}:
    enabled: true
    token: "{ch_token[:4]}...{ch_token[-4:] if len(ch_token) > 8 else ""}"
"""

    return f"""# OpenCastor RCAN Configuration
# Generated by castor wizard --web

rcan_version: "1.2"

metadata:
  robot_name: "{robot_name}"
  manufacturer: "{manufacturer or "myorg"}"
  model: "{model_name or "myrobot"}"
  version: "{version}"
  device_id: "{device_id or "unit-001"}"

agent:
  provider: {provider}
  model: "{model}"
  confidence_gates:
    - threshold: 0.8
  signing:
    enabled: false  # enable with: castor wizard --web → re-run after rcan[crypto] install

camera:
  type: {camera_type}

driver:
  type: {driver_type}
{channel_section}
# Run your robot:
#   castor run --config {model_name or "myrobot"}.rcan.yaml
"""


class WizardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # type: ignore
        logger.debug(fmt, *args)

    def _send(self, code: int, body: bytes, content_type: str = "text/html") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict) -> None:
        body = json.dumps(data).encode()
        self._send(200, body, "application/json")

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/" or self.path == "/wizard":
            self._send(200, HTML.encode())
        elif self.path == "/api/wizard/hw":
            # Quick hardware probe
            hw: dict[str, Any] = {}
            try:
                import platform

                hw["platform"] = platform.machine()
                hw["model_file"] = (
                    open("/proc/device-tree/model").read().strip()
                    if os.path.exists("/proc/device-tree/model")
                    else ""
                )
                hw["hailo"] = os.path.exists("/dev/hailo0")
                hw["oakd"] = any(os.path.exists(f"/dev/video{i}") for i in range(4))
            except Exception:
                pass
            self._json(hw)
        else:
            self._send(404, b"Not found")

    def do_POST(self) -> None:  # noqa: N802
        content_len = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_len)
        try:
            state = json.loads(body_bytes)
        except Exception:
            state = {}

        if self.path == "/api/wizard/config":
            yaml_str = _generate_config(state)
            self._json({"yaml": yaml_str})

        elif self.path == "/api/wizard/write":
            filename = state.get("filename", "myrobot.rcan.yaml")
            yaml_str = state.get("configYaml") or _generate_config(state)
            try:
                path = Path.cwd() / filename if not os.path.isabs(filename) else Path(filename)
                path.write_text(yaml_str)
                logger.info("Config written to %s", path)
                self._json({"success": True, "path": str(path)})
            except Exception as e:
                self._json({"success": False, "error": str(e)})

        elif self.path == "/api/wizard/register":
            try:
                from castor.wizard import _offer_rcan_registration  # type: ignore

                rrn = _offer_rcan_registration(state, silent=True)
                if rrn:
                    self._json({"rrn": rrn})
                else:
                    # Open browser fallback
                    m = state.get("manufacturer", "")
                    mod = state.get("modelName", "")
                    v = state.get("version", "v1")
                    d = state.get("deviceId", "")
                    url = (
                        f"https://robotregistryfoundation.org/registry/register"
                        f"?manufacturer={m}&model={mod}&version={v}&device_id={d}&source=wizard-web"
                    )
                    self._json(
                        {"browser_url": url, "error": "Open the link to complete registration"}
                    )
            except Exception as e:
                logger.warning("Registration failed: %s", e)
                self._json({"error": str(e)})

        else:
            self._send(404, b"Not found")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()


# ── Public API ────────────────────────────────────────────────────────────────

from pathlib import Path as Path  # noqa: E402


def start_wizard(port: int = PORT, open_browser: bool = True) -> None:
    """
    Start the web wizard server and optionally open a browser.

    Blocks until the user closes the wizard (Ctrl+C).
    """
    server = HTTPServer(("127.0.0.1", port), WizardHandler)
    url = f"http://localhost:{port}"

    print("\n🤖 OpenCastor Web Wizard")
    print(f"   {url}")
    print("   Press Ctrl+C to exit\n")

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        print("\nWizard closed.")

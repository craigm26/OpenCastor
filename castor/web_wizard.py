"""
OpenCastor Web Wizard -- browser-based setup wizard.

Launches a local web server with a form-based setup flow,
more approachable for users who prefer GUIs over terminals.

Usage:
    castor wizard --web
    castor wizard --web --port 8080
"""

import json
import logging
import os
import uuid
import webbrowser
from datetime import datetime, timezone

logger = logging.getLogger("OpenCastor.WebWizard")

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OpenCastor Setup Wizard</title>
<style>
  :root { --bg: #0d1117; --card: #161b22; --border: #30363d; --text: #e6edf3;
          --accent: #58a6ff; --green: #3fb950; --yellow: #d29922; --red: #f85149; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); min-height: 100vh;
         display: flex; justify-content: center; padding: 2rem; }
  .container { max-width: 640px; width: 100%; }
  h1 { color: var(--accent); margin-bottom: 0.5rem; font-size: 1.8rem; }
  h2 { color: var(--accent); margin: 1.5rem 0 0.75rem; font-size: 1.2rem; }
  .subtitle { color: #8b949e; margin-bottom: 2rem; }
  .card { background: var(--card); border: 1px solid var(--border);
          border-radius: 8px; padding: 1.5rem; margin-bottom: 1rem; }
  label { display: block; color: #8b949e; font-size: 0.85rem;
          margin-bottom: 0.3rem; margin-top: 1rem; }
  input, select { width: 100%; padding: 0.6rem; border: 1px solid var(--border);
                  border-radius: 6px; background: var(--bg); color: var(--text);
                  font-size: 0.95rem; }
  input:focus, select:focus { outline: none; border-color: var(--accent); }
  .btn { display: inline-block; padding: 0.7rem 2rem; border: none;
         border-radius: 6px; font-size: 1rem; cursor: pointer;
         background: var(--accent); color: #000; font-weight: 600;
         margin-top: 1.5rem; }
  .btn:hover { opacity: 0.9; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .result { padding: 1rem; border-radius: 6px; margin-top: 1rem; display: none; }
  .result.success { background: #0d2818; border: 1px solid var(--green); display: block; }
  .result.error { background: #2d1b1b; border: 1px solid var(--red); display: block; }
  .safety { background: #2d2000; border: 1px solid var(--yellow);
            border-radius: 8px; padding: 1rem; margin-bottom: 1.5rem; }
  .safety h3 { color: var(--yellow); margin-bottom: 0.5rem; }
  .safety ul { padding-left: 1.5rem; color: #d1a620; }
  .radio-group { display: flex; gap: 1rem; margin-top: 0.5rem; }
  .radio-group label { display: flex; align-items: center; gap: 0.4rem;
                       color: var(--text); cursor: pointer; margin-top: 0; }
  .radio-group input { width: auto; }
  code { background: var(--bg); padding: 0.15rem 0.4rem; border-radius: 4px;
         font-size: 0.9rem; }
</style>
</head>
<body>
<div class="container">
  <h1>OpenCastor Setup Wizard</h1>
  <p class="subtitle">Configure your robot in your browser</p>

  <div class="safety">
    <h3>Safety Warning</h3>
    <p>OpenCastor controls <strong>physical motors and servos</strong>.</p>
    <ul>
      <li>Keep hands and cables clear of moving parts</li>
      <li>Have a power switch or kill-cord within reach</li>
      <li>Never leave a running robot unattended</li>
    </ul>
  </div>

  <form id="wizard-form">
    <div class="card">
      <h2>Project</h2>
      <label for="robot_name">Robot Name</label>
      <input type="text" id="robot_name" value="MyRobot" required>
    </div>

    <div class="card">
      <h2>AI Provider</h2>
      <label for="provider">Provider</label>
      <select id="provider">
        <option value="1" selected>Anthropic Claude Opus 4.6 (Recommended)</option>
        <option value="2">Google Gemini 2.5 Flash</option>
        <option value="3">Google Gemini 3 Flash (Preview)</option>
        <option value="4">OpenAI GPT-4.1</option>
        <option value="5">Local Llama (Ollama)</option>
      </select>
      <label for="api_key">API Key</label>
      <input type="password" id="api_key" placeholder="Paste your API key here">
    </div>

    <div class="card">
      <h2>Hardware Preset</h2>
      <select id="preset">
        <option value="rpi_rc_car" selected>RPi RC Car + PCA9685 (Recommended)</option>
        <option value="waveshare_alpha">Waveshare AlphaBot ($45)</option>
        <option value="adeept_generic">Adeept RaspTank ($55)</option>
        <option value="sunfounder_picar">SunFounder PiCar-X ($60)</option>
        <option value="dynamixel_arm">Dynamixel Arm</option>
      </select>
    </div>

    <button type="submit" class="btn" id="submit-btn">Generate Config</button>
  </form>

  <div id="result"></div>
</div>

<script>
document.getElementById('wizard-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('submit-btn');
  btn.disabled = true;
  btn.textContent = 'Generating...';

  const data = {
    robot_name: document.getElementById('robot_name').value,
    provider: document.getElementById('provider').value,
    api_key: document.getElementById('api_key').value,
    preset: document.getElementById('preset').value,
  };

  try {
    const resp = await fetch('/api/wizard/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data),
    });
    const result = await resp.json();
    const el = document.getElementById('result');
    if (resp.ok) {
      el.className = 'result success';
      el.innerHTML = '<h3 style="color:#3fb950">Setup Complete!</h3>' +
        '<p>Config file: <code>' + result.filename + '</code></p>' +
        '<p style="margin-top:0.5rem">Next: <code>castor run --config ' + result.filename + '</code></p>';
    } else {
      el.className = 'result error';
      el.innerHTML = '<h3 style="color:#f85149">Error</h3><p>' + result.detail + '</p>';
    }
  } catch (err) {
    const el = document.getElementById('result');
    el.className = 'result error';
    el.innerHTML = '<h3 style="color:#f85149">Error</h3><p>' + err.message + '</p>';
  }
  btn.disabled = false;
  btn.textContent = 'Generate Config';
});
</script>
</body>
</html>"""


def launch_web_wizard(port: int = 8080):
    """Start the web wizard server and open the browser."""
    try:
        import uvicorn
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse
        from pydantic import BaseModel
    except ImportError:
        print("  Web wizard requires FastAPI and uvicorn (already installed).")
        print("  Falling back to terminal wizard...")
        from castor.wizard import main as run_wizard
        run_wizard()
        return

    import yaml

    app = FastAPI(title="OpenCastor Web Wizard")

    class WizardRequest(BaseModel):
        robot_name: str
        provider: str
        api_key: str = ""
        preset: str = "rpi_rc_car"

    @app.get("/", response_class=HTMLResponse)
    async def wizard_page():
        return _HTML_TEMPLATE

    @app.post("/api/wizard/generate")
    async def generate_config(req: WizardRequest):
        from castor.wizard import PROVIDERS, generate_preset_config, _write_env_var

        agent_config = PROVIDERS.get(req.provider, PROVIDERS["1"])

        # Save API key if provided
        if req.api_key and agent_config.get("env_var"):
            _write_env_var(agent_config["env_var"], req.api_key)

        # Generate config
        config = generate_preset_config(req.preset, req.robot_name, agent_config)
        filename = f"{req.robot_name.lower().replace(' ', '_')}.rcan.yaml"

        with open(filename, "w") as f:
            yaml.dump(config, f, sort_keys=False, default_flow_style=False)

        return {
            "filename": filename,
            "provider": agent_config["label"],
            "model": agent_config["model"],
            "preset": req.preset,
        }

    print(f"\n  Web wizard starting on http://localhost:{port}")
    print("  Press Ctrl+C to stop.\n")

    # Open browser after a short delay
    import threading
    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

"""OpenCastor Web Wizard -- browser-based setup wizard."""

from __future__ import annotations

import contextlib
import logging
import webbrowser

logger = logging.getLogger("OpenCastor.WebWizard")

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
<meta charset=\"UTF-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
<title>OpenCastor Setup Wizard</title>
<style>
  :root { --bg:#0f1115; --card:#181c24; --border:#2d3442; --text:#e8edf7;
          --muted:#98a3ba; --accent:#66d9a3; --warn:#e9c46a; --err:#ef476f; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
         background: radial-gradient(circle at top right, #1b2230, #0b0d12 55%);
         color: var(--text); padding: 22px; }
  .wrap { max-width: 860px; margin: 0 auto; }
  h1 { margin:0 0 8px 0; font-size: 1.9rem; letter-spacing: 0.01em; }
  p { color: var(--muted); }
  .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(260px,1fr)); gap:16px; }
  .card { background: linear-gradient(160deg, #1b2333, #141922); border:1px solid var(--border);
          border-radius: 12px; padding:14px; }
  label { display:block; margin-top:10px; margin-bottom:5px; color:var(--muted); font-size:0.88rem; }
  input, select, button, textarea { width:100%; border-radius:8px; border:1px solid var(--border);
          background:#0f131b; color:var(--text); padding:10px; font-size:0.95rem; }
  button { cursor:pointer; background: linear-gradient(135deg, #66d9a3, #53b78a); color:#04120a;
          border:none; font-weight:700; }
  button.secondary { background:#1d2432; color:var(--text); border:1px solid var(--border); }
  .row { display:flex; gap:10px; margin-top:12px; }
  .result { margin-top:12px; white-space:pre-wrap; font-family:Consolas, monospace; font-size:0.85rem;
            border:1px dashed var(--border); border-radius:10px; padding:10px; background:#0c1017; }
  .ok { color: #66d9a3; }
  .warn { color: var(--warn); }
  .err { color: var(--err); }
  code { background:#101622; padding:2px 6px; border-radius:6px; }
</style>
</head>
<body>
<div class=\"wrap\">
  <h1>OpenCastor Setup Wizard</h1>
  <p>Unified setup flow with stack profiles, preflight checks, and fallback guidance.</p>

  <div class=\"grid\">
    <div class=\"card\">
      <h3>Project</h3>
      <label>Robot Name</label>
      <input id=\"robot_name\" value=\"MyRobot\" />

      <label>Hardware Preset</label>
      <select id=\"preset\"></select>

      <label>Stack Profile</label>
      <select id=\"stack\"></select>
    </div>

    <div class=\"card\">
      <h3>AI Setup</h3>
      <label>Provider</label>
      <select id=\"provider\"></select>

      <label>Model / Profile</label>
      <select id=\"model\"></select>

      <label>API Key (if needed)</label>
      <input id=\"api_key\" type=\"password\" placeholder=\"Optional for local providers\" />

      <label><input id=\"auto_install\" type=\"checkbox\" style=\"width:auto\" checked /> Attempt guided auto-install when required</label>
    </div>
  </div>

  <div class=\"card\" style=\"margin-top:16px\">
    <div class=\"row\">
      <button class=\"secondary\" id=\"preflight_btn\">Run Preflight</button>
      <button class=\"secondary\" id=\"verify_btn\">Verify Config</button>
      <button id=\"generate_btn\">Generate Config</button>
    </div>
    <div id=\"result\" class=\"result\">Loading setup catalog...</div>
  </div>
</div>

<script>
let catalog = null;
let sessionId = null;

/** Return Authorization header if a token was injected by the server. */
function getAuthHeaders(extra) {
  const token = window.__OC_TOKEN || '';
  const headers = Object.assign({}, extra || {});
  if (token) headers['Authorization'] = 'Bearer ' + token;
  return headers;
}

function text(el, msg, cls='') {
  el.className = 'result ' + cls;
  el.textContent = msg;
}

function populateProviders(selected) {
  const providerSel = document.getElementById('provider');
  providerSel.innerHTML = '';
  for (const key of catalog.provider_order) {
    const p = catalog.providers.find(x => x.key === key);
    if (!p) continue;
    const opt = document.createElement('option');
    opt.value = p.key;
    opt.textContent = `${p.label} - ${p.desc}`;
    providerSel.appendChild(opt);
  }
  if (selected) providerSel.value = selected;
}

function populateModels(provider, defaultModel=null) {
  const modelSel = document.getElementById('model');
  modelSel.innerHTML = '';
  const models = catalog.models[provider] || [];
  if (models.length === 0) {
    const opt = document.createElement('option');
    opt.value = 'default-model';
    opt.textContent = 'default-model (enter manually in config later)';
    modelSel.appendChild(opt);
    return;
  }
  for (const m of models) {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = `${m.label} - ${m.desc}`;
    modelSel.appendChild(opt);
  }
  if (defaultModel) {
    modelSel.value = defaultModel;
  }
}

function populatePresets() {
  const presetSel = document.getElementById('preset');
  presetSel.innerHTML = '';
  for (const p of catalog.hardware_presets) {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = p.label;
    presetSel.appendChild(opt);
  }
  presetSel.value = 'rpi_rc_car';
}

function populateStacks() {
  const stackSel = document.getElementById('stack');
  stackSel.innerHTML = '';
  for (const stack of catalog.stack_profiles) {
    const opt = document.createElement('option');
    opt.value = stack.id;
    opt.textContent = `${stack.label}`;
    opt.dataset.provider = stack.provider;
    opt.dataset.model = stack.model_profile_id;
    stackSel.appendChild(opt);
  }
}

async function loadCatalog() {
  const res = await fetch('/setup/api/catalog', { headers: getAuthHeaders() });
  if (!res.ok) throw new Error('Failed to load setup catalog');
  catalog = await res.json();
  populateProviders('anthropic');
  populateModels('anthropic');
  populatePresets();
  populateStacks();
  text(document.getElementById('result'), 'Catalog loaded. Choose a stack or provider/model then run preflight.', 'ok');
}

async function ensureSession() {
  const cached = window.localStorage.getItem('opencastor_setup_session_id');
  if (cached) {
    const resumed = await fetch(`/setup/api/session/${cached}`, { headers: getAuthHeaders() });
    if (resumed.ok) {
      const payload = await resumed.json();
      if (payload.status === 'in_progress') {
        sessionId = cached;
        await fetch(`/setup/api/session/${cached}/resume`, { method: 'POST', headers: getAuthHeaders() });
        return;
      }
    }
  }
  const started = await fetch('/setup/api/session/start', {
    method: 'POST',
    headers: getAuthHeaders({'Content-Type': 'application/json'}),
    body: JSON.stringify({ robot_name: document.getElementById('robot_name').value }),
  });
  if (!started.ok) throw new Error('Failed to start setup session');
  const payload = await started.json();
  sessionId = payload.session_id;
  window.localStorage.setItem('opencastor_setup_session_id', sessionId);
}

async function stageSelect(stage, values) {
  if (!sessionId) return;
  await fetch(`/setup/api/session/${sessionId}/select`, {
    method: 'POST',
    headers: getAuthHeaders({'Content-Type': 'application/json'}),
    body: JSON.stringify({ stage, values }),
  });
}

async function runPreflight() {
  const result = document.getElementById('result');
  const provider = document.getElementById('provider').value;
  const model = document.getElementById('model').value;
  const stackId = document.getElementById('stack').value;
  const autoInstall = document.getElementById('auto_install').checked;
  await stageSelect('profile', { provider, model, stack_id: stackId });
  text(result, 'Running preflight...');

  const res = await fetch('/setup/api/preflight', {
    method: 'POST',
    headers: getAuthHeaders({'Content-Type': 'application/json'}),
    body: JSON.stringify({
      provider,
      model_profile: model,
      auto_install: autoInstall,
      stack_id: stackId,
      session_id: sessionId,
    })
  });
  const payload = await res.json();
  if (!res.ok) {
    text(result, JSON.stringify(payload, null, 2), 'err');
    return;
  }

  const lines = [];
  lines.push(`Provider: ${provider}`);
  lines.push(`Ready: ${payload.ok}`);
  if (payload.reason) lines.push(`Reason: ${payload.reason}`);
  if (payload.issues && payload.issues.length) {
    lines.push('Issues:');
    for (const issue of payload.issues) lines.push(`- ${issue}`);
  }
  if (payload.actions && payload.actions.length) {
    lines.push('Actions:');
    for (const action of payload.actions) lines.push(`- ${action}`);
  }
  if (payload.fallback_stacks && payload.fallback_stacks.length) {
    lines.push(`Fallback stacks: ${payload.fallback_stacks.join(', ')}`);
  }
  text(result, lines.join('\n'), payload.ok ? 'ok' : 'warn');
}

async function verifyConfig() {
  const result = document.getElementById('result');
  const body = {
    robot_name: document.getElementById('robot_name').value,
    provider: document.getElementById('provider').value,
    model: document.getElementById('model').value,
    preset: document.getElementById('preset').value,
    stack_id: document.getElementById('stack').value,
    api_key: document.getElementById('api_key').value,
    allow_warnings: false,
    session_id: sessionId,
  };
  await stageSelect('verify', body);
  text(result, 'Verifying config...');
  const res = await fetch('/setup/api/verify-config', {
    method: 'POST',
    headers: getAuthHeaders({'Content-Type': 'application/json'}),
    body: JSON.stringify(body),
  });
  const payload = await res.json();
  if (!res.ok) {
    text(result, JSON.stringify(payload, null, 2), 'err');
    return;
  }
  const lines = [];
  lines.push(`Verify OK: ${payload.ok}`);
  if (payload.blocking_errors && payload.blocking_errors.length) {
    lines.push('Blocking errors:');
    for (const item of payload.blocking_errors) lines.push(`- ${item}`);
  }
  if (payload.warnings && payload.warnings.length) {
    lines.push('Warnings:');
    for (const item of payload.warnings) lines.push(`- ${item}`);
    lines.push('Tip: re-run verify with allow_warnings=true only if you accept these warnings.');
  }
  text(result, lines.join('\n'), payload.ok ? 'ok' : 'warn');
}

async function generateConfig() {
  const result = document.getElementById('result');
  const body = {
    robot_name: document.getElementById('robot_name').value,
    provider: document.getElementById('provider').value,
    model: document.getElementById('model').value,
    preset: document.getElementById('preset').value,
    stack_id: document.getElementById('stack').value,
    api_key: document.getElementById('api_key').value,
    session_id: sessionId,
  };

  await stageSelect('save', body);
  text(result, 'Generating config...');
  const res = await fetch('/setup/api/generate-config', {
    method: 'POST',
    headers: getAuthHeaders({'Content-Type': 'application/json'}),
    body: JSON.stringify(body),
  });
  const payload = await res.json();
  if (!res.ok) {
    text(result, JSON.stringify(payload, null, 2), 'err');
    return;
  }

  text(
    result,
    `Setup complete.\nConfig file: ${payload.filename}\nProvider: ${payload.provider}\nModel: ${payload.model}\n\nNext: castor run --config ${payload.filename}`,
    'ok'
  );
  if (sessionId) {
    window.localStorage.removeItem('opencastor_setup_session_id');
  }
}

window.addEventListener('DOMContentLoaded', async () => {
  try {
    await loadCatalog();
    await ensureSession();

    document.getElementById('stack').addEventListener('change', (ev) => {
      const stackId = ev.target.value;
      const stack = catalog.stack_profiles.find(s => s.id === stackId);
      if (!stack) return;
      document.getElementById('provider').value = stack.provider;
      populateModels(stack.provider, stack.model_profile_id);
      stageSelect('stack', { stack_id: stackId, provider: stack.provider, model: stack.model_profile_id });
    });

    document.getElementById('provider').addEventListener('change', (ev) => {
      populateModels(ev.target.value);
      stageSelect('profile', { provider: ev.target.value });
    });

    document.getElementById('verify_btn').addEventListener('click', verifyConfig);
    document.getElementById('preflight_btn').addEventListener('click', runPreflight);
    document.getElementById('generate_btn').addEventListener('click', generateConfig);
  } catch (err) {
    text(document.getElementById('result'), err.message, 'err');
  }
});
</script>
</body>
</html>"""


def launch_web_wizard(port: int = 8080):
    """Start the web wizard server and open the browser."""
    try:
        import uvicorn
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import HTMLResponse
        from pydantic import BaseModel, Field
    except ImportError:
        print("  Web wizard requires FastAPI and uvicorn (already installed).")
        print("  Falling back to terminal wizard...")
        from castor.wizard import main as run_wizard

        run_wizard()
        return

    from castor.setup_service import (
        finalize_setup_session,
        generate_setup_config,
        get_setup_catalog,
        get_setup_metrics,
        get_setup_session,
        resume_setup_session,
        run_preflight,
        run_remediation,
        save_config_file,
        save_env_vars,
        select_setup_session,
        start_setup_session,
        verify_setup_config,
    )

    app = FastAPI(title="OpenCastor Web Wizard")

    class PreflightRequest(BaseModel):
        provider: str
        model_profile: str = ""
        auto_install: bool = False
        stack_id: str = ""
        session_id: str = ""

    class GenerateRequest(BaseModel):
        robot_name: str
        provider: str
        model: str
        preset: str = "rpi_rc_car"
        stack_id: str = ""
        api_key: str = ""
        session_id: str = ""

    class SessionStartRequest(BaseModel):
        robot_name: str = ""

    class SessionSelectRequest(BaseModel):
        stage: str
        values: dict = Field(default_factory=dict)

    class RemediationRequest(BaseModel):
        remediation_id: str
        consent: bool = False
        session_id: str = ""
        context: dict = Field(default_factory=dict)

    class VerifyRequest(BaseModel):
        robot_name: str
        provider: str
        model: str
        preset: str = "rpi_rc_car"
        stack_id: str = ""
        api_key: str = ""
        allow_warnings: bool = False
        session_id: str = ""

    @app.get("/", response_class=HTMLResponse)
    async def wizard_page():
        return _HTML_TEMPLATE

    @app.get("/setup/api/catalog")
    async def setup_catalog():
        return get_setup_catalog(wizard_context=True)

    @app.post("/setup/api/session/start")
    async def setup_session_start(req: SessionStartRequest):
        return start_setup_session(robot_name=req.robot_name or None, wizard_context=True)

    @app.get("/setup/api/session/{session_id}")
    async def setup_session_get(session_id: str):
        return get_setup_session(session_id)

    @app.post("/setup/api/session/{session_id}/select")
    async def setup_session_select(session_id: str, req: SessionSelectRequest):
        return select_setup_session(session_id, stage=req.stage, values=req.values or {})

    @app.post("/setup/api/session/{session_id}/resume")
    async def setup_session_resume(session_id: str):
        return resume_setup_session(session_id)

    @app.post("/setup/api/preflight")
    async def setup_preflight(req: PreflightRequest):
        return run_preflight(
            provider=req.provider,
            model_profile=req.model_profile or None,
            auto_install=req.auto_install,
            stack_id=req.stack_id or None,
            session_id=req.session_id or None,
        )

    @app.post("/setup/api/remediate")
    async def setup_remediate(req: RemediationRequest):
        return run_remediation(
            req.remediation_id,
            consent=req.consent,
            session_id=req.session_id or None,
            context=req.context or None,
        )

    @app.post("/setup/api/verify-config")
    async def setup_verify(req: VerifyRequest):
        return verify_setup_config(
            robot_name=req.robot_name,
            provider=req.provider,
            model=req.model,
            preset=req.preset,
            stack_id=req.stack_id or None,
            api_key=req.api_key or None,
            allow_warnings=req.allow_warnings,
            session_id=req.session_id or None,
        )

    @app.get("/setup/api/metrics")
    async def setup_metrics():
        return get_setup_metrics()

    @app.post("/setup/api/generate-config")
    async def setup_generate(req: GenerateRequest):
        try:
            payload = generate_setup_config(
                robot_name=req.robot_name,
                provider=req.provider,
                model=req.model,
                preset=req.preset,
            )
            env_var = payload["agent_config"].get("env_var")
            if req.api_key and env_var:
                save_env_vars({env_var: req.api_key})
            save_config_file(payload["config"], payload["filename"])
            if req.session_id:
                with contextlib.suppress(Exception):
                    select_setup_session(
                        req.session_id,
                        stage="save",
                        values={
                            "robot_name": req.robot_name,
                            "provider": req.provider,
                            "model": req.model,
                            "preset": req.preset,
                            "stack_id": req.stack_id or None,
                        },
                    )
                    finalize_setup_session(req.session_id, success=True, reason_code="READY")
            return {
                "filename": payload["filename"],
                "provider": payload["agent_config"]["provider"],
                "model": payload["agent_config"]["model"],
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc

    # Legacy endpoint for compatibility.
    @app.post("/api/wizard/generate")
    async def legacy_generate(req: GenerateRequest):
        return await setup_generate(req)

    print(f"\n  Web wizard starting on http://localhost:{port}")
    print("  Press Ctrl+C to stop.\n")

    import threading

    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

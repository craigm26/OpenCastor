"""
castor/rrf_cmd.py — RRF v2 registration CLI helpers.

Commands:
  castor rrf register     — register this robot (RRN)
  castor rrf components   — register all hardware components (RCN)
  castor rrf models       — register AI models used (RMN)
  castor rrf harness      — register the AI harness (RHN)
  castor rrf status       — show full provenance chain
  castor rrf wipe         — (dev) delete this robot's records from RRF
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

RRF_BASE = "https://robot-registry-foundation.pages.dev"


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_config(config_path: str | None) -> tuple[dict, Path]:
    from pathlib import Path

    import yaml

    if config_path:
        p = Path(config_path).expanduser()
    else:
        candidates = [
            Path.cwd() / "bob.rcan.yaml",
            Path("~/opencastor/bob.rcan.yaml").expanduser(),
            Path("~/.opencastor/rcan.yaml").expanduser(),
        ]
        p = next((c for c in candidates if c.exists()), candidates[0])

    if not p.exists():
        print(f"❌ Config not found: {p}")
        print("   Pass --config <path> or run from the opencastor directory.")
        sys.exit(1)

    with open(p) as f:
        cfg = yaml.safe_load(f) or {}
    return cfg, p


def _post(path: str, body: dict, token: str | None = None) -> dict:
    """POST via curl subprocess — avoids Cloudflare bot protection blocking urllib UA."""
    import os
    import subprocess
    import tempfile
    url = f"{RRF_BASE}{path}"
    # Write body to temp file to avoid shell quoting issues
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(body, f)
        tmp = f.name
    try:
        cmd = [
            "curl", "-s", "-X", "POST", url,
            "-H", "Content-Type: application/json",
            "-H", "User-Agent: OpenCastor/2026.3.27.1 castor-cli/rrf",
            "--data", f"@{tmp}",
        ]
        if token:
            cmd += ["-H", f"Authorization: Bearer {token}"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"❌ curl failed: {result.stderr}", file=sys.stderr)
            sys.exit(1)
        data = json.loads(result.stdout)
        if "error" in data and "rrn" not in data and "rcn" not in data and "rmn" not in data and "rhn" not in data:
            print(f"❌ RRF error from {path}: {data['error']}", file=sys.stderr)
            sys.exit(1)
        return data
    finally:
        os.unlink(tmp)


def _get(path: str) -> dict:
    """GET via curl subprocess — avoids CF bot protection."""
    import subprocess
    url = f"{RRF_BASE}{path}"
    result = subprocess.run(
        ["curl", "-s", "-H", "User-Agent: OpenCastor/2026.3.27.1 castor-cli/rrf", url],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {"error": "curl_failed"}
    try:
        return json.loads(result.stdout)
    except Exception:
        return {"error": "parse_failed", "raw": result.stdout[:200]}


def _load_pq_pub(key_path: str | None = None) -> str | None:
    """Load ML-DSA-65 public key as base64, from .pub file."""
    candidates = [
        Path(key_path).expanduser() if key_path else None,
        Path("~/.opencastor/pq_signing.pub").expanduser(),
    ]
    for p in candidates:
        if p and p.exists():
            data = p.read_bytes()
            return base64.b64encode(data).decode()
    return None


def _load_token(token_path: str | None = None) -> str | None:
    candidates = [
        Path(token_path).expanduser() if token_path else None,
        Path("~/.config/opencastor/bob-rrf-token.txt").expanduser(),
    ]
    for p in candidates:
        if p and p.exists():
            t = p.read_text().strip()
            return t if t else None
    return None


# ── sub-commands ──────────────────────────────────────────────────────────────

def cmd_rrf_register(args) -> None:
    """Register this robot with the RRF and receive/confirm an RRN."""

    cfg, cfg_path = _load_config(getattr(args, "config", None))
    meta = cfg.get("metadata", {})

    rrn = meta.get("rrn")
    name = meta.get("robot_name", meta.get("name", "unnamed"))
    manufacturer = meta.get("manufacturer", "unknown")
    model = meta.get("model", "unknown")
    firmware_version = meta.get("version", meta.get("firmware_version", "v1"))
    rcan_version = cfg.get("rcan_version", "2.2")
    pq_kid = cfg.get("agent", {}).get("signing", {}).get("pq_kid") or meta.get("pq_kid")
    pq_pub = _load_pq_pub()
    token = _load_token(getattr(args, "token", None))

    print("\n🤖  Registering robot with RRF...")
    print(f"    Name:             {name}")
    print(f"    Manufacturer:     {manufacturer}")
    print(f"    Model:            {model}")
    print(f"    Firmware:         {firmware_version}")
    print(f"    RCAN version:     {rcan_version}")
    print(f"    ML-DSA-65 key:    {pq_kid or '(none)'}")
    print(f"    LoA enforcement:  {cfg.get('loa_enforcement', True)}")

    if rrn and not getattr(args, "force", False):
        existing = _get(f"/v2/robots/{rrn}")
        if "error" not in existing:
            print(f"\n⚠️   Robot already registered: {rrn}")
            print(f"    View: {RRF_BASE}/registry/entity/?type=robot&id={rrn}")
            ans = input("Re-register anyway? [y/N]: ").strip().lower()
            if ans not in ("y", "yes"):
                print("   Skipped. Use --force to override.")
                return

    body: dict[str, Any] = {
        "name": name,
        "manufacturer": manufacturer,
        "model": model,
        "firmware_version": firmware_version,
        "rcan_version": rcan_version,
        "loa_enforcement": cfg.get("loa_enforcement", True),
    }
    if pq_pub:
        body["pq_signing_pub"] = pq_pub
    if pq_kid:
        body["pq_kid"] = pq_kid

    # Build RURI from config
    ruri = meta.get("ruri") or meta.get("rrn_uri")
    if ruri and ruri.startswith("rrn://"):
        # Convert rrn:// → rcan:// format
        ruri = ruri.replace("rrn://", "rcan://")
    if ruri:
        body["ruri"] = ruri

    result = _post("/v2/robots/register", body, token)
    new_rrn = result.get("rrn")
    print(f"\n✅  Registered: {new_rrn}")
    print(f"    Record URL: {result.get('record_url')}")

    # Persist RRN back to config if it changed
    if new_rrn and new_rrn != rrn:
        with open(cfg_path) as f:
            raw = f.read()
        if f"rrn: {rrn}" in raw:
            raw = raw.replace(f"rrn: {rrn}", f"rrn: {new_rrn}")
            with open(cfg_path, "w") as f:
                f.write(raw)
            print(f"    Updated config RRN: {rrn} → {new_rrn}")
    return new_rrn


def cmd_rrf_components(args) -> None:
    """Register hardware components from config to RRF (receives RCNs)."""
    cfg, _ = _load_config(getattr(args, "config", None))
    meta = cfg.get("metadata", {})
    rrn = meta.get("rrn")
    token = _load_token(getattr(args, "token", None))

    if not rrn:
        print("❌ No RRN in config. Run: castor rrf register first.")
        sys.exit(1)

    components = cfg.get("components", [])
    if not components:
        print("⚠️  No components: block in config. Add hardware components to register.")
        return

    print(f"\n🔌  Registering {len(components)} component(s) for {rrn}...\n")
    rcns = []
    for comp in components:
        ctype = comp.get("type", "other")
        model = comp.get("model", "unknown")
        manufacturer = comp.get("manufacturer", "unknown")
        caps = comp.get("capabilities", [])
        firmware = comp.get("firmware_version")

        # Build capabilities list with enriched detail
        enriched_caps = list(caps)
        if ctype == "npu" and comp.get("tops"):
            tops_cap = f"tops:{int(comp['tops'])}"
            if tops_cap not in enriched_caps:
                enriched_caps.append(tops_cap)
        if comp.get("device_path"):
            enriched_caps.append(f"device:{comp['device_path']}")

        specs: dict[str, Any] = {}
        if comp.get("cpu_count"):
            specs["cpu_count"] = comp["cpu_count"]
        if comp.get("tops"):
            specs["tops"] = comp["tops"]
        if comp.get("status"):
            specs["status"] = comp["status"]

        body: dict[str, Any] = {
            "parent_rrn": rrn,
            "type": ctype,
            "model": model,
            "manufacturer": manufacturer,
        }
        if enriched_caps:
            body["capabilities"] = enriched_caps
        if firmware:
            body["firmware_version"] = firmware
        if comp.get("serial_number"):
            body["serial_number"] = comp["serial_number"]
        if specs:
            body["specs"] = specs

        print(f"  📦  {ctype.upper()} — {model} ({manufacturer})")
        result = _post("/v2/components/register", body, token)
        rcn = result.get("rcn")
        rcns.append(rcn)
        print(f"       ✅  {rcn}  →  {result.get('record_url')}")

    print(f"\n✅  {len(rcns)} component(s) registered: {', '.join(rcns)}")
    return rcns


def cmd_rrf_models(args) -> None:
    """Register AI models used by this robot (LeWorldModel, OpenVLA, Claude)."""
    cfg, _ = _load_config(getattr(args, "config", None))
    token = _load_token(getattr(args, "token", None))

    # Derive models from config
    brain = cfg.get("brain", {})
    agent = cfg.get("agent", {})
    layers = agent.get("layers", [])

    # Build model list from config
    models_to_register: list[dict[str, Any]] = []

    # LeWorldModel (world model — used for scene understanding)
    models_to_register.append({
        "name": "LeWorldModel",
        "version": "v0.1.0",
        "model_family": "world_model",
        "architecture": "jepa",
        "parameter_count_b": 0.015,
        "license": "apache-2.0",
        "provider": "local",
        "provider_model_id": "huggingface/lerobot/le_world_model",
        "repo_url": "https://github.com/huggingface/lerobot",
        "rcan_compatible": True,
        "description": "LeRobot world model — jepa-based scene prediction for reactive control",
    })

    # OpenVLA (VLA reactive brain)
    vla_model = brain.get("model", "openvla/openvla-7b")
    models_to_register.append({
        "name": "OpenVLA",
        "version": "7b-1.0",
        "model_family": "vla",
        "architecture": "transformer",
        "parameter_count_b": 7.0,
        "license": "apache-2.0",
        "provider": "local",
        "provider_model_id": vla_model,
        "repo_url": "https://github.com/openvla/openvla",
        "rcan_compatible": True,
        "description": "Vision-Language-Action model — reactive perception-to-action at ~10Hz on Hailo-8 NPU",
    })

    # Claude claude-opus-4-6 (planning brain)
    planning_model = "claude-opus-4-6"
    for layer in layers:
        if "planning" in layer.get("name", ""):
            m = layer.get("model", "")
            if "/" in m:
                planning_model = m.split("/")[-1]

    models_to_register.append({
        "name": "Claude",
        "version": planning_model,
        "model_family": "language",
        "architecture": "transformer",
        "parameter_count_b": None,   # not disclosed
        "license": "proprietary",
        "provider": "anthropic",
        "provider_model_id": f"anthropic/{planning_model}",
        "repo_url": "https://anthropic.com",
        "rcan_compatible": True,
        "description": "Anthropic Claude — planning brain for high-level task decomposition, safety review, low-confidence escalation",
    })

    print(f"\n🧠  Registering {len(models_to_register)} AI model(s) to RRF...\n")
    rmns = []
    for m in models_to_register:
        desc = m.pop("description", "")
        print(f"  🤖  {m['name']} {m['version']} ({m['model_family']})")
        if desc:
            print(f"       {desc}")
        body = {k: v for k, v in m.items() if v is not None}
        import time
        # Retry with backoff to handle CF KV eventual consistency (counter may lag)
        for _attempt in range(3):
            result = _post("/v2/models/register", body, token)
            rmn = result.get("rmn")
            if rmn and rmn not in rmns:
                break
            print(f"       ⚠️  Counter collision ({rmn} already used), retrying in 5s…")
            time.sleep(5)
        rmns.append(rmn)
        print(f"       ✅  {rmn}  →  {result.get('record_url')}")
        time.sleep(5)  # CF KV eventual consistency — wait for counter to propagate

    print(f"\n✅  {len(rmns)} model(s) registered: {', '.join(rmns)}")
    return rmns


def cmd_rrf_harness(args) -> None:
    """Register the AI harness (OpenCastor Dual-Brain) to RRF (receives RHN)."""
    cfg, _ = _load_config(getattr(args, "config", None))
    meta = cfg.get("metadata", {})
    rrn = meta.get("rrn")
    token = _load_token(getattr(args, "token", None))

    if not rrn:
        print("❌ No RRN in config. Run: castor rrf register first.")
        sys.exit(1)

    # Fetch model IDs from RRF to link them
    registry = _get("/v2/registry?type=model")
    model_rmns = [e["id"] for e in registry.get("entries", [])]

    firmware_version = meta.get("version", "v2026.3.27.1")
    body: dict[str, Any] = {
        "name": "OpenCastor Dual-Brain",
        "version": firmware_version,
        "harness_type": "hybrid",
        "rcan_version": "2.2",
        "description": (
            "OpenCastor dual-brain AI harness: VLA reactive layer (OpenVLA-7B on Hailo-8 NPU) "
            "coupled with Claude planning layer. Confidence gate at 0.60 — reactive handles "
            "normal operation, planning escalates on uncertainty or destructive scope. "
            "RCAN v2.2 compliant with ML-DSA-65 signing, LoA enforcement, and Protocol 66 safety."
        ),
        "open_source": True,
        "repo_url": "https://github.com/craigm26/OpenCastor",
        "license": "apache-2.0",
        "compatible_robots": [rrn],
    }
    if model_rmns:
        body["model_ids"] = model_rmns

    print("\n⚙️   Registering AI harness to RRF...")
    print("    Name:     OpenCastor Dual-Brain")
    print(f"    Version:  {firmware_version}")
    print("    Type:     hybrid (VLA + LLM planner)")
    print(f"    Models:   {model_rmns or '(none registered yet)'}")
    print(f"    Robot:    {rrn}")

    result = _post("/v2/harnesses/register", body, token)
    rhn = result.get("rhn")
    print(f"\n✅  Registered: {rhn}")
    print(f"    Record URL: {result.get('record_url')}")
    return rhn


def cmd_rrf_status(args) -> None:
    """Show full provenance chain for this robot."""
    cfg, _ = _load_config(getattr(args, "config", None))
    meta = cfg.get("metadata", {})
    rrn = meta.get("rrn", "RRN-000000000001")

    print(f"\n🔗  RRF Provenance Chain — {rrn}\n")

    registry = _get("/v2/registry")
    entries = registry.get("entries", [])
    counts = registry.get("entity_types_count", {})

    # Print in provenance order: RRN → RCN → RMN → RHN
    labels = [("robot", "🤖 Robot (RRN)"), ("component", "🔌 Components (RCN)"),
               ("model", "🧠 AI Models (RMN)"), ("harness", "⚙️  AI Harness (RHN)")]

    for etype, label in labels:
        elist = [e for e in entries if e["entity_type"] == etype]
        print(f"  {label} — {counts.get(etype, 0)} registered")
        if elist:
            for e in elist:
                print(f"    ├─ {e['id']}  {e['name']}")
                for k, v in e.get("summary", {}).items():
                    print(f"    │   {k}: {v}")
        else:
            print("    └─ (none)")
        print()

    print(f"  Registry: {RRF_BASE}/registry/")
    print(f"  Robot detail: {RRF_BASE}/registry/entity/?type=robot&id={rrn}\n")


def cmd_rrf_wipe(args) -> None:
    """(dev) Wipe all RRF KV records via admin endpoint."""
    import subprocess
    secret = getattr(args, "secret", "clawd-wipe-2026")
    url = f"{RRF_BASE}/admin/wipe?secret={secret}"
    print("🗑️   Wiping all RRF KV records via admin endpoint...")
    result = subprocess.run(
        ["curl", "-s", "-H", "User-Agent: OpenCastor/2026.3.27.1 castor-cli/rrf", url],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"❌  curl failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    data = json.loads(result.stdout)
    if "error" in data:
        print(f"❌  Wipe failed: {data['error']}", file=sys.stderr)
        sys.exit(1)
    deleted = data.get("deleted", [])
    print(f"✅  Deleted {len(deleted)} KV keys:")
    for k in sorted(deleted):
        print(f"    - {k}")


def cmd_rrf(args) -> None:
    """castor rrf — Robot Registry Foundation v2 commands."""
    sub = getattr(args, "rrf_cmd", None) or "status"
    dispatch = {
        "register":   cmd_rrf_register,
        "components": cmd_rrf_components,
        "models":     cmd_rrf_models,
        "harness":    cmd_rrf_harness,
        "status":     cmd_rrf_status,
        "wipe":       cmd_rrf_wipe,
    }
    fn = dispatch.get(sub)
    if fn:
        fn(args)
    else:
        print(f"❌ Unknown rrf subcommand: {sub}")
        print("   Valid: register, components, models, harness, status, wipe")
        sys.exit(1)

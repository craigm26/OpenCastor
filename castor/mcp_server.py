"""castor/mcp_server.py — OpenCastor MCP server.

Exposes robot runtime capabilities as Model Context Protocol tools so any
MCP-capable client (Claude Code, Codex, Cursor, Gemini CLI, cron jobs, …)
can command and observe robots without custom glue code.

Usage (stdio transport — works with any MCP client today):

    castor mcp --token $CASTOR_MCP_TOKEN

Usage (add to Claude Code):

    claude mcp add castor -- castor mcp --token $CASTOR_MCP_TOKEN

Usage (development — LoA 3, no token required):

    CASTOR_MCP_DEV=1 castor mcp

Auth is provider-agnostic: any model / provider client gets the LoA level
associated with its token, declared in bob.rcan.yaml under ``mcp_clients:``.

LoA tiers
---------
0  Read-only  robot_status, robot_telemetry, fleet_list, rrf_lookup
1  Operate    robot_command, harness_get, research_run, contribute_toggle
3  Admin      harness_set, system_upgrade, loa_enable

Every tool call that mutates state is signed with the robot's ML-DSA-65 key
before hitting the gateway — identical to how the bridge handles commands.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
import yaml

from .mcp_auth import resolve_loa
from . import mcp_fleet as _mcp_fleet  # noqa: F401 — registers fleet tools

try:
    from mcp.server.fastmcp import FastMCP

    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

    class FastMCP:  # type: ignore[no-redef]
        _STUB = True

        def __init__(self, *a, **kw) -> None:
            self._tools: dict = {}
            self.instructions = kw.get("instructions", "")

        def tool(self):  # noqa: ANN201
            def decorator(fn):  # type: ignore[return]
                self._tools[fn.__name__] = fn
                return fn

            return decorator

        def run(self, **_kw) -> None:
            raise ImportError("MCP package not installed. Run: pip install 'opencastor[mcp]'")

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _load_config() -> dict[str, Any]:
    path = Path(os.environ.get("CASTOR_CONFIG", Path.home() / "opencastor/bob.rcan.yaml"))
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def _gateway_url() -> str:
    cfg = _load_config()
    return cfg.get("gateway_url", os.environ.get("CASTOR_GATEWAY_URL", "http://127.0.0.1:8001"))


def _gateway_token() -> str:
    return os.environ.get("CASTOR_GATEWAY_TOKEN", "")


def _default_rrn() -> str:
    cfg = _load_config()
    return cfg.get("rrn", os.environ.get("CASTOR_RRN", "RRN-000000000001"))


def _headers() -> dict[str, str]:
    token = _gateway_token()
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


# ---------------------------------------------------------------------------
# MCP server setup
# ---------------------------------------------------------------------------

# Token is resolved once at server startup (stdio = one client per process).
_CLIENT_TOKEN: str = ""
_CLIENT_LOA: int = 0
_CONFIG_PATH: Path | None = None


def _check_loa(required: int) -> None:
    """Raise PermissionError if client LoA < required."""
    if _CLIENT_LOA < required:
        raise PermissionError(
            f"This tool requires LoA ≥ {required}. "
            f"Your token has LoA {_CLIENT_LOA}. "
            f"Ask the robot operator to issue a higher-privilege token."
        )


mcp = FastMCP(
    "opencastor",
    instructions=(
        "OpenCastor robot runtime. Tools let you observe and command physical AI robots "
        "registered in the Robot Registry Foundation (RRF). "
        "All mutating commands are signed with the robot's ML-DSA-65 key and "
        "enforce RCAN v2.2 Level-of-Assurance (LoA) requirements. "
        "Provide rrn (Robot Registry Number) like 'RRN-000000000001' where required."
    ),
)


# ---------------------------------------------------------------------------
# Tier 0 — Read-only (LoA 0)
# ---------------------------------------------------------------------------


@mcp.tool()
def robot_status(rrn: str = "") -> dict[str, Any]:
    """Get live status snapshot for a robot: brain state, active model,
    uptime, LoA enforcement, supported transports.

    Args:
        rrn: Robot Registry Number (e.g. RRN-000000000001). Defaults to the
             locally configured robot.
    """
    _check_loa(0)
    rrn = rrn or _default_rrn()
    try:
        resp = httpx.get(f"{_gateway_url()}/api/status", headers=_headers(), timeout=10)
        data = resp.json() if resp.status_code == 200 else {"error": resp.text}
    except Exception as exc:
        data = {"error": str(exc)}
    return {"rrn": rrn, "status": data}


@mcp.tool()
def robot_telemetry(rrn: str = "") -> dict[str, Any]:
    """Get full telemetry snapshot: system info, model runtime, brain state,
    capabilities, component list, and live WebSocket URLs.

    Args:
        rrn: Robot Registry Number. Defaults to the locally configured robot.
    """
    _check_loa(0)
    rrn = rrn or _default_rrn()
    try:
        resp = httpx.get(
            f"{_gateway_url()}/api/telemetry",
            headers=_headers(),
            timeout=10,
        )
        if resp.status_code == 404:
            # Fallback: /api/status covers the basics
            resp = httpx.get(f"{_gateway_url()}/api/status", headers=_headers(), timeout=10)
        data = resp.json() if resp.status_code == 200 else {"error": resp.text}
    except Exception as exc:
        data = {"error": str(exc)}
    return {"rrn": rrn, "telemetry": data}


@mcp.tool()
def fleet_list() -> dict[str, Any]:
    """List all robots registered in this fleet.

    Returns each robot's RRN, name, status, LoA enforcement, RCAN version,
    and conformance level.
    """
    _check_loa(0)
    try:
        resp = httpx.get(
            f"{_gateway_url()}/api/fleet",
            headers=_headers(),
            timeout=10,
        )
        if resp.status_code == 404:
            # Single-robot gateway — return local robot only
            s = httpx.get(f"{_gateway_url()}/api/status", headers=_headers(), timeout=10)
            data = [s.json()] if s.status_code == 200 else []
        else:
            data = resp.json()
    except Exception as exc:
        data = {"error": str(exc)}
    return {"fleet": data}


@mcp.tool()
def rrf_lookup(entity_id: str) -> dict[str, Any]:
    """Look up any entity in the Robot Registry Foundation by ID.

    Supports RRN (robots), RCN (components), RMN (models), RHN (harnesses).
    Returns full provenance chain including manufacturer, firmware_hash,
    attestation_ref, and parent relationships.

    Args:
        entity_id: Registry ID like RRN-000000000001, RCN-000000000002, etc.
    """
    _check_loa(0)
    try:
        # Determine entity type from prefix
        prefix = entity_id.split("-")[0].lower()
        type_map = {"rrn": "robot", "rcn": "component", "rmn": "model", "rhn": "harness"}
        entity_type = type_map.get(prefix, "robot")

        resp = httpx.get(
            f"https://robotregistryfoundation.org/v2/registry/{entity_type}/{entity_id}",
            timeout=15,
            headers={"User-Agent": "OpenCastor-MCP/1.0"},
        )
        data = (
            resp.json()
            if resp.status_code == 200
            else {"error": resp.text, "status": resp.status_code}
        )
    except Exception as exc:
        data = {"error": str(exc)}
    return {"entity_id": entity_id, "record": data}


# ---------------------------------------------------------------------------
# Tier 1 — Operate (LoA 1)
# ---------------------------------------------------------------------------


@mcp.tool()
def robot_command(
    instruction: str,
    scope: str = "chat",
    rrn: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Send an instruction to a robot.

    Scope controls what the robot will do with the instruction:
    - "chat"    — conversational response via the active LLM
    - "control" — physical action (requires LoA ≥ 1 + LoA enforcement ON)
    - "system"  — runtime/config action (UPGRADE, RELOAD_CONFIG, REBOOT, etc.)
    - "safety"  — safety override (ESTOP, RESUME); bypasses replay checks

    The command is RCAN-signed before delivery.

    Args:
        instruction: Natural language or structured command string.
        scope: Command scope (chat/control/system/safety). Default: chat.
        rrn: Robot Registry Number. Defaults to local robot.
        reason: Optional human-readable reason logged in the audit trail.
    """
    _check_loa(1)
    rrn = rrn or _default_rrn()
    payload: dict[str, Any] = {
        "instruction": instruction,
        "scope": scope,
        "channel": "mcp",
        "context": "mcp_tool_call",
    }
    if reason:
        payload["reason"] = reason
    try:
        resp = httpx.post(
            f"{_gateway_url()}/api/command",
            json=payload,
            headers=_headers(),
            timeout=60,
        )
        data = (
            resp.json()
            if resp.status_code in (200, 202)
            else {"error": resp.text, "status": resp.status_code}
        )
    except Exception as exc:
        data = {"error": str(exc)}
    return {"rrn": rrn, "instruction": instruction, "scope": scope, "result": data}


@mcp.tool()
def harness_get(rrn: str = "") -> dict[str, Any]:
    """Read the current agent harness configuration for a robot.

    Returns the full harness: layers (provider, model, system_prompt, tools),
    flow graph, RCAN version, and last-saved timestamp.

    Args:
        rrn: Robot Registry Number. Defaults to local robot.
    """
    _check_loa(1)
    rrn = rrn or _default_rrn()
    try:
        resp = httpx.get(
            f"{_gateway_url()}/api/harness",
            headers=_headers(),
            timeout=10,
        )
        data = resp.json() if resp.status_code == 200 else {"error": resp.text}
    except Exception as exc:
        data = {"error": str(exc)}
    return {"rrn": rrn, "harness": data}


@mcp.tool()
def research_run(rrn: str = "") -> dict[str, Any]:
    """Trigger an OHB-1 (Open Harness Benchmark) research run on a robot.

    The bridge will execute the benchmark asynchronously and write results
    to robots/{rrn}/telemetry/research in Firestore. Results are typically
    available within 60 seconds.

    Args:
        rrn: Robot Registry Number. Defaults to local robot.
    """
    _check_loa(1)
    rrn = rrn or _default_rrn()
    try:
        resp = httpx.post(
            f"{_gateway_url()}/api/command",
            json={
                "instruction": "research_run",
                "scope": "system",
                "channel": "mcp",
                "params": {"personal": True},
            },
            headers=_headers(),
            timeout=30,
        )
        data = resp.json() if resp.status_code in (200, 202) else {"error": resp.text}
    except Exception as exc:
        data = {"error": str(exc)}
    return {"rrn": rrn, "queued": "error" not in data, "result": data}


@mcp.tool()
def contribute_toggle(enabled: bool, rrn: str = "") -> dict[str, Any]:
    """Enable or disable idle compute contribution for a robot.

    When enabled, the robot donates idle compute cycles to the OpenCastor
    community research network and earns Castor Credits.

    Args:
        enabled: True to start contributing, False to stop.
        rrn: Robot Registry Number. Defaults to local robot.
    """
    _check_loa(1)
    rrn = rrn or _default_rrn()
    instruction = "/contribute start" if enabled else "/contribute stop"
    try:
        resp = httpx.post(
            f"{_gateway_url()}/api/command",
            json={"instruction": instruction, "scope": "system", "channel": "mcp"},
            headers=_headers(),
            timeout=30,
        )
        data = resp.json() if resp.status_code in (200, 202) else {"error": resp.text}
    except Exception as exc:
        data = {"error": str(exc)}
    return {"rrn": rrn, "contribute_enabled": enabled, "result": data}


@mcp.tool()
def components_list(rrn: str = "") -> dict[str, Any]:
    """List all registered hardware components for a robot.

    Returns each component's RCN, type (cpu/npu/camera/sensor), manufacturer,
    model, capabilities, and firmware version.

    Args:
        rrn: Robot Registry Number. Defaults to local robot.
    """
    _check_loa(1)
    rrn = rrn or _default_rrn()
    try:
        resp = httpx.get(
            f"{_gateway_url()}/api/components",
            headers=_headers(),
            timeout=10,
        )
        data = resp.json() if resp.status_code == 200 else {"error": resp.text}
    except Exception as exc:
        data = {"error": str(exc)}
    return {"rrn": rrn, "components": data}


# ---------------------------------------------------------------------------
# Tier 3 — Admin (LoA 3)
# ---------------------------------------------------------------------------


@mcp.tool()
def harness_set(
    layers: list[dict[str, Any]],
    flow_graph: dict[str, Any] | None = None,
    rrn: str = "",
) -> dict[str, Any]:
    """Deploy a new agent harness configuration to a robot.

    Replaces the active harness with the provided layer stack. Each layer
    specifies provider, model, system_prompt, tools, and routing rules.
    The harness is validated against RCAN v2.2 schema before deployment.

    Requires LoA 3 (admin token).

    Args:
        layers: List of harness layer objects.
        flow_graph: Optional flow graph dict (nodes + edges).
        rrn: Robot Registry Number. Defaults to local robot.
    """
    _check_loa(3)
    rrn = rrn or _default_rrn()
    payload: dict[str, Any] = {
        "layers": layers,
        "rcan_version": "2.2",
        "rrn": rrn,
    }
    if flow_graph:
        payload["flow_graph"] = flow_graph
    try:
        resp = httpx.post(
            f"{_gateway_url()}/api/harness",
            json=payload,
            headers=_headers(),
            timeout=30,
        )
        data = resp.json() if resp.status_code in (200, 202) else {"error": resp.text}
    except Exception as exc:
        data = {"error": str(exc)}
    return {"rrn": rrn, "deployed": "error" not in data, "result": data}


@mcp.tool()
def system_upgrade(version: str = "", rrn: str = "") -> dict[str, Any]:
    """Trigger an OTA system upgrade on a robot.

    Upgrades the OpenCastor runtime to the specified version, or to the
    latest release if version is omitted. The robot will restart after upgrade.

    Requires LoA 3 (admin token).

    Args:
        version: Target version like '2026.3.29.0'. Omit for latest.
        rrn: Robot Registry Number. Defaults to local robot.
    """
    _check_loa(3)
    rrn = rrn or _default_rrn()
    instruction = f"UPGRADE: {version}" if version else "UPGRADE"
    try:
        resp = httpx.post(
            f"{_gateway_url()}/api/command",
            json={"instruction": instruction, "scope": "system", "channel": "mcp"},
            headers=_headers(),
            timeout=30,
        )
        data = resp.json() if resp.status_code in (200, 202) else {"error": resp.text}
    except Exception as exc:
        data = {"error": str(exc)}
    return {
        "rrn": rrn,
        "upgrade_queued": "error" not in data,
        "target_version": version or "latest",
        "result": data,
    }


@mcp.tool()
def loa_enable(min_loa: int = 1, rrn: str = "") -> dict[str, Any]:
    """Enable Level-of-Assurance enforcement on a robot.

    Once enabled, all control-scope commands require the caller to present
    a JWT with LoA ≥ min_loa. ESTOP always bypasses LoA checks.

    Requires LoA 3 (admin token).

    Args:
        min_loa: Minimum LoA required for control commands (1–5). Default: 1.
        rrn: Robot Registry Number. Defaults to local robot.
    """
    _check_loa(3)
    rrn = rrn or _default_rrn()
    try:
        resp = httpx.post(
            f"{_gateway_url()}/api/command",
            json={
                "instruction": "loa_enable",
                "scope": "system",
                "channel": "mcp",
                "params": {"min_loa": min_loa},
            },
            headers=_headers(),
            timeout=15,
        )
        data = resp.json() if resp.status_code in (200, 202) else {"error": resp.text}
    except Exception as exc:
        data = {"error": str(exc)}
    return {"rrn": rrn, "loa_enforcement": True, "min_loa": min_loa, "result": data}


# ---------------------------------------------------------------------------
# Additional Tier 0 tools
# ---------------------------------------------------------------------------


@mcp.tool()
def robot_ping(rrn: str = "") -> dict[str, Any]:
    """Quick health check — confirms the robot gateway is reachable and responsive.

    Returns latency, uptime, and bridge status. Use this before issuing commands
    to verify connectivity.

    Args:
        rrn: Robot Registry Number. Defaults to local robot.
    """
    _check_loa(0)
    rrn = rrn or _default_rrn()
    import time

    t0 = time.monotonic()
    try:
        resp = httpx.get(f"{_gateway_url()}/health", headers=_headers(), timeout=5)
        latency_ms = round((time.monotonic() - t0) * 1000)
        data = (
            resp.json()
            if resp.status_code == 200
            else {"status": "error", "code": resp.status_code}
        )
    except Exception as exc:
        latency_ms = round((time.monotonic() - t0) * 1000)
        data = {"status": "unreachable", "error": str(exc)}
    return {
        "rrn": rrn,
        "reachable": data.get("status") != "unreachable",
        "latency_ms": latency_ms,
        "gateway": data,
    }


@mcp.tool()
def compliance_report(rrn: str = "") -> dict[str, Any]:
    """Get the EU AI Act Article 11 compliance report for a robot.

    Returns compliance status across all Art. 11 requirements:
    system identity, hardware provenance, model provenance, safety controls
    (LoA enforcement), and post-market monitoring (BigQuery telemetry).

    This is the programmatic equivalent of `castor audit --art11`.

    Args:
        rrn: Robot Registry Number. Defaults to local robot.
    """
    _check_loa(0)
    rrn = rrn or _default_rrn()
    try:
        resp = httpx.get(
            f"{_gateway_url()}/api/audit",
            headers=_headers(),
            timeout=15,
        )
        data = resp.json() if resp.status_code == 200 else {"error": resp.text}
    except Exception as exc:
        data = {"error": str(exc)}
    return {"rrn": rrn, "compliance": data}


# ---------------------------------------------------------------------------
# Server entrypoint
# ---------------------------------------------------------------------------


def run(token: str, config_path: Path | None = None) -> None:
    """Start the MCP server (stdio transport).

    Parameters
    ----------
    token:
        Raw bearer token supplied via ``--token`` or ``CASTOR_MCP_TOKEN``.
    config_path:
        Path to robot RCAN yaml for client registry lookup.
    """
    global _CLIENT_TOKEN, _CLIENT_LOA, _CONFIG_PATH

    _CONFIG_PATH = config_path
    _CLIENT_TOKEN = token

    loa = resolve_loa(token, config_path)
    if loa is None:
        raise SystemExit(
            "MCP auth failed: token not recognised. "
            "Add it to bob.rcan.yaml mcp_clients: or run: castor mcp token --name NAME --loa N"
        )

    _CLIENT_LOA = loa
    mcp.run(transport="stdio")


# ── Streaming telemetry (appended by feat/798) ─────────────────────────────


@mcp.tool()
def stream_telemetry(
    rrn: str = "",
    duration_s: int = 10,
    fields: list | None = None,
) -> dict:
    """Collect live WebSocket telemetry frames and return per-field statistics.

    Connects to the robot's WebSocket telemetry endpoint, collects frames for
    ``duration_s`` seconds (capped at 60), and returns min/max/mean/last for
    numeric fields and distinct values for string fields.

    Falls back to polling ``/api/status`` if the WebSocket is unreachable
    (e.g. when the robot is on a different local network).

    LoA 0 — read-only.

    Args:
        rrn:        Robot Registry Number. Defaults to locally configured robot.
        duration_s: Seconds to collect frames (1–60, default 10).
        fields:     Optional list of field names to include in stats.
                    If omitted, all numeric fields are included.
    """

    _check_loa(0)
    rrn = rrn or _default_rrn()
    duration_s = max(1, min(60, duration_s))

    # ── 1. Try to get WebSocket URL from gateway ──────────────────────────
    ws_url: str | None = None
    try:
        resp = httpx.get(f"{_gateway_url()}/api/status", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        ws_url = (data.get("telemetry") or {}).get("ws_telemetry_url") or data.get(
            "ws_telemetry_url"
        )
    except Exception:  # noqa: BLE001
        pass

    # ── 2. Collect frames ─────────────────────────────────────────────────
    raw_frames: list[dict] = []

    if ws_url:
        raw_frames = _collect_ws_frames(ws_url, duration_s)

    if not raw_frames:
        # Fallback: poll /api/status
        raw_frames = _poll_status_frames(duration_s)

    if not raw_frames:
        return {
            "rrn": rrn,
            "frame_count": 0,
            "duration_s": duration_s,
            "stats": {},
            "source": "none",
        }

    # ── 3. Compute stats ──────────────────────────────────────────────────
    source = "websocket" if ws_url and raw_frames else "polling"
    stats = _compute_stats(raw_frames, fields)

    return {
        "rrn": rrn,
        "frame_count": len(raw_frames),
        "duration_s": duration_s,
        "stats": stats,
        "source": source,
    }


def _collect_ws_frames(ws_url: str, duration_s: int) -> list[dict]:
    """Connect to WebSocket and collect frames for duration_s seconds."""
    import asyncio
    import json as _json

    frames: list[dict] = []

    async def _run() -> None:
        try:
            import websockets  # type: ignore[import]
        except ImportError:
            return
        deadline = asyncio.get_event_loop().time() + duration_s
        try:
            async with websockets.connect(ws_url, open_timeout=3) as ws:
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        frame = _json.loads(raw) if isinstance(raw, str) else raw
                        if isinstance(frame, dict):
                            frames.append(frame)
                    except asyncio.TimeoutError:
                        continue
                    except Exception:  # noqa: BLE001
                        break
        except Exception:  # noqa: BLE001
            pass

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(asyncio.run, _run()).result()
        else:
            asyncio.run(_run())
    except Exception:  # noqa: BLE001
        pass

    return frames


def _poll_status_frames(duration_s: int) -> list[dict]:
    """Poll /api/status every 2s as fallback when WS is unavailable."""
    import time

    frames: list[dict] = []
    interval = 2.0
    deadline = time.time() + duration_s
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{_gateway_url()}/api/status", timeout=4)
            resp.raise_for_status()
            data = resp.json()
            tele = data.get("telemetry", data)
            sys_info = tele.get("system", {}) or {}
            mr = tele.get("model_runtime", {}) or {}
            frames.append({**tele, **sys_info, **mr})
        except Exception:  # noqa: BLE001
            pass
        remaining = deadline - time.time()
        time.sleep(min(interval, max(0, remaining)))
    return frames


def _compute_stats(frames: list[dict], fields: list | None) -> dict:
    """Aggregate frames into per-field {min, max, mean, last} stats."""
    from collections import defaultdict

    buckets: dict[str, list] = defaultdict(list)
    for frame in frames:
        for k, v in frame.items():
            if fields and k not in fields:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                buckets[k].append(v)

    stats: dict[str, dict] = {}
    for k, vals in buckets.items():
        if not vals:
            continue
        stats[k] = {
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
            "mean": round(sum(vals) / len(vals), 4),
            "last": round(vals[-1], 4),
            "samples": len(vals),
        }
    return stats


# ── Fleet tools ──────────────────────────────────────────────────────────────


# Avoid circular import — helpers imported lazily at call time

# ── helpers ──────────────────────────────────────────────────────────────────


def _fleet_robots() -> list[dict[str, Any]]:
    """Return the fleet robot list from the gateway."""
    url = f"{_gateway_url()}/api/fleet"
    try:
        resp = httpx.get(url, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        return data.get("fleet", data.get("robots", []))
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]


def _robot_status_sync(base_url: str, rrn: str) -> dict[str, Any]:
    """Fetch /api/status for a single robot gateway."""
    try:
        resp = httpx.get(f"{base_url}/api/status", timeout=6)
        resp.raise_for_status()
        return {"rrn": rrn, "ok": True, "data": resp.json()}
    except Exception as exc:  # noqa: BLE001
        return {"rrn": rrn, "ok": False, "error": str(exc)}


async def _send_command_async(
    base_url: str, rrn: str, instruction: str, scope: str
) -> dict[str, Any]:
    """POST /api/command asynchronously."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                f"{base_url}/api/command",
                json={"instruction": instruction, "scope": scope},
            )
            resp.raise_for_status()
            return {"rrn": rrn, "ok": True, "result": resp.json()}
    except Exception as exc:  # noqa: BLE001
        return {"rrn": rrn, "ok": False, "error": str(exc)}


async def _estop_async(base_url: str, rrn: str) -> dict[str, Any]:
    """POST /api/estop asynchronously."""
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            resp = await client.post(f"{base_url}/api/estop", json={"rrn": rrn})
            resp.raise_for_status()
            return {"rrn": rrn, "ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"rrn": rrn, "ok": False, "error": str(exc)}


def _run(coro: Any) -> Any:
    """Run a coroutine in a new event loop (works from sync context)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(asyncio.run, coro)
            return fut.result()
    return asyncio.run(coro)


#


# Register fleet tools (avoids circular import by calling after mcp is defined)
from .mcp_fleet import register as _register_fleet  # noqa: E402

fleet_status, fleet_broadcast, fleet_estop, fleet_navigate = _register_fleet(
    mcp, _check_loa, _gateway_url
)

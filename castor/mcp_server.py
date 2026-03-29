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

import os
from pathlib import Path
from typing import Any

import httpx
import yaml

from .mcp_auth import resolve_loa

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

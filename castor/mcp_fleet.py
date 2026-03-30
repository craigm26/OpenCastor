"""Fleet-level MCP tools for OpenCastor.

Registered into mcp_server.py.  All tools fan out to the gateway /api/fleet
endpoint and/or per-robot gateway APIs.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .mcp_server import _check_loa, _gateway_url, mcp

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


# ── fleet tools ───────────────────────────────────────────────────────────────


@mcp.tool()
def fleet_status() -> dict[str, Any]:
    """Return live status + telemetry summary for every robot in the fleet.

    LoA 0 — read-only, no authentication required beyond a valid token.

    Returns a dict with:
    - fleet: list of per-robot {rrn, name, online, cpu_temp_c, active_model, ...}
    - summary: {total, online, offline}
    """
    _check_loa(0)
    robots = _fleet_robots()
    if robots and "error" in robots[0]:
        return {"error": robots[0]["error"], "fleet": [], "summary": {}}

    results: list[dict[str, Any]] = []
    for robot in robots:
        rrn = robot.get("rrn", "")
        name = robot.get("name", rrn)
        online = robot.get("online", False)
        tele = robot.get("telemetry", {}) or {}
        sys_info = tele.get("system", {}) or {}
        mr = tele.get("model_runtime", {}) or {}
        results.append(
            {
                "rrn": rrn,
                "name": name,
                "online": online,
                "version": robot.get("version") or tele.get("opencastor_version"),
                "cpu_temp_c": sys_info.get("cpu_temp_c"),
                "ram_used_pct": sys_info.get("ram_used_pct"),
                "active_model": mr.get("active_model") or tele.get("brain_active_model"),
                "provider": mr.get("provider"),
                "loa_enforcement": robot.get("loa_enforcement", False),
            }
        )

    online_count = sum(1 for r in results if r.get("online"))
    return {
        "fleet": results,
        "summary": {
            "total": len(results),
            "online": online_count,
            "offline": len(results) - online_count,
        },
    }


@mcp.tool()
def fleet_broadcast(
    instruction: str,
    scope: str = "chat",
    rrns: list[str] | None = None,
) -> dict[str, Any]:
    """Send the same instruction to multiple (or all) robots simultaneously.

    LoA 1 — requires a token with LoA ≥ 1.

    Args:
        instruction: RCAN instruction string (e.g. 'STATUS', 'navigate forward').
        scope:       RCAN scope string (default 'chat').
        rrns:        List of RRNs to target.  If empty/omitted, targets all
                     online robots in the fleet.

    Returns per-robot {rrn, ok, result/error}.
    """
    _check_loa(1)
    base = _gateway_url()

    # Resolve target robots
    if not rrns:
        robots = _fleet_robots()
        rrns = [r["rrn"] for r in robots if r.get("online") and "rrn" in r]

    if not rrns:
        return {"results": [], "note": "No online robots found"}

    async def _gather() -> list[dict[str, Any]]:
        tasks = [_send_command_async(base, rrn, instruction, scope) for rrn in rrns]
        return list(await asyncio.gather(*tasks))

    results = _run(_gather())
    return {
        "instruction": instruction,
        "scope": scope,
        "results": results,
        "summary": {
            "sent": len(rrns),
            "ok": sum(1 for r in results if r.get("ok")),
            "failed": sum(1 for r in results if not r.get("ok")),
        },
    }


@mcp.tool()
def fleet_estop(rrns: list[str] | None = None) -> dict[str, Any]:
    """Emergency stop all robots (or a specified subset).

    LoA 0 — ESTOP is always available regardless of token LoA level.
    This is synchronous and safety-critical; it does NOT use asyncio.gather.

    Args:
        rrns: Optional list of RRNs to stop.  Defaults to all online robots.

    Returns per-robot {rrn, ok} result.
    """
    # ESTOP is LoA 0 — no _check_loa() gate; always passes
    base = _gateway_url()

    if not rrns:
        robots = _fleet_robots()
        rrns = [r["rrn"] for r in robots if r.get("online") and "rrn" in r]

    if not rrns:
        return {"results": [], "note": "No online robots found"}

    # Synchronous sequential ESTOPs — safety-critical, no async fan-out
    results: list[dict[str, Any]] = []
    for rrn in rrns:
        try:
            resp = httpx.post(
                f"{base}/api/estop",
                json={"rrn": rrn},
                timeout=5,
            )
            resp.raise_for_status()
            results.append({"rrn": rrn, "ok": True})
        except Exception as exc:  # noqa: BLE001
            results.append({"rrn": rrn, "ok": False, "error": str(exc)})

    return {
        "results": results,
        "summary": {
            "stopped": sum(1 for r in results if r.get("ok")),
            "failed": sum(1 for r in results if not r.get("ok")),
        },
    }


@mcp.tool()
def fleet_navigate(waypoints_by_rrn: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Dispatch coordinated navigation commands to multiple robots in parallel.

    LoA 1 — requires a token with LoA ≥ 1.

    Args:
        waypoints_by_rrn: Dict mapping RRN → {x, y, heading} waypoint.
            Example: {"RRN-000000000001": {"x": 1.5, "y": 0.0, "heading": 90.0}}

    Returns per-robot {rrn, ok, result/error}.
    """
    _check_loa(1)
    base = _gateway_url()

    if not waypoints_by_rrn:
        return {"results": [], "note": "No waypoints provided"}

    async def _gather() -> list[dict[str, Any]]:
        tasks = []
        for rrn, wp in waypoints_by_rrn.items():
            x = wp.get("x", 0.0)
            y = wp.get("y", 0.0)
            heading = wp.get("heading", 0.0)
            instruction = f"navigate {x} {y} {heading}"
            tasks.append(_send_command_async(base, rrn, instruction, "control"))
        return list(await asyncio.gather(*tasks))

    results = _run(_gather())
    return {
        "waypoints_by_rrn": waypoints_by_rrn,
        "results": results,
        "summary": {
            "dispatched": len(waypoints_by_rrn),
            "ok": sum(1 for r in results if r.get("ok")),
            "failed": sum(1 for r in results if not r.get("ok")),
        },
    }

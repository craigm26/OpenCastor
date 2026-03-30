from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any

import httpx


def _fleet_robots(gateway_url: str) -> list[dict[str, Any]]:
    """Return the fleet robot list from the gateway."""
    url = f"{gateway_url}/api/fleet"
    try:
        resp = httpx.get(url, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        return data.get("fleet", data.get("robots", []))
    except Exception as exc:
        return [{"error": str(exc)}]


async def _send_command_async(
    base_url: str, rrn: str, instruction: str, scope: str
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=8) as client:
        try:
            resp = await client.post(
                f"{base_url}/api/command",
                json={"instruction": instruction, "scope": scope},
            )
            resp.raise_for_status()
            return {"rrn": rrn, "ok": True, "result": resp.json()}
        except Exception as exc:
            return {"rrn": rrn, "ok": False, "error": str(exc)}


def _run_async(coro: Any) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def register(mcp: Any, check_loa: Any, gateway_url_fn: Any) -> None:
    """Register fleet tools onto an existing FastMCP instance."""

    @mcp.tool()
    def fleet_status() -> dict[str, Any]:
        """Return live status summary for every robot in the fleet. LoA 0."""
        check_loa(0)
        robots = _fleet_robots(gateway_url_fn())
        if robots and "error" in robots[0]:
            return {"error": robots[0]["error"], "fleet": [], "summary": {}}
        results: list[dict[str, Any]] = []
        for robot in robots:
            rrn = robot.get("rrn", "")
            tele = robot.get("telemetry", {}) or {}
            sys_info = tele.get("system", {}) or {}
            mr = tele.get("model_runtime", {}) or {}
            results.append(
                {
                    "rrn": rrn,
                    "name": robot.get("name", rrn),
                    "online": robot.get("online", False),
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
        """Send the same instruction to multiple robots in parallel. LoA 1."""
        check_loa(1)
        base = gateway_url_fn()
        if not rrns:
            robots = _fleet_robots(base)
            rrns = [r["rrn"] for r in robots if r.get("online") and "rrn" in r]
        if not rrns:
            return {"results": [], "note": "No online robots found"}

        async def _gather() -> list[dict[str, Any]]:
            tasks = [_send_command_async(base, rrn, instruction, scope) for rrn in rrns]
            return list(await asyncio.gather(*tasks))

        results = _run_async(_gather())
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
        """Emergency stop all robots. LoA 0 — always available."""
        # No check_loa call — ESTOP is always LoA 0
        base = gateway_url_fn()
        if not rrns:
            robots = _fleet_robots(base)
            rrns = [r["rrn"] for r in robots if r.get("online") and "rrn" in r]
        if not rrns:
            return {"results": [], "note": "No online robots found"}
        results: list[dict[str, Any]] = []
        for rrn in rrns:
            try:
                resp = httpx.post(f"{base}/api/estop", json={"rrn": rrn}, timeout=5)
                resp.raise_for_status()
                results.append({"rrn": rrn, "ok": True})
            except Exception as exc:
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
        """Dispatch coordinated navigation to multiple robots in parallel. LoA 1."""
        check_loa(1)
        base = gateway_url_fn()
        if not waypoints_by_rrn:
            return {"results": [], "note": "No waypoints provided"}

        async def _gather() -> list[dict[str, Any]]:
            tasks = [
                _send_command_async(
                    base,
                    rrn,
                    f"navigate {wp.get('x', 0)} {wp.get('y', 0)} {wp.get('heading', 0)}",
                    "control",
                )
                for rrn, wp in waypoints_by_rrn.items()
            ]
            return list(await asyncio.gather(*tasks))

        results = _run_async(_gather())
        return {
            "waypoints_by_rrn": waypoints_by_rrn,
            "results": results,
            "summary": {
                "dispatched": len(waypoints_by_rrn),
                "ok": sum(1 for r in results if r.get("ok")),
                "failed": sum(1 for r in results if not r.get("ok")),
            },
        }

    return fleet_status, fleet_broadcast, fleet_estop, fleet_navigate

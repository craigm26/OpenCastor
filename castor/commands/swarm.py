"""
castor/commands/swarm.py — Multi-robot swarm CLI commands.

Reads ``config/swarm.yaml`` and talks to each node's OpenCastor gateway.
Queries are performed concurrently with a thread pool.

Usage (via CLI)::
    castor swarm status
    castor swarm status --json
    castor swarm command "move forward"
    castor swarm command "turn left" --node alex
    castor swarm stop
    castor swarm sync config/robot.rcan.yaml

Usage (programmatic)::
    from castor.commands.swarm import cmd_swarm_status, load_swarm_config
    nodes = load_swarm_config()
    cmd_swarm_status(output_json=False)
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# httpx is a core dependency (requirements.txt: httpx>=0.26.0).
# Importing at module level makes it patchable in unit tests.
import httpx  # noqa: E402

logger = logging.getLogger("OpenCastor.Swarm")

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_DEFAULT_SWARM_PATH = Path(__file__).parent.parent.parent / "config" / "swarm.yaml"


def _find_swarm_config(config_path: Optional[str] = None) -> Path:
    """Return the path to swarm.yaml, checking several candidate locations."""
    if config_path:
        return Path(config_path)
    env_cfg = os.getenv("OPENCASTOR_CONFIG")
    if env_cfg:
        candidate = Path(env_cfg).parent / "swarm.yaml"
        if candidate.exists():
            return candidate
    return _DEFAULT_SWARM_PATH


def load_swarm_config(config_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load swarm.yaml and return the list of node dicts.

    Returns an empty list (and logs a warning) if the file cannot be found or
    parsed, so callers never crash on missing config.
    """
    try:
        import yaml
    except ImportError:
        logger.error("pyyaml not installed — cannot load swarm config")
        return []

    path = _find_swarm_config(config_path)
    if not path.exists():
        logger.warning("swarm.yaml not found at %s", path)
        return []

    try:
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
        nodes = data.get("nodes", [])
        logger.debug("Loaded %d node(s) from %s", len(nodes), path)
        return nodes
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to parse swarm config: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Node URL / header helpers
# ---------------------------------------------------------------------------


def _node_base_url(node: Dict[str, Any]) -> str:
    """Build the base URL for a node, preferring ``ip`` over ``host``."""
    host = node.get("ip") or node.get("host", "localhost")
    port = node.get("port", 8000)
    return f"http://{host}:{port}"


def _node_headers(node: Dict[str, Any]) -> Dict[str, str]:
    token = node.get("token", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


# ---------------------------------------------------------------------------
# Node health query
# ---------------------------------------------------------------------------


def _query_node_health(node: Dict[str, Any], timeout: float = 3.0) -> Dict[str, Any]:
    """GET /health for a single node.  Returns a result dict (never raises)."""
    name = node.get("name", "?")
    base = _node_base_url(node)
    start = time.monotonic()
    result: Dict[str, Any] = {
        "name": name,
        "ip": node.get("ip") or node.get("host", "?"),
        "port": node.get("port", 8000),
        "brain": False,
        "driver": False,
        "uptime": "—",
        "latency_ms": None,
        "online": False,
        "raw": {},
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{base}/health", headers=_node_headers(node))
        elapsed = (time.monotonic() - start) * 1000.0
        result["latency_ms"] = round(elapsed, 1)

        if resp.status_code == 200:
            data = resp.json()
            result["online"] = True
            result["brain"] = bool(data.get("brain"))
            result["driver"] = bool(data.get("driver"))
            uptime_s = data.get("uptime_s", 0)
            try:
                s = int(float(uptime_s))
                h, rem = divmod(s, 3600)
                m, sc = divmod(rem, 60)
                result["uptime"] = f"{h:02d}:{m:02d}:{sc:02d}" if h else f"{m:02d}:{sc:02d}"
            except Exception:
                result["uptime"] = str(uptime_s)
            result["raw"] = data
        else:
            logger.debug("Node %s returned HTTP %s", name, resp.status_code)
    except Exception as exc:  # noqa: BLE001
        elapsed = (time.monotonic() - start) * 1000.0
        result["latency_ms"] = round(elapsed, 1)
        logger.debug("Node %s unreachable: %s", name, exc)

    return result


def _query_all_nodes_concurrent(
    nodes: List[Dict[str, Any]], timeout: float = 3.0
) -> List[Dict[str, Any]]:
    """Query all nodes concurrently using a thread pool."""
    if not nodes:
        return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes)) as executor:
        futures = {executor.submit(_query_node_health, n, timeout): n for n in nodes}
        results = []
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                node = futures[fut]
                logger.warning("Unexpected error querying %s: %s", node.get("name"), exc)

    # Restore original node order
    name_order = {n.get("name"): i for i, n in enumerate(nodes)}
    results.sort(key=lambda r: name_order.get(r["name"], 999))
    return results


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------


def cmd_swarm_status(
    config_path: Optional[str] = None,
    output_json: bool = False,
    timeout: float = 3.0,
) -> List[Dict[str, Any]]:
    """Query every node in swarm.yaml and display a Rich status table.

    Parameters
    ----------
    config_path:
        Override path to swarm.yaml.
    output_json:
        If True, print raw JSON instead of the Rich table.
    timeout:
        Per-node HTTP timeout in seconds.

    Returns
    -------
    List of per-node result dicts (useful for programmatic callers / tests).
    """
    nodes = load_swarm_config(config_path)
    if not nodes:
        if not output_json:
            try:
                from rich.console import Console
                Console().print("[yellow]No nodes found in swarm.yaml[/yellow]")
            except ImportError:
                print("No nodes found in swarm.yaml")
        else:
            print(json.dumps([]))
        return []

    results = _query_all_nodes_concurrent(nodes, timeout=timeout)

    if output_json:
        print(json.dumps(results, indent=2))
        return results

    # Rich table output
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="Swarm Status", show_header=True, header_style="bold cyan")
        table.add_column("Name", style="bold")
        table.add_column("IP")
        table.add_column("Brain")
        table.add_column("Driver")
        table.add_column("Uptime")
        table.add_column("Latency (ms)")
        table.add_column("Status")

        for r in results:
            if not r["online"]:
                status_icon = "[dim]⚫ offline[/dim]"
            elif r["brain"] and r["driver"]:
                status_icon = "[green]🟢 healthy[/green]"
            else:
                status_icon = "[yellow]🟡 degraded[/yellow]"

            brain_icon = "[green]✅[/green]" if r["brain"] else "[red]❌[/red]"
            driver_icon = "[green]✅[/green]" if r["driver"] else "[red]❌[/red]"
            latency_str = f"{r['latency_ms']:.0f}" if r["latency_ms"] is not None else "—"

            table.add_row(
                r["name"],
                str(r["ip"]),
                brain_icon,
                driver_icon,
                r["uptime"],
                latency_str,
                status_icon,
            )

        console.print(table)
    except ImportError:
        header = (
            f"{'Name':<16} {'IP':<20} {'Brain':<6} {'Driver':<7} "
            f"{'Uptime':<10} {'Lat(ms)':<10} Status"
        )
        print(header)
        print("-" * len(header))
        for r in results:
            status_str = (
                "offline" if not r["online"]
                else ("healthy" if r["brain"] and r["driver"] else "degraded")
            )
            lat = f"{r['latency_ms']:.0f}" if r["latency_ms"] is not None else "—"
            print(
                f"{r['name']:<16} {str(r['ip']):<20} {'yes' if r['brain'] else 'no':<6} "
                f"{'yes' if r['driver'] else 'no':<7} {r['uptime']:<10} {lat:<10} {status_str}"
            )

    return results


# ---------------------------------------------------------------------------
# Command broadcast
# ---------------------------------------------------------------------------


def _post_command_to_node(
    node: Dict[str, Any], instruction: str, timeout: float = 10.0
) -> Dict[str, Any]:
    """POST /api/command to a single node. Returns response dict (never raises)."""
    name = node.get("name", "?")
    base = _node_base_url(node)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base}/api/command",
                json={"instruction": instruction},
                headers=_node_headers(node),
            )
        if resp.status_code == 200:
            data = resp.json()
            data["_node"] = name
            data["_status"] = "ok"
            return data
        return {
            "_node": name,
            "_status": "error",
            "code": resp.status_code,
            "body": resp.text[:200],
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("Command to %s failed: %s", name, exc)
        return {"_node": name, "_status": "unreachable", "error": str(exc)}


def cmd_swarm_command(
    instruction: str,
    node: Optional[str] = None,
    config_path: Optional[str] = None,
    output_json: bool = False,
    timeout: float = 10.0,
) -> List[Dict[str, Any]]:
    """POST an instruction to all nodes (or a specific node).

    Parameters
    ----------
    instruction:
        Natural-language command sent to each node's brain.
    node:
        If set, only send to the node with this name.
    config_path:
        Override path to swarm.yaml.
    output_json:
        Print raw JSON instead of formatted output.
    timeout:
        Per-node HTTP timeout in seconds.
    """
    all_nodes = load_swarm_config(config_path)

    if node:
        targets = [n for n in all_nodes if n.get("name") == node]
        if not targets:
            msg = f"Node '{node}' not found in swarm.yaml"
            if output_json:
                print(json.dumps({"error": msg}))
            else:
                print(f"  Error: {msg}")
            return []
    else:
        targets = all_nodes

    results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(targets))) as executor:
        futures = {
            executor.submit(_post_command_to_node, n, instruction, timeout): n
            for n in targets
        }
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                n = futures[fut]
                results.append({
                    "_node": n.get("name"),
                    "_status": "error",
                    "error": str(exc),
                })

    if output_json:
        print(json.dumps(results, indent=2))
        return results

    try:
        from rich.console import Console
        console = Console()
        for r in results:
            status = r.get("_status", "?")
            name_str = r.get("_node", "?")
            if status == "ok":
                reply = r.get("raw_text", str(r))[:80]
                console.print(f"[green]{name_str}[/green]: {reply}")
            else:
                console.print(f"[red]{name_str}[/red]: {status} — {r.get('error', '')}")
    except ImportError:
        for r in results:
            print(f"{r.get('_node', '?')}: {r.get('_status', '?')}")

    return results


# ---------------------------------------------------------------------------
# Stop broadcast
# ---------------------------------------------------------------------------


def _post_stop_to_node(node: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
    """POST /api/stop to a single node. Returns result dict (never raises)."""
    name = node.get("name", "?")
    base = _node_base_url(node)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(f"{base}/api/stop", headers=_node_headers(node))
        return {
            "_node": name,
            "_status": "ok" if resp.status_code == 200 else "error",
            "code": resp.status_code,
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("Stop to %s failed: %s", name, exc)
        return {"_node": name, "_status": "unreachable", "error": str(exc)}


def cmd_swarm_stop(
    config_path: Optional[str] = None,
    output_json: bool = False,
    timeout: float = 5.0,
) -> List[Dict[str, Any]]:
    """POST /api/stop to every node in the swarm (emergency broadcast).

    Parameters
    ----------
    config_path:
        Override path to swarm.yaml.
    output_json:
        Print raw JSON instead of formatted output.
    timeout:
        Per-node HTTP timeout in seconds.
    """
    nodes = load_swarm_config(config_path)
    if not nodes:
        if not output_json:
            print("  No nodes found in swarm.yaml")
        else:
            print(json.dumps([]))
        return []

    results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(nodes))) as executor:
        futures = {executor.submit(_post_stop_to_node, n, timeout): n for n in nodes}
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                n = futures[fut]
                results.append({
                    "_node": n.get("name"),
                    "_status": "error",
                    "error": str(exc),
                })

    if output_json:
        print(json.dumps(results, indent=2))
        return results

    try:
        from rich.console import Console
        console = Console()
        for r in results:
            name_str = r.get("_node", "?")
            status = r.get("_status", "?")
            if status == "ok":
                console.print(f"[green]{name_str}[/green]: stopped ✓")
            else:
                console.print(f"[red]{name_str}[/red]: {status} — {r.get('error', '')}")
    except ImportError:
        for r in results:
            print(f"{r.get('_node', '?')}: {r.get('_status', '?')}")

    return results


# ---------------------------------------------------------------------------
# Config sync
# ---------------------------------------------------------------------------


def _post_reload_to_node(
    node: Dict[str, Any], config_data: Dict[str, Any], timeout: float = 10.0
) -> Dict[str, Any]:
    """POST /api/config/reload to a single node. Returns result dict (never raises)."""
    name = node.get("name", "?")
    base = _node_base_url(node)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base}/api/config/reload",
                json=config_data,
                headers=_node_headers(node),
            )
        return {
            "_node": name,
            "_status": "ok" if resp.status_code == 200 else "error",
            "code": resp.status_code,
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("Config reload to %s failed: %s", name, exc)
        return {"_node": name, "_status": "unreachable", "error": str(exc)}


def cmd_swarm_sync(
    config_path: str,
    swarm_config_path: Optional[str] = None,
    output_json: bool = False,
    timeout: float = 10.0,
) -> List[Dict[str, Any]]:
    """Push an updated RCAN config to each node's /api/config/reload endpoint.

    Parameters
    ----------
    config_path:
        Path to the RCAN config file to push to each node.
    swarm_config_path:
        Override path to swarm.yaml.
    output_json:
        Print raw JSON instead of formatted output.
    timeout:
        Per-node HTTP timeout in seconds.
    """
    try:
        import yaml
    except ImportError:
        msg = "pyyaml not installed — cannot load config for sync"
        logger.error(msg)
        if output_json:
            print(json.dumps({"error": msg}))
        else:
            print(f"  Error: {msg}")
        return []

    cfg_path = Path(config_path)
    if not cfg_path.exists():
        msg = f"Config file not found: {config_path}"
        if output_json:
            print(json.dumps({"error": msg}))
        else:
            print(f"  Error: {msg}")
        return []

    with open(cfg_path) as fh:
        config_data = yaml.safe_load(fh) or {}

    nodes = load_swarm_config(swarm_config_path)
    if not nodes:
        if not output_json:
            print("  No nodes found in swarm.yaml")
        else:
            print(json.dumps([]))
        return []

    results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(nodes))) as executor:
        futures = {
            executor.submit(_post_reload_to_node, n, config_data, timeout): n for n in nodes
        }
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                n = futures[fut]
                results.append({
                    "_node": n.get("name"),
                    "_status": "error",
                    "error": str(exc),
                })

    if output_json:
        print(json.dumps(results, indent=2))
        return results

    try:
        from rich.console import Console
        console = Console()
        for r in results:
            name_str = r.get("_node", "?")
            status = r.get("_status", "?")
            if status == "ok":
                console.print(f"[green]{name_str}[/green]: config reloaded ✓")
            else:
                console.print(
                    f"[red]{name_str}[/red]: {status} — {r.get('error', r.get('code', ''))}"
                )
    except ImportError:
        for r in results:
            print(f"{r.get('_node', '?')}: {r.get('_status', '?')}")

    return results

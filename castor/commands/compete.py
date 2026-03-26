"""
castor/commands/compete.py — Competition entry and management.

Subcommands::
    castor compete list
    castor compete enter COMPETITION_ID
    castor compete status COMPETITION_ID
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger("OpenCastor.Compete")


def _gateway_base() -> str:
    return os.getenv("OPENCASTOR_GATEWAY_URL", "http://127.0.0.1:8001")


def _api_get(path: str, timeout: int = 5) -> Optional[dict]:
    """GET request to the gateway, returns parsed JSON or None on error."""
    import urllib.request

    url = f"{_gateway_base()}{path}"
    token = os.getenv("OPENCASTOR_API_TOKEN", "")
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        logger.debug("GET %s failed: %s", path, exc)
        return None


def _api_post(path: str, data: Optional[dict] = None, timeout: int = 5) -> Optional[dict]:
    """POST request to the gateway, returns parsed JSON or None on error."""
    import urllib.request

    url = f"{_gateway_base()}{path}"
    token = os.getenv("OPENCASTOR_API_TOKEN", "")
    body = json.dumps(data or {}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        logger.debug("POST %s failed: %s", path, exc)
        return None


def _format_countdown(seconds: float) -> str:
    hours = int(seconds // 3600)
    return f"{hours}h"


def _cmd_compete_list() -> None:
    """List active competitions."""
    data = _api_get("/api/competitions")
    if data is None:
        print("\n  Could not reach gateway. Is it running? (castor gateway)\n")
        return

    competitions = data if isinstance(data, list) else data.get("competitions", [])
    if not competitions:
        print("\n  No active competitions.\n")
        return

    print()
    for comp in competitions:
        name = comp.get("name", comp.get("id", "—"))
        hours = _format_countdown(comp.get("seconds_remaining", comp.get("duration_s", 0)))
        pool = comp.get("credit_pool", comp.get("pool", 0))
        n_robots = comp.get("robot_count", comp.get("n_robots", 0))
        kind = comp.get("type", "sprint")
        icon = "🏃" if kind == "sprint" else "🏁"
        print(
            f"  {icon} {kind.capitalize()}: {name}  ⏱ {hours} remaining"
            f"  💰 {pool} credits  {n_robots} robots"
        )
    print()


def _cmd_compete_enter(competition_id: str) -> None:
    """Enter a competition and emit an RCAN COMPETITION_ENTER message."""
    result = _api_post(f"/api/competitions/{competition_id}/enter")
    if result is None:
        print(f"\n  Could not reach gateway. Failed to enter '{competition_id}'.\n")
        return

    if result.get("error"):
        print(f"\n  Error entering competition: {result['error']}\n")
        return

    print(f"\n  Entered competition: {competition_id}")

    # Emit RCAN COMPETITION_ENTER message (type=37) via bridge if running
    try:
        from castor.rcan.message import RCANMessage

        msg = RCANMessage(
            type=37,  # COMPETITION_ENTER (future spec extension)
            source="rcan://localhost/castor-cli",
            target="rcan://localhost/bridge",
            payload={"competition_id": competition_id, "action": "enter"},
        )
        bridge_result = _api_post("/rcan", msg.to_dict())
        if bridge_result and not bridge_result.get("error"):
            print("  RCAN COMPETITION_ENTER sent (msg_type=37)")
        else:
            logger.debug("Bridge not running or rejected RCAN message")
    except Exception as exc:
        logger.debug("RCAN emit failed: %s", exc)

    print()


def _cmd_compete_status(competition_id: str) -> None:
    """Show the robot's rank in a competition."""
    data = _api_get(f"/api/competitions/{competition_id}/leaderboard")
    if data is None:
        print(f"\n  Could not reach gateway for competition '{competition_id}'.\n")
        return

    rows = data if isinstance(data, list) else data.get("leaderboard", [])
    robot_name = os.getenv("OPENCASTOR_ROBOT_NAME", "")

    print(f"\n  Competition: {competition_id}")
    print("  " + "─" * 40)

    your_row = None
    for row in rows:
        if robot_name and row.get("robot_name") == robot_name:
            your_row = row

    if your_row:
        print(f"  Your rank:  #{your_row.get('rank', '?')}  /  {len(rows)} robots")
        print(f"  Score:      {your_row.get('score', '—')}")
    else:
        print(f"  Total robots: {len(rows)}")
        if robot_name:
            print(f"  (Your robot '{robot_name}' not found — have you entered?)")

    print()


def cmd_compete(args) -> None:
    """Manage competition entry and status."""
    action = getattr(args, "compete_action", "list") or "list"
    competition_id = getattr(args, "competition_id", None)

    if action == "list":
        _cmd_compete_list()
    elif action == "enter":
        if not competition_id:
            print("\n  Usage: castor compete enter COMPETITION_ID\n")
            return
        _cmd_compete_enter(competition_id)
    elif action == "status":
        if not competition_id:
            print("\n  Usage: castor compete status COMPETITION_ID\n")
            return
        _cmd_compete_status(competition_id)
    else:
        print(f"\n  Unknown compete action: {action}\n")

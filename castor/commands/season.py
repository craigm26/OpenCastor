"""
castor/commands/season.py — Season overview and class standings.

Usage (via CLI)::
    castor season
    castor season --list
    castor season --class medium
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger("OpenCastor.Season")


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


def cmd_season(args) -> None:
    """Display season overview, list all seasons, or filter by class."""
    list_all = getattr(args, "list_seasons", False)
    class_id = getattr(args, "class_id", None)

    if list_all:
        data = _api_get("/api/seasons")
        if data is None:
            print("\n  Could not reach gateway.\n")
            return

        seasons = data if isinstance(data, list) else data.get("seasons", [])
        if not seasons:
            print("\n  No seasons found.\n")
            return

        print("\n  All Seasons")
        print("  " + "─" * 50)
        for s in seasons:
            sid = s.get("id", "—")
            status = s.get("status", "—")
            start = s.get("start_date", "—")
            end = s.get("end_date", "—")
            print(f"  {sid:<20}  {status:<12}  {start} → {end}")
        print()
        return

    if class_id:
        import urllib.parse

        path = f"/api/seasons/current?{urllib.parse.urlencode({'class': class_id})}"
        data = _api_get(path)
        if data is None:
            print(f"\n  Could not reach gateway for class '{class_id}'.\n")
            return

        print(f"\n  Class: {class_id}")
        print("  " + "─" * 50)
        leaderboard = data.get("leaderboard", [])
        for i, row in enumerate(leaderboard):
            rank = row.get("rank", i + 1)
            robot = row.get("robot_name") or row.get("robot") or "—"
            score = row.get("score", "—")
            print(f"  #{rank:<4}  {robot:<24}  {score}")
        print()
        return

    # Default: current season overview
    data = _api_get("/api/seasons/current")
    if data is None:
        print("\n  Could not reach gateway.\n")
        return

    sid = data.get("id", "—")
    days_remaining = data.get("days_remaining", "—")
    class_name = data.get("class_id", "—")
    rank = data.get("your_rank", data.get("rank", "—"))
    score = data.get("your_score", data.get("score", "—"))

    print(f"\n  Season {sid} · {days_remaining} days remaining")
    print(f"    Class: {class_name}")
    print(f"    Rank: #{rank} · Score: {score}")
    print()

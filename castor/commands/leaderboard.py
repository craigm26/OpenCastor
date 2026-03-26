"""
castor/commands/leaderboard.py — Fleet leaderboard display.

Reads from the gateway API or Firestore harness_leaderboard collection.

Usage (via CLI)::
    castor leaderboard
    castor leaderboard --tier medium --top 20
    castor leaderboard --season 2026-spring --json
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger("OpenCastor.Leaderboard")

_BADGES: dict[int, str] = {1: "🥇", 2: "🥈", 3: "🥉"}


def _gateway_base() -> str:
    """Return gateway base URL from env or default."""
    return os.getenv("OPENCASTOR_GATEWAY_URL", "http://127.0.0.1:8001")


def _detect_tier(config_path: Optional[str] = None) -> str:
    """Auto-detect tier from RCAN config or return community default."""
    try:
        import yaml  # type: ignore[import-untyped]

        path = config_path or "robot.rcan.yaml"
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("compete", {}).get("tier", "community")
    except Exception:
        return "community"


def _fetch_leaderboard_http(
    base: str, tier: str, season: Optional[str], top: int
) -> Optional[list[dict]]:
    """Fetch leaderboard from gateway API."""
    import urllib.parse
    import urllib.request

    params: dict[str, str] = {"tier": tier, "top": str(top)}
    if season:
        params["season"] = season
    url = f"{base}/api/compete/leaderboard?{urllib.parse.urlencode(params)}"
    token = os.getenv("OPENCASTOR_API_TOKEN", "")
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return data if isinstance(data, list) else data.get("leaderboard", [])
    except Exception as exc:
        logger.debug("Gateway leaderboard fetch failed: %s", exc)
        return None


def _fetch_leaderboard_firestore(
    tier: str, season: Optional[str], top: int
) -> Optional[list[dict]]:
    """Fallback: read from Firestore harness_leaderboard collection."""
    try:
        from google.cloud import firestore  # type: ignore[import-not-found]

        db = firestore.Client()
        col = db.collection("harness_leaderboard")
        query = (
            col.where("tier", "==", tier)
            .order_by("score", direction=firestore.Query.DESCENDING)
            .limit(top)
        )
        if season:
            query = query.where("season", "==", season)
        return [doc.to_dict() for doc in query.stream()]
    except Exception as exc:
        logger.debug("Firestore leaderboard fetch failed: %s", exc)
        return None


def _print_leaderboard_table(rows: list[dict]) -> None:
    """Print a rich table of leaderboard entries."""
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="Fleet Leaderboard", header_style="bold cyan")
        table.add_column("Rank", justify="right", min_width=4)
        table.add_column("Badge", min_width=4)
        table.add_column("Robot", min_width=20)
        table.add_column("Score", justify="right", min_width=8)
        table.add_column("Evals", justify="right", min_width=6)
        table.add_column("Last Eval", min_width=16)

        for i, row in enumerate(rows):
            rank = row.get("rank", i + 1)
            badge = _BADGES.get(rank, "·")
            robot = row.get("robot_name") or row.get("robot") or "—"
            score = str(row.get("score", "—"))
            evals = str(row.get("evals", row.get("eval_count", "—")))
            last_eval = row.get("last_eval") or row.get("last_eval_at") or "—"
            table.add_row(str(rank), badge, robot, score, evals, last_eval)

        console.print()
        console.print(table)
        console.print()
    except ImportError:
        _print_plain_leaderboard(rows)


def _print_plain_leaderboard(rows: list[dict]) -> None:
    """Fallback plain-text leaderboard when Rich is unavailable."""
    header = f"  {'Rank':>4}  {'':4}  {'Robot':<24}  {'Score':>8}  {'Evals':>6}  {'Last Eval':<16}"
    sep = "  " + "-" * (len(header) - 2)

    print("\n  Fleet Leaderboard")
    print(sep)
    print(header)
    print(sep)

    for i, row in enumerate(rows):
        rank = row.get("rank", i + 1)
        badge = _BADGES.get(rank, "·")
        robot = (row.get("robot_name") or row.get("robot") or "—")[:24]
        score = str(row.get("score", "—"))
        evals = str(row.get("evals", row.get("eval_count", "—")))
        last_eval = (row.get("last_eval") or row.get("last_eval_at") or "—")[:16]
        print(f"  {rank:>4}  {badge:<4}  {robot:<24}  {score:>8}  {evals:>6}  {last_eval:<16}")

    print(sep)
    print()


def cmd_leaderboard(args) -> None:
    """Print fleet leaderboard table."""
    tier = getattr(args, "tier", None) or _detect_tier(getattr(args, "config", None))
    season = getattr(args, "season", None)
    top = getattr(args, "top", 10)
    output_json = getattr(args, "output_json", False)

    base = _gateway_base()
    rows = _fetch_leaderboard_http(base, tier, season, top)

    if rows is None and os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        rows = _fetch_leaderboard_firestore(tier, season, top)

    if rows is None:
        print("\n  Could not reach gateway — showing cached data")
        rows = []

    if not rows:
        print("\n  No leaderboard data available.\n")
        return

    if output_json:
        print(json.dumps(rows, indent=2))
        return

    _print_leaderboard_table(rows)

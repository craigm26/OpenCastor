"""
castor/commands/research.py — Harness research pipeline management.

Subcommands::
    castor research [status]
    castor research history
    castor research champion
    castor research queue
    castor research dashboard
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from typing import Optional

logger = logging.getLogger("OpenCastor.Research")

_DEFAULT_OPS_DIR = pathlib.Path.home() / "opencastor-ops"
_HARNESS_DIR_NAME = "harness-research"
_CHAMPION_FILE = "champion.yaml"
_CANDIDATES_DIR = "candidates"


def _ops_dir() -> pathlib.Path:
    env = os.getenv("OPENCASTOR_OPS_DIR")
    return pathlib.Path(env) if env else _DEFAULT_OPS_DIR


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


def _load_champion_yaml() -> Optional[dict]:
    """Load champion.yaml from ops dir."""
    champion_path = _ops_dir() / _HARNESS_DIR_NAME / _CHAMPION_FILE
    try:
        import yaml  # type: ignore[import-untyped]

        with open(champion_path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.debug("champion.yaml not found at %s", champion_path)
        return None
    except Exception as exc:
        logger.debug("Could not load champion.yaml: %s", exc)
        return None


def _list_winner_files() -> list[pathlib.Path]:
    """Return sorted list of *-winner.yaml files in candidates/."""
    candidates = _ops_dir() / _HARNESS_DIR_NAME / _CANDIDATES_DIR
    try:
        return sorted(candidates.glob("*-winner.yaml"))
    except Exception:
        return []


def _queue_depth() -> int:
    """Count pending candidate YAML files (non-winner) as queue depth."""
    candidates = _ops_dir() / _HARNESS_DIR_NAME / _CANDIDATES_DIR
    try:
        all_files = list(candidates.glob("*.yaml"))
        winner_files = list(candidates.glob("*-winner.yaml"))
        return max(0, len(all_files) - len(winner_files))
    except Exception:
        return 0


def _cmd_research_status() -> None:
    champion = _load_champion_yaml()
    winner_files = _list_winner_files()
    queue = _queue_depth()

    print("\n  Harness Research Pipeline")
    print("  " + "─" * 40)

    if champion:
        score = champion.get("score", champion.get("eval_score", "—"))
        last_run = champion.get("evaluated_at", champion.get("date", "—"))
        print(f"  Champion score:   {score}")
        print(f"  Last run:         {last_run}")
    else:
        print("  Champion score:   (not available)")
        print("  Last run:         (not available)")

    next_run = "—"
    if winner_files:
        try:
            import yaml  # type: ignore[import-untyped]

            with open(winner_files[-1]) as f:
                last_winner = yaml.safe_load(f) or {}
            next_run = last_winner.get("next_run_at", last_winner.get("next_eval", "—"))
        except Exception:
            pass

    print(f"  Next run est.:    {next_run}")
    print(f"  Queue depth:      {queue}")
    print()


def _cmd_research_history() -> None:
    winner_files = _list_winner_files()
    if not winner_files:
        print(
            "\n  No history found in",
            _ops_dir() / _HARNESS_DIR_NAME / _CANDIDATES_DIR,
        )
        print("  Run some evals first: castor eval --all\n")
        return

    recent = winner_files[-5:]
    print("\n  Recent Harness Winners (last 5)")
    print("  " + "─" * 50)

    try:
        import yaml  # type: ignore[import-untyped]

        for path in recent:
            try:
                with open(path) as f:
                    data = yaml.safe_load(f) or {}
                score = data.get("score", data.get("eval_score", "—"))
                date = data.get("evaluated_at", data.get("date", path.stem))
                harness = data.get("harness", path.stem.replace("-winner", ""))
                print(f"  {date:<20}  {harness:<30}  score: {score}")
            except Exception:
                print(f"  {path.name}")
    except ImportError:
        for path in recent:
            print(f"  {path.name}")

    print()


def _cmd_research_champion() -> None:
    champion = _load_champion_yaml()
    if champion is None:
        print("\n  No champion.yaml found.\n")
        print(f"  Expected: {_ops_dir() / _HARNESS_DIR_NAME / _CHAMPION_FILE}\n")
        return

    try:
        import yaml  # type: ignore[import-untyped]

        print(yaml.dump(champion, default_flow_style=False))
    except ImportError:
        print(json.dumps(champion, indent=2))


def _cmd_research_queue() -> None:
    data = _api_get("/api/research/status")
    if data is None:
        depth = _queue_depth()
        print(f"\n  Gateway not reachable. Local queue depth: {depth}\n")
        return

    queue = data.get("queue_depth", data.get("pending", "—"))
    running = data.get("running", "—")
    last_eval = data.get("last_eval", "—")

    print("\n  Research Queue")
    print("  " + "─" * 30)
    print(f"  Queue depth:  {queue}")
    print(f"  Running:      {running}")
    print(f"  Last eval:    {last_eval}")
    print()


def _cmd_research_dashboard() -> None:
    ops = _ops_dir()
    if not ops.exists():
        print(f"\n  OPENCASTOR_OPS_DIR not set or {ops} does not exist.\n")
        return

    try:
        from harness_research.dashboard import main as dash_main  # type: ignore[import-not-found]

        dash_main()
    except ImportError:
        print("\n  harness_research.dashboard not available.")
        print(f"  Ops dir: {ops}\n")


def cmd_research(args) -> None:
    """Manage the harness research pipeline."""
    action = getattr(args, "research_action", "status") or "status"

    if action == "status":
        _cmd_research_status()
    elif action == "history":
        _cmd_research_history()
    elif action == "champion":
        _cmd_research_champion()
    elif action == "queue":
        _cmd_research_queue()
    elif action == "dashboard":
        _cmd_research_dashboard()
    elif action == "recommend":
        from castor.commands.recommend import cmd_recommend  # noqa: PLC0415

        cmd_recommend(args)
    else:
        print(f"\n  Unknown research action: {action}\n")

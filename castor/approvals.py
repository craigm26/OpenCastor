"""
OpenCastor Approvals -- safety gate for dangerous hardware commands.

Queues risky motor commands (high speed, limit overrides, untested actions)
for human review before execution. Integrates with the perception-action
loop and the API gateway.

Usage:
    castor approvals                      # List pending approvals
    castor approvals --approve ID         # Approve a pending action
    castor approvals --deny ID            # Deny a pending action
    castor approvals --clear              # Clear all pending
"""

import json
import logging
import os
import threading
import time
from datetime import datetime

logger = logging.getLogger("OpenCastor.Approvals")

_APPROVALS_FILE = ".opencastor-approvals.json"


class ApprovalGate:
    """Gate that holds dangerous commands for human review.

    Actions exceeding configured thresholds are queued instead of executed.
    A human must approve or deny them via the CLI or API.
    """

    def __init__(self, config: dict):
        physics = config.get("physics", {})
        agent = config.get("agent", {})

        # Thresholds from config (or sensible defaults)
        self.max_safe_linear = physics.get("max_speed_ms", 0.5)
        self.max_safe_angular = physics.get("max_angular_speed", 1.5)
        self.require_approval = agent.get("require_approval", False)

        self._queue = []
        self._lock = threading.Lock()
        self._next_id = 1

        # Load any previously saved pending approvals
        self._load()

    def check(self, action: dict) -> dict:
        """Check if an action needs approval.

        Returns the action unchanged if safe, or a ``{"status": "pending",
        "approval_id": N}`` dict if the action was queued.
        """
        if not self.require_approval:
            return action

        reasons = self._evaluate(action)
        if not reasons:
            return action

        return self._enqueue(action, reasons)

    def _evaluate(self, action: dict) -> list:
        """Evaluate whether an action exceeds safety thresholds."""
        reasons = []
        if not action or action.get("type") != "move":
            return reasons

        linear = abs(action.get("linear", 0))
        angular = abs(action.get("angular", 0))

        if linear > self.max_safe_linear:
            reasons.append(
                f"Linear speed {linear:.2f} exceeds safe limit {self.max_safe_linear}"
            )
        if angular > self.max_safe_angular:
            reasons.append(
                f"Angular speed {angular:.2f} exceeds safe limit {self.max_safe_angular}"
            )

        return reasons

    def _enqueue(self, action: dict, reasons: list) -> dict:
        """Queue an action for human review."""
        with self._lock:
            approval_id = self._next_id
            self._next_id += 1

            entry = {
                "id": approval_id,
                "action": action,
                "reasons": reasons,
                "status": "pending",
                "timestamp": datetime.now().isoformat(),
            }
            self._queue.append(entry)
            self._save()

            logger.warning(
                f"Action queued for approval (ID={approval_id}): "
                + "; ".join(reasons)
            )

            return {"status": "pending", "approval_id": approval_id, "reasons": reasons}

    def list_pending(self) -> list:
        """Return all pending approvals."""
        with self._lock:
            return [e for e in self._queue if e["status"] == "pending"]

    def approve(self, approval_id: int) -> dict:
        """Approve a pending action and return it for execution."""
        with self._lock:
            for entry in self._queue:
                if entry["id"] == approval_id and entry["status"] == "pending":
                    entry["status"] = "approved"
                    entry["resolved_at"] = datetime.now().isoformat()
                    self._save()
                    logger.info(f"Approval {approval_id} approved")
                    return entry["action"]
            return None

    def deny(self, approval_id: int) -> bool:
        """Deny a pending action."""
        with self._lock:
            for entry in self._queue:
                if entry["id"] == approval_id and entry["status"] == "pending":
                    entry["status"] = "denied"
                    entry["resolved_at"] = datetime.now().isoformat()
                    self._save()
                    logger.info(f"Approval {approval_id} denied")
                    return True
            return False

    def clear(self):
        """Clear all resolved approvals (keep pending ones)."""
        with self._lock:
            self._queue = [e for e in self._queue if e["status"] == "pending"]
            self._save()

    def _save(self):
        """Persist approvals to disk."""
        try:
            with open(_APPROVALS_FILE, "w") as f:
                json.dump(self._queue, f, indent=2, default=str)
        except Exception as exc:
            logger.debug(f"Could not save approvals: {exc}")

    def _load(self):
        """Load persisted approvals from disk."""
        if not os.path.exists(_APPROVALS_FILE):
            return
        try:
            with open(_APPROVALS_FILE) as f:
                data = json.load(f)
            self._queue = data
            if data:
                self._next_id = max(e.get("id", 0) for e in data) + 1
        except Exception:
            pass


def print_approvals(pending: list):
    """Print pending approvals to the terminal."""
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False
        console = None

    if not pending:
        msg = "  No pending approvals."
        if has_rich:
            console.print(f"\n[dim]{msg}[/]\n")
        else:
            print(f"\n{msg}\n")
        return

    if has_rich:
        table = Table(title=f"Pending Approvals ({len(pending)})", show_header=True)
        table.add_column("ID", justify="right", style="bold")
        table.add_column("Action")
        table.add_column("Reasons", style="yellow")
        table.add_column("Time", style="dim")

        for entry in pending:
            action = entry.get("action", {})
            action_str = (
                f"{action.get('type', '?')} "
                f"L={action.get('linear', 0):.2f} "
                f"A={action.get('angular', 0):.2f}"
            )
            reasons_str = "; ".join(entry.get("reasons", []))
            table.add_row(
                str(entry["id"]),
                action_str,
                reasons_str,
                entry.get("timestamp", "?")[:19],
            )

        console.print()
        console.print(table)
        console.print(
            "\n  Approve: [cyan]castor approvals --approve ID[/]"
            "\n  Deny:    [cyan]castor approvals --deny ID[/]\n"
        )
    else:
        print(f"\n  Pending Approvals ({len(pending)}):\n")
        for entry in pending:
            action = entry.get("action", {})
            print(f"  ID {entry['id']}:")
            print(f"    Action:  {action.get('type', '?')} "
                  f"L={action.get('linear', 0):.2f} "
                  f"A={action.get('angular', 0):.2f}")
            print(f"    Reasons: {'; '.join(entry.get('reasons', []))}")
            print(f"    Time:    {entry.get('timestamp', '?')[:19]}")
            print()
        print("  Approve: castor approvals --approve ID")
        print("  Deny:    castor approvals --deny ID\n")

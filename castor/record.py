"""
OpenCastor Record & Replay -- session recording for debugging.

Records each perception-action step to a ``.jsonl`` file.
Replay mode reads the file and re-executes actions without API calls.

Usage:
    castor record --config robot.rcan.yaml --output session.jsonl
    castor replay session.jsonl
"""

import json
import logging
import os
import time
from datetime import datetime

logger = logging.getLogger("OpenCastor.Record")


class SessionRecorder:
    """Records perception-action steps to a JSONL file."""

    def __init__(self, output_path: str):
        self.output_path = output_path
        self._file = open(output_path, "w")
        self._step = 0
        self._start = time.time()

        # Write header
        self._write({
            "type": "header",
            "timestamp": datetime.now().isoformat(),
            "version": "1.0",
        })

    def record_step(
        self,
        frame_size: int,
        instruction: str,
        thought_text: str,
        action: dict,
        latency_ms: float,
    ):
        """Record a single perception-action step."""
        self._step += 1
        self._write({
            "type": "step",
            "step": self._step,
            "elapsed_s": round(time.time() - self._start, 3),
            "frame_size": frame_size,
            "instruction": instruction[:500],
            "thought": thought_text[:1000],
            "action": action,
            "latency_ms": round(latency_ms, 1),
        })

    def close(self):
        """Close the recording file."""
        self._write({
            "type": "footer",
            "total_steps": self._step,
            "total_time_s": round(time.time() - self._start, 2),
        })
        self._file.close()
        logger.info(f"Session recorded: {self._step} steps to {self.output_path}")

    def _write(self, data: dict):
        self._file.write(json.dumps(data) + "\n")
        self._file.flush()


def replay_session(recording_path: str, execute: bool = False, config_path: str = None):
    """Replay a recorded session.

    Args:
        recording_path: Path to the ``.jsonl`` recording file.
        execute: If True, re-execute actions on hardware (requires config).
        config_path: RCAN config for hardware execution.
    """
    if not os.path.exists(recording_path):
        print(f"  Recording not found: {recording_path}")
        return

    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False

    # Load driver if executing
    driver = None
    if execute and config_path:
        import yaml

        from castor.main import get_driver
        with open(config_path) as f:
            config = yaml.safe_load(f)
        driver = get_driver(config)

    steps = []
    header = {}
    footer = {}

    with open(recording_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry["type"] == "header":
                header = entry
            elif entry["type"] == "footer":
                footer = entry
            elif entry["type"] == "step":
                steps.append(entry)

    if has_rich:
        console.print(f"\n[bold cyan]  Replaying: {recording_path}[/]")
        console.print(f"  Recorded: {header.get('timestamp', '?')}")
        console.print(f"  Steps: {len(steps)}\n")
    else:
        print(f"\n  Replaying: {recording_path}")
        print(f"  Recorded: {header.get('timestamp', '?')}")
        print(f"  Steps: {len(steps)}\n")

    for step in steps:
        action = step.get("action", {})
        action_str = action.get("type", "none")
        if action.get("linear") is not None:
            action_str += f" L={action['linear']:.1f}"
        if action.get("angular") is not None:
            action_str += f" A={action['angular']:.1f}"

        if has_rich:
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_column(style="bold", width=10)
            table.add_column()
            table.add_row("Thought", step.get("thought", "")[:80])
            table.add_row("Action", action_str)
            table.add_row("Latency", f"{step.get('latency_ms', 0):.0f}ms")
            console.print(Panel(table, title=f"Step {step['step']}", border_style="dim"))
        else:
            print(f"  Step {step['step']}:")
            print(f"    Thought: {step.get('thought', '')[:80]}")
            print(f"    Action:  {action_str}")
            print(f"    Latency: {step.get('latency_ms', 0):.0f}ms")
            print()

        # Execute action on hardware if requested
        if execute and driver and action:
            try:
                if action.get("type") == "move":
                    driver.move(action.get("linear", 0), action.get("angular", 0))
                    time.sleep(0.5)
                    driver.stop()
                elif action.get("type") == "stop":
                    driver.stop()
            except Exception as exc:
                print(f"    Execution error: {exc}")

        time.sleep(0.3)  # Brief pause between steps

    if driver:
        driver.stop()
        driver.close()

    total_time = footer.get("total_time_s", 0)
    if has_rich:
        console.print(f"\n  [green]Replay complete.[/] {len(steps)} steps, {total_time:.1f}s original runtime.\n")
    else:
        print(f"\n  Replay complete. {len(steps)} steps, {total_time:.1f}s original runtime.\n")

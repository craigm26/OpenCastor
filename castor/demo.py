"""
OpenCastor Demo -- simulated perception-action loop.

Runs a demo without any hardware or API keys.  Generates synthetic
"camera frames" and mock AI responses so new users can see the
system working in seconds.

Usage:
    castor demo
    castor demo --steps 5
"""

import logging
import random
import time

logger = logging.getLogger("OpenCastor.Demo")

# Simulated AI responses (no API key needed)
_MOCK_THOUGHTS = [
    {"raw_text": "I see a clear path ahead. Moving forward slowly.", "action": {"type": "move", "linear": 0.3, "angular": 0.0}},
    {"raw_text": "Obstacle detected on the right. Turning left.", "action": {"type": "move", "linear": 0.2, "angular": -0.5}},
    {"raw_text": "Open space ahead. Increasing speed.", "action": {"type": "move", "linear": 0.6, "angular": 0.0}},
    {"raw_text": "Wall approaching. Stopping to reassess.", "action": {"type": "stop"}},
    {"raw_text": "Narrow corridor. Proceeding with caution.", "action": {"type": "move", "linear": 0.15, "angular": 0.1}},
    {"raw_text": "Person detected ahead. Stopping safely.", "action": {"type": "stop"}},
    {"raw_text": "Turning around to explore the other direction.", "action": {"type": "move", "linear": 0.1, "angular": 0.8}},
    {"raw_text": "Found an interesting object. Moving closer.", "action": {"type": "move", "linear": 0.4, "angular": -0.1}},
]


def _generate_mock_frame(step: int) -> dict:
    """Generate synthetic frame metadata."""
    return {
        "step": step,
        "size_bytes": random.randint(20000, 80000),
        "resolution": "640x480",
        "timestamp": time.time(),
    }


def run_demo(steps: int = 10, delay: float = 1.5):
    """Run a simulated perception-action loop.

    Args:
        steps: Number of loop iterations.
        delay: Seconds between each step (simulates latency budget).
    """
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False

    if has_rich:
        console.print()
        console.print(
            Panel.fit(
                "[bold cyan]OpenCastor Demo Mode[/]\n"
                "Simulated perception-action loop -- no hardware or API keys required.\n"
                f"Running [bold]{steps}[/] steps with [bold]{delay}s[/] delay.",
                border_style="cyan",
            )
        )
        console.print()
    else:
        print("\n  OpenCastor Demo Mode")
        print("  Simulated perception-action loop -- no hardware or API keys required.")
        print(f"  Running {steps} steps with {delay}s delay.\n")

    for i in range(1, steps + 1):
        loop_start = time.time()

        # OBSERVE
        frame = _generate_mock_frame(i)

        # ORIENT & DECIDE
        thought = random.choice(_MOCK_THOUGHTS)

        # TELEMETRY
        latency_ms = random.uniform(80, 250)

        if has_rich:
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_column(style="bold")
            table.add_column()

            action = thought["action"]
            action_str = action.get("type", "?")
            if action.get("linear") is not None:
                action_str += f"  linear={action['linear']:.1f}"
            if action.get("angular") is not None:
                action_str += f"  angular={action['angular']:.1f}"

            table.add_row("Frame", f"{frame['size_bytes']:,} bytes ({frame['resolution']})")
            table.add_row("Thought", thought["raw_text"])
            table.add_row("Action", action_str)
            table.add_row("Latency", f"{latency_ms:.0f} ms")

            color = "green" if latency_ms < 200 else "yellow"
            console.print(
                Panel(table, title=f"[bold]Step {i}/{steps}[/]", border_style=color)
            )
        else:
            print(f"  --- Step {i}/{steps} ---")
            print(f"  Frame:   {frame['size_bytes']:,} bytes ({frame['resolution']})")
            print(f"  Thought: {thought['raw_text']}")
            print(f"  Action:  {thought['action']}")
            print(f"  Latency: {latency_ms:.0f} ms")
            print()

        # Simulate loop timing
        elapsed = time.time() - loop_start
        remaining = delay - elapsed
        if remaining > 0:
            time.sleep(remaining)

    if has_rich:
        console.print(f"\n[bold green]Demo complete.[/] {steps} steps executed.\n")
        console.print("  Next steps:")
        console.print("    1. Run [cyan]castor wizard[/] to configure real hardware")
        console.print("    2. Run [cyan]castor doctor[/] to check your environment")
        console.print()
    else:
        print(f"\n  Demo complete. {steps} steps executed.\n")
        print("  Next steps:")
        print("    1. Run `castor wizard` to configure real hardware")
        print("    2. Run `castor doctor` to check your environment")
        print()

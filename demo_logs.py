"""
OpenCastor Demo Logs - "Hollywood OS" terminal visualization.
Uses the `rich` library for cinematic terminal output.
Run: python demo_logs.py
"""

import json
import random
import time

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax
from rich.tree import Tree

console = Console()


def sleep_random(min_t=0.1, max_t=0.3):
    time.sleep(random.uniform(min_t, max_t))


def boot_sequence():
    console.clear()
    console.print(
        Panel.fit(
            "[bold blue]OpenCastor OS[/bold blue] v2026.2.17.3\n"
            "[dim]The Body for the Gemini Brain[/dim]",
            border_style="blue",
        )
    )
    time.sleep(1)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        transient=True,
    ) as progress:
        task1 = progress.add_task("[green]Initializing Kernel...", total=100)
        while not progress.finished:
            progress.update(task1, advance=random.uniform(2, 5))
            time.sleep(0.05)

    console.log("[green]Kernel Loaded[/green]")
    sleep_random()
    console.log("[green]RCAN Spec Validated (schema/v1.0)[/green]")
    sleep_random()
    console.log("[green]Camera Driver Online (/dev/video0)[/green]")
    sleep_random()

    # Hardware topology
    tree = Tree("[bold white]Hardware Topology[/bold white]")
    arm = tree.add("[cyan]I2C Bus 1[/cyan]")
    arm.add("PCA9685 PWM Controller (0x40)")
    arm.add("Motor Driver A (Left)")
    arm.add("Motor Driver B (Right)")
    console.print(tree)
    time.sleep(1)


def mind_loop_simulation():
    """Simulates the AI Thinking Process."""
    console.print("\n[bold yellow]ENABLING AGENT LOOP...[/bold yellow]")
    time.sleep(1)

    steps = [
        {
            "phase": "OBSERVE",
            "log": "Capturing Frame #1042...",
            "data": {
                "resolution": "1920x1080",
                "latency": "12ms",
                "objects": ["floor", "shoe"],
            },
        },
        {
            "phase": "ORIENT",
            "log": "Identifying Objects...",
            "data": {
                "detected": [
                    "red_apple (0.98)",
                    "green_ball (0.95)",
                    "power_cord (0.40)",
                ]
            },
        },
        {
            "phase": "DECIDE",
            "log": "Processing User Intent: 'Go to the fruit'",
            "data": {
                "reasoning": "User requested 'fruit'.",
                "classification": {"red_apple": "fruit", "green_ball": "toy"},
                "target": "red_apple",
                "strategy": "approach_and_stop",
            },
        },
        {
            "phase": "ACT",
            "log": "Generating Kinematics...",
            "data": {
                "cmd": "diff_drive",
                "vectors": {"linear": 0.5, "angular": 0.1},
                "duration_ms": 1500,
            },
        },
    ]

    for step in steps:
        console.rule(f"[bold magenta]{step['phase']}[/bold magenta]")
        time.sleep(0.5)

        console.log(f"[cyan]{step['log']}[/cyan]")
        sleep_random(0.5, 1.0)

        json_str = json.dumps(step["data"], indent=2)
        syntax = Syntax(json_str, "json", theme="monokai", line_numbers=False)
        console.print(Panel(syntax, title="Cortex Stream", border_style="green"))
        sleep_random(1.5, 2.5)


def safety_trigger():
    """Simulates the Emergency Stop Event."""
    console.rule("[bold red]!!! INTERRUPT !!![/bold red]")
    time.sleep(0.2)

    alert = {
        "event": "COLLISION_WARNING",
        "sensor": "lidar_front",
        "distance_mm": 85,
        "threshold_mm": 100,
        "action": "EMERGENCY_STOP",
    }

    json_str = json.dumps(alert, indent=2)
    console.print(
        Panel(
            Syntax(json_str, "json", theme="monokai"),
            title="SAFETY DAEMON",
            border_style="red",
        )
    )
    console.print("[bold red]MOTORS DISENGAGED[/bold red]")


if __name__ == "__main__":
    try:
        boot_sequence()
        mind_loop_simulation()
        safety_trigger()
        console.print("\n[dim]Session Ended. Log saved to /var/log/castor/session_01.json[/dim]")
    except KeyboardInterrupt:
        console.print("[red]Aborted.[/red]")

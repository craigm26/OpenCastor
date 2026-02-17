"""
OpenCastor Watch -- live terminal dashboard.

Displays a continuously updating view of the robot's state:
thought, action, latency, camera, and memory stats.

Usage:
    castor watch --config robot.rcan.yaml
    castor watch --config robot.rcan.yaml --refresh 2
"""

import logging
import os
import time

import yaml

logger = logging.getLogger("OpenCastor.Watch")


def _format_uptime(seconds: float) -> str:
    """Format seconds into human-readable uptime."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def launch_watch(config_path: str, refresh: float = 1.0, gateway_url: str = None):
    """Launch the live terminal dashboard.

    Connects to the gateway API to poll status, or reads from the
    virtual filesystem directly if running in the same process.
    """
    try:
        from rich.console import Console
        from rich.live import Live
        from rich.panel import Panel
        from rich.table import Table
    except ImportError:
        print("  castor watch requires the 'rich' library.")
        print("  Install with: pip install rich")
        return

    console = Console()

    # Determine data source
    if gateway_url is None:
        gateway_url = f"http://127.0.0.1:{os.getenv('OPENCASTOR_API_PORT', '8000')}"

    with open(config_path) as f:
        config = yaml.safe_load(f)

    robot_name = config.get("metadata", {}).get("robot_name", "Robot")

    def _fetch_status() -> dict:
        """Poll the gateway API for status."""
        try:
            import httpx

            resp = httpx.get(f"{gateway_url}/health", timeout=2)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return {}

    def _fetch_proc() -> dict:
        """Poll the gateway API for /proc telemetry."""
        try:
            import httpx

            resp = httpx.get(f"{gateway_url}/api/fs/proc", timeout=2)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return {}

    def _build_display(health: dict, proc: dict, elapsed: float) -> Panel:
        """Build the Rich display panel."""
        layout = Table.grid(padding=(0, 2))
        layout.add_column(ratio=1)
        layout.add_column(ratio=1)

        # Left column: Status
        status_table = Table(show_header=False, box=None, padding=(0, 1))
        status_table.add_column(style="bold", width=12)
        status_table.add_column()

        uptime = health.get("uptime_s", 0)
        brain_status = "[green]online[/]" if health.get("brain") else "[red]offline[/]"
        driver_status = "[green]online[/]" if health.get("driver") else "[yellow]mock[/]"
        channels = ", ".join(health.get("channels", [])) or "none"

        status_table.add_row("Robot", robot_name)
        status_table.add_row("Uptime", _format_uptime(uptime))
        status_table.add_row("Brain", brain_status)
        status_table.add_row("Driver", driver_status)
        status_table.add_row("Channels", channels)
        status_table.add_row("Watching", f"{elapsed:.0f}s")

        # Right column: Telemetry
        telem_table = Table(show_header=False, box=None, padding=(0, 1))
        telem_table.add_column(style="bold", width=12)
        telem_table.add_column()

        loop_count = proc.get("loop_count", 0)
        avg_latency = proc.get("avg_latency_ms", 0)
        last_thought = proc.get("last_thought", "")
        camera = proc.get("camera", "?")
        speaker = proc.get("speaker", "?")

        if isinstance(last_thought, str) and len(last_thought) > 60:
            last_thought = last_thought[:57] + "..."

        latency_color = "green" if avg_latency < 200 else "yellow" if avg_latency < 1000 else "red"

        telem_table.add_row("Loops", str(loop_count))
        telem_table.add_row("Avg Latency", f"[{latency_color}]{avg_latency:.0f}ms[/]")
        telem_table.add_row("Camera", camera)
        telem_table.add_row("Speaker", speaker)
        telem_table.add_row("Thought", str(last_thought) if last_thought else "[dim]none[/]")

        layout.add_row(status_table, telem_table)

        return Panel(
            layout,
            title=f"[bold cyan]OpenCastor Watch[/]  [dim]({gateway_url})[/]",
            border_style="cyan",
            subtitle="[dim]Ctrl+C to stop[/]",
        )

    console.print(f"\n  Connecting to {gateway_url}...\n")

    start_time = time.time()
    try:
        with Live(console=console, refresh_per_second=1 / refresh) as live:
            while True:
                health = _fetch_status()
                proc = _fetch_proc()
                elapsed = time.time() - start_time
                display = _build_display(health, proc, elapsed)
                live.update(display)
                time.sleep(refresh)
    except KeyboardInterrupt:
        console.print("\n  [dim]Watch stopped.[/]\n")

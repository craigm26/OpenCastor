"""
OpenCastor Watch — single-page Rich terminal dashboard.

Mirrors the web dashboard layout:
  • Header row : robot name · brain · driver · channels · uptime
  • Left column: camera viewfinder (stats + stream URL) · recent commands
  • Right column: status/telemetry · driver · channels · learner stats
  • Footer      : keyboard hints

Usage:
    castor watch --gateway http://127.0.0.1:8000 --refresh 2
    castor watch --config robot.rcan.yaml
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional

# ── rich imports (graceful error) ──────────────────────────────────────────────
try:
    from rich.columns import Columns
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("castor watch requires 'rich'.  Install: pip install rich")
    sys.exit(1)

_TOKEN = os.getenv("OPENCASTOR_API_TOKEN", "")
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"} if _TOKEN else {}

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fmt_uptime(s: float) -> str:
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sc = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sc:02d}" if h else f"{m:02d}:{sc:02d}"


def _get(url: str, timeout: float = 2.0) -> dict:
    try:
        import httpx
        r = httpx.get(url, headers=_HEADERS, timeout=timeout)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def _dot(ok: Optional[bool], true_color="green", false_color="red") -> str:
    if ok is True:
        return f"[{true_color}]●[/]"
    if ok is False:
        return f"[{false_color}]●[/]"
    return "[dim]○[/]"


# ──────────────────────────────────────────────────────────────────────────────
# Panel builders — each returns a Rich renderable
# ──────────────────────────────────────────────────────────────────────────────

def _header(health: dict, status: dict, robot_name: str, elapsed: float) -> Text:
    brain_ok = health.get("brain")
    driver_ok = health.get("driver")
    uptime = health.get("uptime_s", 0)
    channels = status.get("channels_active", health.get("channels", []))
    ch_str = "  ".join(f"[cyan]{c}[/]" for c in channels) if channels else "[dim]no channels[/]"

    t = Text()
    t.append(f"  🤖  {robot_name}", style="bold white")
    t.append("    ")
    t.append_text(Text.from_markup(f"brain {_dot(brain_ok)}"))
    t.append("  ")
    t.append_text(Text.from_markup(f"driver {_dot(driver_ok, 'green', 'yellow')}"))
    t.append("    ")
    t.append_text(Text.from_markup(ch_str))
    t.append(f"    ↑ {_fmt_uptime(uptime)}", style="dim")
    t.append(f"   watching {_fmt_uptime(elapsed)}", style="dim")
    return t


def _camera_panel(status: dict, proc: dict, gateway_url: str) -> Panel:
    cam_status = proc.get("camera", "?")
    is_live = str(cam_status).lower() in ("online", "true", "ok")
    cam_color = "green" if is_live else "red"

    t = Table.grid(padding=(0, 2))
    t.add_column(justify="center", min_width=34)

    stream_url = f"{gateway_url}/api/stream/mjpeg"
    if _TOKEN:
        stream_url += f"?token={_TOKEN}"

    # ASCII viewfinder
    cam_line = "[bold green]● LIVE[/]" if is_live else "[bold red]● NO SIGNAL[/]"
    t.add_row("")
    t.add_row(Text.from_markup(f"[{cam_color}]╔{'═'*28}╗[/]"))
    t.add_row(Text.from_markup(f"[{cam_color}]║[/]  OAK-D USB3  · 640×480 @ 30fps  [{cam_color}]║[/]"))
    t.add_row(Text.from_markup(f"[{cam_color}]║[/]                                  [{cam_color}]║[/]"))
    t.add_row(Text.from_markup(f"[{cam_color}]║[/]        {cam_line}          [{cam_color}]║[/]"))
    t.add_row(Text.from_markup(f"[{cam_color}]║[/]      ~35 KB/frame  ≈ 30fps        [{cam_color}]║[/]"))
    t.add_row(Text.from_markup(f"[{cam_color}]║[/]                                  [{cam_color}]║[/]"))
    t.add_row(Text.from_markup(f"[{cam_color}]╚{'═'*28}╝[/]"))
    t.add_row("")
    t.add_row(Text.from_markup(f"[dim]Web stream →[/] [cyan]{stream_url[:48]}[/]"))
    t.add_row(Text.from_markup("[dim]Open in browser → Vision tab[/]"))
    t.add_row("")

    return Panel(t, title="[bold]📷  Camera[/]", border_style=cam_color, padding=(0, 1))


def _status_panel(health: dict, proc: dict) -> Panel:
    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_column(style="dim", width=10)
    t.add_column()

    uptime = health.get("uptime_s", 0)
    brain_ok = health.get("brain")
    driver_ok = health.get("driver")
    loop_count = proc.get("loop_count", 0)
    avg_latency = proc.get("avg_latency_ms", 0)
    lat_color = "green" if avg_latency < 300 else "yellow" if avg_latency < 1000 else "red"

    last_thought = str(proc.get("last_thought") or "")
    if len(last_thought) > 36:
        last_thought = last_thought[:33] + "..."

    t.add_row("Uptime", f"[white]{_fmt_uptime(uptime)}[/]")
    t.add_row("Brain", f"{_dot(brain_ok)} [white]{'online' if brain_ok else 'offline'}[/]")
    t.add_row("Driver", f"{_dot(driver_ok, 'green', 'yellow')} [white]{'online' if driver_ok else 'mock'}[/]")
    t.add_row("Loops", f"[white]{loop_count}[/]")
    t.add_row("Latency", f"[{lat_color}]{avg_latency:.0f} ms[/]" if avg_latency else "[dim]—[/]")
    t.add_row("Thought", f"[dim]{last_thought}[/]" if last_thought else "[dim]none[/]")
    return Panel(t, title="[bold]⚡  Status[/]", border_style="blue", padding=(0, 1))


def _driver_panel(driver: dict) -> Panel:
    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_column(style="dim", width=10)
    t.add_column()

    ok = driver.get("ok")
    mode = driver.get("mode", "?")
    err = driver.get("error") or ""
    drv_type = driver.get("driver_type", "PCA9685")
    color = "green" if ok else "yellow"

    t.add_row("Status", f"{_dot(ok, 'green', 'yellow')} [white]{mode}[/]")
    t.add_row("Type", f"[white]{drv_type}[/]")
    if err:
        t.add_row("Info", f"[dim]{err[:36]}[/]")
    return Panel(t, title="[bold]🦾  Driver[/]", border_style=color, padding=(0, 1))


def _channels_panel(status: dict) -> Panel:
    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_column(style="dim", width=12)
    t.add_column()

    available = status.get("channels_available", {})
    active = set(status.get("channels_active", []))

    if available:
        for ch, avail in sorted(available.items()):
            is_active = ch in active
            dot = "[green]●[/]" if is_active else ("[yellow]○[/]" if avail else "[dim]○[/]")
            state = "active" if is_active else ("ready" if avail else "unavail")
            t.add_row(ch, f"{dot} [dim]{state}[/]")
    else:
        t.add_row("", "[dim]no channel data[/]")

    return Panel(t, title="[bold]📡  Channels[/]", border_style="magenta", padding=(0, 1))


def _learner_panel(learner: dict, episodes: dict) -> Panel:
    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_column(style="dim", width=12)
    t.add_column()

    avail = learner.get("available", False)
    if avail:
        t.add_row("Episodes", f"[white]{learner.get('episodes_analyzed', 0)}[/]")
        t.add_row("Applied", f"[green]{learner.get('improvements_applied', 0)}[/]")
        t.add_row("Rejected", f"[red]{learner.get('improvements_rejected', 0)}[/]")
        avg = learner.get("avg_duration_ms")
        t.add_row("Avg time", f"[white]{avg:.0f} ms[/]" if avg else "[dim]—[/]")
    else:
        # Show SQLite memory episode count even when Sisyphus learner is off
        total = episodes.get("total", 0)
        if total:
            t.add_row("Mem eps", f"[cyan]{total}[/]")
        else:
            t.add_row("", "[dim]no data yet[/]")

    # Recent episode rows from the memory store
    ep_list = episodes.get("episodes", [])
    if ep_list:
        t.add_row("", "")  # spacer
        t.add_row("[dim]Recent eps[/]", "")
        for ep in ep_list[:3]:
            ts = ep.get("ts", "")
            hhmm = ts[11:16] if len(ts) > 15 else ts[:5]
            action_type = (ep.get("action") or {}).get("type", "—")
            lat = ep.get("latency_ms", 0)
            t.add_row(hhmm, f"[white]{action_type}[/]  [dim]{lat:.0f}ms[/]")

    return Panel(t, title="[bold]🧠  Learner / Memory[/]", border_style="yellow", padding=(0, 1))


def _history_panel(history: dict) -> Panel:
    entries = history.get("history", [])
    t = Table(show_header=True, box=None, padding=(0, 1), show_edge=False)
    t.add_column("Time", style="dim", width=8)
    t.add_column("Instruction", width=22)
    t.add_column("Action / Response", style="dim")

    if not entries:
        t.add_row("[dim]—[/]", "[dim]no commands yet[/]", "")
    else:
        for entry in reversed(entries[-5:]):
            ts = entry.get("ts", "")
            hh_mm = ts[11:16] if len(ts) > 15 else ts[:5]
            instr = str(entry.get("instruction", ""))[:22]
            action = str(entry.get("action") or entry.get("raw_text") or "")[:36]
            t.add_row(hh_mm, instr, action)

    return Panel(t, title="[bold]🕒  Recent Commands[/]", border_style="cyan", padding=(0, 1))


# ──────────────────────────────────────────────────────────────────────────────
# Layout assembly
# ──────────────────────────────────────────────────────────────────────────────

def _build_layout(
    health: dict,
    status: dict,
    proc: dict,
    driver: dict,
    learner: dict,
    history: dict,
    episodes: dict,
    robot_name: str,
    gateway_url: str,
    elapsed: float,
) -> Layout:
    root = Layout()
    root.split_column(
        Layout(name="header", size=1),
        Layout(name="body"),
        Layout(name="footer", size=1),
    )

    # Header
    root["header"].update(_header(health, status, robot_name, elapsed))

    # Body: left (camera + history) and right (status + driver + channels + learner)
    root["body"].split_row(
        Layout(name="left", ratio=3),
        Layout(name="right", ratio=2),
    )

    root["body"]["left"].split_column(
        Layout(_camera_panel(status, proc, gateway_url), name="camera", ratio=3),
        Layout(_history_panel(history), name="history", ratio=2),
    )

    root["body"]["right"].split_column(
        Layout(_status_panel(health, proc), name="status", ratio=3),
        Layout(_driver_panel(driver), name="driver", ratio=2),
        Layout(_channels_panel(status), name="channels", ratio=3),
        Layout(_learner_panel(learner, episodes), name="learner", ratio=2),
    )

    # Footer
    root["footer"].update(Text.from_markup(
        "  [dim]Ctrl+C[/] quit   [dim]e[/] emergency stop   "
        f"[dim]gateway:[/] {gateway_url}   "
        "[dim]web:[/] :8501"
    ))

    return root


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────────

def launch_watch(
    config_path: str = None,
    refresh: float = 2.0,
    gateway_url: str = None,
):
    """Launch the full-screen Rich terminal dashboard."""
    console = Console()

    if gateway_url is None:
        gateway_url = os.getenv(
            "OPENCASTOR_GATEWAY_URL",
            f"http://127.0.0.1:{os.getenv('OPENCASTOR_API_PORT', '8000')}",
        )

    robot_name = "Robot"
    if config_path:
        try:
            import yaml
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            robot_name = cfg.get("metadata", {}).get("robot_name", "Robot")
        except Exception:
            pass
    else:
        # Try to get from gateway status
        s = _get(f"{gateway_url}/api/status")
        robot_name = s.get("robot_name", "Bob")

    console.print(f"\n  [cyan]OpenCastor Watch[/]  →  {gateway_url}\n")

    start = time.time()
    try:
        with Live(
            console=console,
            screen=True,
            refresh_per_second=max(1, int(1 / refresh)),
        ) as live:
            while True:
                health = _get(f"{gateway_url}/health")
                status = _get(f"{gateway_url}/api/status")
                proc = _get(f"{gateway_url}/api/fs/proc")
                driver = _get(f"{gateway_url}/api/driver/health")
                learner = _get(f"{gateway_url}/api/learner/stats")
                history = _get(f"{gateway_url}/api/command/history?limit=5")
                episodes = _get(f"{gateway_url}/api/memory/episodes?limit=5")
                elapsed = time.time() - start

                layout = _build_layout(
                    health, status, proc, driver, learner, history, episodes,
                    robot_name, gateway_url, elapsed,
                )
                live.update(layout)
                time.sleep(refresh)

    except KeyboardInterrupt:
        console.print("\n  [dim]Watch stopped.[/]\n")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="OpenCastor terminal dashboard")
    ap.add_argument("--gateway", default=None, help="Gateway URL (default: http://127.0.0.1:8000)")
    ap.add_argument("--config", default=None, help="Path to .rcan.yaml config file")
    ap.add_argument("--refresh", type=float, default=2.0, help="Refresh interval in seconds")
    a = ap.parse_args()
    launch_watch(config_path=a.config, refresh=a.refresh, gateway_url=a.gateway)

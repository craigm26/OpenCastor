"""
OpenCastor Network -- VPN/tunnel exposure controls.

Provides Tailscale integration for secure remote robot control
and network binding configuration. Integrates with the wizard
and gateway for safe remote access.

Usage:
    castor network status                  # Show network config + Tailscale status
    castor network expose --mode serve     # Expose via Tailscale serve (private)
    castor network expose --mode funnel    # Expose via Tailscale funnel (public)
    castor network expose --mode off       # Remove exposure
"""

import logging
import os
import subprocess

logger = logging.getLogger("OpenCastor.Network")


def check_tailscale() -> dict:
    """Check Tailscale installation and status.

    Returns a dict with keys: installed, running, ip, hostname, version.
    """
    result = {
        "installed": False,
        "running": False,
        "ip": None,
        "hostname": None,
        "version": None,
    }

    # Check if tailscale binary exists
    try:
        proc = subprocess.run(
            ["tailscale", "version"], capture_output=True, text=True, timeout=5
        )
        if proc.returncode == 0:
            result["installed"] = True
            result["version"] = proc.stdout.strip().split("\n")[0]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return result

    # Check if connected
    try:
        proc = subprocess.run(
            ["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5
        )
        if proc.returncode == 0:
            import json
            status = json.loads(proc.stdout)
            self_node = status.get("Self", {})
            if self_node:
                result["running"] = True
                ips = self_node.get("TailscaleIPs", [])
                if ips:
                    result["ip"] = ips[0]
                result["hostname"] = self_node.get("HostName", None)
    except Exception:
        pass

    return result


def get_lan_ip() -> str:
    """Get the local LAN IP address."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def network_status(config_path: str = None):
    """Show network configuration and exposure status."""
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False
        console = None

    lan_ip = get_lan_ip()
    ts = check_tailscale()

    # Load gateway port from config
    port = 8000
    if config_path and os.path.exists(config_path):
        try:
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f)
            port = config.get("rcan_protocol", {}).get("port", 8000)
        except Exception:
            pass

    if has_rich:
        console.print("\n[bold cyan]  OpenCastor Network Status[/]\n")

        table = Table(show_header=True, box=None, padding=(0, 2))
        table.add_column("Setting", style="bold")
        table.add_column("Value")

        table.add_row("LAN IP", lan_ip)
        table.add_row("Gateway Port", str(port))
        table.add_row("Local URL", f"http://{lan_ip}:{port}")

        table.add_row("", "")
        table.add_row("Tailscale Installed", _yes_no(ts["installed"], has_rich))
        if ts["installed"]:
            table.add_row("Tailscale Running", _yes_no(ts["running"], has_rich))
            if ts["running"]:
                table.add_row("Tailscale IP", ts["ip"] or "?")
                table.add_row("Tailscale Hostname", ts["hostname"] or "?")
                table.add_row(
                    "Tailnet URL",
                    f"http://{ts['hostname']}:{port}" if ts["hostname"] else "?"
                )
            table.add_row("Tailscale Version", ts["version"] or "?")

        console.print(table)
        console.print()
    else:
        print("\n  OpenCastor Network Status\n")
        print(f"  LAN IP:        {lan_ip}")
        print(f"  Gateway Port:  {port}")
        print(f"  Local URL:     http://{lan_ip}:{port}")
        print()
        print(f"  Tailscale:     {'installed' if ts['installed'] else 'not installed'}")
        if ts["installed"]:
            print(f"  TS Running:    {'yes' if ts['running'] else 'no'}")
            if ts["running"]:
                print(f"  TS IP:         {ts['ip'] or '?'}")
                print(f"  TS Hostname:   {ts['hostname'] or '?'}")
            print(f"  TS Version:    {ts['version'] or '?'}")
        print()


def _yes_no(value: bool, has_rich: bool) -> str:
    if has_rich:
        return "[green]yes[/]" if value else "[red]no[/]"
    return "yes" if value else "no"


def expose(mode: str, port: int = 8000):
    """Configure Tailscale exposure.

    Args:
        mode: ``"serve"``, ``"funnel"``, or ``"off"``.
        port: Local port to expose.
    """
    ts = check_tailscale()
    if not ts["installed"]:
        print("\n  Tailscale is not installed.")
        print("  Install from: https://tailscale.com/download\n")
        return

    if not ts["running"]:
        print("\n  Tailscale is not connected.")
        print("  Run: tailscale up\n")
        return

    if mode == "off":
        # Remove any existing serve/funnel
        try:
            subprocess.run(
                ["tailscale", "serve", "reset"], capture_output=True, timeout=10
            )
            print("  Tailscale exposure removed.\n")
        except Exception as exc:
            print(f"  Failed to reset: {exc}\n")
        return

    if mode == "serve":
        # Tailscale serve (private HTTPS for tailnet only)
        try:
            proc = subprocess.run(
                ["tailscale", "serve", "--bg", f"http://127.0.0.1:{port}"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                hostname = ts.get("hostname", "?")
                print(f"\n  Tailscale serve active (tailnet only).")
                print(f"  URL: https://{hostname}/\n")
            else:
                print(f"  Failed: {proc.stderr}\n")
        except Exception as exc:
            print(f"  Failed: {exc}\n")
        return

    if mode == "funnel":
        # Tailscale funnel (public HTTPS)
        print("\n  WARNING: Funnel exposes your robot to the PUBLIC internet.")
        try:
            answer = input("  Continue? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer != "y":
            print("  Cancelled.\n")
            return

        try:
            proc = subprocess.run(
                ["tailscale", "funnel", "--bg", f"http://127.0.0.1:{port}"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                hostname = ts.get("hostname", "?")
                print(f"\n  Tailscale funnel active (PUBLIC).")
                print(f"  URL: https://{hostname}/")
                print("  WARNING: Anyone with this URL can access your robot.\n")
            else:
                print(f"  Failed: {proc.stderr}\n")
        except Exception as exc:
            print(f"  Failed: {exc}\n")

    print(f"  Unknown mode: {mode}. Use: serve, funnel, or off\n")

"""
OpenCastor Fleet -- multi-robot management.

Discovers RCAN peers via mDNS and provides a unified status view.
Can send commands to multiple robots simultaneously.

Usage:
    castor fleet status                    # Show all discovered robots
    castor fleet status --timeout 10       # Longer scan duration
"""

import logging
import time

logger = logging.getLogger("OpenCastor.Fleet")


def fleet_status(timeout: float = 5.0):
    """Discover RCAN peers and show a unified status view."""
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False
        console = None

    if has_rich:
        console.print("\n[bold cyan]  OpenCastor Fleet[/]")
        console.print(f"  Scanning for RCAN peers ({timeout:.0f}s)...\n")
    else:
        print("\n  OpenCastor Fleet")
        print(f"  Scanning for RCAN peers ({timeout:.0f}s)...\n")

    # Try mDNS discovery
    peers = _discover_peers(timeout)

    if not peers:
        msg = "  No RCAN peers found on the local network."
        if has_rich:
            console.print(f"  [dim]{msg}[/]")
        else:
            print(msg)

        print("\n  Tips:")
        print("    - Ensure robots are running: castor gateway --config <file>")
        print("    - Enable mDNS in config: rcan_protocol.enable_mdns: true")
        print("    - Check that devices are on the same network\n")
        return

    if has_rich:
        table = Table(title=f"Fleet: {len(peers)} Robot(s)", show_header=True)
        table.add_column("Name", style="bold")
        table.add_column("Model")
        table.add_column("RURI", style="dim")
        table.add_column("Address")
        table.add_column("Status")
        table.add_column("Capabilities")

        for peer in peers:
            status = peer.get("status", "unknown")
            status_style = {
                "active": "[green]active[/]",
                "idle": "[yellow]idle[/]",
                "error": "[red]error[/]",
            }.get(status, status)

            table.add_row(
                peer.get("robot_name", "?"),
                peer.get("model", "?"),
                peer.get("ruri", "?")[:40],
                f"{', '.join(peer.get('addresses', ['?']))}:{peer.get('port', '?')}",
                status_style,
                ", ".join(peer.get("capabilities", [])) or "none",
            )

        console.print(table)
    else:
        print(f"  Found {len(peers)} robot(s):\n")
        for peer in peers:
            print(f"    Name:    {peer.get('robot_name', '?')}")
            print(f"    Model:   {peer.get('model', '?')}")
            print(f"    RURI:    {peer.get('ruri', '?')}")
            addrs = ", ".join(peer.get("addresses", ["?"]))
            print(f"    Address: {addrs}:{peer.get('port', '?')}")
            print(f"    Status:  {peer.get('status', '?')}")
            caps = ", ".join(peer.get("capabilities", []))
            print(f"    Caps:    {caps or 'none'}")
            print()

    # Try to fetch health from each peer
    _check_peer_health(peers, has_rich, console)

    print()


def _discover_peers(timeout: float) -> list:
    """Discover RCAN peers via mDNS."""
    try:
        from castor.rcan.mdns import RCANServiceBrowser
    except ImportError:
        logger.debug("zeroconf not installed -- mDNS discovery unavailable")
        return []

    found = []

    def on_found(peer):
        found.append(peer)

    try:
        browser = RCANServiceBrowser(on_found=on_found)
        browser.start()
        time.sleep(timeout)
        browser.stop()
    except Exception as exc:
        logger.debug(f"mDNS scan error: {exc}")

    return found


def _check_peer_health(peers: list, has_rich: bool, console):
    """Attempt to fetch /health from each discovered peer."""
    try:
        import httpx
    except ImportError:
        return

    reachable = 0
    for peer in peers:
        addresses = peer.get("addresses", [])
        port = peer.get("port", 8000)

        for addr in addresses:
            try:
                url = f"http://{addr}:{port}/health"
                resp = httpx.get(url, timeout=2.0)
                if resp.status_code == 200:
                    reachable += 1
                    break
            except Exception:
                continue

    if has_rich:
        console.print(
            f"\n  [dim]Health check: {reachable}/{len(peers)} reachable via HTTP[/]"
        )
    else:
        print(f"\n  Health check: {reachable}/{len(peers)} reachable via HTTP")

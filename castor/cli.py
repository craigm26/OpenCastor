"""
OpenCastor CLI entry point.
Provides a unified command interface similar to OpenClaw's 'openclaw' command.

Usage:
    castor run      --config robot.rcan.yaml          # Run the robot
    castor gateway  --config robot.rcan.yaml          # Start the API gateway
    castor wizard                                      # Interactive setup
    castor dashboard                                   # Launch CastorDash
    castor status                                      # Check provider/channel readiness
"""

import argparse
import sys


def cmd_run(args):
    """Run the main perception-action loop."""
    from castor.main import main as run_main

    sys.argv = ["castor.main", "--config", args.config]
    if args.simulate:
        sys.argv.append("--simulate")
    run_main()


def cmd_gateway(args):
    """Start the FastAPI gateway server."""
    from castor.api import main as run_gateway

    sys.argv = [
        "castor.api", "--config", args.config,
        "--host", args.host, "--port", str(args.port),
    ]
    run_gateway()


def cmd_wizard(args):
    """Run the interactive setup wizard."""
    from castor.wizard import main as run_wizard

    run_wizard()


def cmd_dashboard(args):
    """Launch the Streamlit dashboard."""
    import subprocess

    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", "castor/dashboard.py"],
        check=True,
    )


def cmd_token(args):
    """Issue a JWT token for RCAN API access."""
    from castor.auth import load_dotenv_if_available

    load_dotenv_if_available()

    import os
    jwt_secret = os.getenv("OPENCASTOR_JWT_SECRET")
    if not jwt_secret:
        print("Error: OPENCASTOR_JWT_SECRET is not set in environment or .env file.")
        print("Generate one with: openssl rand -hex 32")
        raise SystemExit(1)

    try:
        from castor.rcan.jwt_auth import RCANTokenManager
        from castor.rcan.rbac import RCANRole

        role = RCANRole[args.role.upper()]
        scopes = args.scope.split(",") if args.scope else None

        ruri = os.getenv("OPENCASTOR_RURI", "rcan://opencastor.unknown.00000000")
        mgr = RCANTokenManager(secret=jwt_secret, issuer=ruri)
        token = mgr.issue(
            subject=args.subject or "cli-user",
            role=role,
            scopes=scopes,
            ttl_seconds=int(args.ttl) * 3600,
        )
        print(f"\n  RCAN JWT Token (role={role.name}, ttl={args.ttl}h)\n")
        print(f"  {token}\n")
    except ImportError:
        print("Error: PyJWT is not installed. Install with: pip install PyJWT")
        raise SystemExit(1)
    except KeyError:
        print(f"Error: Invalid role '{args.role}'. Valid: GUEST, USER, OPERATOR, ADMIN, CREATOR")
        raise SystemExit(1)


def cmd_discover(args):
    """Discover RCAN peers on the local network."""
    print("\n  Scanning for RCAN peers (5 seconds)...\n")

    try:
        from castor.rcan.mdns import RCANServiceBrowser
    except ImportError:
        print("  Error: zeroconf is not installed.")
        print("  Install with: pip install opencastor[rcan]")
        raise SystemExit(1)

    import time

    found = []

    def on_found(peer):
        found.append(peer)

    browser = RCANServiceBrowser(on_found=on_found)
    browser.start()
    time.sleep(float(args.timeout))
    browser.stop()

    if not found:
        print("  No RCAN peers found on the local network.\n")
    else:
        print(f"  Found {len(found)} peer(s):\n")
        for peer in found:
            print(f"    RURI:    {peer.get('ruri', '?')}")
            print(f"    Name:    {peer.get('robot_name', '?')}")
            print(f"    Model:   {peer.get('model', '?')}")
            print(f"    Caps:    {', '.join(peer.get('capabilities', []))}")
            print(f"    Address: {', '.join(peer.get('addresses', []))}:{peer.get('port', '?')}")
            print(f"    Status:  {peer.get('status', '?')}")
            print()


def cmd_status(args):
    """Show which providers and channels are ready."""
    from castor.auth import (
        list_available_channels,
        list_available_providers,
        load_dotenv_if_available,
    )

    load_dotenv_if_available()

    print("\n  OpenCastor Status\n")

    print("  AI Providers:")
    for name, ready in list_available_providers().items():
        icon = "+" if ready else "-"
        label = "ready" if ready else "no key"
        print(f"    [{icon}] {name:12s} {label}")

    print("\n  Messaging Channels:")
    for name, ready in list_available_channels().items():
        icon = "+" if ready else "-"
        label = "ready" if ready else "not configured"
        print(f"    [{icon}] {name:12s} {label}")

    print()


def main():
    parser = argparse.ArgumentParser(
        prog="castor",
        description="OpenCastor - The Universal Runtime for Embodied AI",
    )
    sub = parser.add_subparsers(dest="command")

    # castor run
    p_run = sub.add_parser("run", help="Run the robot perception-action loop")
    p_run.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_run.add_argument("--simulate", action="store_true", help="Run without hardware")

    # castor gateway
    p_gw = sub.add_parser("gateway", help="Start the API gateway server")
    p_gw.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_gw.add_argument("--host", default="127.0.0.1", help="Bind address")
    p_gw.add_argument("--port", type=int, default=8000, help="Port number")

    # castor wizard
    sub.add_parser("wizard", help="Interactive setup wizard")

    # castor dashboard
    sub.add_parser("dashboard", help="Launch the Streamlit web UI")

    # castor token
    p_token = sub.add_parser("token", help="Issue a JWT token for RCAN API access")
    p_token.add_argument("--role", default="user",
                         help="RCAN role (guest/user/operator/admin/creator)")
    p_token.add_argument("--scope", default=None,
                         help="Comma-separated scopes (e.g. status,control)")
    p_token.add_argument("--ttl", default="24", help="Token lifetime in hours (default: 24)")
    p_token.add_argument("--subject", default=None, help="Principal name (default: cli-user)")

    # castor discover
    p_discover = sub.add_parser("discover", help="Discover RCAN peers on the local network")
    p_discover.add_argument("--timeout", default="5", help="Scan duration in seconds (default: 5)")

    # castor status
    sub.add_parser("status", help="Show provider and channel readiness")

    args = parser.parse_args()

    commands = {
        "run": cmd_run,
        "gateway": cmd_gateway,
        "wizard": cmd_wizard,
        "dashboard": cmd_dashboard,
        "token": cmd_token,
        "discover": cmd_discover,
        "status": cmd_status,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

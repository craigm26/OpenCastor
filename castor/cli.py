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

import sys
import argparse
import logging


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

    sys.argv = ["castor.api", "--config", args.config, "--host", args.host, "--port", str(args.port)]
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


def cmd_status(args):
    """Show which providers and channels are ready."""
    from castor.auth import (
        load_dotenv_if_available,
        list_available_providers,
        list_available_channels,
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

    # castor status
    sub.add_parser("status", help="Show provider and channel readiness")

    args = parser.parse_args()

    commands = {
        "run": cmd_run,
        "gateway": cmd_gateway,
        "wizard": cmd_wizard,
        "dashboard": cmd_dashboard,
        "status": cmd_status,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

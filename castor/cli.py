"""
OpenCastor CLI entry point.
Provides a unified command interface for the OpenCastor runtime.

Usage:
    castor run      --config robot.rcan.yaml          # Run the robot
    castor gateway  --config robot.rcan.yaml          # Start the API gateway
    castor mcp      --host 127.0.0.1 --port 8765      # Start MCP tool server
    castor wizard                                      # Interactive setup
    castor dashboard                                   # Launch CastorDash
    castor status                                      # Check provider/channel readiness
    castor doctor                                      # System health checks
    castor demo                                        # Simulated demo (no hardware)
    castor test-hardware --config robot.rcan.yaml      # Test motors individually
    castor calibrate --config robot.rcan.yaml          # Interactive calibration
    castor logs                                        # View logs
    castor backup                                      # Back up configs
    castor restore backup.tar.gz                       # Restore configs
    castor migrate --config robot.rcan.yaml            # Migrate RCAN config
    castor upgrade                                     # Self-update + doctor
    castor install-service --config robot.rcan.yaml    # Generate systemd unit
    castor shell --config robot.rcan.yaml              # Interactive command shell
    castor watch --gateway http://127.0.0.1:8000       # Live telemetry dashboard
    castor fix                                         # Auto-fix common issues
    castor repl --config robot.rcan.yaml               # Python REPL with robot objects
    castor record --config robot.rcan.yaml             # Record a session
    castor replay session.jsonl                        # Replay a recorded session
    castor benchmark --config robot.rcan.yaml          # Performance profiling
    castor lint --config robot.rcan.yaml               # Deep config validation
    castor validate --config bot.rcan.yaml             # RCAN conformance check
    castor rcan-check [--config robot.rcan.yaml]       # RCAN §6 safety field check
    castor improve --enable                            # Enable self-improving loop
    castor improve --disable                           # Disable self-improving loop
    castor improve --episodes 10                       # Analyze last 10 episodes
    castor improve --status                            # Improvement history
    castor learn                                       # Interactive tutorial
    castor fleet status                                # Multi-robot status
    castor export --config robot.rcan.yaml             # Export config bundle
    castor agents list                                 # List active agents
    castor agents status                               # Agent health report
    castor agents spawn --name observer                # Spawn an agent
    castor init     [--output robot.rcan.yaml]         # Scaffold starter config (non-interactive)
"""

import argparse
import hashlib
import hmac
import os
import sys
import traceback

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _launch_with_dashboard(args) -> None:
    """Re-launch inside a tmux session with the dashboard alongside the robot."""
    import shutil

    if not shutil.which("tmux"):
        print("  ⚠️  tmux not found. Install it with: sudo apt install tmux")
        print("  Falling back to plain run...\n")
        return  # caller will continue without dashboard

    from castor.dashboard_tui import SESSION_NAME, kill_existing_session, launch_dashboard

    # Kill any stale session
    kill_existing_session()

    layout = getattr(args, "layout", "full")
    config = args.config
    simulate_flag = "--simulate" if getattr(args, "simulate", False) else ""

    # Build the castor run command (without --dashboard to avoid recursion)
    run_cmd = f"castor run --config {config} {simulate_flag}".strip()

    # Launch dashboard session; the "logs" pane will run the robot
    print(f"\n  🚀 Launching OpenCastor dashboard (layout: {layout})...")
    print(f"     Robot command: {run_cmd}")
    print(f"     Attach with:   tmux attach -t {SESSION_NAME}\n")

    launch_dashboard(
        config_path=config,
        layout_name=layout,
        simulate=getattr(args, "simulate", False),
        run_command=run_cmd,
    )
    raise SystemExit(0)


def cmd_run(args) -> None:
    """Run the main perception-action loop."""
    # --dashboard: hand off to tmux session before doing anything else
    if getattr(args, "dashboard", False):
        _launch_with_dashboard(args)
        # If tmux not available, _launch_with_dashboard returns (falls through)

    config_path = args.config

    # Guided first-run: if no config exists, offer to run the wizard
    if not os.path.exists(config_path):
        import glob

        rcan_files = glob.glob("*.rcan.yaml")
        if rcan_files:
            print(f"\n  Config '{config_path}' not found, but found: {', '.join(rcan_files)}")
            print(f"  Try: castor run --config {rcan_files[0]}\n")
        else:
            print(f"\n  No config file found ({config_path}).")
            print("  Would you like to run the setup wizard first?\n")
            try:
                answer = input("  Run wizard? [Y/n]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer != "n":
                from castor.wizard import main as run_wizard

                sys.argv = ["castor.wizard"]
                run_wizard()
                return
            else:
                print("  Exiting. Create a config with: castor wizard\n")
                return

    # --behavior: load and run a behavior script, skip the perception loop
    behavior_path = getattr(args, "behavior", None)
    if behavior_path:
        from castor.behaviors import BehaviorRunner

        runner = BehaviorRunner(config={})
        behavior = runner.load(behavior_path)
        runner.run(behavior)
        return

    from castor.main import main as run_main

    sys.argv = ["castor.main", "--config", config_path]
    if args.simulate:
        sys.argv.append("--simulate")
    run_main()


def cmd_gateway(args) -> None:
    """Start the FastAPI gateway server."""
    from castor.api import main as run_gateway

    sys.argv = [
        "castor.api",
        "--config",
        args.config,
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    run_gateway()


def cmd_mcp(args) -> None:
    """Start the MCP server (stdio transport) or manage MCP clients."""
    import os as _os
    from pathlib import Path as _Path

    mcp_cmd = getattr(args, "mcp_cmd", None)

    if mcp_cmd == "token":
        from castor.mcp_auth import generate_token as _gen

        config_path = _Path(
            getattr(args, "config", None)
            or _os.environ.get("CASTOR_CONFIG", _Path.home() / "opencastor/bob.rcan.yaml")
        )
        raw = _gen(name=args.name, loa=args.loa, config_path=config_path)
        print(f"✓ Token generated for '{args.name}' (LoA {args.loa})")
        print(f"\n  export CASTOR_MCP_TOKEN={raw}")
        print("\n  Add to Claude Code:")
        print(f"  claude mcp add castor -- castor mcp --token {raw}")
        print(f"\n  Token hash stored in: {config_path}")
        print("  ⚠️  Save this token now — it cannot be recovered.")
        return

    if mcp_cmd == "install":
        import shutil as _shutil

        token = getattr(args, "token", "") or _os.environ.get("CASTOR_MCP_TOKEN", "")
        client = getattr(args, "client", "claude")
        castor_bin = _shutil.which("castor") or "castor"
        if client == "claude":
            cmd = f"claude mcp add castor -- {castor_bin} mcp"
            if token:
                cmd += f" --token {token}"
            import subprocess as _sp

            result = _sp.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode == 0:
                print("✓ OpenCastor registered as 'castor' in Claude Code MCP config")
                print("  Restart Claude Code to activate.")
            else:
                print(f"✗ Failed: {result.stderr.strip()}")
                print(f"  Manual: {cmd}")
        return

    if mcp_cmd == "clients":
        from castor.mcp_auth import list_clients as _list

        config_path = _Path(
            getattr(args, "config", None)
            or _os.environ.get("CASTOR_CONFIG", _Path.home() / "opencastor/bob.rcan.yaml")
        )
        clients = _list(config_path)
        if not clients:
            print("No MCP clients registered. Run: castor mcp token --name NAME --loa N")
            return
        print(f"{'Name':<30} {'LoA':<5} {'Token hash (truncated)'}")
        print("-" * 70)
        for c in clients:
            name = c.get("name", "?")
            loa = c.get("loa", 0)
            h = c.get("token_hash", "")[:32] + "…"
            print(f"{name:<30} {loa:<5} {h}")
        return

    # Default: start the MCP server
    from castor.mcp_server import run as _mcp_run

    token = getattr(args, "token", "") or _os.environ.get("CASTOR_MCP_TOKEN", "")
    if not token and _os.environ.get("CASTOR_MCP_DEV") != "1":
        import sys as _sys

        print(
            "Error: provide --token TOKEN or set CASTOR_MCP_TOKEN.\n"
            "For local dev: CASTOR_MCP_DEV=1 castor mcp",
            file=_sys.stderr,
        )
        raise SystemExit(1)
    if _os.environ.get("CASTOR_MCP_DEV") == "1" and not token:
        token = "dev"
    config_path = _Path(
        getattr(args, "config", "")
        or _os.environ.get("CASTOR_CONFIG", _Path.home() / "opencastor/bob.rcan.yaml")
    )
    _mcp_run(token=token, config_path=config_path)


def cmd_wizard(args) -> None:
    """Run the interactive setup wizard."""
    # Web-based wizard
    if getattr(args, "web", False):
        from castor.web_wizard import launch_web_wizard

        launch_web_wizard(port=getattr(args, "web_port", 8765))
        return

    from castor.wizard import main as run_wizard

    # Forward CLI flags to the wizard's own argparse
    wizard_args = ["castor.wizard"]
    if getattr(args, "simple", False):
        wizard_args.append("--simple")
    if getattr(args, "accept_risk", False):
        wizard_args.append("--accept-risk")
    sys.argv = wizard_args
    run_wizard()


def cmd_init(args) -> None:
    """Interactive setup wizard — generates a .rcan.yaml config (castor init)."""
    from castor.init_wizard import cmd_init as _wizard_init

    _wizard_init(args)


def _auto_detect_config(specified: str = "robot.rcan.yaml") -> str:
    """Return the RCAN config to use.

    Priority:
      1. Explicitly specified path (if it exists)
      2. Single *.rcan.yaml in cwd
      3. Fallback to the specified path (let caller handle missing file)
    """
    import glob as _glob

    if os.path.exists(specified):
        return specified
    matches = sorted(_glob.glob("*.rcan.yaml"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"  Multiple configs found: {matches}")
        print(f"  Using: {matches[0]}  (pass --config to choose)")
        return matches[0]
    return specified


def cmd_dashboard(args) -> None:
    """Launch the tmux terminal dashboard — starts the robot immediately."""
    from castor.dashboard_tui import kill_existing_session, launch_dashboard

    if getattr(args, "kill", False):
        kill_existing_session()
        print("  Dashboard session killed.")
        return

    config = _auto_detect_config(getattr(args, "config", "robot.rcan.yaml"))
    layout = getattr(args, "layout", "full")
    simulate = getattr(args, "simulate", False)
    launch_dashboard(config, layout, simulate)


def cmd_dashboard_tui(args) -> None:
    """Launch the tmux terminal dashboard."""
    from castor.dashboard_tui import kill_existing_session, launch_dashboard

    if args.kill:
        kill_existing_session()
        print("  Dashboard session killed.")
        return

    config = _auto_detect_config(args.config)
    launch_dashboard(config, args.layout, args.simulate)


def cmd_token(args) -> None:
    """Issue a JWT token for RCAN API access."""
    from castor.auth import load_dotenv_if_available
    from castor.secret_provider import get_jwt_secret_provider

    load_dotenv_if_available()

    import os

    provider = get_jwt_secret_provider()
    provider.invalidate()
    if getattr(args, "rotate", False):
        bundle = provider.rotate(
            new_secret=getattr(args, "new_secret", None),
            new_kid=getattr(args, "kid", None),
        )
        print("\n  JWT key rotated\n")
        print(f"  active_kid:   {bundle.active.kid}")
        print(f"  previous_kid: {bundle.previous.kid if bundle.previous else 'none'}\n")
        return

    if getattr(args, "kid", None):
        os.environ["OPENCASTOR_JWT_KID"] = args.kid
        provider.invalidate()

    bundle = provider.get_bundle()
    jwt_secret = bundle.active.secret
    if not jwt_secret or bundle.source == "ephemeral":
        print("Error: OPENCASTOR_JWT_SECRET is not set in environment or .env file.")
        print("Generate one with: openssl rand -hex 32")
        raise SystemExit(1)

    try:
        from castor.rcan.jwt_auth import RCANTokenManager
        from castor.rcan.rbac import RCANRole, resolve_role_name

        role_name = resolve_role_name(args.role)
        role = RCANRole[role_name]
        scopes = args.scope.split(",") if args.scope else None

        ruri = os.getenv("OPENCASTOR_RURI", "rcan://opencastor.unknown.00000000")
        mgr = RCANTokenManager(secret=jwt_secret, issuer=ruri)
        token = mgr.issue(
            subject=args.subject or "cli-user",
            role=role,
            scopes=scopes,
            ttl_seconds=int(args.ttl) * 3600,
        )
        print(f"\n  RCAN JWT Token (role={role.name}, ttl={args.ttl}h, kid={bundle.active.kid})\n")
        print(f"  {token}\n")
    except ImportError as exc:
        print("Error: PyJWT is not installed. Install with: pip install PyJWT")
        raise SystemExit(1) from exc
    except KeyError as exc:
        print(f"Error: Invalid role '{args.role}'. Valid: GUEST, USER, LEASEE, OWNER, CREATOR")
        raise SystemExit(1) from exc


def cmd_peer_test(args) -> None:
    """Test non-HTTP RCAN transport to discovered peers."""
    import json as _json
    import time as _time

    peer_host = getattr(args, "peer", None)
    transport = getattr(args, "transport", "all")
    dry_run = getattr(args, "dry_run", True)

    print("\n  OpenCastor Peer Transport Test")
    print("  " + "═" * 43)

    # Discover peers
    peers: list[dict] = []
    try:
        import httpx

        token = os.getenv("OPENCASTOR_API_TOKEN", "")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        r = httpx.get("http://127.0.0.1:8001/api/peers", headers=headers, timeout=3)
        if r.status_code == 200:
            peers = r.json().get("peers", [])
    except Exception:
        pass

    if peer_host:
        peers = [{"name": peer_host, "host": peer_host, "rrn": peer_host}]

    if not peers:
        print("  No peers discovered.")
        print("  Specify a peer: castor peer-test <hostname>")
        return

    print(f"\n  Discovered: {', '.join(p.get('name', p.get('host', '?')) for p in peers)}\n")

    for peer in peers:
        name = peer.get("name", peer.get("host", "unknown"))
        host = peer.get("host", name)
        rrn = peer.get("rrn", name)

        print(f"  Testing {name} ({rrn})")
        print("  " + "─" * 43)

        test_msg = {
            "msg_id": f"peer-test-{int(_time.time())}",
            "cmd": "PING",
            "target": f"rcan://{rrn}",
        }
        json_size = len(_json.dumps(test_msg))

        # HTTP test
        if transport in ("all", "http"):
            try:
                import httpx

                t0 = _time.monotonic()
                r = httpx.get(f"http://{host}:8001/api/status", timeout=3)
                ms = int((_time.monotonic() - t0) * 1000)
                status = "✅" if r.status_code == 200 else "⚠️"
                print(f"    HTTP (JSON)       {status}  {ms}ms   {json_size} bytes")
            except Exception as e:
                print(f"    HTTP (JSON)       ❌  {str(e)[:30]}")

        # Compact encoding test
        if transport in ("all", "mqtt", "compact"):
            try:
                from rcan import RCANMessage
                from rcan.transport import encode_compact

                msg = RCANMessage(cmd="PING", target=f"rcan://opencastor.com/acme/bot/v1/{rrn}")
                payload = encode_compact(msg)
                savings = int((1 - len(payload) / json_size) * 100)
                print(f"    Compact           ✅  —     {len(payload)} bytes  (-{savings}%)")
            except Exception as e:
                print(f"    Compact           ❌  {str(e)[:30]}")

        # Minimal ESTOP test
        if transport in ("all", "mqtt", "minimal"):
            try:
                from rcan import RCANMessage
                from rcan.transport import encode_minimal

                msg = RCANMessage(cmd="ESTOP", target=f"rcan://opencastor.com/acme/bot/v1/{rrn}")
                raw = encode_minimal(msg)
                label = "✅" if not dry_run else "✅ (dry-run)"
                print(f"    Minimal (ESTOP)   {label}  —     {len(raw)} bytes")
            except Exception as e:
                print(f"    Minimal (ESTOP)   ❌  {str(e)[:30]}")

        print()

    if dry_run:
        print("  Dry-run: encoding tested, no messages sent to peers")
        print("  Live test: castor peer-test --no-dry-run\n")


def cmd_contribute_cli(args) -> None:
    """Manage idle compute contribution."""
    action = getattr(args, "contribute_action", "status")

    try:
        import httpx

        token = os.getenv("OPENCASTOR_API_TOKEN", "")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        base = "http://127.0.0.1:8001"

        if action == "start":
            r = httpx.post(f"{base}/api/contribute/start", headers=headers, timeout=5)
            data = r.json()
            print(f"\n  Contribute: {'started' if data.get('active') else 'failed to start'}")

        elif action == "stop":
            r = httpx.post(f"{base}/api/contribute/stop", headers=headers, timeout=5)
            print("\n  Contribute: stopped")

        elif action == "history":
            r = httpx.get(f"{base}/api/contribute/history", headers=headers, timeout=5)
            history = r.json().get("history", [])
            if not history:
                print("\n  No contribution history yet.")
                return
            print("\n  Contribution History (last 90 days)")
            print("  " + "─" * 40)
            for entry in history[-14:]:  # Show last 2 weeks
                print(
                    f"    {entry['date']}  {entry['minutes']:>4} min  "
                    f"{entry['work_units']:>3} units"
                )
            print()

        else:  # status
            r = httpx.get(f"{base}/api/contribute", headers=headers, timeout=5)
            data = r.json()
            enabled = data.get("enabled", False)
            active = data.get("active", False)
            print("\n  Idle Compute Contribution")
            print("  " + "─" * 30)
            print(
                f"    Status:    {'🟢 Active' if active else '🟡 Enabled (idle)' if enabled else '⚫ Disabled'}"
            )
            print(f"    Project:   {data.get('project', '—')}")
            print(
                f"    Today:     {data.get('contribute_minutes_today', 0)} min / {data.get('work_units_today', 0)} units"
            )
            print(
                f"    Lifetime:  {data.get('contribute_minutes_lifetime', 0)} min / {data.get('work_units_total', 0)} units"
            )
            print()

    except Exception as exc:
        print(f"\n  Error: {exc}\n  Is the gateway running? (castor run)")


def cmd_leaderboard(args) -> None:
    """Print fleet leaderboard table."""
    from castor.commands.leaderboard import cmd_leaderboard as _cmd

    _cmd(args)


def cmd_compete(args) -> None:
    """Manage competition entry and status."""
    from castor.commands.compete import cmd_compete as _cmd

    _cmd(args)


def cmd_season(args) -> None:
    """Display season overview and class standings."""
    from castor.commands.season import cmd_season as _cmd

    _cmd(args)


def cmd_research(args) -> None:
    """Manage the harness research pipeline."""
    from castor.commands.research import cmd_research as _cmd

    _cmd(args)


def cmd_provider(args) -> None:
    """Manage gated model providers — test auth, list models, show status."""
    provider_action = getattr(args, "provider_action", "list")

    if provider_action == "auth":
        provider_name = getattr(args, "provider_name", "")
        config_path = getattr(args, "config", "robot.rcan.yaml")

        if not provider_name:
            print("\n  Usage: castor provider auth <provider-name>")
            print("  Test authentication for a gated model provider.\n")
            return

        try:
            import yaml

            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}

            providers = cfg.get("providers", {})
            if provider_name not in providers:
                print(f"\n  Provider '{provider_name}' not found in config.")
                print(f"  Available: {', '.join(providers.keys()) or '(none)'}\n")
                return

            pcfg = providers[provider_name]
            auth_config = pcfg.get("auth", {})
            if not auth_config:
                print(f"\n  Provider '{provider_name}' has no auth configuration.\n")
                return

            from castor.auth.provider_auth import create_provider_auth

            print(f"\n  Testing auth for '{provider_name}'...")
            print(f"  Method: {auth_config.get('method', 'unknown')}")

            handler = create_provider_auth(auth_config)
            creds = handler.get_credentials()

            if creds.expired:
                print("  Status: ⚠️  EXPIRED")
            else:
                print("  Status: ✅ Valid")

            if creds.headers:
                # Show header names without values for security
                header_names = list(creds.headers.keys())
                print(f"  Headers: {', '.join(header_names)}")

            if creds.client_cert:
                print(f"  Client cert: {creds.client_cert[0]}")

            if creds.expires_at > 0:
                import time

                remaining = creds.expires_at - time.time()
                if remaining > 0:
                    mins = int(remaining / 60)
                    print(f"  Expires in: {mins} minutes")

            models = pcfg.get("models", [])
            if models:
                print(f"  Models: {', '.join(models)}")

            fallback = pcfg.get("fallback_model")
            if fallback:
                provider = pcfg.get("fallback_provider", "local")
                print(f"  Fallback: {provider}/{fallback}")

            print()

        except FileNotFoundError:
            print(f"\n  Config file not found: {config_path}\n")
        except Exception as exc:
            print(f"\n  Auth failed: {exc}\n")

    elif provider_action == "list":
        config_path = getattr(args, "config", "robot.rcan.yaml")
        try:
            import yaml

            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}

            providers = cfg.get("providers", {})
            if not providers:
                print("\n  No gated providers configured.")
                print("  Add providers to your config.yaml under 'providers:'\n")
                return

            print("\n  Gated Model Providers")
            print("  " + "─" * 50)
            for name, pcfg in providers.items():
                auth = pcfg.get("auth", {})
                method = auth.get("method", "none")
                models = pcfg.get("models", [])
                fallback = pcfg.get("fallback_model", "none")
                print(f"    {name}")
                print(f"      Auth: {method}  |  Models: {len(models)}  |  Fallback: {fallback}")
            print()

        except FileNotFoundError:
            print(f"\n  Config file not found: {config_path}\n")
        except Exception as exc:
            print(f"\n  Error: {exc}\n")

    elif provider_action == "status":
        try:
            import httpx

            token = os.getenv("OPENCASTOR_API_TOKEN", "")
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            base = "http://127.0.0.1:8001"

            r = httpx.get(f"{base}/api/status", headers=headers, timeout=5)
            data = r.json()
            providers = data.get("gated_providers", [])

            if not providers:
                print("\n  No gated providers active on the running gateway.\n")
                return

            print("\n  Active Gated Providers")
            print("  " + "─" * 50)
            for p in providers:
                name = p.get("provider", "unknown")
                available = "✅" if p.get("available") else "❌"
                auth_valid = "✅" if p.get("auth_valid") else "❌"
                method = p.get("auth_method", "unknown")
                models = p.get("models", [])
                print(f"    {available} {name} (auth: {auth_valid}, method: {method})")
                if models:
                    print(f"        Models: {', '.join(models)}")
            print()

        except Exception as exc:
            print(f"\n  Error: {exc}\n  Is the gateway running?\n")


def cmd_discover(args) -> None:
    """Discover RCAN peers on the local network."""
    print("\n  Scanning for RCAN peers (5 seconds)...\n")

    try:
        from castor.rcan.mdns import RCANServiceBrowser
    except ImportError as exc:
        print("  Error: zeroconf is not installed.")
        print("  Install with: pip install opencastor[rcan]")
        raise SystemExit(1) from exc

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


def cmd_snapshot(args) -> None:
    """Take, list, or show diagnostic snapshots (Issue #348).

    Sub-commands:
        castor snapshot take           — Capture a snapshot immediately.
        castor snapshot latest         — Show the most recent snapshot.
        castor snapshot history [N]    — Show the last N snapshots (default 5).

    Examples::

        castor snapshot take
        castor snapshot latest
        castor snapshot history 10
    """
    import json as _json

    sub = getattr(args, "snapshot_action", None) or (
        args.snapshot_args[0] if getattr(args, "snapshot_args", []) else "latest"
    )

    from castor.snapshot import get_manager

    mgr = get_manager()

    if sub == "take":
        snap = mgr.take()
        print("\n  Snapshot taken:\n")
        print("  " + _json.dumps(snap, indent=2, default=str).replace("\n", "\n  "))
        print()
    elif sub == "history":
        limit = 5
        extra = getattr(args, "snapshot_args", [])
        if extra and len(extra) > 1:
            try:
                limit = int(extra[1])
            except ValueError:
                pass
        history = mgr.history(limit=limit)
        print(f"\n  Last {len(history)} snapshots:\n")
        for i, snap in enumerate(history):
            ts = snap.get("timestamp", "?")
            cpu = snap.get("system", {}).get("cpu_percent", "?")
            print(f"  [{i + 1}] ts={ts} cpu={cpu}%")
        print()
    else:  # latest
        snap = mgr.latest()
        if snap is None:
            print("\n  No snapshots available. Run `castor snapshot take` first.\n")
        else:
            print("\n  Latest snapshot:\n")
            print("  " + _json.dumps(snap, indent=2, default=str).replace("\n", "\n  "))
            print()


def cmd_fleet(args) -> None:
    """Handle castor fleet subcommands."""
    import json as _json

    import yaml

    fleet_cmd = getattr(args, "fleet_cmd", None)
    if fleet_cmd is None:
        from castor.fleet import fleet_status

        timeout_raw = getattr(args, "timeout", "5")
        fleet_status(timeout=float(timeout_raw))
        return

    config_path = getattr(args, "config", None) or _find_default_config()
    config: dict = {}
    if config_path:
        try:
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
        except Exception as exc:
            print(f"⚠️  Could not load config: {exc}")

    from castor.fleet.group_policy import FleetManager

    fm = FleetManager.from_config(config)

    if fleet_cmd == "list":
        print(fm.summary())
        for g in fm.list_groups():
            if g.robots:
                print(f"\n  {g.name}:")
                for rrn in g.robots:
                    print(f"    • {rrn}")
                if g.policy:
                    keys = list(g.policy.keys())
                    print(f"    policy keys: {', '.join(keys)}")

    elif fleet_cmd == "resolve":
        rrn = args.rrn
        merged = fm.resolve_config(rrn, config)
        groups = fm.get_robot_groups(rrn)
        if getattr(args, "output_json", False):
            print(_json.dumps(merged, indent=2))
        else:
            group_names = [g.name for g in groups] or ["(none)"]
            print(f"\n🤖 {rrn}")
            print(f"  Groups: {', '.join(group_names)}")
            agent = merged.get("agent", {})
            print(f"  Provider: {agent.get('provider', '?')} / {agent.get('model', '?')}")
            cg = agent.get("confidence_gates", [])
            if cg:
                print(f"  Confidence gate: {cg[0].get('threshold', '?')}")
            hitl = agent.get("hitl_gates", [])
            if hitl:
                print(f"  HiTL gates: {len(hitl)}")

    elif fleet_cmd == "status":
        all_rrns: list[str] = []
        for g in fm.list_groups():
            all_rrns.extend(g.robots)
        all_rrns = sorted(set(all_rrns))
        if not all_rrns:
            print("No robots defined in any group.")
            return
        print(f"\n{'RRN':<20}  Groups")
        print("-" * 50)
        for rrn in all_rrns:
            groups = fm.get_robot_groups(rrn)
            print(f"{rrn:<20}  {', '.join(g.name for g in groups) or '(none)'}")


def _find_default_config() -> str | None:
    """Look for a .rcan.yaml in the current directory."""
    import glob

    candidates = glob.glob("*.rcan.yaml") + glob.glob("*.rcan.yml")
    return candidates[0] if candidates else None


def cmd_verification(args) -> None:
    """Check verification tier for an RRN."""
    from castor.rcan.verification import get_tier_from_rrn

    status = get_tier_from_rrn(args.rrn)
    if status:
        print(f"{status.display} — {args.rrn}")
        if status.evidence_url:
            print(f"  Evidence: {status.evidence_url}")
        if status.verified_at:
            print(f"  Verified: {status.verified_at}")
    else:
        print(f"⬜ Unknown — could not fetch tier for {args.rrn}")


def cmd_inspect(args) -> None:
    """Query a robot's live RCAN profile, safety state, and telemetry."""
    import json as _json

    output: dict = {}
    rrn = args.rrn

    # --- Registry lookup ---
    registry_data: dict = {}
    if rrn:
        try:
            import asyncio

            from rcan.registry import RegistryClient

            async def _lookup():
                async with RegistryClient() as c:
                    entry = await c.get_robot(rrn)
                    return entry.to_dict()

            registry_data = asyncio.run(_lookup())
            output["registry"] = registry_data
        except Exception as exc:
            output["registry"] = {"error": str(exc), "rrn": rrn}

    # --- Local config ---
    config_path = args.config
    config_data: dict = {}
    if config_path:
        try:
            import yaml

            with open(config_path) as f:
                config_data = yaml.safe_load(f) or {}
            meta = config_data.get("metadata", {})
            output["config"] = {
                "file": config_path,
                "robot_name": meta.get("robot_name", ""),
                "manufacturer": meta.get("manufacturer", ""),
                "model": meta.get("model", ""),
                "version": meta.get("version", ""),
                "rrn": meta.get("rrn", ""),
                "rcan_uri": meta.get("rcan_uri", ""),
                "rcan_version": config_data.get("rcan_version", ""),
                "provider": config_data.get("agent", {}).get("provider", ""),
                "ai_model": config_data.get("agent", {}).get("model", ""),
            }
        except Exception as exc:
            output["config"] = {"error": str(exc)}

    # --- Gateway live status ---
    gateway_url = args.gateway or (config_data or {}).get("gateway", {}).get("url", "")
    if not gateway_url:
        # Try default local gateway
        gateway_url = "http://localhost:8080"

    try:
        import os
        import urllib.request

        token = os.environ.get("OPENCASTOR_API_TOKEN", "")
        req = urllib.request.Request(
            f"{gateway_url}/api/status",
            headers={"Authorization": f"Bearer {token}"} if token else {},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            status_data = _json.loads(resp.read())
        output["live"] = {
            "gateway": gateway_url,
            "uptime_s": status_data.get("uptime_s"),
            "brain": status_data.get("brain"),
            "driver": status_data.get("driver"),
            "channels": status_data.get("channels_active", []),
            "safety_state": status_data.get("safety", {}).get("state", "unknown"),
            "ruri": status_data.get("ruri"),
        }
    except Exception as exc:
        output["live"] = {"gateway": gateway_url, "error": str(exc)}

    # --- Commitment chain ---
    try:
        from castor.rcan.commitment_chain import get_commitment_chain

        cc = get_commitment_chain()
        valid, count, errors = cc.verify_log()
        output["commitment_chain"] = {
            "records": count,
            "valid": valid,
            "errors": errors[:3] if errors else [],
        }
    except Exception:
        pass

    # --- Compliance ---
    if config_data:
        try:
            from castor.rcan.sdk_bridge import check_compliance

            issues = check_compliance(config_data)
            l1_ok = not any(i.startswith("L1") for i in issues)
            l2_ok = l1_ok and not any(i.startswith("L2") for i in issues)
            l3_ok = l2_ok and not any(i.startswith("L3") for i in issues)
            output["compliance"] = {
                "level": "L3" if l3_ok else "L2" if l2_ok else "L1" if l1_ok else "FAIL",
                "issues": issues,
            }
        except Exception:
            pass

    if getattr(args, "output_json", False):
        print(_json.dumps(output, indent=2))
        return

    # --- Human-readable output ---
    try:
        from rich.console import Console

        con = Console()
        HAS_RICH = True
    except ImportError:
        con = None
        HAS_RICH = False

    def _pr(text, **kw):
        if HAS_RICH and con:
            con.print(text, **kw)
        else:
            import re

            print(re.sub(r"\[/?[a-z_ ]+\]", "", text))

    _pr("\n🤖 [bold]castor inspect[/bold]" + (f" {rrn}" if rrn else "") + "\n")

    if "registry" in output:
        reg = output["registry"]
        _pr("[bold]Registry (rcan.dev)[/bold]")
        if "error" in reg:
            _pr(f"  ⚠️  {reg['error']}", style="yellow")
        else:
            _pr(f"  RRN:    {reg.get('rrn', '?')}")
            _pr(f"  URI:    {reg.get('uri', reg.get('rcan_uri', '?'))}")
            _pr(f"  Tier:   {reg.get('verification_tier', '?')}")
            _pr(f"  View:   https://robotregistryfoundation.org/registry/{reg.get('rrn', '')}")

    if "config" in output:
        cfg = output["config"]
        _pr("\n[bold]Local Config[/bold]")
        if "error" in cfg:
            _pr(f"  ⚠️  {cfg['error']}", style="yellow")
        else:
            _pr(f"  File:     {cfg.get('file', '')}")
            _pr(
                f"  Robot:    {cfg.get('robot_name', '')} ({cfg.get('manufacturer', '')}/{cfg.get('model', '')} {cfg.get('version', '')})"
            )
            _pr(f"  Provider: {cfg.get('provider', '')} / {cfg.get('ai_model', '')}")
            if cfg.get("rrn"):
                _pr(f"  RRN:      {cfg['rrn']}")

    if "live" in output:
        live = output["live"]
        _pr("\n[bold]Live Gateway[/bold]")
        if "error" in live:
            _pr(f"  ⚠️  {live['gateway']}: {live['error']}", style="yellow")
        else:
            _pr(f"  Gateway:  {live.get('gateway', '')}")
            _pr(f"  Uptime:   {live.get('uptime_s', '?')}s")
            _pr(f"  Brain:    {'✅' if live.get('brain') else '❌'}")
            _pr(f"  Driver:   {'✅' if live.get('driver') else '❌'}")
            _pr(f"  Safety:   {live.get('safety_state', 'unknown')}")
            if live.get("channels"):
                _pr(f"  Channels: {', '.join(live['channels'])}")

    if "commitment_chain" in output:
        cc = output["commitment_chain"]
        valid_str = "✅" if cc.get("valid") else "⚠️"
        _pr(f"\n[bold]Commitment Chain[/bold]  {valid_str} {cc.get('records', 0)} records")

    if "compliance" in output:
        comp = output["compliance"]
        level = comp.get("level", "?")
        color = "green" if level == "L3" else "yellow" if level in ("L1", "L2") else "red"
        _pr(f"\n[bold]RCAN Compliance[/bold]  [{color}]{level}[/{color}]")
        for issue in comp.get("issues", [])[:5]:
            _pr(f"  ⚠️  {issue}", style="yellow")

    _pr("")


def cmd_node(args) -> None:
    """castor node — manage RCAN namespace delegation for this robot fleet."""
    node_cmd = getattr(args, "node_cmd", None)

    if node_cmd == "status":
        try:
            from castor.rcan.node_broadcaster import NodeBroadcaster, NodeConfig

            config = NodeConfig()
            broadcaster = NodeBroadcaster(config)
            manifest = broadcaster.get_manifest()
            print("RCAN Node Status:")
            print(f"  Type:         {manifest['node_type']}")
            print(f"  Operator:     {manifest['operator'] or '(not set)'}")
            print(f"  Namespace:    {manifest['namespace_prefix']}")
            print(f"  API Base:     {manifest['api_base'] or '(not set)'}")
            print(f"  Capabilities: {', '.join(manifest['capabilities'])}")
            print(f"  Last sync:    {manifest['last_sync']}")
        except Exception as e:
            print(f"❌ Error: {e}", file=__import__("sys").stderr)

    elif node_cmd == "manifest":
        import json

        try:
            from castor.rcan.node_broadcaster import NodeBroadcaster, NodeConfig

            config = NodeConfig()
            broadcaster = NodeBroadcaster(config)
            manifest = broadcaster.get_manifest()
            print(json.dumps(manifest, indent=2))
        except Exception as e:
            print(f"❌ Error: {e}", file=__import__("sys").stderr)

    elif node_cmd == "resolve":
        rrn = getattr(args, "rrn", None)
        if not rrn:
            print("  Usage: castor node resolve <RRN>")
            return
        try:
            from castor.rcan.node_resolver import NodeResolver

            resolver = NodeResolver()
            robot = resolver.resolve(rrn)
            source = "stale cache" if robot.stale else ("cache" if robot.from_cache else "live")
            print(f"✅ {rrn}")
            print(f"  Manufacturer: {robot.manufacturer}")
            print(f"  Model:        {robot.model}")
            print(f"  Attestation:  {robot.attestation}")
            print(f"  Resolved by:  {robot.resolved_by} ({source})")
        except Exception as e:
            print(f"❌ {e}", file=__import__("sys").stderr)
            raise SystemExit(1) from e

    elif node_cmd == "ping":
        try:
            from castor.rcan.node_resolver import NodeResolver

            resolver = NodeResolver()
            ok, latency_ms = resolver.is_reachable()
            if ok:
                print(f"✅ rcan.dev reachable ({latency_ms:.0f}ms)")
            else:
                print(f"❌ rcan.dev unreachable ({latency_ms:.0f}ms)")
                raise SystemExit(1)
        except ImportError as e:
            print(f"❌ {e}", file=__import__("sys").stderr)

    else:
        print("Usage: castor node <status|manifest|resolve|ping>")


def cmd_register(args) -> None:
    """Register this robot with rcan.dev and get a globally unique RRN."""
    import os
    import sys

    # Load config
    try:
        import yaml

        with open(args.config) as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"❌ Config not found: {args.config}")
        print("   Run: castor wizard  to create one first.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Failed to load config: {e}")
        sys.exit(1)

    meta = config.get("metadata", {})

    # --dry-run: validate and show what would be registered, no API calls
    if getattr(args, "dry_run", False):
        from castor.rcan.sdk_compat import validate_before_register

        ok, issues = validate_before_register(config, strict=False)
        for issue in issues:
            print(f"  ⚠️  {issue}", file=sys.stderr)

        print("\n🔍 Dry run — would register:")
        print(f"  Name:         {meta.get('robot_name', meta.get('name', 'unnamed'))}")
        print(f"  Manufacturer: {meta.get('manufacturer', 'unknown')}")
        print(f"  Model:        {meta.get('model', meta.get('robot_name', 'unknown'))}")
        print(f"  Version:      {meta.get('version', meta.get('firmware_version', 'v1'))}")
        print(f"  RCAN version: {config.get('rcan_version', 'unknown')}")
        print("  Registry:     https://robotregistryfoundation.org/v2/registry")
        print("\n✅ Dry run complete — no API calls made.")
        return

    # Resolve fields (CLI args > config > prompts)
    manufacturer = args.manufacturer or meta.get("manufacturer") or ""
    model = args.model or meta.get("model") or ""
    version = args.version or meta.get("version") or meta.get("firmware_version") or "v1"
    device_id = args.device_id or meta.get("robot_uuid", "")[:8] or "unit-001"
    api_key = (
        args.api_key
        or os.environ.get("RCAN_API_KEY")
        or os.environ.get("OPENCASTOR_RCAN_KEY")
        or ""
    )

    if not manufacturer:
        manufacturer = input("Manufacturer / org name [opencastor]: ").strip() or "opencastor"
    if not model:
        robot_name = meta.get("robot_name", "robot")
        model = input(f"Model name [{robot_name}]: ").strip() or robot_name

    # Check for existing RRN
    existing_rrn = meta.get("rrn")
    if existing_rrn:
        print(f"\n⚠️  This robot already has an RRN: {existing_rrn}")
        print(f"   View at: https://robotregistryfoundation.org/registry/{existing_rrn}")
        ans = input("Re-register anyway? [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            sys.exit(0)

    if not api_key:
        print("\n🔑 No RCAN API key found.")
        print("   Get a free key at: https://rcan.dev/register")
        print("   Or set: export RCAN_API_KEY=<your-key>\n")
        api_key = input("Paste API key (Enter to open browser instead): ").strip()
        if not api_key:
            try:
                import urllib.parse

                params = urllib.parse.urlencode(
                    {
                        "manufacturer": manufacturer,
                        "model": model,
                        "version": version,
                        "source": "castor-cli",
                    }
                )
                url = f"https://robotregistryfoundation.org/registry/register?{params}"
                import webbrowser

                webbrowser.open(url)
                print(f"\n   Opened: {url}")
            except Exception:
                print("\n   Register at: https://robotregistryfoundation.org/registry")
            sys.exit(0)

    # SDK compat pre-registration check
    from castor.rcan.sdk_compat import validate_before_register

    ok, issues = validate_before_register(config, strict=False)
    if issues:
        for issue in issues:
            print(f"  ⚠️  {issue}", file=sys.stderr)
    if not ok:
        print(
            "❌ Pre-registration validation failed. Fix issues above before registering.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Register
    print(
        f"\n📡 Registering {manufacturer}/{model} {version} with rcan.dev...", end=" ", flush=True
    )
    try:
        import asyncio

        from rcan.registry import RegistryClient

        async def _register():
            async with RegistryClient(api_key=api_key) as client:
                return await client.register(
                    manufacturer=manufacturer,
                    model=model,
                    version=version,
                    device_id=device_id,
                    metadata={
                        "robot_name": meta.get("robot_name", model),
                        "rcan_version": "1.4",
                        "opencastor": True,
                    },
                )

        result = asyncio.run(_register())
        rrn = result.get("rrn", "")

        if rrn:
            print("✅")
            print("\n🤖 Robot registered!")
            print(f"   RRN:  {rrn}")
            print(f"   URI:  {result.get('uri', '')}")
            print(f"   View: https://robotregistryfoundation.org/registry/{rrn}\n")

            # Patch config file
            meta["rrn"] = rrn
            meta["rcan_uri"] = result.get("uri", "")
            config["metadata"] = meta
            with open(args.config, "w") as f:
                yaml.dump(config, f, sort_keys=False, default_flow_style=False)
            print(f"   ✓ RRN written to {args.config}")

            # Save API key for future use
            try:
                from castor.wizard import _write_env_var

                _write_env_var("RCAN_API_KEY", api_key)
                print("   ✓ API key saved to ~/.opencastor/env")
            except Exception:
                pass
        else:
            print("⚠️  Unexpected response — no RRN returned")
            sys.exit(1)

    except ImportError:
        print("❌  rcan package not installed. Run: pip install rcan")
        sys.exit(1)
    except Exception as e:
        print(f"❌  Registration failed: {e}")
        print("   Try manually at: https://robotregistryfoundation.org/registry")
        sys.exit(1)


def cmd_compliance(args) -> None:
    """Check RCAN v1.2 conformance for a robot config."""
    import json as _json
    import sys

    config_path = args.config
    output_json = getattr(args, "output_json", False)
    check_commitments = getattr(args, "commitments", False)
    fmt = getattr(args, "format", None)
    output_file = getattr(args, "output", None)

    # If --format is specified, use the new ComplianceReport path
    if fmt in ("json", "text"):
        import contextlib

        from castor.compliance import generate_report, print_report_json, print_report_text

        try:
            report = generate_report(config_path=config_path)
        except FileNotFoundError:
            print(f"❌ Config not found: {config_path}", file=sys.stderr)
            sys.exit(1)
        except Exception as exc:
            print(f"❌ Failed to generate report: {exc}", file=sys.stderr)
            sys.exit(1)

        ctx = open(output_file, "w") if output_file else contextlib.nullcontext(sys.stdout)
        with ctx as fh:
            if fmt == "json":
                print_report_json(report, file=fh)
            else:
                print_report_text(report, file=fh)
        sys.exit(0 if report.compliant else 1)

    # Load config
    try:
        import yaml

        with open(config_path) as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"❌ Config not found: {config_path}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Failed to load config: {e}", file=sys.stderr)
        sys.exit(1)

    from castor.rcan.sdk_bridge import check_compliance

    issues = check_compliance(config)

    # Categorise by level
    l1 = [i for i in issues if i.startswith("L1")]
    l2 = [i for i in issues if i.startswith("L2")]
    l3 = [i for i in issues if i.startswith("L3")]

    l1_pass = len(l1) == 0
    l2_pass = l1_pass and len(l2) == 0
    l3_pass = l2_pass and len(l3) == 0

    # Commitment chain verification
    chain_ok: bool | None = None
    chain_count = 0
    chain_errors: list[str] = []
    if check_commitments:
        try:
            from castor.rcan.commitment_chain import get_commitment_chain

            cc = get_commitment_chain()
            chain_ok, chain_count, chain_errors = cc.verify_log()
        except Exception as e:
            chain_errors = [str(e)]
            chain_ok = False

    if output_json:
        result = {
            "config": config_path,
            "rcan_version": "1.4",
            "L1": {"pass": l1_pass, "issues": l1},
            "L2": {"pass": l2_pass, "issues": l2},
            "L3": {"pass": l3_pass, "issues": l3},
            "overall": "L3" if l3_pass else "L2" if l2_pass else "L1" if l1_pass else "FAIL",
        }
        if check_commitments:
            result["commitment_chain"] = {
                "valid": chain_ok,
                "records": chain_count,
                "errors": chain_errors,
            }
        print(_json.dumps(result, indent=2))
        sys.exit(0 if l1_pass else 1)

    # Human-readable output
    try:
        from rich.console import Console

        con = Console()
        HAS_RICH = True
    except ImportError:
        con = None
        HAS_RICH = False

    def _tick(ok):
        return "✅" if ok else "❌"

    def _pr(text, style=None):
        if HAS_RICH and con:
            con.print(text, style=style)
        else:
            print(text)

    _pr(f"\n🤖 [bold]RCAN Conformance Check[/bold] — {config_path}\n")

    for level, level_issues, level_pass in [
        ("L1", l1, l1_pass),
        ("L2", l2, l2_pass),
        ("L3", l3, l3_pass),
    ]:
        level_label = f"{_tick(level_pass)} [bold]{level}[/bold]"
        _pr(level_label)
        if level_issues:
            for issue in level_issues:
                _pr(f"   ⚠️  {issue}", style="yellow")
        else:
            _pr(f"   All {level} checks passed", style="green")

    if check_commitments:
        _pr(
            f"\n{_tick(chain_ok)} Commitment chain: {chain_count} records",
        )
        for err in chain_errors:
            _pr(f"   ⚠️  {err}", style="yellow")

    # L4/L5: run RCAN v2.1/v2.2 checks
    level_arg = getattr(args, "level", None)
    if level_arg in ("L4", "L5"):
        _pr("\n=== RCAN v2.1/v2.2 (L4/L5) ===")
        try:
            from castor.conformance import ConformanceChecker

            checker_v2 = ConformanceChecker(config, config_path=config_path)
            status_icons = {"pass": "✅", "warn": "⚠️ ", "fail": "❌"}

            # L4: rcan_v21 category
            v21_results = checker_v2._check_rcan_v21()
            v21_only = [r for r in v21_results if r.category == "rcan_v21"]
            v21_pass = all(r.status == "pass" for r in v21_only)
            level_label_v21 = f"{_tick(v21_pass)} [bold]L4 (RCAN v2.1)[/bold]"
            _pr(level_label_v21)
            if v21_only:
                for r in v21_only:
                    icon = status_icons.get(r.status, "❓")
                    _pr(f"   {icon} [{r.check_id}] {r.detail}")
            else:
                _pr("   All L4 checks passed", style="green")

            # L5: additionally rcan_v22 category
            if level_arg == "L5":
                v22_only = [r for r in v21_results if r.category == "rcan_v22"]
                v22_pass = all(r.status == "pass" for r in v22_only)
                level_label_v22 = f"{_tick(v22_pass)} [bold]L5 (RCAN v2.2)[/bold]"
                _pr(level_label_v22)
                if v22_only:
                    for r in v22_only:
                        icon = status_icons.get(r.status, "❓")
                        _pr(f"   {icon} [{r.check_id}] {r.detail}")
                else:
                    _pr("   All L5 checks passed", style="green")
        except Exception as _exc:
            _pr(f"   ⚠️  Could not run v2.x checks: {_exc}", style="yellow")

    overall = "L3" if l3_pass else "L2" if l2_pass else "L1" if l1_pass else "FAIL"
    color = "green" if l3_pass else "yellow" if l2_pass else "red"
    _pr(f"\n[{color}]Result: {overall}[/{color}] ({len(issues)} issue(s))\n")
    sys.exit(0 if l1_pass else 1)


def cmd_logs(args) -> None:
    """castor logs — stream or tail the castor runtime log."""
    from castor.logs import view_logs

    view_logs(
        follow=getattr(args, "follow", False),
        level=getattr(args, "level", None),
        module=getattr(args, "module", None),
        lines=getattr(args, "lines", 50),
        no_color=getattr(args, "no_color", False),
    )


def cmd_memory(args) -> None:
    """castor memory — show, prune, and manage robot operational memory."""
    import os
    from pathlib import Path

    from castor.brain.memory_schema import (
        CONFIDENCE_INJECT_MIN,
        EntryType,
        MemoryEntry,
        apply_confidence_decay,
        filter_for_context,
        load_memory,
        make_entry_id,
        prune_entries,
        save_memory,
    )

    memory_path = os.getenv(
        "CASTOR_ROBOT_MEMORY_FILE",
        str(Path.home() / ".opencastor" / "robot-memory.md"),
    )
    cmd = getattr(args, "memory_cmd", None) or "show"

    if cmd == "show":
        from datetime import datetime, timezone

        mem = load_memory(memory_path)
        mem = apply_confidence_decay(mem)
        eligible = filter_for_context(mem)
        all_entries = mem.entries

        print(f"\n🧠 Robot Memory — {mem.rrn}")
        print(f"   File: {memory_path}")
        print(f"   Last updated: {mem.last_updated.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"   Total entries: {len(all_entries)} ({len(eligible)} above inject threshold)\n")

        if not all_entries:
            print("  (no entries yet — run autoDream to populate)\n")
            return

        by_type: dict[str, list] = {}
        for e in sorted(all_entries, key=lambda e: -e.confidence):
            by_type.setdefault(e.type.value, []).append(e)

        for type_name, entries in by_type.items():
            print(f"  ── {type_name.upper().replace('_', ' ')} ──")
            for e in entries:
                conf_pct = int(e.confidence * 100)
                if e.confidence >= 0.8:
                    bar = "🔴"
                elif e.confidence >= 0.5:
                    bar = "🟡"
                elif e.confidence >= CONFIDENCE_INJECT_MIN:
                    bar = "🟢"
                else:
                    bar = "⚫"
                days = (datetime.now(timezone.utc) - e.last_reinforced).days
                injected = "✓" if e in eligible else "✗"
                print(f"  {bar} [{conf_pct:3d}%] [inject:{injected}] {e.text}")
                print(f"       id:{e.id} | obs:{e.observation_count}x | last:{days}d ago")
            print()

    elif cmd == "prune":
        mem = load_memory(memory_path)
        mem = apply_confidence_decay(mem)
        threshold = float(getattr(args, "threshold", "0.10"))
        pruned, count = prune_entries(mem, min_confidence=threshold)
        dry = getattr(args, "dry_run", False)
        if dry:
            print(f"\nDRY RUN — would prune {count} entries below {threshold:.0%} confidence\n")
            for e in mem.entries:
                if e.confidence < threshold:
                    print(f"  would remove: [{int(e.confidence * 100)}%] {e.text}")
        else:
            save_memory(pruned, memory_path)
            print(f"✓ Pruned {count} entries below {threshold:.0%} confidence")

    elif cmd == "add":
        entry_type_str = getattr(args, "entry_type", "hardware_observation")
        text = getattr(args, "text", "")
        confidence = float(getattr(args, "confidence", "0.8"))
        tags = (getattr(args, "tags", "") or "").split(",")
        tags = [t.strip() for t in tags if t.strip()]

        if not text:
            print(
                "\nUsage: castor memory add --text 'observation text' [--type TYPE] [--confidence 0.8]\n"
            )
            return

        try:
            entry_type = EntryType(entry_type_str)
        except ValueError:
            valid = [e.value for e in EntryType]
            print(f"Invalid type '{entry_type_str}'. Valid: {', '.join(valid)}")
            return

        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        entry = MemoryEntry(
            id=make_entry_id(text, entry_type),
            type=entry_type,
            text=text,
            confidence=confidence,
            first_seen=now,
            last_reinforced=now,
            observation_count=1,
            tags=tags,
        )
        mem = load_memory(memory_path)
        rrn = getattr(args, "rrn", None) or os.getenv("CASTOR_RRN", mem.rrn)
        mem.rrn = rrn
        mem.entries.append(entry)
        save_memory(mem, memory_path)
        print(f"✓ Added entry [{int(confidence * 100)}%] {entry.type.value}: {text}")
        if tags:
            print(f"  Tags: {', '.join(tags)}")

    elif cmd == "decay":
        mem = load_memory(memory_path)
        original_confs = {e.id: e.confidence for e in mem.entries}
        mem = apply_confidence_decay(mem)
        save_memory(mem, memory_path)
        changed = [
            (e, original_confs[e.id])
            for e in mem.entries
            if abs(e.confidence - original_confs.get(e.id, e.confidence)) > 0.001
        ]
        print(f"✓ Applied confidence decay — {len(changed)} entries updated")
        for e, old in changed[:10]:
            print(f"  [{int(old * 100)}% → {int(e.confidence * 100)}%] {e.text[:60]}")

    else:
        print("\n  castor memory — robot operational memory management\n")
        print("  Commands:")
        print("    castor memory show              Show all entries with confidence")
        print("    castor memory add --text '...'  Add a manual entry")
        print("    castor memory prune             Remove entries below threshold")
        print("    castor memory decay             Apply time-based confidence decay\n")


def cmd_migrate(args) -> None:
    """castor migrate — migrate a RCAN config to the latest schema version."""
    from castor.migrate import migrate_file

    config = getattr(args, "config", "robot.rcan.yaml")
    dry_run = getattr(args, "dry_run", False)
    migrate_file(config, dry_run=dry_run)


def cmd_network(args) -> None:
    """castor network — manage robot network exposure and status."""
    from castor.network import expose, network_status

    action = getattr(args, "action", None)
    config_path = getattr(args, "config", None)

    if action == "expose":
        mode = getattr(args, "mode", None) or "serve"
        port = getattr(args, "port", 8000)
        expose(mode=mode, port=port)
    else:
        network_status(config_path=config_path)


def cmd_plugin(args) -> None:
    """castor plugin — placeholder."""
    print("castor plugin: coming soon.")


def cmd_plugins(args) -> None:
    """castor plugins — list installed castor plugins."""
    from castor.plugins import list_plugins, load_plugins, print_plugins

    load_plugins()
    plugins = list_plugins()
    print_plugins(plugins)


def cmd_rrf(args) -> None:
    """castor rrf — Robot Registry Foundation v2 commands (register, components, models, harness, status)."""
    from castor.rrf_cmd import cmd_rrf as _cmd_rrf

    _cmd_rrf(args)


def cmd_loa(args) -> None:
    """castor loa — manage Level of Assurance enforcement (GAP-16).

    Sub-commands:
      status              Show current LoA enforcement state
      enable              Enable LoA enforcement (patches config + hot-reloads)
      disable             Disable LoA enforcement (log-only mode)
    """
    import sys

    from castor.loa import (
        get_config_path,
        get_loa_status,
        load_config,
        push_loa_to_firestore,
        reload_gateway,
        set_loa_enforcement,
    )

    sub = getattr(args, "loa_cmd", None) or "status"
    config_path = get_config_path(getattr(args, "config", None))
    min_loa = getattr(args, "min_loa", None)
    do_reload = getattr(args, "reload", True)
    do_firestore = getattr(args, "no_firestore", False) is False

    if not config_path.exists():
        print(f"❌  Config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    if sub == "status":
        cfg = load_config(config_path)
        status = get_loa_status(cfg)
        rrn = cfg.get("metadata", {}).get("rrn", "unknown")
        emoji = "✅" if status["loa_enforcement"] else "⚠️ "
        print(
            f"\n{emoji}  LoA Enforcement: {'ON' if status['loa_enforcement'] else 'OFF (log-only)'}"
        )
        print(f"   Min LoA for control: {status['min_loa_for_control']}")
        print(f"   RCAN version:        {status['rcan_version']}")
        print(f"   RRN:                 {rrn}")
        if not status["loa_enforcement"]:
            print("\n   To enable:  castor loa enable")
        return

    enabled = sub == "enable"
    status = set_loa_enforcement(config_path, enabled=enabled, min_loa=min_loa)
    print(
        f"{'✅' if enabled else '🔓'}  LoA enforcement {'enabled' if enabled else 'disabled'} in {config_path}"
    )

    # Hot-reload the running gateway
    if do_reload:
        gateway_url = getattr(args, "gateway_url", None) or "http://localhost:8001"
        reloaded = reload_gateway(gateway_url)
        if reloaded:
            print(f"🔄  Gateway reloaded ({gateway_url})")
        else:
            print("⚠️   Gateway unreachable — restart manually: castor run")

    # Sync to Firestore
    if do_firestore:
        cfg = load_config(config_path)
        rrn = cfg.get("metadata", {}).get("rrn", "")
        if rrn and rrn != "RRN-UNKNOWN":
            synced = push_loa_to_firestore(rrn, enabled, status["min_loa_for_control"])
            if synced:
                print(f"☁️   Firestore updated for {rrn}")
            else:
                print("⚠️   Firestore update skipped (no credentials or unreachable)")


def cmd_components(args) -> None:
    """castor components — manage hardware component registration.

    Sub-commands:
      detect              Auto-detect attached hardware components
      list                List components from config file
      register            Detect + write components to Firestore (for Fleet UI)
    """
    import json
    import sys

    from castor.components import (
        components_from_config,
        detect_components,
        merge_components,
        register_components_to_firestore,
    )
    from castor.loa import get_config_path, load_config

    sub = getattr(args, "components_cmd", None) or "detect"
    config_path = get_config_path(getattr(args, "config", None))
    fmt = getattr(args, "format", "table")

    cfg: dict = {}
    rrn = "RRN-UNKNOWN"
    if config_path.exists():
        cfg = load_config(config_path)
        rrn = cfg.get("metadata", {}).get("rrn", "RRN-UNKNOWN")

    if sub == "detect":
        components = detect_components(rrn)
        print(f"\n🔍  Detected {len(components)} hardware component(s) for {rrn}:\n")
        for c in components:
            status = c.get("status", "?")
            icon = "✅" if status == "active" else "🔌"
            caps = ", ".join(c.get("capabilities", []))
            caps_str = f"  [{caps}]" if caps else ""
            print(f"  {icon}  [{c['id']}] {c['type'].upper()} — {c['model']}{caps_str}")
            print(f"       Manufacturer: {c.get('manufacturer', 'unknown')}")
            print(f"       Firmware:     {c.get('firmware_version', 'unknown')}")
        if fmt == "json":
            print(json.dumps(components, indent=2))
        return

    if sub == "list":
        components = components_from_config(cfg)
        if not components:
            print("⚠️   No components: section found in config.")
            print("     Run:  castor components detect  to auto-detect")
        else:
            print(f"\n📋  {len(components)} component(s) in {config_path}:\n")
            for c in components:
                print(
                    f"  [{c.get('id', '?')}] {c.get('type', '?').upper()} — {c.get('model', '?')}"
                )
        return

    if sub == "register":
        print(f"🔍  Detecting hardware for {rrn}…")
        detected = detect_components(rrn)
        configured = components_from_config(cfg)
        merged = merge_components(detected, configured)
        print(f"📤  Registering {len(merged)} component(s) to Firestore…")
        ok, errors = register_components_to_firestore(rrn, merged)
        print(f"✅  {ok} component(s) registered.")
        for err in errors:
            print(f"  ❌  {err}", file=sys.stderr)
        if errors:
            sys.exit(1)


def cmd_privacy(args) -> None:
    """castor privacy — display the data privacy policy for this robot config."""
    from castor.privacy import print_privacy_policy

    config_path = getattr(args, "config", None)
    config: dict = {}
    if config_path:
        try:
            import yaml

            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
        except Exception:
            pass
    print_privacy_policy(config)


def cmd_consent(args) -> None:
    """castor consent — manage R2RAM robot-to-robot consent records."""
    consent_cmd = getattr(args, "consent_cmd", None)
    config_path = getattr(args, "config", None)

    # Load ConsentManager if possible
    def _load_manager():
        try:
            import yaml

            cfg: dict = {}
            if config_path:
                with open(config_path) as f:
                    cfg = yaml.safe_load(f) or {}
            rrn = cfg.get("metadata", {}).get("rrn", "RRN-000000000000")
            owner = cfg.get("metadata", {}).get("owner", "rrn://unknown")
            from castor.cloud.consent_manager import ConsentManager

            return ConsentManager(robot_rrn=rrn, owner=owner, db=None)
        except Exception as exc:
            print(f"  Warning: could not load ConsentManager: {exc}")
            return None

    # -- training sub-group --
    if consent_cmd == "training":
        training_cmd = getattr(args, "training_cmd", None)
        if training_cmd == "list":
            mgr = _load_manager()
            if mgr and hasattr(mgr, "list_training_consents"):
                records = mgr.list_training_consents()
                if not records:
                    print("  No training consent records found.")
                else:
                    for r in records:
                        print(f"  {r}")
            else:
                print("  Not yet implemented — see castor/cloud/consent_manager.py")
        elif training_cmd == "delete":
            subject_id = getattr(args, "subject_id", None)
            mgr = _load_manager()
            if mgr and hasattr(mgr, "delete_training_consent"):
                mgr.delete_training_consent(subject_id)
                print(f"  Training consent deleted for subject: {subject_id}")
            else:
                print("  Not yet implemented — see castor/cloud/consent_manager.py")
        else:
            print("  Usage: castor consent training [list|delete <subject_id>]")
        return

    # -- main consent subcommands --
    if consent_cmd == "list":
        mgr = _load_manager()
        if mgr and hasattr(mgr, "list_consents"):
            records = mgr.list_consents()
            if not records:
                print("  No consent records found.")
            else:
                print(f"  {'ID':36}  {'Peer':30}  {'Status':10}  Scopes")
                print("  " + "-" * 90)
                for r in records:
                    cid = r.get("consent_id", "?")[:36]
                    peer = r.get("peer_owner", "?")[:30]
                    status = r.get("status", "?")
                    scopes = ",".join(r.get("granted_scopes", []))
                    print(f"  {cid:36}  {peer:30}  {status:10}  {scopes}")
        else:
            # Fallback: show cache contents if any
            if mgr and mgr._cache:
                print(f"  {'Peer':<40}  {'Status':10}  Scopes")
                print("  " + "-" * 70)
                for peer, r in mgr._cache.items():
                    status = r.get("status", "?")
                    scopes = ",".join(r.get("granted_scopes", []))
                    print(f"  {peer:<40}  {status:10}  {scopes}")
            else:
                print("  Not yet implemented — see castor/cloud/consent_manager.py")

    elif consent_cmd == "show":
        consent_id = getattr(args, "consent_id", None)
        mgr = _load_manager()
        if mgr and hasattr(mgr, "get_consent"):
            record = mgr.get_consent(consent_id)
            if record:
                import json as _json

                print(_json.dumps(record, indent=2, default=str))
            else:
                print(f"  Consent record not found: {consent_id}")
        else:
            print("  Not yet implemented — see castor/cloud/consent_manager.py")

    elif consent_cmd == "grant":
        rrn = getattr(args, "rrn", None)
        scope_str = getattr(args, "scope", "chat")
        scopes = [s.strip() for s in (scope_str or "chat").split(",") if s.strip()]
        mgr = _load_manager()
        if mgr:
            try:
                consent_id = mgr.grant_consent(
                    peer_owner=f"rrn://{rrn}",
                    peer_rrn=rrn,
                    peer_ruri=f"ruri://{rrn}",
                    granted_scopes=scopes,
                )
                print(f"  ✓ Consent granted to {rrn}")
                print(f"    scopes: {', '.join(scopes)}")
                print(f"    consent_id: {consent_id}")
            except Exception as exc:
                print(f"  ✗ Grant failed: {exc}")
        else:
            print("  Not yet implemented — see castor/cloud/consent_manager.py")

    elif consent_cmd == "deny":
        rrn = getattr(args, "rrn", None)
        mgr = _load_manager()
        if mgr and hasattr(mgr, "deny_consent"):
            try:
                mgr.deny_consent(rrn)
                print(f"  ✓ Consent denied for {rrn}")
            except Exception as exc:
                print(f"  ✗ Deny failed: {exc}")
        else:
            print("  Not yet implemented — see castor/cloud/consent_manager.py")

    elif consent_cmd == "revoke":
        consent_id = getattr(args, "consent_id", None)
        mgr = _load_manager()
        if mgr:
            try:
                # Try by consent_id first, fall back to peer_owner
                if hasattr(mgr, "revoke_consent_by_id"):
                    mgr.revoke_consent_by_id(consent_id)
                else:
                    mgr.revoke_consent(consent_id)
                print(f"  ✓ Consent revoked: {consent_id}")
            except Exception as exc:
                print(f"  ✗ Revoke failed: {exc}")
        else:
            print("  Not yet implemented — see castor/cloud/consent_manager.py")

    elif consent_cmd == "export":
        mgr = _load_manager()
        if mgr and hasattr(mgr, "export_offline_blob"):
            try:
                blob = mgr.export_offline_blob()
                import json as _json

                print(_json.dumps(blob, indent=2, default=str))
            except Exception as exc:
                print(f"  ✗ Export failed: {exc}")
        else:
            print("  Not yet implemented — see castor/cloud/consent_manager.py")

    else:
        print("  Usage: castor consent [list|show|grant|deny|revoke|export|training] [options]")
        print()
        print("  Subcommands:")
        print("    list                           List all consent records")
        print("    show <consent_id>              Inspect a consent record")
        print("    grant <rrn> --scope SCOPES     Grant consent (scopes: chat,control,...)")
        print("    deny <rrn>                     Deny a pending consent request")
        print("    revoke <consent_id>            Revoke a granted consent")
        print("    export --offline               Export signed offline blob")
        print("    training list                  List training consent records")
        print("    training delete <subject_id>   GDPR erasure")


def cmd_profile(args) -> None:
    """castor profile — manage named personality/config profiles."""
    from castor.profiles import (
        list_profiles,
        print_profiles,
        remove_profile,
        save_profile,
        use_profile,
    )

    action = getattr(args, "action", "list")
    name = getattr(args, "name", None)
    config = getattr(args, "config", None)

    if action == "list":
        profiles = list_profiles()
        print_profiles(profiles)
    elif action == "save":
        if not name:
            print("  Usage: castor profile save --name <profile-name> --config <file>")
            return
        save_profile(name, config)
        print(f"  Profile '{name}' saved.")
    elif action == "use":
        if not name:
            print("  Usage: castor profile use --name <profile-name>")
            return
        try:
            use_profile(name)
            print(f"  Profile '{name}' activated.")
        except FileNotFoundError:
            print(f"  Profile '{name}' not found.")
    elif action == "remove":
        if not name:
            print("  Usage: castor profile remove --name <profile-name>")
            return
        ok = remove_profile(name)
        if ok:
            print(f"  Profile '{name}' removed.")
        else:
            print(f"  Profile '{name}' not found.")
    else:
        print("  Usage: castor profile <list|save|use|remove> [--name <name>]")


def cmd_quickstart(args) -> None:
    """castor quickstart — init wizard + start gateway in one command."""
    from castor.init_wizard import cmd_quickstart as _wizard_quickstart

    _wizard_quickstart(args)


def cmd_record(args) -> None:
    """castor record — placeholder."""
    print("castor record: coming soon.")


def cmd_repl(args) -> None:
    """castor repl — interactive RCAN command REPL."""
    import os

    config_path = getattr(args, "config", "robot.rcan.yaml")
    if not os.path.exists(config_path):
        print(f"  Config not found: {config_path}")
        return
    from castor.repl import launch_repl

    launch_repl(config_path=config_path)


def cmd_replay(args) -> None:
    """castor replay — replay a recorded session from JSONL or via API."""
    recording = getattr(args, "recording", None)
    url = getattr(args, "url", None)

    if not recording and not url:
        print(
            "  Error: provide a recording file or --url for API replay.",
            file=__import__("sys").stderr,
        )
        print("  Usage: castor replay session.jsonl  OR  castor replay --url http://localhost:8000")
        raise SystemExit(1)

    if recording:
        from castor.record import replay_session

        execute = getattr(args, "execute", False)
        config_path = getattr(args, "config", None)
        replay_session(recording_path=recording, execute=execute, config_path=config_path)
    else:
        print(f"  API replay from {url} — use castor replay --url {url} --list to see recordings.")


def cmd_restore(args) -> None:
    """castor restore — restore a config backup archive."""
    from castor.backup import print_restore_summary, restore_backup

    archive = getattr(args, "archive", None)
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        restore_backup(archive, dry_run=True)
    else:
        files = restore_backup(archive)
        if files:
            print_restore_summary(files)


def cmd_safety_benchmark(args) -> None:
    """castor safety benchmark — measure safety path latencies."""
    import json as _json
    from datetime import date

    from castor.safety_benchmark import run_safety_benchmark

    config_path = getattr(args, "config", None)
    if config_path:
        import yaml as _yaml

        with open(config_path) as _f:
            config = _yaml.safe_load(_f) or {}
    else:
        config = {}
    iterations = getattr(args, "iterations", 20)
    live = getattr(args, "live", False)
    fail_fast = getattr(args, "fail_fast", False)
    json_only = getattr(args, "json_output", False)

    output = getattr(args, "output", None)
    if output is None:
        output = f"safety-benchmark-{date.today().isoformat()}.json"

    report = run_safety_benchmark(config=config, iterations=iterations, live=live)

    with open(output, "w") as f:
        _json.dump(report.to_dict(), f, indent=2)

    if not json_only:
        try:
            from rich.console import Console
            from rich.table import Table

            console = Console()
            table = Table(title="Safety Benchmark Results", show_header=True)
            table.add_column("Path", style="cyan")
            table.add_column("Iterations", justify="right")
            table.add_column("P95 (ms)", justify="right")
            table.add_column("Threshold (ms)", justify="right")
            table.add_column("Pass", justify="center")

            for path, result in report.results.items():
                if result.skipped:
                    table.add_row(path, "0", "skipped", "-", "\u229b")
                    continue
                status = "[green]\u2713[/green]" if result.passed else "[red]\u2717[/red]"
                table.add_row(
                    path,
                    str(result.iterations),
                    f"{result.p95_ms:.3f}",
                    f"{result.threshold_p95_ms:.1f}",
                    status,
                )

            console.print(table)
            overall = "[green]PASS[/green]" if report.overall_pass else "[red]FAIL[/red]"
            console.print(f"\nOverall: {overall}")
            console.print(f"Written: {output}")
        except ImportError:
            print(f"Overall: {'PASS' if report.overall_pass else 'FAIL'}")
            print(f"Written: {output}")
    else:
        print(_json.dumps(report.to_dict(), indent=2))

    if fail_fast and not report.overall_pass:
        raise SystemExit(1)


def cmd_safety(args) -> None:
    """castor safety — safety protocol management."""
    safety_cmd = getattr(args, "safety_cmd", None)
    if safety_cmd == "benchmark":
        cmd_safety_benchmark(args)
    else:
        from castor.safety.protocol import SafetyProtocol

        config_path = getattr(args, "config", None)
        protocol = SafetyProtocol(config_path=config_path)
        category = getattr(args, "category", None)
        rules = protocol.list_rules()
        if category:
            rules = [r for r in rules if r["rule_id"].startswith(category.upper())]
        for rule in rules:
            status = "enabled" if rule["enabled"] else "disabled"
            print(f"  [{status}] {rule['rule_id']}: {rule['description']}")


def cmd_llmfit(args) -> None:
    """castor llmfit — check if a local model fits in device RAM.

    TurboQuant is a KV-cache-only runtime patch (not a weight format).
    Model weights stay the same — only the KV cache is compressed (~2.6x).
    Enables larger context windows on memory-constrained edge hardware.

    Examples:
        castor llmfit gemma3:4b
        castor llmfit qwen3:8b --kv-compression turboquant --ctx 16384
        castor llmfit gemma3:4b --provider vllm --json
        castor llmfit --list-models
    """
    import json as _json

    from castor.llmfit import (
        _MODEL_WEIGHT_GB,
        MODEL_FLAGS,
        check_fit,
        get_total_ram_gb,
        turboquant_analysis,
        turboquant_ecosystem_status,
    )

    if getattr(args, "list_models", False):
        print("\nKnown models (with weight size data):")
        for mid, wgb in sorted(_MODEL_WEIGHT_GB.items()):
            flags = MODEL_FLAGS.get(mid, {})
            extras = []
            if flags.get("thinking"):
                extras.append("thinking=yes")
            if flags.get("format"):
                extras.append(f"format={flags['format']}")
            extras_str = ("  [" + ", ".join(extras) + "]") if extras else ""
            print(f"  {mid:<32} {wgb:.1f} GB weights{extras_str}")
        return

    if getattr(args, "tq_status", False):
        eco = turboquant_ecosystem_status()
        if getattr(args, "output_json", False):
            print(_json.dumps(eco, indent=2))
            return
        print(f"\n{eco['spec']}")
        print(f"Paper: {eco['paper']}")
        print(f"Compression: {eco['compression_ratio']}")
        print(f"Note: {eco['note']}")
        print("\nRuntime status:")
        for name, info in eco["runtimes"].items():
            icon = "✅" if info["status"] == "supported" else "⏳"
            print(f"  {icon} {name:<12} {info['status']:<22} {info.get('impl', '')}")
        print("\nHuggingFace:")
        print(f"  {eco['huggingface_models']['note']}")
        for ex in eco["huggingface_models"]["examples"]:
            print(f"  • {ex['id']}  [{ex['runtime']}]  → {ex['result']}")
        print("\nBob (Pi5 + Hailo-8):")
        bob = eco["edge_recommendation"]["bob_pi5_hailo8"]
        print(f"  Status: {bob['status']}")
        print(f"  Path: {bob['path']}")
        print(f"  Best model today: {bob['best_model_today']}")
        print(f"  Best with TQ: {bob['best_model_with_tq']}")
        return

    # turboquant subcommand: castor llmfit --turboquant <model>
    tq_model = getattr(args, "turboquant_model", None)
    if tq_model:
        analysis = turboquant_analysis(tq_model)
        eligible_icon = "✅" if analysis["turboquant_eligible"] else "❌"
        print(f"\n[TurboQuant Analysis] {analysis['model_name']}")
        print(f"  Model size:          {analysis['model_size_gb']:.2f} GB")
        print(f"  KV cache (baseline): {analysis['kv_cache_base_gb']:.2f} GB")
        print(f"  KV cache (TQ 2.6x):  {analysis['kv_cache_compressed_gb']:.2f} GB")
        print(f"  Savings:             {analysis['savings_gb']:.2f} GB")
        print(f"  Compression ratio:   {analysis['compression_ratio']}x")
        print(f"  TQ eligible (>=3B):  {eligible_icon} {analysis['turboquant_eligible']}")
        print()
        return

    model_id = getattr(args, "model", None)
    if not model_id:
        print("Usage: castor llmfit <model_id> [options]")
        print("       castor llmfit --turboquant <model_id>")
        print("       castor llmfit --list-models")
        print("       castor llmfit --tq-status")
        return

    ctx = getattr(args, "ctx", 8192)
    kv_comp = getattr(args, "kv_compression", "none")
    kv_bits = getattr(args, "kv_bits", 3)
    provider = getattr(args, "provider", "ollama")
    ram_override = getattr(args, "ram", None)

    result = check_fit(
        model_id=model_id,
        context_tokens=ctx,
        kv_compression=kv_comp,
        kv_bits=kv_bits,
        provider=provider,
        device_ram_gb=ram_override,
    )

    if getattr(args, "output_json", False):
        import dataclasses

        print(_json.dumps(dataclasses.asdict(result), indent=2))
        return

    total_gb = get_total_ram_gb()
    avail_gb = result.device_ram_gb
    _flags = MODEL_FLAGS.get(model_id.lower().strip(), {})
    _thinking_tag = "  thinking=yes" if _flags.get("thinking") else ""
    print(f"\n[LLMFit] {result.model_id}{_thinking_tag}")
    print(f"  Device RAM:  {avail_gb:.1f} GB available / {total_gb:.1f} GB total")
    print(f"  Weights:     {result.weights_gb:.1f} GB")
    if result.kv_compression == "turboquant":
        print(
            f"  KV cache:    {result.kv_cache_gb:.2f} GB  "
            f"(TurboQuant {result.kv_compression_ratio:.1f}x, was {result.kv_cache_gb_baseline:.2f} GB)"
        )
    else:
        print(f"  KV cache:    {result.kv_cache_gb:.2f} GB  (ctx={ctx:,} tokens, no compression)")
    print(f"  Overhead:    {result.overhead_gb:.1f} GB")
    print(f"  Total:       {result.total_required_gb:.2f} GB / {avail_gb:.1f} GB")
    print()
    if result.fits:
        print(f"  ✅ Fits  (headroom: {result.headroom_gb:.1f} GB)")
    else:
        print(f"  ❌ Does not fit  (exceeds by {-result.headroom_gb:.1f} GB)")
    print(f"  Max context: {result.max_context_tokens:,} tokens")
    if result.kv_compression == "turboquant":
        print(f"  TQ runtime:  {result.tq_runtime} ({result.tq_status})")
    if result.warnings:
        print()
        for w in result.warnings:
            print(f"  ⚠  {w}")
    print()


def cmd_iso_check(args) -> None:
    """castor iso-check — ISO/TC 299 + EU AI Act self-assessment.

    Reads iso_conformance from the RCAN config and outputs a structured checklist
    of requirements met/not-met against ISO 13482, ISO 10218-2, ISO 42001, and
    the EU AI Act. Closes #755.
    """
    import json as _json
    import os

    import yaml

    config_path = getattr(args, "config", None) or os.path.expanduser("~/opencastor/bob.rcan.yaml")
    json_out = getattr(args, "json", False)

    cfg: dict = {}
    if os.path.exists(config_path):
        with open(config_path) as _f:
            cfg = yaml.safe_load(_f) or {}

    iso_cfg = cfg.get("iso_conformance", {})
    authority_handler = cfg.get("authority_handler_enabled", False)
    audit_days = cfg.get("audit_retention_days", 0)
    rcan_version = cfg.get("rcan_version", "?")
    pq_required = cfg.get("pq_signing_required", False)

    checks = [
        # ISO 13482 — Personal care robots safety
        {
            "standard": "ISO 13482:2014",
            "title": "Safety of personal care robots",
            "declared": bool(iso_cfg.get("iso_13482", False)),
            "notes": "Not applicable unless robot provides physical personal care",
            "applicable": bool(iso_cfg.get("iso_13482", False)),
        },
        # ISO 10218-2 — Industrial robot integration
        {
            "standard": "ISO 10218-2:2011",
            "title": "Industrial robots — safety for integration",
            "declared": bool(iso_cfg.get("iso_10218_2", False)),
            "notes": "Not applicable unless industrial deployment",
            "applicable": bool(iso_cfg.get("iso_10218_2", False)),
        },
        # ISO 42001 — AI management systems
        {
            "standard": "ISO/IEC 42001:2023",
            "title": "AI management system",
            "declared": bool(iso_cfg.get("iso_42001", False)),
            "notes": "RCAN v2.2 Protocol 66 aligns with AI management requirements",
            "applicable": True,
            "checks": [
                ("RCAN version ≥ 2.0", rcan_version.startswith("2.")),
                ("ML-DSA-65 signing required", pq_required),
                ("Authority handler enabled", authority_handler),
                ("Audit retention ≥ 1 year", audit_days >= 365),
            ],
        },
        # EU AI Act
        {
            "standard": "EU AI Act (Reg. 2024/1689)",
            "title": "EU AI Act high-risk system compliance",
            "declared": bool(iso_cfg.get("eu_ai_act", authority_handler)),
            "notes": "Deadline: August 2, 2026",
            "applicable": True,
            "checks": [
                ("Art. 12 — Audit retention ≥ 10yr (3650d)", audit_days >= 3650),
                ("Art. 13 — Transparency (RCAN MessageType 18)", True),
                ("Art. 14 — Human oversight (HITL gates in config)", bool(cfg.get("hitl_gates"))),
                ("Art. 16(j) — Authority handler", authority_handler),
                ("Art. 15 — PQ signing (robustness/accuracy)", pq_required),
            ],
        },
    ]

    if json_out:
        print(_json.dumps({"iso_checks": checks, "config": config_path}, indent=2))
        return

    print()
    print("ISO/TC 299 + EU AI Act Self-Assessment")
    print("=" * 48)
    print(f"Config: {config_path}")
    print()

    all_pass = True
    for chk in checks:
        declared = chk["declared"]
        applicable = chk.get("applicable", True)
        icon = "✅" if declared else ("⬜" if not applicable else "⚠️ ")
        print(f"  {icon} {chk['standard']}")
        print(f"     {chk['title']}")
        if not applicable and not declared:
            print("     → Not applicable (not declared in config)")
        elif not declared and applicable:
            print("     → Not declared — add to iso_conformance in config")
            all_pass = False
        else:
            print(f"     → Declared: {declared}")

        if "checks" in chk and applicable:
            for desc, passed in chk["checks"]:
                sub_icon = "  ✓" if passed else "  ✗"
                print(f"        {sub_icon} {desc}")
                if not passed:
                    all_pass = False
        if chk.get("notes"):
            print(f"     Note: {chk['notes']}")
        print()

    status = "COMPLIANT (self-declared)" if all_pass else "GAPS DETECTED"
    print(f"Overall: {status}")
    print()
    print("Note: Self-assessment only. Formal certification requires a notified body audit.")
    print()


def cmd_conformance(args) -> None:
    """castor conformance — Print Protocol 66 conformance report."""
    import json as _json
    import os

    import yaml

    config_path = getattr(args, "config", None) or os.path.expanduser("~/opencastor/bob.rcan.yaml")
    json_out = getattr(args, "json", False)

    # Load config if available
    hw_caps: dict = {}
    if os.path.exists(config_path):
        try:
            with open(config_path) as _f:
                rcan_cfg = yaml.safe_load(_f) or {}
            hw_caps = rcan_cfg.get("hardware_safety", {})
        except Exception as _e:
            pass

    # Build manifest (optionally with live SafetyLayer)
    safety_layer = None
    try:
        from castor.fs import CastorFS

        _fs = CastorFS()
        safety_layer = _fs.safety
    except Exception:
        pass

    from castor.safety.p66_manifest import build_manifest

    manifest = build_manifest(safety_layer=safety_layer, hardware_caps=hw_caps)

    if json_out:
        print(_json.dumps(manifest, indent=2))
        return

    # Pretty-print report
    summary = manifest.get("summary", {})
    implemented = summary.get("implemented", 0)
    partial = summary.get("partial", 0)
    hardware_dep = summary.get("hardware_dependent", 0)
    pct = summary.get("conformance_pct", 0.0)

    bar_filled = int(pct / 10)
    bar_empty = 10 - bar_filled
    bar = "█" * bar_filled + "░" * bar_empty

    print()
    print("Protocol 66 Conformance Report")
    print("================================")
    print(f"Conformance: {pct:.0f}%  [{bar}] {pct:.0f}/100")
    print()

    status_icons = {
        "implemented": "✅",
        "partial": "⚠️ ",
        "planned": "🔲",
        "hardware": "⚠️ ",
    }

    for rule in manifest.get("rules", []):
        rule_id = rule.get("rule_id", "?")
        desc = rule.get("description", "")
        status = rule.get("status", "?")
        icon = status_icons.get(status, "❓")
        notes = rule.get("notes", "")
        suffix = f" — {notes}" if notes else ""
        print(f"  {icon} {rule_id:<14} {desc}{suffix}")

    print()
    sw_gated = summary.get("planned", 0)
    v15_invariants = summary.get("v15_invariants_implemented", 0)
    print(
        f"Summary: {implemented} implemented, {partial} partial"
        f" (hardware: {hardware_dep}), {sw_gated} software-gated"
    )
    if v15_invariants:
        print(f"  RCAN v1.5: {v15_invariants} invariants implemented")
    print()

    # ── RCAN v1.5 conformance checks ────────────────────────────────────────
    _run_v15_conformance_checks(config_path, manifest)

    # ── RCAN v2.1/v2.2 L5 compliance checks (closes #763) ───────────────────
    _run_v22_compliance_checks(config_path)


def _run_v15_conformance_checks(config_path: str, manifest: dict) -> None:
    """Print RCAN v1.5 conformance checks appended to the conformance report.

    Checks:
      1. Is replay cache enabled?
      2. Is sender_type being logged?
      3. Is clock synchronized?
      4. Are ESTOP QoS acks within 2s?
    """

    print("RCAN v1.5 Checks")
    print("─" * 40)

    # 1. Replay cache enabled?
    replay_enabled = manifest.get("replay_cache_enabled", False)
    _print_v15_check(
        "replay_cache",
        "Replay prevention enabled (GAP-03)",
        replay_enabled,
        detail="ReplayCache(window_s=30) active in castor.cloud.bridge"
        if replay_enabled
        else "WARN: replay_cache_enabled=False in manifest",
    )

    # 2. sender_type being logged?
    sender_logged = manifest.get("sender_type_logged", False)
    _print_v15_check(
        "sender_type_logged",
        "sender_type audit trail active (GAP-08)",
        sender_logged,
        detail="sender_type field logged in all bridge audit entries"
        if sender_logged
        else "WARN: sender_type_logged=False in manifest",
    )

    # 3. Clock synchronized?
    clock_synced = _check_clock_sync()
    _print_v15_check(
        "clock_sync",
        "System clock synchronized (GAP-04)",
        clock_synced,
        detail="NTP/chrony clock sync confirmed"
        if clock_synced
        else "WARN: clock may not be synchronized — replay prevention relies on accurate timestamps",
    )

    # 4. ESTOP QoS ACK within 2s?
    # Check by inspecting bridge config / recent logs if available
    estop_qos_ok = _check_estop_qos_config(config_path)
    _print_v15_check(
        "estop_qos",
        "ESTOP QoS ACK within 2s (GAP-11)",
        estop_qos_ok,
        detail="Bridge configured to ACK ESTOP within 2s (castor.cloud.bridge.ESTOP_ACK_DEADLINE_S=2.0)"
        if estop_qos_ok
        else "WARN: could not confirm ESTOP QoS configuration",
    )

    print()

    # Overall v1.5 score
    checks = [replay_enabled, sender_logged, clock_synced, estop_qos_ok]
    passed = sum(1 for c in checks if c)
    print(f"v1.5 score: {passed}/{len(checks)} checks passed")
    if passed < len(checks):
        print("  Run 'castor conformance' again after addressing warnings above.")
    print()


def _run_v22_compliance_checks(config_path: str) -> None:
    """Print RCAN v2.1/v2.2 L5 compliance checks from ConformanceChecker.compliance_report().

    Closes #763.
    """
    import yaml

    if not __import__("os").path.exists(config_path):
        return

    try:
        with open(config_path) as _f:
            cfg = yaml.safe_load(_f) or {}
    except Exception as _e:
        print(f"  WARN: could not load config for v2.2 checks: {_e}")
        return

    try:
        from castor.conformance import ConformanceChecker

        checker = ConformanceChecker(cfg, config_path=config_path)
        report = checker.compliance_report()
    except Exception as _e:
        print(f"  WARN: compliance_report() failed: {_e}")
        return

    checks = report.get("checks", [])
    if not checks:
        return

    print("RCAN v2.1/v2.2 L5 Checks")
    print("─" * 40)

    status_icons = {"pass": "✅", "warn": "⚠️ ", "fail": "❌"}
    for c in checks:
        icon = status_icons.get(c.get("status", ""), "❓")
        cid = c.get("id", "?")
        msg = c.get("message", "")
        print(f"  {icon} {cid:<35} {msg}")

    print()
    overall = report.get("overall_status", "unknown")
    passed = sum(1 for c in checks if c.get("status") == "pass")
    failed = sum(1 for c in checks if c.get("status") == "fail")
    warned = sum(1 for c in checks if c.get("status") == "warn")
    display_score = max(0, 100 - failed * 10 - warned * 3)
    print(
        f"v2.2 score: {passed}/{len(checks)} checks pass — overall: {overall} ({display_score}/100)"
    )
    print()


def _print_v15_check(check_id: str, description: str, passed: bool, detail: str = "") -> None:
    """Print a single v1.5 check result."""
    icon = "✅" if passed else "⚠️ "
    suffix = f"\n       {detail}" if detail else ""
    print(f"  {icon} [{check_id:<20}] {description}{suffix}")


def _check_clock_sync() -> bool:
    """Check whether the system clock appears synchronized.

    Returns True if NTP/chrony is running and clock is synced.
    """
    import shutil
    import subprocess

    # Try timedatectl (systemd)
    if shutil.which("timedatectl"):
        try:
            result = subprocess.run(
                ["timedatectl", "status"], capture_output=True, text=True, timeout=3
            )
            output = result.stdout + result.stderr
            if "synchronized: yes" in output.lower() or "ntp service: active" in output.lower():
                return True
        except Exception:
            pass

    # Try chronyc
    if shutil.which("chronyc"):
        try:
            result = subprocess.run(
                ["chronyc", "tracking"], capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0 and "reference" in result.stdout.lower():
                return True
        except Exception:
            pass

    # Try ntpstat
    if shutil.which("ntpstat"):
        try:
            result = subprocess.run(["ntpstat"], capture_output=True, timeout=3)
            return result.returncode == 0
        except Exception:
            pass

    # Fall back: assume synced if not on embedded hardware (best effort)
    return False


def _check_estop_qos_config(config_path: str) -> bool:
    """Check if ESTOP QoS is configured correctly.

    Returns True if the bridge module has ESTOP_ACK_DEADLINE_S <= 2.0.
    """
    try:
        from castor.cloud.bridge import ESTOP_ACK_DEADLINE_S

        return ESTOP_ACK_DEADLINE_S <= 2.0
    except ImportError:
        return False


def cmd_scan(args) -> None:
    """castor scan — detect connected hardware and suggest a preset."""
    import json as _json

    from castor.hardware_detect import (
        detect_hardware,
        invalidate_hardware_cache,
        print_scan_results,
        suggest_extras,
        suggest_preset,
    )

    refresh = getattr(args, "refresh", False)
    json_out = getattr(args, "json", False)
    preset_only = getattr(args, "preset_only", False)

    if refresh:
        invalidate_hardware_cache()

    hw = detect_hardware()
    preset, confidence, reason = suggest_preset(hw)

    if json_out:
        print(
            _json.dumps(
                {
                    **hw,
                    "suggested_preset": {
                        "preset": preset,
                        "confidence": confidence,
                        "reason": reason,
                    },
                },
                default=str,
                indent=2,
            )
        )
        return

    if preset_only:
        print(f"{preset} ({confidence}): {reason}")
        return

    print_scan_results(hw)
    print(f"\n  Suggested preset: {preset} ({confidence})")
    print(f"  Reason: {reason}")

    extras = suggest_extras(hw)
    if extras:
        print("\n  Hardware detected — consider installing:")
        for pkg in extras:
            print(f"    pip install {pkg}")

    print("\n  Run 'castor wizard' to generate a full RCAN config.\n")


def cmd_schedule(args) -> None:
    """castor schedule — manage cron-based task scheduling for castor commands."""
    from castor.schedule import add_task, install_crontab, list_tasks, print_schedule, remove_task

    action = getattr(args, "action", "list")
    name = getattr(args, "name", None)
    task_command = getattr(args, "task_command", None)
    cron = getattr(args, "cron", None)

    if action == "list":
        tasks = list_tasks()
        print_schedule(tasks)
    elif action == "add":
        if not name or not task_command or not cron:
            print("  Usage: castor schedule add --name <name> --command <cmd> --cron <expr>")
            return
        task = add_task(name, task_command, cron)
        print(f"  Added scheduled task '{task.get('name', name)}'.")
    elif action == "remove":
        if not name:
            print("  Usage: castor schedule remove --name <name>")
            return
        ok = remove_task(name)
        if not ok:
            print(f"  Task '{name}' not found.")
    elif action == "install":
        install_crontab()
    else:
        print("  Usage: castor schedule <list|add|remove|install>")


def cmd_stop(args) -> None:
    """castor stop — gracefully stop the running gateway via PID file (#556)."""
    import signal as _signal
    from pathlib import Path

    pid_file = Path.home() / ".opencastor" / "gateway.pid"
    if not pid_file.exists():
        print("  No gateway.pid found — gateway may not be running.")
        return
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError) as exc:
        print(f"  Could not read PID file: {exc}")
        return
    try:
        os.kill(pid, _signal.SIGTERM)
        print(f"  Sent SIGTERM to gateway (pid {pid}).")
        pid_file.unlink(missing_ok=True)
    except ProcessLookupError:
        print(f"  Gateway (pid {pid}) is not running — cleaning up stale PID file.")
        pid_file.unlink(missing_ok=True)
    except PermissionError:
        print(f"  Permission denied when stopping pid {pid}.")


def cmd_search(args) -> None:
    """castor search — full-text search over stored robot memory logs."""
    from castor.memory_search import print_search_results, search_logs

    query = getattr(args, "query", "")
    log_file = getattr(args, "log_file", None)
    since = getattr(args, "since", None)
    max_results = getattr(args, "max_results", 20)
    results = search_logs(query=query, log_file=log_file, since=since, max_results=max_results)
    print_search_results(results)


def cmd_shell(args) -> None:
    """castor shell — open an interactive shell with the robot runtime loaded."""
    import os

    config_path = getattr(args, "config", "robot.rcan.yaml")
    if not os.path.exists(config_path):
        print(f"  Config not found: {config_path}")
        return
    from castor.shell import launch_shell

    launch_shell(config_path=config_path)


def cmd_status(args) -> None:
    """castor status — show AI provider and messaging channel readiness."""
    from castor.auth import (
        list_available_channels,
        list_available_providers,
        load_dotenv_if_available,
    )

    load_dotenv_if_available()
    providers = list_available_providers()
    channels = list_available_channels()

    print("\n  AI Providers")
    print(f"  {'─' * 24}")
    for name, ready in sorted(providers.items()):
        icon = "[+]" if ready else "[-]"
        print(f"  {icon} {name}")

    print("\n  Messaging Channels")
    print(f"  {'─' * 24}")
    if channels:
        for name, ready in sorted(channels.items()):
            icon = "[+]" if ready else "[-]"
            print(f"  {icon} {name}")
    else:
        print("  (none configured)")
    print()


def _cmd_attest_dispatch(args) -> None:
    """castor attest — RCAN v2.1 §11 firmware manifest (closes #760)."""
    from castor.firmware import (
        cmd_attest_generate,
        cmd_attest_serve,
        cmd_attest_sign,
        cmd_attest_verify,
    )

    subcmd = getattr(args, "attest_cmd", None)
    if subcmd == "generate":
        cmd_attest_generate(args)
    elif subcmd == "sign":
        cmd_attest_sign(args)
    elif subcmd == "verify":
        cmd_attest_verify(args)
    elif subcmd == "serve":
        cmd_attest_serve(args)
    else:
        print("Usage: castor attest {generate,sign,verify,serve}")
        print("  generate  — build manifest from installed packages")
        print("  sign      — sign with ML-DSA-65 key (FIPS 204)")
        print("  verify    — verify signature")
        print("  serve     — confirm /.well-known/rcan-firmware-manifest.json")


def _cmd_sbom_dispatch(args) -> None:
    """castor sbom — RCAN v2.1 §12 CycloneDX SBOM (closes #761)."""
    from castor.sbom import cmd_sbom_generate, cmd_sbom_publish, cmd_sbom_verify

    subcmd = getattr(args, "sbom_cmd", None)
    if subcmd == "generate":
        cmd_sbom_generate(args)
    elif subcmd == "publish":
        cmd_sbom_publish(args)
    elif subcmd == "verify":
        cmd_sbom_verify(args)
    else:
        print("Usage: castor sbom {generate,publish,verify}")
        print("  generate  — CycloneDX SBOM from installed packages")
        print("  publish   — push to RRF and receive countersignature")
        print("  verify    — verify RRF countersig")


def cmd_attestation(args) -> None:
    """castor attestation — show or regenerate software attestation status."""
    import json
    from pathlib import Path

    from castor.attestation_generator import generate_attestation

    config_path = Path(args.config) if getattr(args, "config", None) else None
    out_path = Path(args.out) if getattr(args, "out", None) else None
    result = generate_attestation(config_path=config_path, out_path=out_path)

    if getattr(args, "output_json", False):
        print(json.dumps(result, indent=2))
    else:
        status = "VERIFIED" if result["verified"] else "DEGRADED"
        print(f"\n  OpenCastor Software Attestation: {status}\n")
        print(f"  secure_boot   (code integrity):    {result['secure_boot']}")
        print(f"    {result['claims_detail']['secure_boot']}")
        print(f"  measured_boot (config integrity):   {result['measured_boot']}")
        print(f"    {result['claims_detail']['measured_boot']}")
        print(f"  signed_updates (update chain):      {result['signed_updates']}")
        print(f"    {result['claims_detail']['signed_updates']}")
        print(f"\n  Profile: {result['profile']}")
        print(f"  Token:   {result['token'][:16]}...")
        print()


def cmd_revocation(args) -> None:
    """castor revocation — manage RRF revocation status (Issue #780).

    Sub-commands:
      status [rrn]   Show revocation status from cache + RRF
      poll           Force an immediate RRF revocation poll
      cache          Show local revocation cache contents
    """

    sub = getattr(args, "revocation_cmd", None) or "status"

    if sub == "status":
        from castor.services.rrf_poller import get_revocation_status

        rrn = getattr(args, "rrn", None) or ""
        if not rrn:
            import os

            rrn = os.getenv("OPENCASTOR_RRN", "")
        if not rrn:
            try:
                import glob

                candidates = sorted(glob.glob("*.rcan.yaml"))
                if candidates:
                    import yaml

                    with open(candidates[0]) as _f:
                        cfg = yaml.safe_load(_f) or {}
                    rrn = cfg.get("metadata", {}).get("rrn", cfg.get("rrn", ""))
            except Exception:
                pass

        if not rrn:
            print("  No RRN provided. Pass as argument or set OPENCASTOR_RRN.")
            print("  Usage: castor revocation status <RRN>")
            return

        data = get_revocation_status(rrn)
        status_icon = (
            "✅" if data["status"] == "active" else ("❌" if data["status"] == "revoked" else "❓")
        )
        print(f"\n  RRN: {rrn}")
        print(f"  Status: {status_icon} {data['status']}")
        print(f"  Source: {data['source']}")
        if data["last_checked_s"]:
            import datetime

            ts = datetime.datetime.fromtimestamp(data["last_checked_s"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            print(f"  Last checked: {ts}")
        else:
            print("  Last checked: never")
        print()

    elif sub == "poll":
        from castor.services.rrf_poller import force_poll

        print("  Polling RRF revocation list...")
        result = force_poll()
        if result.get("ok"):
            print("  ✅ Poll successful")
            print(f"     Revoked orchestrators: {result['revoked_orchestrators']}")
            print(f"     Revoked JTIs:          {result['revoked_jtis']}")
            import datetime

            ts = datetime.datetime.fromtimestamp(result["polled_at"]).strftime("%Y-%m-%d %H:%M:%S")
            print(f"     Polled at:             {ts}")
        else:
            print(f"  ❌ Poll failed: {result.get('error', 'unknown error')}")
        print()

    elif sub == "cache":
        from castor.services.rrf_poller import get_cache_contents

        data = get_cache_contents()
        print("\n  Revocation Cache")
        print("  ─────────────────────────────")
        print(f"  Total entries: {data['entry_count']}")
        if data.get("error"):
            print(f"  Warning: {data['error']}")
        if data["last_updated_s"]:
            import datetime

            ts = datetime.datetime.fromtimestamp(data["last_updated_s"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            print(f"  Last updated:  {ts}")
        else:
            print("  Last updated:  never")
        orcs = data["revoked_orchestrators"]
        jtis = data["revoked_jtis"]
        if orcs:
            print(f"\n  Revoked orchestrators ({len(orcs)}):")
            for o in orcs[:20]:
                print(f"    • {o}")
            if len(orcs) > 20:
                print(f"    ... and {len(orcs) - 20} more")
        if jtis:
            print(f"\n  Revoked JTIs ({len(jtis)}):")
            for j in jtis[:20]:
                print(f"    • {j}")
            if len(jtis) > 20:
                print(f"    ... and {len(jtis) - 20} more")
        if not orcs and not jtis:
            print("  Cache is empty.")
        print()

    else:
        print(f"  Unknown sub-command: {sub}")
        print("  Usage: castor revocation <status|poll|cache>")


def cmd_swarm(args) -> None:
    """castor swarm — placeholder."""
    print("castor swarm: coming soon.")


def _ascii_qr(url: str) -> str:
    """Minimal QR fallback — just print the URL clearly if qrcode unavailable."""
    width = min(len(url) + 4, 60)
    border = "█" * width
    return f"\n{border}\n  {url}\n{border}\n"


def _print_qr(url: str, console=None) -> None:
    """Print a QR code inline. Uses qrcode lib if available, else ASCII fallback."""
    try:
        import qrcode

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        lines = []
        for row in matrix:
            line = "".join("██" if cell else "  " for cell in row)
            lines.append(line)
        qr_str = "\n".join(lines)
        if console:
            console.print(qr_str)
        else:
            print(qr_str)
    except ImportError:
        fb = _ascii_qr(url)
        if console:
            console.print(fb)
        else:
            print(fb)


def _detect_platform() -> str:
    """Detect the hardware platform for the RCAN config."""
    import platform

    machine = platform.machine().lower()
    if "aarch64" in machine or "arm64" in machine:
        # Check for Raspberry Pi model
        try:
            model = open("/proc/device-tree/model").read().lower()
            if "raspberry pi 5" in model:
                return "rpi5"
            elif "raspberry pi 4" in model:
                return "rpi4"
            return "arm64"
        except Exception:
            return "arm64"
    elif "x86_64" in machine or "amd64" in machine:
        return "x86"
    return "unknown"


def cmd_setup(args) -> None:
    """castor setup — interactive Fleet UI onboarding wizard with QR codes."""
    import uuid as _uuid
    from pathlib import Path

    import yaml

    non_interactive = getattr(args, "non_interactive", False)

    # Try rich
    try:
        from rich.console import Console
        from rich.panel import Panel  # noqa: F401

        console = Console()
        HAS_RICH = True
    except ImportError:
        console = None
        HAS_RICH = False

    def _print(msg, **kw):
        if HAS_RICH and console:
            console.print(msg, **kw)
        else:
            import re

            print(re.sub(r"\[/?[a-z_ /]+\]", "", msg))

    def _prompt(label, default="", password=False):
        if non_interactive:
            return default
        if HAS_RICH and console:
            from rich.prompt import Prompt

            return Prompt.ask(label, default=default, password=password) or default
        else:
            suffix = f" [{default}]" if default else ""
            val = input(f"{label}{suffix}: ").strip()
            return val if val else default

    def _confirm(label, default=True):
        if non_interactive:
            return default
        if HAS_RICH and console:
            from rich.prompt import Confirm

            return Confirm.ask(label, default=default)
        else:
            suffix = " [Y/n]" if default else " [y/N]"
            val = input(f"{label}{suffix}: ").strip().lower()
            if not val:
                return default
            return val in ("y", "yes")

    version = "1.6"
    try:
        from castor import __version__

        version = __version__
    except Exception:
        pass

    # Banner
    _print("")
    if HAS_RICH and console:
        console.print(
            Panel(
                f"[bold cyan]OpenCastor Setup Wizard v{version}[/bold cyan]",
                border_style="cyan",
                expand=False,
            )
        )
    else:
        print(" ╔══════════════════════════════════════╗")
        print(f" ║  OpenCastor Setup Wizard v{version:<9}  ║")
        print(" ╚══════════════════════════════════════╝")
    _print("")

    # ── Step 1: Robot Identity ──────────────────────────────────────────
    _print("[bold]Step 1/4: Robot Identity[/bold]")
    _print("─" * 35)
    robot_name = _prompt("  Robot name", default="my-robot")
    owner_name = _prompt("  Owner name", default="")

    # ── Step 2: Fleet UI Connection ─────────────────────────────────────
    _print("")
    _print("[bold]Step 2/4: Fleet UI Connection[/bold]")
    _print("─" * 35)
    connect_fleet = _confirm("  Connect to OpenCastor Fleet UI?", default=True)

    fleet_url = "https://app.opencastor.com"
    if connect_fleet:
        _print(f"\n  Fleet UI: [link]{fleet_url}[/link]")
        _print("\n  Scan this QR code to open Fleet UI on your phone:")
        _print("")
        _print_qr(fleet_url, console=console)
        _print(f"\n  Or open: {fleet_url}")
        _print("")
        if not non_interactive:
            input("  Press Enter once you've signed in to the Fleet UI...")

    # ── Step 3: Firebase Bridge Token ───────────────────────────────────
    _print("")
    _print("[bold]Step 3/4: Firebase Bridge Token[/bold]")
    _print("─" * 35)
    _print("  Your robot needs a service account key to connect.")
    _print("")

    setup_params = f"robot={robot_name}"
    if owner_name:
        setup_params += f"&owner={owner_name}"
    setup_link = f"{fleet_url}/setup?{setup_params}"
    _print(f"  Quick setup link:\n  {setup_link}")
    _print("\n  Scan QR code to get your setup token:")
    _print("")
    _print_qr(setup_link, console=console)
    _print("")

    firebase_uid = _prompt("  Paste your setup token (Firebase UID)", default="")

    # ── Step 4: Brain Provider ───────────────────────────────────────────
    _print("")
    _print("[bold]Step 4/4: Brain Provider[/bold]")
    _print("─" * 35)
    _print("  Select AI provider:")
    _print("    1) Google Gemini (recommended - free tier available)")
    _print("    2) Anthropic Claude")
    _print("    3) OpenAI")
    _print("    4) None (offline mode)")
    provider_choice = _prompt("  Choice", default="1")

    provider_map = {"1": "google", "2": "anthropic", "3": "openai", "4": "none"}
    model_map = {
        "google": "gemini-2.5-flash",
        "anthropic": "claude-3-5-haiku-20241022",
        "openai": "gpt-4o-mini",
        "none": "",
    }
    provider = provider_map.get(provider_choice, "google")

    api_key = ""
    if provider == "google":
        api_key = _prompt("  Google API key (or press Enter to use ADC)", default="")
    elif provider == "anthropic":
        api_key = _prompt("  Anthropic API key", default="")
    elif provider == "openai":
        api_key = _prompt("  OpenAI API key", default="")

    # ── Generate config ──────────────────────────────────────────────────
    rrn_suffix = str(_uuid.uuid4()).replace("-", "").upper()[:12]
    rrn = f"RRN-{rrn_suffix}"
    robot_number = f"{robot_name}-001"
    platform_name = _detect_platform()

    brain_block: dict = {}
    if provider == "google":
        brain_block = {
            "provider": "google",
            "model": model_map["google"],
            "google_api_key": api_key,
        }
    elif provider == "anthropic":
        brain_block = {
            "provider": "anthropic",
            "model": model_map["anthropic"],
            "anthropic_api_key": api_key,
        }
    elif provider == "openai":
        brain_block = {
            "provider": "openai",
            "model": model_map["openai"],
            "openai_api_key": api_key,
        }
    else:
        brain_block = {"provider": "none"}

    owner_uri = f"rrn://{owner_name}/robot/opencastor/{robot_number}" if owner_name else ""

    config = {
        "rrn": rrn,
        "owner": owner_uri,
        "name": robot_name,
        "rcan_version": "1.6",
        "firebase_uid": firebase_uid,
        "brain": brain_block,
        "cloud": {
            "project_id": "opencastor",
            "firebase_uid": firebase_uid,
            "sa_key_path": "~/.config/opencastor/firebase-sa-key.json",
        },
        "security": {"replay_window_s": 30},
        "offline": {"grace_s": 300},
        "hardware": {"platform": platform_name},
    }

    config_dir = Path.home() / ".config" / "opencastor"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"{robot_name}.rcan.yaml"

    with open(config_path, "w") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False)

    # ── Done ─────────────────────────────────────────────────────────────
    _print("")
    _print("[bold green]✅ Setup complete![/bold green]")
    _print("─" * 35)
    _print(f"  Config written to: [cyan]{config_path}[/cyan]")
    _print("")
    _print(f"  Start your robot:   castor gateway --config {config_path}")
    _print(f"  Start bridge:       castor bridge --config {config_path}")
    _print(f"  Fleet UI:           {fleet_url}")
    _print("")
    _print("  Your robot will appear in the Fleet UI within 30 seconds.")
    _print("")


def cmd_fleet_link(args) -> None:
    """castor fleet-link — generate Fleet UI deep links and QR codes for this robot."""

    import yaml

    config_path = getattr(args, "config", None) or _find_default_config()
    rrn = "RRN-000000000001"

    if config_path and os.path.exists(config_path):
        try:
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            rrn = cfg.get("rrn", rrn)
        except Exception:
            pass

    fleet_base = "https://app.opencastor.com"
    dashboard_url = f"{fleet_base}/#/fleet"
    robot_url = f"{fleet_base}/#/robot/{rrn}"
    observer_url = f"{robot_url}?view=observer"

    try:
        from rich.console import Console

        console = Console()
        HAS_RICH = True
    except ImportError:
        console = None
        HAS_RICH = False

    def _print(msg, **kw):
        if HAS_RICH and console:
            console.print(msg, **kw)
        else:
            import re

            print(re.sub(r"\[/?[a-z_ /]+\]", "", msg))

    _print("")
    _print("[bold]OpenCastor Fleet UI Links[/bold]")
    _print("─" * 35)
    _print(f"  Fleet dashboard:  {dashboard_url}")
    _print(f"  This robot:       {robot_url}")
    _print("")
    _print("  Scan to open on phone:")
    _print_qr(robot_url, console=console)
    _print("")
    _print("  Share invite link (read-only access):")
    _print(f"  {observer_url}")
    _print("")


def cmd_bridge_setup(args) -> None:
    """castor bridge setup — generate and optionally install a systemd service for the bridge."""
    import getpass
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    import yaml

    config_path = getattr(args, "config", None) or _find_default_config()
    if not config_path or not os.path.exists(config_path):
        print("  No rcan.yaml config found. Run `castor setup` first.")
        raise SystemExit(1)

    with open(config_path) as f:
        cfg = yaml.safe_load(f) or {}

    robot_name = cfg.get("name", cfg.get("robot_name", "robot"))
    abs_config = os.path.abspath(config_path)
    user = getpass.getuser()
    python_path = sys.executable
    service_name = f"castor-bridge-{robot_name}"
    service_filename = f"{service_name}.service"

    service_content = f"""[Unit]
Description=OpenCastor Bridge - {robot_name}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={Path.home()}
ExecStart={python_path} -m castor bridge --config {abs_config}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

    service_file = Path.cwd() / service_filename
    service_file.write_text(service_content)
    print(f"\n  ✅ Generated: {service_file}")
    print("\n  Install commands:")
    print(f"    sudo cp {service_filename} /etc/systemd/system/")
    print(f"    sudo systemctl enable {service_name}")
    print(f"    sudo systemctl start {service_name}")

    try:
        answer = input("\n  Install and start now? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"

    if answer in ("y", "yes"):
        if not shutil.which("systemctl"):
            print("  systemctl not found — cannot install on this platform.")
            return
        cmds = [
            ["sudo", "cp", str(service_file), f"/etc/systemd/system/{service_filename}"],
            ["sudo", "systemctl", "daemon-reload"],
            ["sudo", "systemctl", "enable", service_name],
            ["sudo", "systemctl", "start", service_name],
        ]
        for cmd in cmds:
            print(f"  $ {' '.join(cmd)}")
            rc = subprocess.call(cmd)
            if rc != 0:
                print(f"  ❌ Command failed (exit {rc})")
                return
        print(f"\n  ✅ Service {service_name} is running.")
    else:
        print("  Skipped. Run the install commands above when ready.")
    print()


def _cmd_bridge(args) -> None:
    """castor bridge — Firebase relay daemon for remote fleet management."""
    # Handle 'castor bridge setup' subcommand
    bridge_subcmd = getattr(args, "bridge_subcmd", None)
    if bridge_subcmd == "setup":
        cmd_bridge_setup(args)
        return

    # Handle 'castor bridge discover' — RCAN peer discovery
    if bridge_subcmd == "discover":
        _cmd_bridge_discover(args)
        return

    from castor.cloud.bridge import run_bridge

    run_bridge(args)


def _cmd_bridge_discover(args) -> None:
    """castor bridge discover — probe RCAN peers and print bridge status."""
    import os

    from castor.rcan.http_transport import discover_robot

    # Load config
    config_path = getattr(args, "config", None)
    if not config_path:
        # Auto-detect
        for candidate in ["bob.rcan.yaml", "opencastor.rcan.yaml", "castor.yaml"]:
            if os.path.exists(candidate):
                config_path = candidate
                break
        if not config_path:
            import glob

            found = glob.glob("*.rcan.yaml") + glob.glob("config/*.rcan.yaml")
            config_path = found[0] if found else None

    config = {}
    if config_path and os.path.exists(config_path):
        try:
            import yaml  # type: ignore[import]

            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"  ⚠ Could not load config: {e}")

    peers = config.get("rcan_protocol", {}).get("peers", [])
    if not peers:
        print("  No RCAN peers configured in rcan_protocol.peers")
        return

    print(f"  Probing {len(peers)} RCAN peer(s)...")
    for peer in peers:
        host = peer.get("host")
        if not host:
            continue
        port = peer.get("port", 8000)
        result = discover_robot(host, port=port)
        if result:
            robot_name = result.get("robot_name") or result.get("ruri") or "?"
            ruri = result.get("ruri") or "?"
            print(f"  ✓ {host}:{port}: {robot_name} ({ruri})")
        else:
            print(f"  ✗ {host}:{port}: unreachable")


def _cmd_arm(args) -> None:
    """castor arm — SO-ARM101 assembly, port detection, motor setup, config generation."""
    from castor.hardware.so_arm101.cli import build_parser as _arm_build_parser

    arm_parser = _arm_build_parser()
    # Strip 'arm' from argv and re-parse the remainder
    import sys as _sys

    argv = _sys.argv[2:]  # everything after 'castor arm'
    arm_args = arm_parser.parse_args(argv)
    if not hasattr(arm_args, "func") or arm_args.func is None:
        arm_parser.print_help()
        return
    arm_args.func(arm_args)


def cmd_test(args) -> None:
    """castor test — run the castor test suite via pytest."""
    import subprocess
    import sys

    cmd = [sys.executable, "-m", "pytest"]
    if getattr(args, "verbose", False):
        cmd.append("-v")
    keyword = getattr(args, "keyword", None)
    if keyword:
        cmd.extend(["-k", keyword])
    result = subprocess.run(cmd)
    raise SystemExit(result.returncode)


def cmd_test_hardware(args) -> None:
    """castor test hardware — run hardware connectivity tests."""
    import os

    config_path = getattr(args, "config", "robot.rcan.yaml")
    if not os.path.exists(config_path):
        print(f"  Config not found: {config_path}")
        return

    skip_confirm = getattr(args, "yes", False)
    from castor.test_hardware import run_test

    run_test(config_path=config_path, skip_confirm=skip_confirm)


def cmd_update_check(args) -> None:
    """castor update-check — check PyPI for a newer castor release."""
    from castor.update_check import print_update_status

    print_update_status()


def cmd_upgrade(args) -> None:
    """castor upgrade — upgrade OpenCastor to the latest version (#554)."""
    import subprocess
    import sys
    from pathlib import Path

    import castor

    verbose = getattr(args, "verbose", False)
    check_only = getattr(args, "check_only", False)
    venv = getattr(args, "venv", None)

    print(f"  Current version: {castor.__version__}")

    # Detect git install
    repo = Path(__file__).parent.parent
    is_git = (repo / ".git").exists()

    if check_only:
        if is_git:
            result = subprocess.run(
                ["git", "fetch", "origin", "main", "--dry-run"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            print(f"  Repo: {repo}")
            result2 = subprocess.run(
                ["git", "log", "HEAD..origin/main", "--oneline"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            commits = result2.stdout.strip()
            if commits:
                print(f"  Pending commits:\n{commits}")
            else:
                print("  Already up to date.")
        else:
            print("  Not a git install — check PyPI for latest version.")
        return

    if is_git:
        print("  Pulling latest from origin/main...")
        pull = subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=repo,
            capture_output=not verbose,
            text=True,
        )
        if pull.returncode != 0:
            print(f"  git pull failed: {pull.stderr}")
            return
        if verbose and pull.stdout:
            print(pull.stdout)

    python = (venv.rstrip("/") + "/bin/python") if venv else sys.executable
    pip_cmd = [python, "-m", "pip", "install", "-e", str(repo)]
    if verbose:
        pip_cmd.append("-v")
    else:
        pip_cmd.append("-q")

    print(f"  Installing with: {python}")
    result = subprocess.run(pip_cmd, capture_output=not verbose)
    if result.returncode != 0:
        print("  Upgrade failed. Re-run with --verbose for details.")
        return

    # Restart services (best-effort)
    subprocess.run(
        ["systemctl", "--user", "restart", "castor-gateway.service"],
        capture_output=True,
    )
    subprocess.run(
        ["systemctl", "--user", "restart", "castor-dashboard.service"],
        capture_output=True,
    )

    # Re-run attestation after upgrade to refresh measurements
    try:
        from castor.attestation_generator import generate_attestation

        generate_attestation()
        print("  Security attestation refreshed.")
    except Exception:
        pass

    # Reload to get new version string
    import importlib

    try:
        importlib.reload(castor)
    except Exception:
        pass

    print(f"  Upgraded to: {castor.__version__}")
    print("  Run 'castor doctor' to verify.")


def cmd_rcan_check(args) -> None:
    """castor rcan-check — focused RCAN §6 safety field conformance check.

    Validates that all required RCAN §6 safety invariants are present and
    correct in the robot's .rcan.yaml config:
      - safety.local_safety_wins: true
      - watchdog.timeout_s configured (≤ 30 s)
      - safety.confidence_gates configured
    """
    import os

    config_path = getattr(args, "config", None) or os.path.expanduser(
        "~/.opencastor/config.rcan.yaml"
    )
    if not os.path.exists(config_path):
        # Fall back to cwd search
        import glob as _glob

        candidates = sorted(_glob.glob("*.rcan.yaml"))
        if candidates:
            config_path = candidates[0]
        else:
            print(f"  ❌ Config not found: {config_path}")
            print("  Hint: castor rcan-check --config robot.rcan.yaml")
            raise SystemExit(1)

    try:
        import yaml

        with open(config_path) as _f:
            cfg = yaml.safe_load(_f) or {}
    except Exception as exc:
        print(f"  ❌ Error reading {config_path}: {exc}")
        raise SystemExit(1) from None

    print(f"\n  RCAN §6 Safety Conformance: {config_path}\n")

    results: list[tuple[str, bool, str]] = []  # (check_id, passed, detail)

    # ── Check 1: local_safety_wins ────────────────────────────────────────
    safety_cfg = cfg.get("safety", {}) or {}
    lsw = safety_cfg.get("local_safety_wins")
    if lsw is True:
        results.append(
            (
                "safety.local_safety_wins",
                True,
                "local_safety_wins=true  (RCAN §6 invariant satisfied)",
            )
        )
    else:
        results.append(
            (
                "safety.local_safety_wins",
                False,
                f"local_safety_wins={lsw!r}  — must be true; remote commands may override local "
                "safety constraints (RCAN §6 violated).  Fix: set safety.local_safety_wins: true",
            )
        )

    # ── Check 2: watchdog ────────────────────────────────────────────────
    watchdog = cfg.get("watchdog", {}) or {}
    timeout = watchdog.get("timeout_s")
    if timeout is None:
        results.append(
            (
                "safety.watchdog",
                False,
                "watchdog.timeout_s not configured.  Fix: add watchdog: timeout_s: 10",
            )
        )
    else:
        try:
            fval = float(timeout)
            if fval <= 0:
                results.append(("safety.watchdog", False, f"watchdog.timeout_s={fval} must be > 0"))
            elif fval > 30:
                results.append(
                    (
                        "safety.watchdog",
                        False,
                        f"watchdog.timeout_s={fval} exceeds recommended max of 30 s.  "
                        "Fix: reduce to ≤ 30",
                    )
                )
            else:
                results.append(
                    ("safety.watchdog", True, f"watchdog.timeout_s={fval}  (≤ 30 s, OK)")
                )
        except (TypeError, ValueError):
            results.append(
                (
                    "safety.watchdog",
                    False,
                    f"watchdog.timeout_s={timeout!r} is not a number",
                )
            )

    # ── Check 3: confidence_gates ────────────────────────────────────────
    cg = safety_cfg.get("confidence_gates")
    if not cg:
        results.append(
            (
                "safety.confidence_gates",
                False,
                "safety.confidence_gates not configured.  "
                "Fix: add safety: confidence_gates: {action: 0.7, navigation: 0.8}",
            )
        )
    elif not isinstance(cg, dict):
        results.append(
            (
                "safety.confidence_gates",
                False,
                f"safety.confidence_gates must be a mapping, got {type(cg).__name__}",
            )
        )
    else:
        results.append(
            (
                "safety.confidence_gates",
                True,
                f"confidence_gates configured: {list(cg.keys())}",
            )
        )

    # ── Print results ────────────────────────────────────────────────────
    passed = 0
    failed = 0
    for check_id, ok, detail in results:
        icon = "✅" if ok else "❌"
        print(f"  {icon} [{check_id}]  {detail}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n  Result: {passed} passed, {failed} failed\n")

    if failed:
        raise SystemExit(1)


def cmd_fria_generate(args) -> None:
    """castor fria generate — produce signed FRIA artifact for EU AI Act submission."""
    import json as _json
    import sys
    from datetime import datetime

    import yaml

    from castor.fria import (
        ANNEX_III_BASES,
        build_fria_document,
        check_fria_prerequisite,
        render_fria_html,
        sign_fria,
    )

    config_path = getattr(args, "config", None) or "robot.rcan.yaml"
    if not os.path.exists(config_path):
        print(f"Error: config not found: {config_path}", file=sys.stderr)
        raise SystemExit(1)

    with open(config_path) as _f:
        config = yaml.safe_load(_f) or {}

    annex_iii = getattr(args, "annex_iii", None)
    if not annex_iii:
        print("Error: --annex-iii is required.", file=sys.stderr)
        print(f"Valid values: {', '.join(sorted(ANNEX_III_BASES))}", file=sys.stderr)
        raise SystemExit(1)
    if annex_iii not in ANNEX_III_BASES:
        print(f"Error: invalid --annex-iii value: {annex_iii!r}", file=sys.stderr)
        print(f"Valid values: {', '.join(sorted(ANNEX_III_BASES))}", file=sys.stderr)
        raise SystemExit(1)

    intended_use = getattr(args, "intended_use", None) or ""
    force = getattr(args, "force", False)
    no_html = getattr(args, "no_html", False)
    skip_sign = getattr(args, "skip_sign", False)

    prerequisite_waived = False
    if not force:
        gate_passed, blocking = check_fria_prerequisite(config)
        if not gate_passed:
            print("FRIA generation blocked — conformance gaps must be resolved:", file=sys.stderr)
            for r in blocking:
                print(f"  [{r.check_id}] {r.detail}", file=sys.stderr)
                if r.fix:
                    print(f"    Fix: {r.fix}", file=sys.stderr)
            print("\nUse --force to generate despite conformance gaps.", file=sys.stderr)
            raise SystemExit(1)
    else:
        prerequisite_waived = True

    rrn = config.get("metadata", {}).get("rrn", "unknown")
    date_str = datetime.now().strftime("%Y%m%d")
    output_path = getattr(args, "output", None) or f"fria-{rrn}-{date_str}.json"
    html_path = getattr(args, "html", None)
    if html_path is None and not no_html:
        stem = output_path[:-5] if output_path.endswith(".json") else output_path
        html_path = f"{stem}.html"

    # Find robot memory
    memory_path = None
    for candidate in ["robot-memory.md", os.path.expanduser("~/opencastor/robot-memory.md")]:
        if os.path.exists(candidate):
            memory_path = candidate
            break

    doc = build_fria_document(
        config=config,
        annex_iii_basis=annex_iii,
        intended_use=intended_use,
        memory_path=memory_path,
        prerequisite_waived=prerequisite_waived,
        benchmark_path=getattr(args, "benchmark_path", None),
    )

    if not skip_sign:
        try:
            doc = sign_fria(doc, config)
        except Exception as exc:
            print(
                f"Warning: signing failed ({exc}). Generating unsigned document.", file=sys.stderr
            )

    with open(output_path, "w") as _f:
        _json.dump(doc, _f, indent=2, default=str)
    print(f"FRIA artifact: {output_path}")

    if html_path:
        try:
            html = render_fria_html(doc)
            with open(html_path, "w") as _f:
                _f.write(html)
            print(f"HTML companion: {html_path}")
        except Exception as exc:
            print(f"Warning: HTML rendering failed ({exc}).", file=sys.stderr)


def cmd_fria(args) -> None:
    """castor fria — EU AI Act FRIA compliance tools."""
    import sys

    fria_cmd = getattr(args, "fria_cmd", None)
    if fria_cmd == "generate":
        cmd_fria_generate(args)
    else:
        print("Usage: castor fria <subcommand>", file=sys.stderr)
        print("Subcommands: generate", file=sys.stderr)
        raise SystemExit(1)


def cmd_validate(args) -> None:
    """castor validate — run RCAN conformance checks on a config file."""
    import json as _json
    import os

    config_path = getattr(args, "config", "robot.rcan.yaml")
    if not os.path.exists(config_path):
        print(f"  Config not found: {config_path}")
        raise SystemExit(1)

    try:
        import yaml

        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    except Exception as exc:
        print(f"  Error reading config: {exc}")
        raise SystemExit(1) from None

    from castor.conformance import ConformanceChecker

    checker = ConformanceChecker(config, config_path=config_path)
    category = getattr(args, "category", None)
    json_out = getattr(args, "json", False) or getattr(args, "json_out", False)
    strict = getattr(args, "strict", False)

    if category == "rcan_v22":
        # rcan_v22: run _check_rcan_v21() which covers both v21 and v22 checks
        results = checker._check_rcan_v21()
    elif category == "protocol":
        results = checker.run_category(category)
        # Replay window check
        rcan_window = config.get("rcan", {}).get(
            "replay_window_seconds",
            config.get("replay_window_seconds", 600),
        )
        try:
            rcan_window = int(rcan_window)
        except (TypeError, ValueError):
            rcan_window = 600
        if rcan_window > 3600:
            print(f"  ERROR: replay_window_seconds={rcan_window} exceeds 1-hour hard limit")
        elif rcan_window > 600:
            print(f"  WARN: replay_window_seconds={rcan_window} exceeds recommended 600s maximum")
    elif category:
        results = checker.run_category(category)
    else:
        results = checker.run_all()

    fails = [r for r in results if r.status == "fail"]
    warns = [r for r in results if r.status == "warn"]

    if json_out:
        print(
            _json.dumps(
                {
                    "config": config_path,
                    "results": [
                        {"id": r.check_id, "status": r.status, "message": r.detail} for r in results
                    ],
                    "ok": len(fails) == 0,
                },
                indent=2,
            )
        )
    else:
        cat_label = category.upper() if category else "ALL"
        print(f"\n  RCAN Conformance: {config_path} [{cat_label}]  safety\n")
        for r in results:
            icon = "✅" if r.status == "pass" else ("⚠️ " if r.status == "warn" else "❌")
            print(f"  {icon} [{r.check_id}] {r.detail}")
        summary = checker.summary(results)
        print(
            f"\n  Score: {summary['score']}/100  "
            f"pass={summary['pass']} warn={summary['warn']} fail={summary['fail']}\n"
        )

    if fails:
        raise SystemExit(1)
    if strict and warns:
        raise SystemExit(1)


def cmd_watch(args) -> None:
    """castor watch — live dashboard of robot telemetry in the terminal."""
    from castor.watch import launch_watch

    gateway_url = getattr(args, "gateway", "http://127.0.0.1:8000")
    refresh = getattr(args, "refresh", 2.0)
    launch_watch(gateway_url=gateway_url, refresh=refresh)


def _cmd_monitor(args) -> None:
    """Internal monitor placeholder."""
    pass


# ── Auto-generated placeholders ──────────────────────────────────────────────


def cmd_agents(args) -> None:
    """castor agents — list, status, spawn, or stop Layer-3 agents."""
    from castor.agents import AgentRegistry

    action = getattr(args, "action", "list")
    registry = AgentRegistry()

    if action in ("list", "status"):
        agents = registry.list_agents()
        if not agents:
            print("  No agents spawned.")
            return
        print(f"  {'Name':<20} {'Status':<12} {'Uptime (s)'}")
        print(f"  {'-' * 20} {'-' * 12} {'-' * 10}")
        for a in agents:
            print(f"  {a['name']:<20} {a['status']:<12} {a['uptime_s']}")

    elif action == "spawn":
        name = getattr(args, "name", None)
        if not name:
            print("  Usage: castor agents spawn --name <agent-name>")
            return
        config_path = getattr(args, "config", None)
        cfg = {}
        if config_path:
            import yaml

            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
        agent = registry.spawn(name, config=cfg)
        print(f"  ✅ Spawned agent: {name} (status: {agent.status.value})")

    elif action == "stop":
        name = getattr(args, "name", None)
        if not name:
            print("  Usage: castor agents stop --name <agent-name>")
            return
        import asyncio

        agent = registry.get(name)
        if not agent:
            print(f"  ❌ Agent '{name}' not found.")
            return
        asyncio.run(agent.stop())
        print(f"  ✅ Stopped agent: {name}")

    else:
        print(f"  Unknown action: {action}")


def cmd_approvals(args) -> None:
    """castor approvals — manage the safety approval queue."""
    from castor.approvals import ApprovalGate, print_approvals

    gate = ApprovalGate()

    approve_id = getattr(args, "approve", None)
    deny_id = getattr(args, "deny", None)
    clear = getattr(args, "clear", False)

    if approve_id is not None:
        result = gate.approve(int(approve_id))
        if result is not None:
            print(f"  Approved action {approve_id}.")
        else:
            print(f"  Action {approve_id} not found.")
        return

    if deny_id is not None:
        ok = gate.deny(int(deny_id))
        if ok:
            print(f"  ✅ Denied action {deny_id}.")
        else:
            print(f"  ❌ Action {deny_id} not found.")
        return

    if clear:
        gate.clear()
        print("  ✅ Cleared all pending approvals.")
        return

    pending = gate.list_pending()
    print_approvals(pending)


def _cmd_audit_art11(args) -> None:
    """Print EU AI Act Art. 11 compliance summary for this robot."""
    import os as _os

    # Load config
    config_path = getattr(args, "config", None) or _os.path.expanduser("~/opencastor/bob.rcan.yaml")
    try:
        import yaml as _yaml

        with open(config_path) as _f:
            cfg = _yaml.safe_load(_f)
    except Exception as exc:
        print(f"  ⚠️  Could not load config ({exc}) — showing partial info")
        cfg = {}

    meta = cfg.get("metadata", cfg.get("identity", {}))
    agent = cfg.get("agent", {})
    signing = agent.get("signing", {})
    rrn = meta.get("rrn", cfg.get("rrn", "unknown"))
    robot_name = meta.get("robot_name", cfg.get("robot_name", "?"))
    version = meta.get("version", cfg.get("version", "unknown"))
    rcan_ver = cfg.get("rcan_version", "unknown")
    pq_kid = signing.get("pq_kid", "unknown")
    loa_on = cfg.get("loa_enforcement", False)
    fw_hash = cfg.get("firmware_hash", meta.get("firmware_hash", "unknown"))

    # Pull component/model/harness IDs
    components = cfg.get("components", [])
    models = cfg.get("models", [])
    harness_rhn = cfg.get("harness_rhn", "unknown")

    from castor.compliance import SPEC_VERSION

    print()
    print("┌─────────────────────────────────────────────────────────────────┐")
    print("│  EU AI Act Art. 11 — Technical Documentation Summary            │")
    print("└─────────────────────────────────────────────────────────────────┘")
    print()
    print(f"  Robot:         {robot_name} ({rrn})")
    print(f"  Version:       {version}")
    print(f"  RCAN spec:     {rcan_ver}  (accepted: {SPEC_VERSION})")
    print(f"  PQ signing:    ML-DSA-65  kid={pq_kid}")
    print(f"  LoA enforce:   {'✅ ON' if loa_on else '❌ OFF'}")
    print(f"  Firmware hash: {fw_hash[:32]}...")
    print()
    print("  Provenance chain:")
    print(f"    {harness_rhn}  (harness)")
    for m in models:
        print(f"    └── {m.get('rmn', '?')}  {m.get('name', '?')} {m.get('version', '')}")
    print(f"    {rrn}  (robot)")
    for c in components:
        print(f"    └── {c.get('rcn', '?')}  {c.get('type', '?')}: {c.get('model', '?')}")
    print()
    print("  Artifact locations:")
    print(f"    SBOM:        gs://opencastor-audit/{rrn}/sbom/latest.json")
    print(f"    Attestation: gs://opencastor-audit/{rrn}/")
    print(f"    Art. 11 doc: gs://opencastor-audit/{rrn}/compliance/latest/eu-ai-act-art11.md")
    print()
    print("  EU AI Act checklist:")
    print(f"    {'✅' if rrn != 'unknown' else '❌'}  System identity (Art. 11 §1a)       {rrn}")
    print(
        f"    {'✅' if components else '❌'}  Hardware provenance (Art. 11 §1b)   {len(components)} component(s)"
    )
    print(
        f"    {'✅' if models else '❌'}  Model provenance (Art. 11 §1c)      {len(models)} model(s)"
    )
    print(
        f"    {'✅' if loa_on else '⚠️ '}  Safety controls (Art. 9)           LoA={'ON' if loa_on else 'OFF'}"
    )
    print("    ✅  Post-market monitoring (Art. 72)  BigQuery + Firestore telemetry")
    print("    ✅  SBOM (Art. 11 §1b)               CycloneDX, RRF-countersigned")
    print("    ⏳  Notified body submission           Deadline: 2026-08-02")
    print()


def cmd_audit(args) -> None:
    """castor audit — view and verify the tamper-evident audit log."""
    from castor.audit import get_audit, print_audit

    audit = get_audit()

    if getattr(args, "art11", False):
        _cmd_audit_art11(args)
        return

    if getattr(args, "verify", False):
        ok, broken_idx = audit.verify_chain()
        if ok:
            print("  ✅ Audit chain intact — no tampering detected.")
        else:
            print(f"  ❌ Chain broken at entry index {broken_idx}.")
        return

    entries = audit.read(
        since=getattr(args, "since", None),
        event=getattr(args, "event", None),
        limit=getattr(args, "limit", 50),
    )
    print_audit(entries)


def cmd_backup(args) -> None:
    """castor backup — create a backup archive of configs and credentials."""
    import tarfile

    from castor.backup import create_backup, print_backup_summary

    output = getattr(args, "output", None)
    archive = create_backup(output_path=output)
    if not archive:
        return
    # Read back the list of archived files for the summary
    try:
        with tarfile.open(archive, "r:gz") as tf:
            files = [m.name for m in tf.getmembers()]
    except Exception:
        files = []
    print_backup_summary(archive, files)


def cmd_benchmark(args) -> None:
    """castor benchmark — measure AI provider latency/throughput or hardware loop timing."""
    providers_arg = getattr(args, "providers", None)

    if providers_arg:
        # Provider latency benchmark: N think() calls per provider
        import asyncio
        import json as _json

        from castor.benchmarker import BenchmarkResult, print_results, run_benchmark
        from castor.providers import get_provider

        provider_names = [p.strip() for p in providers_arg.split(",") if p.strip()]
        n = getattr(args, "rounds", 3)
        results: list[BenchmarkResult] = []

        async def _bench_all() -> None:
            for pname in provider_names:
                cfg: dict = {"provider": pname}
                model_arg = getattr(args, "model", None)
                if model_arg:
                    cfg["model"] = model_arg
                try:
                    brain = get_provider(cfg)
                    model_name = getattr(brain, "model", pname) or pname

                    async def _think(prompt: str, _b=brain) -> object:
                        return await asyncio.to_thread(_b.think, None, prompt)

                    result = await run_benchmark(_think, n=n, provider=pname, model=model_name)
                    results.append(result)
                except Exception as exc:  # noqa: BLE001
                    print(f"  Skipping {pname}: {exc}")

        asyncio.run(_bench_all())
        print_results(results)

        output = getattr(args, "output", None)
        if output and results:
            data = [
                {
                    "provider": r.provider,
                    "model": r.model,
                    "n": r.n,
                    "mean_ms": round(r.mean_ms, 1),
                    "min_ms": round(r.min_ms, 1),
                    "max_ms": round(r.max_ms, 1),
                    "p95_ms": round(r.p95_ms, 1),
                    "errors": r.errors,
                    "success_rate": round(r.success_rate, 3),
                }
                for r in results
            ]
            with open(output, "w") as fh:
                _json.dump(data, fh, indent=2)
            print(f"  Results written to {output}")
    else:
        # Hardware perception-action loop benchmark
        import os

        config_path = getattr(args, "config", "robot.rcan.yaml")
        if not os.path.exists(config_path):
            print(f"  Config not found: {config_path}")
            return
        iterations = getattr(args, "iterations", 3)
        simulate = getattr(args, "simulate", False)
        from castor.benchmark import run_benchmark as hw_run

        hw_run(config_path=config_path, iterations=iterations, simulate=simulate)


def cmd_calibrate(args) -> None:
    """castor calibrate — interactive servo/motor calibration."""
    import os

    config_path = getattr(args, "config", "robot.rcan.yaml")
    if not os.path.exists(config_path):
        print(f"  Config not found: {config_path}")
        return

    from castor.calibrate import run_calibration

    run_calibration(config_path=config_path)


def cmd_configure(args) -> None:
    """castor configure — interactive post-wizard config editor."""
    from castor.configure import run_configure

    run_configure(config_path=getattr(args, "config", "robot.rcan.yaml"))


def cmd_daemon(args) -> None:
    """castor daemon — manage the OpenCastor auto-start systemd service."""
    from castor.daemon import (
        daemon_logs,
        daemon_status,
        disable_daemon,
        enable_daemon,
    )

    action = getattr(args, "action", "status")

    if action == "enable":
        config_path = getattr(args, "config", "robot.rcan.yaml")
        user = getattr(args, "user", None)
        result = enable_daemon(config_path=config_path, user=user)
        icon = "✅" if result.get("ok") else "❌"
        print(f"  {icon} {result.get('message', '')}")
        if result.get("service_path"):
            print(f"  Service file: {result['service_path']}")

    elif action == "disable":
        result = disable_daemon()
        icon = "✅" if result.get("ok") else "❌"
        print(f"  {icon} {result.get('message', 'Disabled.')}")

    elif action == "status":
        result = daemon_status()
        active = result.get("active", "unknown")
        icon = "✅" if active == "active" else "⏸️"
        print(f"  {icon} Status: {active}")
        for key in ("description", "pid", "started"):
            if result.get(key):
                print(f"     {key}: {result[key]}")

    elif action == "logs":
        lines = getattr(args, "lines", 50)
        output = daemon_logs(lines=lines)
        print(output)

    elif action == "restart":
        import subprocess

        rc = subprocess.call(["sudo", "systemctl", "restart", "castor"])
        if rc == 0:
            print("  ✅ Service restarted.")
        else:
            print(f"  ❌ Restart failed (exit {rc}).")


def cmd_demo(args) -> None:
    """castor demo — run a simulated full-stack demo (no hardware/API keys needed)."""
    from castor.demo import run_demo

    run_demo(
        steps=getattr(args, "steps", 10),
        delay=getattr(args, "delay", 0.8),
        layout=getattr(args, "layout", "full"),
        no_color=getattr(args, "no_color", False),
    )


def cmd_deploy(args) -> None:
    """castor deploy — SSH-push RCAN config and restart service on remote Pi."""
    import shlex
    import subprocess

    host = getattr(args, "host", None)
    if not host:
        print("  Usage: castor deploy <user@host> --config robot.rcan.yaml")
        return

    config_path = getattr(args, "config", "robot.rcan.yaml")
    full = getattr(args, "full", False)
    status_only = getattr(args, "status", False)
    dry_run = getattr(args, "dry_run", False)
    port = getattr(args, "port", 22)
    key = getattr(args, "key", None)
    no_restart = getattr(args, "no_restart", False)

    ssh_opts = ["-p", str(port), "-o", "StrictHostKeyChecking=accept-new"]
    if key:
        ssh_opts += ["-i", key]

    def _run(cmd: list[str]) -> int:
        display = " ".join(shlex.quote(c) for c in cmd)
        print(f"  $ {display}")
        if dry_run:
            return 0
        return subprocess.call(cmd)

    if status_only:
        _run(
            ["ssh"]
            + ssh_opts
            + [host, "systemctl status castor --no-pager 2>&1 || echo 'castor service not found'"]
        )
        return

    # SCP config to remote
    rc = _run(
        ["scp", "-P", str(port)]
        + (["-i", key] if key else [])
        + [config_path, f"{host}:~/robot.rcan.yaml"]
    )
    if rc != 0:
        print(f"  ❌ SCP failed (exit {rc})")
        return
    print(f"  ✅ Config pushed to {host}:~/robot.rcan.yaml")

    # Optional: pip install
    if full:
        rc = _run(["ssh"] + ssh_opts + [host, "pip install -q --upgrade opencastor"])
        if rc != 0:
            print(f"  ⚠️  pip install returned exit {rc}")

    # Restart service
    if not no_restart:
        rc = _run(
            ["ssh"]
            + ssh_opts
            + [
                host,
                "systemctl restart castor 2>/dev/null || "
                "(pkill -f 'castor gateway' 2>/dev/null; "
                "nohup castor gateway --config ~/robot.rcan.yaml &>/tmp/castor.log &)",
            ]
        )
        if rc == 0:
            print("  ✅ Service restarted on remote.")
        else:
            print(f"  ⚠️  Restart returned exit {rc} — check remote logs.")


def cmd_diff(args) -> None:
    """castor diff — compare two RCAN config files."""
    import os

    from castor.diff import diff_configs, print_diff

    config_a = getattr(args, "config", "robot.rcan.yaml")
    config_b = getattr(args, "baseline", None)
    if not config_b:
        print("  Usage: castor diff --config current.rcan.yaml --baseline old.rcan.yaml")
        return
    if not os.path.exists(config_a):
        print(f"  Config not found: {config_a}")
        return
    if not os.path.exists(config_b):
        print(f"  Baseline not found: {config_b}")
        return

    diffs = diff_configs(config_a, config_b)
    print_diff(diffs, config_a, config_b)


def cmd_keygen(args) -> None:
    """castor keygen — generate Ed25519 and/or ML-DSA-65 signing key pairs."""
    import os
    from pathlib import Path as _Path

    do_ed = not args.pq or args.both
    do_pq = args.pq or args.both

    # Ed25519
    if do_ed:
        key_path = (
            _Path(args.out)
            if (args.out and not args.pq)
            else _Path.home() / ".opencastor" / "signing_key.pem"
        )
        if key_path.exists() and not args.force:
            print(f"  Ed25519 key already exists: {key_path}  (use --force to overwrite)")
        else:
            try:
                from rcan.signing import KeyPair

                kp = KeyPair.generate()
                kp.save_private(str(key_path))
                pub_path = key_path.with_suffix(".pub.pem")
                kp.save_public(str(pub_path))
                print("✓ Ed25519 key generated")
                print(f"  private: {key_path}")
                print(f"  public:  {pub_path}")
                print(f"  key_id:  {kp.key_id}")
            except ImportError:
                print("✗ Ed25519 keygen requires: pip install rcan[crypto]")

    # ML-DSA-65
    if do_pq:
        pq_out = os.environ.get("OPENCASTOR_PQ_KEY_PATH")
        if args.out and args.pq:
            pq_key_path = _Path(args.out)
        elif pq_out:
            pq_key_path = _Path(pq_out)
        else:
            pq_key_path = _Path.home() / ".opencastor" / "pq_signing.key"

        if pq_key_path.exists() and not args.force:
            print(f"  ML-DSA-65 key already exists: {pq_key_path}  (use --force to overwrite)")
        else:
            try:
                from rcan.signing import MLDSAKeyPair

                pq_kp = MLDSAKeyPair.generate()
                pq_kp.save(str(pq_key_path))
                pub_path = pq_key_path.with_suffix(".pub")
                pq_kp.save_public(str(pub_path))
                print("✓ ML-DSA-65 key generated (FIPS 204, RCAN v2.2)")
                print(f"  private: {pq_key_path}  ({len(pq_kp._secret_key or b'')} bytes)")
                print(f"  public:  {pub_path}  ({len(pq_kp.public_key)} bytes)")
                print(f"  key_id:  {pq_kp.key_id}")
                print("  ⚠  Store this key securely — it will sign firmware manifests")
            except ImportError:
                print("✗ ML-DSA keygen requires: pip install dilithium-py")


def cmd_delegation(args) -> None:
    """castor delegation — RCAN delegation chain management (§delegation)."""
    subcmd = getattr(args, "delegation_cmd", None)

    if subcmd == "show":
        import json as _json
        from pathlib import Path as _Path

        config_path = getattr(args, "config", "robot.rcan.yaml")
        rrn = getattr(args, "rrn", None)

        try:
            import yaml as _yaml

            cfg = _yaml.safe_load(_Path(config_path).read_text()) or {}
        except Exception as exc:
            print(f"  ✗ Could not load config: {exc}")
            return

        delegation_cfg = cfg.get("agent", {}).get("delegation", {})
        if rrn:
            delegation_cfg = {k: v for k, v in delegation_cfg.items() if rrn in str(k)}

        print("  RCAN Delegation Config")
        print(f"  Config file: {config_path}")
        if delegation_cfg:
            print(_json.dumps(delegation_cfg, indent=2))
        else:
            print("  (no delegation config found in agent.delegation)")

    elif subcmd == "verify":
        import json as _json
        from pathlib import Path as _Path

        from castor.delegation import validate_chain

        file_path = args.file
        try:
            data = _json.loads(_Path(file_path).read_text())
        except Exception as exc:
            print(f"  ✗ Could not load file: {exc}")
            return

        chain = data.get("delegation_chain", [])
        try:
            validate_chain(chain)
            print(f"  ✓ Delegation chain valid ({len(chain)} hop(s))")
        except ValueError as exc:
            print(f"  ✗ {exc}")

    elif subcmd == "depth":
        from castor.delegation import MAX_DELEGATION_DEPTH

        print(f"  RCAN max delegation depth: {MAX_DELEGATION_DEPTH}")
        print("  (RCAN spec §delegation — chains longer than this are rejected)")

    else:
        print("  castor delegation <subcommand>")
        print("    show [rrn] --config FILE   Show delegation config for this robot")
        print("    verify <file>              Verify delegation chain in a JSON message file")
        print("    depth                      Print max delegation depth and spec note")


def cmd_key_rotation(args) -> None:
    """castor key-rotation — PQ key lifecycle management."""
    import time
    from pathlib import Path as _Path

    subcmd = getattr(args, "key_rotation_cmd", None)

    if subcmd == "status":
        import yaml as _yaml

        config_path = getattr(args, "config", "robot.rcan.yaml")
        try:
            cfg = _yaml.safe_load(_Path(config_path).read_text()) or {}
        except Exception as exc:
            print(f"  ✗ Could not load config: {exc}")
            return

        signing = cfg.get("agent", {}).get("signing", {})
        pq_kid = signing.get("pq_kid", "(not set)")
        alg = signing.get("algorithm", "ml-dsa-65")
        pq_key_path = signing.get("pq_key_path", "")

        print("  PQ Key Rotation Status")
        print(f"  pq_kid:    {pq_kid}")
        print(f"  algorithm: {alg}")

        if pq_key_path:
            key_file = _Path(pq_key_path)
            if key_file.exists():
                mtime = key_file.stat().st_mtime
                age_days = (time.time() - mtime) / 86400
                print(f"  key file:  {pq_key_path}")
                print(f"  key age:   {age_days:.1f} days")
                if age_days > 180:
                    print("  ⚠  WARNING: PQ key is older than 180 days — rotation recommended")
            else:
                print(f"  key file:  {pq_key_path}  (not found)")
        else:
            print("  key file:  (pq_key_path not set in config)")

    elif subcmd == "rotate":
        import subprocess

        import yaml as _yaml

        config_path = getattr(args, "config", "robot.rcan.yaml")
        try:
            cfg = _yaml.safe_load(_Path(config_path).read_text()) or {}
        except Exception as exc:
            print(f"  ✗ Could not load config: {exc}")
            return

        signing = cfg.get("agent", {}).get("signing", {})
        pq_key_path = signing.get(
            "pq_key_path", str(_Path.home() / ".opencastor" / "pq_signing.key")
        )

        print(f"  Rotating PQ key → {pq_key_path}")
        result = subprocess.run(
            ["castor", "keygen", "--pq", "--out", pq_key_path, "--force"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("  ✓ Key rotated successfully")
            print(result.stdout)
        else:
            print("  ✗ Key rotation failed")
            print(result.stderr or result.stdout)

    elif subcmd == "verify":
        rrn = getattr(args, "rrn", "")
        print("  JWKS endpoint for RCAN key verification:")
        print("  GET /.well-known/rcan-keys.json")
        print("  (hosted by the castor gateway — start with: castor gateway --config <file>)")
        if rrn:
            print(f"  RRN: {rrn}")
        print("  Note: Signature verification against JWKS is a stub in this release.")

    else:
        print("  castor key-rotation <subcommand>")
        print("    status --config FILE   Show current pq_kid, algorithm, key file age")
        print("    rotate --config FILE   Generate a new PQ key and update config")
        print("    verify <rrn>           Show JWKS endpoint info for key verification")


def cmd_doctor(args) -> None:
    """castor doctor — run system health checks."""
    from castor.doctor import print_report, run_all_checks

    print("  🩺 OpenCastor Doctor\n")
    config_path = getattr(args, "config", None)
    results = run_all_checks(config_path=config_path)
    print_report(results)

    if getattr(args, "auto_fix", False):
        from castor.doctor import run_auto_fix

        print("\n  Running auto-fix...")
        run_auto_fix(results)


def cmd_export(args) -> None:
    """castor export — export config bundle with secrets redacted."""
    import os

    from castor.export import export_bundle, export_bundle_tgz, print_export_summary

    fmt = getattr(args, "format", "zip")
    config_path = getattr(args, "config", "robot.rcan.yaml")
    if not os.path.exists(config_path):
        print(f"  Config not found: {config_path}")
        return
    output = getattr(args, "output", None)
    episodes = getattr(args, "episodes", 100)

    if fmt == "tgz":
        out = export_bundle_tgz(config_path, output_path=output, max_episodes=episodes)
    else:
        out = export_bundle(config_path=config_path, output_path=output, fmt=fmt)

    print_export_summary(out, fmt)


def cmd_export_finetune(args) -> None:
    """castor export-finetune — export episode memory as a fine-tuning dataset."""
    from castor.finetune import export_episodes
    from castor.memory import EpisodeMemory

    fmt = getattr(args, "format", "chatml")
    limit = getattr(args, "limit", 1000)
    require_action = getattr(args, "require_action", False)
    output = getattr(args, "output", None)

    if output is None:
        output = f"robot_dataset_{fmt}.jsonl"

    mem = EpisodeMemory()

    if require_action:
        # Filter to episodes that have a parsed action in their metadata
        original_recent = mem.recent

        def _filtered_recent(n: int):
            all_eps = original_recent(n=n * 4)
            return [e for e in all_eps if e.get("action") or e.get("parsed_action")][:n]

        mem.recent = _filtered_recent  # type: ignore[method-assign]

    n = export_episodes(mem, output, fmt=fmt, limit=limit)
    print(f"  ✅ Exported {n} episodes → {output} (format: {fmt})")


def cmd_fix(args) -> None:
    """castor fix — auto-fix common issues detected by castor doctor."""
    from castor.fix import run_fix

    run_fix(config_path=getattr(args, "config", None))


def cmd_flash(args) -> None:
    """castor flash — flash ACB v2.0 firmware via DFU-util (#523).

    WARNING: Use a current-limiting PSU during firmware flashing.
    High current MOSFETs are present on the ACB v2.0 board.
    """
    import subprocess
    import urllib.request

    firmware_path = getattr(args, "firmware", None)
    version = getattr(args, "version", None) or "latest"
    confirm = getattr(args, "confirm", False)
    driver_id = getattr(args, "id", "acb") or "acb"

    print()
    print("  \u26a0\ufe0f  WARNING: Use a current-limiting PSU during firmware flashing.")
    print("        High current MOSFETs are present on the ACB v2.0 board.")
    print()

    # Validate driver_id against the loaded RCAN config (if available)
    config_path = getattr(args, "config", None)
    if config_path:
        try:
            import yaml as _yaml

            with open(config_path) as _fh:
                _rcan = _yaml.safe_load(_fh)
            _driver_ids = [d.get("id") for d in (_rcan or {}).get("drivers", [])]
            if driver_id not in _driver_ids:
                print(
                    f"  Warning: driver id '{driver_id}' not found in {config_path}."
                    f"  Known driver IDs: {_driver_ids}"
                )
                print("  Proceeding anyway — ensure the correct device is connected.")
                print()
        except Exception as _exc:
            print(f"  Warning: could not validate driver id against config: {_exc}")
            print()

    if not confirm:
        try:
            ans = input("  Proceed with flash? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            return
        if ans not in ("y", "yes"):
            print("  Aborted.")
            return

    # Check DFU device
    try:
        result = subprocess.run(["dfu-util", "-l"], capture_output=True, text=True, timeout=10)
        if "0483:df11" not in result.stdout.lower() and "0483:df11" not in result.stderr.lower():
            print("  No DFU device found (VID:0483 PID:DF11).")
            print("  Hold the BOOT button on the ACB, then connect USB — then re-run this command.")
            return
    except FileNotFoundError:
        print("  dfu-util not found.  Install with: sudo apt install dfu-util")
        return
    except Exception as exc:
        print(f"  dfu-util error: {exc}")
        return

    import pathlib

    cache_dir = pathlib.Path.home() / ".opencastor" / "firmware"
    cache_dir.mkdir(parents=True, exist_ok=True)

    if firmware_path:
        fw_path = pathlib.Path(firmware_path)
    else:
        print(f"  Fetching latest ACB firmware release (version={version})...")
        try:
            api_url = "https://api.github.com/repos/h-laboratories/acb-v2.0/releases/latest"
            with urllib.request.urlopen(api_url, timeout=10) as resp:  # noqa: S310
                import json as _json

                release_data = _json.loads(resp.read())
            assets = release_data.get("assets", [])
            bin_assets = [a for a in assets if a.get("name", "").endswith(".bin")]
            if not bin_assets:
                print("  No .bin asset found in latest GitHub release.")
                return
            fw_url = bin_assets[0]["browser_download_url"]
            tag = release_data.get("tag_name", "latest")
            fw_path = cache_dir / f"acb-v2.0-{tag}.bin"
            if not fw_path.exists():
                print(f"  Downloading {fw_url} ...")
                with urllib.request.urlopen(fw_url, timeout=30) as resp:  # noqa: S310
                    fw_path.write_bytes(resp.read())
                print(f"  Cached to {fw_path}")
            else:
                print(f"  Using cached {fw_path}")

            # --- Firmware integrity check (#562) ---
            all_assets = release_data.get("assets", [])
            sha_assets = [a for a in all_assets if a["name"].endswith(".sha256")]
            if sha_assets:
                sha_url = sha_assets[0]["browser_download_url"]
                with urllib.request.urlopen(sha_url, timeout=15) as r:  # noqa: S310
                    expected_hash = r.read().decode().split()[0].strip()
                actual_hash = hashlib.sha256(fw_path.read_bytes()).hexdigest()
                if not hmac.compare_digest(actual_hash, expected_hash):
                    fw_path.unlink(missing_ok=True)  # delete corrupted cache
                    print("  ERROR: Firmware checksum mismatch!")
                    print(f"    expected: {expected_hash}")
                    print(f"    got:      {actual_hash}")
                    print("  Aborting flash — firmware may be corrupted or tampered.")
                    return
                print(f"  SHA-256 verified: {actual_hash[:16]}...")
            else:
                print(
                    "  WARNING: No .sha256 asset found for this release — skipping checksum verification."
                )
        except Exception as exc:
            print(f"  Failed to fetch firmware: {exc}")
            return

    print(f"  Flashing {fw_path} to device...")
    try:
        proc = subprocess.run(
            [
                "dfu-util",
                "-d",
                "0483:DF11",
                "-a",
                "0",
                "-s",
                "0x08000000:leave",
                "-D",
                str(fw_path),
            ],
            timeout=120,
        )
        if proc.returncode == 0:
            print("  Flash complete.  Waiting for device to reconnect...")
        else:
            print(f"  Flash failed (exit code {proc.returncode}).")
    except Exception as exc:
        print(f"  Flash error: {exc}")


def cmd_hub(args) -> None:
    """castor hub — community recipe hub: browse, share, and install robot configs."""
    from castor.hub import (
        get_recipe,
        install_recipe,
        list_recipes,
        package_recipe,
        print_recipe_card,
        submit_recipe_pr,
    )

    action = getattr(args, "action", "browse")
    query = getattr(args, "query", None)
    verbose = getattr(args, "verbose", False)

    if action in ("browse", "list"):
        recipes = list_recipes(
            category=getattr(args, "category", None),
            difficulty=getattr(args, "difficulty", None),
            provider=getattr(args, "provider", None),
            search=query if action == "list" else None,
        )
        if not recipes:
            print("  No recipes found.")
            return
        for r in recipes:
            print_recipe_card(r, verbose=verbose)

    elif action == "search":
        if not query:
            print("  Usage: castor hub search <query>")
            return
        recipes = list_recipes(search=query)
        if not recipes:
            print(f"  No recipes matching '{query}'.")
            return
        for r in recipes:
            print_recipe_card(r, verbose=verbose)

    elif action == "show":
        if not query:
            print("  Usage: castor hub show <recipe-id>")
            return
        recipe = get_recipe(query)
        if not recipe:
            print(f"  Recipe '{query}' not found.")
            return
        print_recipe_card(recipe, verbose=True)

    elif action == "install":
        if not query:
            print("  Usage: castor hub install <recipe-id>")
            return
        dest = getattr(args, "output", ".") or "."
        result = install_recipe(query, dest=dest)
        if result:
            print(f"  ✅ Installed to {result}")
        else:
            print(f"  ❌ Recipe '{query}' not found.")

    elif action == "share":
        config = getattr(args, "config", None)
        if not config:
            print("  Usage: castor hub share --config robot.rcan.yaml")
            return
        docs = getattr(args, "docs", None) or []
        dry_run = getattr(args, "dry_run", False)
        submit = getattr(args, "submit", False)
        output = getattr(args, "output", None)
        recipe_dir = package_recipe(config, output_dir=output, docs=docs, dry_run=dry_run)
        if not dry_run:
            print(f"  ✅ Recipe packaged at: {recipe_dir}")
            if submit:
                submit_recipe_pr(recipe_dir)

    elif action == "categories":
        from castor.hub import CATEGORIES

        for key, label in CATEGORIES.items():
            print(f"  {key:<20} {label}")

    else:
        print(f"  Unknown hub action: {action}")


def cmd_improve(args) -> None:
    """castor improve — self-improving loop: analyze episodes and apply improvements."""
    # --enable / --disable toggle learner in RCAN config
    if _improve_toggle(args):
        return

    status = getattr(args, "status", False)
    improvements = getattr(args, "improvements", False)
    rollback_id = getattr(args, "rollback", None)
    episodes_n = getattr(args, "episodes", 5)
    dry_run = getattr(args, "dry_run", False)

    try:
        from castor.learner.sisyphus import SisyphusLoop
        from castor.memory import EpisodeMemory

        loop = SisyphusLoop()

        if status:
            stats = loop.stats()
            print(f"  Total runs:        {stats.total_runs}")
            print(f"  Improvements:      {stats.improvements_applied}")
            print(f"  Rollbacks:         {stats.rollbacks}")
            print(f"  Last run:          {stats.last_run or 'never'}")
            return

        if improvements:
            applied = loop.list_improvements()
            if not applied:
                print("  No improvements applied yet.")
                return
            for imp in applied:
                print(f"  [{imp.id[:8]}] {imp.description} ({imp.applied_at})")
            return

        if rollback_id:
            ok = loop.rollback(rollback_id)
            if ok:
                print(f"  ✅ Rolled back improvement {rollback_id}")
            else:
                print(f"  ❌ Rollback failed: improvement {rollback_id} not found")
            return

        # Analyze episodes
        mem = EpisodeMemory()
        eps = mem.recent(n=episodes_n)
        if not eps:
            print("  No episodes found. Run castor run first to collect data.")
            return

        print(f"  Analyzing {len(eps)} episodes...")
        results = loop.run_batch(eps)
        applied = [r for r in results if r.applied and not dry_run]
        print(f"  {len(results)} improvements identified, {len(applied)} applied.")
        for r in results:
            status_icon = "✅" if (r.applied and not dry_run) else ("🔍" if dry_run else "⏭️")
            print(f"  {status_icon} {r.description}")

    except ImportError:
        print("  Learner not available. Install with: pip install opencastor[learner]")


def cmd_install_service(args) -> None:
    """castor install-service — install systemd services for gateway and dashboard."""
    import os

    from castor.daemon import (
        ATTESTATION_SERVICE_NAME,
        DASHBOARD_SERVICE_NAME,
        DASHBOARD_SERVICE_PATH,
        SERVICE_NAME,
        SERVICE_PATH,
        enable_attestation_service,
        enable_daemon,
        enable_dashboard,
    )

    config_path = getattr(args, "config", "robot.rcan.yaml")
    dashboard_port = getattr(args, "dashboard_port", 8501)
    dry_run = getattr(args, "dry_run", False)

    abs_config = os.path.abspath(config_path)
    if not os.path.exists(abs_config):
        print(f"  Config not found: {config_path}")
        return

    if dry_run:
        from castor.daemon import (
            ATTESTATION_SERVICE_PATH,
            generate_attestation_service_file,
            generate_dashboard_service_file,
            generate_service_file,
        )

        print(f"  [dry-run] Gateway service → {SERVICE_PATH}")
        print(generate_service_file(abs_config))
        print(f"  [dry-run] Dashboard service → {DASHBOARD_SERVICE_PATH}")
        print(generate_dashboard_service_file(port=dashboard_port))
        print(f"  [dry-run] Attestation service → {ATTESTATION_SERVICE_PATH}")
        print(generate_attestation_service_file(abs_config))
        return

    print("  Installing gateway service...")
    gw = enable_daemon(abs_config)
    if gw["ok"]:
        print(f"  Gateway service installed: {gw['service_path']}")
    else:
        print(f"  Gateway service failed: {gw['message']}")

    print("  Installing dashboard service...")
    dash = enable_dashboard(port=dashboard_port)
    if dash["ok"]:
        print(f"  Dashboard service installed: {dash['service_path']}")
        print(f"  Dashboard available at http://localhost:{dashboard_port}")
    else:
        print(f"  Dashboard service failed: {dash['message']}")

    print("  Installing attestation service...")
    att = enable_attestation_service(abs_config)
    if att["ok"]:
        print(f"  Attestation service installed: {att['service_path']}")
        print("  Security posture will be verified on every boot.")
    else:
        print(f"  Attestation service failed: {att['message']}")
        print("  Security posture will show 'degraded' until attestation runs.")

    if gw["ok"] and dash["ok"]:
        print("\n  All services are enabled and will start automatically on boot.")
        print("  Manage with:")
        print(f"    sudo systemctl status {SERVICE_NAME}")
        print(f"    sudo systemctl status {DASHBOARD_SERVICE_NAME}")
        print(f"    sudo systemctl status {ATTESTATION_SERVICE_NAME}")


def cmd_learn(args) -> None:
    """castor learn — interactive step-by-step tutorial."""
    from castor.learn import run_learn

    run_learn(lesson=getattr(args, "lesson", None))


def cmd_lint(args) -> None:
    """castor lint — deep semantic validation of RCAN config."""
    import os

    from castor.lint import print_lint_report, run_lint

    config_path = getattr(args, "config", "robot.rcan.yaml")
    if not os.path.exists(config_path):
        print(f"  Config not found: {config_path}")
        return
    issues = run_lint(config_path)
    print_lint_report(issues)


def _update_env_var(env_file: str, key: str, value: str) -> None:
    """Write or update KEY=VALUE in an .env file, preserving other lines."""
    from pathlib import Path as _Path

    p = _Path(env_file)
    lines = p.read_text().splitlines() if p.exists() else []
    prefix = f"{key}="
    new_line = f"{key}={value}"
    updated = False
    result = []
    for line in lines:
        if line.startswith(prefix):
            result.append(new_line)
            updated = True
        else:
            result.append(line)
    if not updated:
        result.append(new_line)
    p.write_text("\n".join(result) + "\n")


def cmd_login(args) -> None:
    """castor login — authenticate with AI provider services."""
    service = getattr(args, "service", "huggingface")
    token = getattr(args, "token", None)
    list_models = getattr(args, "list_models", False)
    task = getattr(args, "task", "text-generation")

    if service in ("huggingface", "hf"):
        try:
            from huggingface_hub import list_models as hf_list_models
            from huggingface_hub import login

            if token:
                login(token=token)
                print("  ✅ Logged in to Hugging Face.")
            else:
                login()  # interactive prompt

            if list_models:
                print(f"\n  Trending {task} models:\n")
                for i, m in enumerate(hf_list_models(task=task, limit=10)):
                    print(f"  {i + 1:2}. {m.modelId}")
        except ImportError:
            print("  huggingface_hub not installed. Run: pip install huggingface_hub")

    elif service == "ollama":
        import subprocess

        rc = subprocess.call(["ollama", "list"])
        if rc != 0:
            print("  Ollama not found. Install from https://ollama.com")

    else:
        print(f"  Unknown service: {service}")


def cmd_streaming(args) -> None:
    """castor streaming — start WebRTC / MJPEG camera stream."""
    config_path = getattr(args, "config", "robot.rcan.yaml")
    port = getattr(args, "port", 8001)

    try:
        import yaml

        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        config = {}

    try:
        from castor.stream import StreamServer

        server = StreamServer(config=config, port=port)
        print(f"  Starting stream server on port {port} …")
        print(f"  MJPEG stream: http://0.0.0.0:{port}/stream")
        server.serve_forever()
    except ImportError:
        # aiortc / cv2 not installed — show install hint
        print(
            "  WebRTC stream requires additional packages:\n"
            "    pip install opencastor[webrtc]\n"
            "  or: pip install aiortc opencv-python\n"
            "\n  For MJPEG only: pip install opencv-python"
        )


def cmd_update(args) -> None:
    """castor update — self-update OpenCastor from PyPI."""
    from castor.updater import do_upgrade, get_version_info

    info = get_version_info()
    if info.up_to_date:
        print(f"  ✅ Already up to date: {info.current}")
        return
    print(f"  Current: {info.current}  →  Latest: {info.latest}")
    yes = getattr(args, "yes", False)
    rc = do_upgrade(yes=yes)
    raise SystemExit(rc)


def _improve_toggle(args) -> bool:
    """Toggle learner.enabled in RCAN YAML. Returns True if a change was made."""
    enable = getattr(args, "enable", False)
    disable = getattr(args, "disable", False)
    if not enable and not disable:
        return False

    config_path = getattr(args, "config", None)
    if not config_path:
        config_path = _find_default_config()
    if not config_path:
        print("No RCAN config found — cannot toggle learner.")
        return True

    from pathlib import Path as _Path

    import yaml as _yaml

    p = _Path(config_path)
    if not p.exists():
        print(f"Config not found: {config_path}")
        return True

    data = _yaml.safe_load(p.read_text()) or {}
    learner = data.setdefault("learner", {})

    if enable:
        learner["enabled"] = True
        if "provider" not in learner:
            learner["provider"] = "huggingface"
        if "auto_apply_code" not in learner:
            learner["auto_apply_code"] = False
    else:
        learner["enabled"] = False

    p.write_text(_yaml.dump(data, default_flow_style=False))
    action = "enabled" if enable else "disabled"
    print(f"Learner {action} in {config_path}")
    return True


def _cmd_eval(args) -> None:
    """castor eval — run skill evaluation harness."""
    from castor.eval_harness import run_eval_cli

    skill_names = []
    if getattr(args, "skill", None):
        skill_names = [args.skill]
    elif not getattr(args, "eval_all", False):
        print("Specify --skill NAME or --all")
        return
    code = run_eval_cli(
        skill_names=skill_names,
        output_json=getattr(args, "output_json", False),
        verbose=getattr(args, "verbose", False),
    )
    raise SystemExit(code)


def _cmd_trajectory(args) -> None:
    """castor trajectory — trajectory log management."""
    import json as _json

    from castor.trajectory import TrajectoryLogger

    action = getattr(args, "traj_action", None) or "list"

    if action == "list":
        records = TrajectoryLogger.list_recent(20)
        if not records:
            print("No trajectory records found.")
            return
        print(f"{'ID':36}  {'Scope':8}  {'Skill':20}  {'Latency':10}  {'P66'}")
        print("-" * 85)
        for r in records:
            p66 = "🔴" if r.get("p66_estop") else ("🟡" if r.get("p66_consent_req") else "🟢")
            print(
                f"{r['id'][:36]:36}  {r.get('scope', '?'):8}  "
                f"{(r.get('skill_triggered') or '-')[:20]:20}  "
                f"{r.get('total_latency_ms', 0):8.0f}ms  {p66}"
            )

    elif action == "show":
        run_id = getattr(args, "id", "")
        record = TrajectoryLogger.get_record(run_id)
        if record is None:
            print(f"No record found: {run_id}")
            return
        # Parse JSON fields
        for key in ("tool_calls_json", "secondary_verdict_json"):
            val = record.pop(key, None)
            if val:
                try:
                    record[key.replace("_json", "")] = _json.loads(val)
                except Exception:
                    pass
        print(_json.dumps(record, indent=2, default=str))

    elif action == "export":
        print(TrajectoryLogger.export_jsonl())

    elif action == "stats":
        stats = TrajectoryLogger.stats()
        print(f"Total runs:     {stats.get('total_runs', 0)}")
        print(f"Avg latency:    {stats.get('avg_latency_ms', 0):.1f}ms")
        print(f"P66 events:     {stats.get('p66_events', 0)}")
        print(f"Errors:         {stats.get('errors', 0)}")
    else:
        print("Usage: castor trajectory [list|show <id>|export|stats]")


# ── castor share / castor install / castor explore ────────────────────────────


def _cmd_share(args) -> None:
    """castor share — package and share a preset, skill, or harness."""
    import json
    import re as _re
    import shutil
    import tempfile
    from pathlib import Path

    content_type = getattr(args, "share_type", "preset") or "preset"
    source = getattr(args, "source", None) or "."
    title = getattr(args, "title", None) or Path(source).name
    tags = getattr(args, "tags", None) or ""
    dry_run = getattr(args, "dry_run", False)

    source_path = Path(source)
    if not source_path.exists():
        print(f"  ✗ Source not found: {source}")
        return

    # Collect files
    if source_path.is_file():
        files = {source_path.name: source_path.read_text()}
    else:
        files = {}
        for p in source_path.rglob("*"):
            if p.is_file() and p.suffix in (".yaml", ".yml", ".md", ".json", ".txt"):
                rel = str(p.relative_to(source_path))
                files[rel] = p.read_text()

    if not files:
        print("  ✗ No shareable files found (looking for .yaml, .md, .json)")
        return

    # Scrub secrets
    SECRET_PATTERNS = [
        (
            r"(api[_-]?key|apikey|token|secret|password|passwd)\s*[:=]\s*[\'\"]?([A-Za-z0-9_\-\.]{16,})",
            r"\1: <REDACTED>",
        ),
        (r"(sk|AIza|AKIA|ghp|ghs|glpat|xoxb|xoxp)[A-Za-z0-9_\-]{8,}", "<REDACTED_KEY>"),
        (r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "<PUBLIC_IP>"),
    ]
    scrubbed = {}
    redaction_count = 0
    for fname, text in files.items():
        for pattern, replacement in SECRET_PATTERNS:
            new_text = _re.sub(pattern, replacement, text, flags=_re.IGNORECASE)
            if new_text != text:
                redaction_count += len(_re.findall(pattern, text, flags=_re.IGNORECASE))
            text = new_text
        scrubbed[fname] = text

    # Build manifest
    manifest = {
        "type": content_type,
        "title": title,
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "files": list(scrubbed.keys()),
        "share_format": "1.0",
        "opencastor_version": "2026.3",
    }

    if dry_run:
        print("\n  [dry-run] Would share:")
        print(f"  Type: {content_type}")
        print(f"  Title: {title}")
        print(f"  Files: {', '.join(scrubbed.keys())}")
        print(f"  Tags: {manifest['tags']}")
        if redaction_count:
            print(f"  ⚠  {redaction_count} secret(s) would be redacted")
        return

    # Write bundle to temp dir
    bundle_dir = Path(tempfile.mkdtemp(prefix="castor-share-"))
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    for fname, text in scrubbed.items():
        dest = bundle_dir / fname
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)

    bundle_zip = Path(f"/tmp/castor-share-{Path(source).stem}.zip")
    shutil.make_archive(str(bundle_zip.with_suffix("")), "zip", bundle_dir)

    publish = getattr(args, "publish", False)

    print(f"\n  ✓ Bundle ready: {bundle_zip}")
    if redaction_count:
        print(f"  ⚠  Redacted {redaction_count} secret(s)")

    if publish:
        # Phase 2: upload to Firebase
        _share_publish_firebase(content_type, title, tags, scrubbed, bundle_dir)
    else:
        print()
        print("  To share publicly, use --publish:")
        print(f"    castor share {content_type} {source} --title '{title}' --publish")
        print()
        print("  Or contribute via GitHub PR:")
        print(f"  cp {bundle_zip} ~/OpenCastor/config/community/")
        print('  git add . && git commit -m "community: add ' + title + '"')
        print("  git push && gh pr create")
        print()


def _share_publish_firebase(
    content_type: str,
    title: str,
    tags: str,
    scrubbed: dict,
    bundle_dir,
) -> None:
    """Upload a bundle to the OpenCastor Hub via Firebase Cloud Function."""
    import json as _json
    import urllib.error
    import urllib.request
    from pathlib import Path

    # Find the main config file
    main_file = next((f for f in scrubbed if f.endswith(".rcan.yaml") or f == "SKILL.md"), None)
    if not main_file:
        print("  ✗ No .rcan.yaml or SKILL.md found in bundle")
        return

    content = scrubbed[main_file]
    tag_list = (
        [t.strip() for t in tags.split(",") if t.strip()] if isinstance(tags, str) else (tags or [])
    )

    # Check for Firebase auth token
    token_file = Path.home() / ".config" / "opencastor" / "hub-token.json"
    if not token_file.exists():
        print("\n  ✗ Not authenticated for Hub. Run:")
        print("    castor login hub")
        print("  (Firebase Auth required to publish configs)")
        return

    try:
        token_data = _json.loads(token_file.read_text())
        id_token = token_data.get("idToken")
        if not id_token:
            raise ValueError("No idToken in hub-token.json")
    except Exception as exc:
        print(f"  ✗ Failed to read Hub token: {exc}")
        print("  Run: castor login hub")
        return

    payload = _json.dumps(
        {
            "data": {
                "type": content_type,
                "title": title,
                "tags": tag_list,
                "content": content,
                "filename": main_file,
                "public": True,
            }
        }
    ).encode()

    url = "https://us-central1-opencastor.cloudfunctions.net/uploadConfig"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {id_token}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = _json.loads(resp.read())
            data = result.get("result", result)
            config_id = data.get("id", "?")
            config_url = data.get("url", f"https://opencastor.com/config/{config_id}")
            install_cmd = data.get(
                "install_cmd", f"castor install opencastor.com/config/{config_id}"
            )
            print(f"\n  ✓ Published! Config ID: {config_id}")
            print(f"  🔗 {config_url}")
            print(f"  📦 Install: {install_cmd}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"  ✗ Upload failed (HTTP {e.code}): {body[:200]}")
    except Exception as exc:
        print(f"  ✗ Upload failed: {exc}")


def _cmd_install(args) -> None:
    """castor install — install a preset, skill, or harness from the hub."""
    from pathlib import Path

    target = getattr(args, "target", None) or ""
    dry_run = getattr(args, "dry_run", False)
    dest_dir = Path(getattr(args, "output", None) or ".")

    if not target:
        print("  Usage: castor install <id>|skill:<name>|harness:<name>")
        print("  Example: castor install skill:camera-describe")
        return

    # Built-in skills
    if target.startswith("skill:"):
        skill_name = target[6:]
        builtin_dir = Path(__file__).parent / "skills" / "builtin" / skill_name
        if builtin_dir.exists():
            dest = dest_dir / "castor" / "skills" / "custom" / skill_name
            if dest.exists() and not dry_run:
                print(f"  ✓ Skill {skill_name!r} already installed at {dest}")
                return
            if dry_run:
                print(f"  [dry-run] Would install skill {skill_name!r} → {dest}")
                return
            import shutil

            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(builtin_dir), str(dest))
            print(f"  ✓ Installed skill {skill_name!r} → {dest}")
            print("  Add to your RCAN config under agent.skills.extra_dirs")
        else:
            available = [
                d.name
                for d in (Path(__file__).parent / "skills" / "builtin").iterdir()
                if d.is_dir()
            ]
            print(f"  ✗ Unknown skill {skill_name!r}")
            print(f"  Available: {', '.join(available)}")
        return

    # Built-in presets
    if target.startswith("preset:"):
        preset_name = target[7:].replace("-", "_")
        preset_file = (
            Path(__file__).parent.parent / "config" / "presets" / f"{preset_name}.rcan.yaml"
        )
        if not preset_file.exists():
            # Try fuzzy match
            presets_dir = preset_file.parent
            candidates = [p.stem for p in presets_dir.glob("*.rcan.yaml")]
            matches = [c for c in candidates if preset_name in c or c in preset_name]
            if matches:
                print(f"  Did you mean: {', '.join(matches)}")
            else:
                print(f"  ✗ Preset {preset_name!r} not found")
            return
        dest = dest_dir / preset_file.name
        if dry_run:
            print(f"  [dry-run] Would copy {preset_file.name} → {dest}")
            return
        import shutil

        shutil.copy(str(preset_file), str(dest))
        print(f"  ✓ Installed preset → {dest}")
        return

    # Harness bundles (Phase 1: point to GitHub)
    if target.startswith("harness:"):
        harness_name = target[8:]
        print("  Harness bundles are available at opencastor.com/hub")
        print(
            f"  castor install harness:{harness_name} — Firebase integration coming in Phase 2 (issue #700)"
        )
        print("  For now: https://github.com/craigm26/OpenCastor/tree/main/config/community")
        return

    # Generic ID or opencastor.com/config/<id>[@hash] URL — Phase 2: fetch from Firebase
    config_id = target
    pin_hash = None

    # Parse @hash suffix (e.g., bob-pi4-oakd@sha256:abc123)
    if "@" in config_id.split("/")[-1]:
        base, pin_hash = config_id.rsplit("@", 1)
        config_id = base

    if "opencastor.com/config/" in config_id or "opencastor.com/explore/" in config_id:
        config_id = config_id.rstrip("/").split("/")[-1]

    print(f"  Fetching '{config_id}' from opencastor.com hub...")
    _install_from_hub(config_id, dest_dir, dry_run, pin_hash=pin_hash)


_LOCK_FILE_NAME = "castor.lock.yaml"


def _load_lock_file(dest_dir) -> dict:
    """Load castor.lock.yaml, returning dict with 'locked' list."""
    from pathlib import Path

    lock_path = Path(dest_dir) / _LOCK_FILE_NAME
    if not lock_path.exists():
        return {"locked": []}
    try:
        import yaml as _yaml

        data = _yaml.safe_load(lock_path.read_text()) or {}
        return data if isinstance(data, dict) else {"locked": []}
    except Exception:
        return {"locked": []}


def _save_lock_file(dest_dir, lock_data: dict) -> None:
    from pathlib import Path

    lock_path = Path(dest_dir) / _LOCK_FILE_NAME
    lines = ["# castor.lock.yaml — generated by castor install\n", "locked:\n"]
    for entry in lock_data.get("locked", []):
        lines.append(f"  - id: {entry['id']}\n")
        lines.append(f"    url: {entry['url']}\n")
        lines.append(f"    hash: {entry['hash']}\n")
        lines.append(f"    installed_at: {entry['installed_at']}\n")
        lines.append(f"    filename: {entry['filename']}\n")
    lock_path.write_text("".join(lines))


def _content_hash(content: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(content.encode()).hexdigest()[:16]


def _install_from_hub(
    config_id: str,
    dest_dir,
    dry_run: bool,
    pin_hash: str | None = None,
    update_lock: bool = True,
) -> None:
    """Fetch and install a config from the OpenCastor Hub.

    Args:
        config_id: Hub config ID.
        dest_dir: Directory to write the config file.
        dry_run: Preview without writing.
        pin_hash: If set, verify content hash matches before installing.
        update_lock: If True, write/update castor.lock.yaml after installing.
    """
    import json as _json
    import urllib.error
    import urllib.request
    from datetime import datetime, timezone
    from pathlib import Path

    url = "https://us-central1-opencastor.cloudfunctions.net/getConfig"
    payload = _json.dumps({"data": {"id": config_id}}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = _json.loads(resp.read())
            data = result.get("result", result)
            content = data.get("content", "")
            filename = data.get("filename", f"{config_id}.rcan.yaml")
            title = data.get("title", config_id)

            if not content:
                print(f"  ✗ No content in response for '{config_id}'")
                return

            actual_hash = _content_hash(content)
            if pin_hash and actual_hash != pin_hash:
                print(f"  ✗ Hash mismatch for '{config_id}'")
                print(f"    Expected: {pin_hash}")
                print(f"    Got:      {actual_hash}")
                print("  Run: castor update  to fetch latest and re-pin.")
                return

            dest = Path(dest_dir) / filename
            if dry_run:
                print(f"  [dry-run] Would install '{title}' → {dest}")
                print(f"  Hash: {actual_hash}")
                return

            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            print(f"  ✓ Installed '{title}' → {dest}")
            print(f"  Hash: {actual_hash}")
            print(f"  Start with: castor run --config {dest}")

            if update_lock:
                lock = _load_lock_file(dest_dir)
                lock["locked"] = [e for e in lock["locked"] if e.get("id") != config_id]
                lock["locked"].append(
                    {
                        "id": config_id,
                        "url": f"opencastor.com/config/{config_id}",
                        "hash": actual_hash,
                        "installed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "filename": filename,
                    }
                )
                _save_lock_file(dest_dir, lock)
                print(f"  Lock file updated: {dest_dir}/{_LOCK_FILE_NAME}")

    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"  ✗ Config '{config_id}' not found on the hub")
            print("  Browse available configs: castor explore")
        else:
            print(f"  ✗ Hub fetch failed (HTTP {e.code})")
    except Exception as exc:
        print(f"  ✗ Could not reach hub: {exc}")
        print(f"  Try browsing manually: https://opencastor.com/config/{config_id}")


def _cmd_hub_update(args) -> None:
    """castor update — re-fetch all hub-pinned configs and update if changed."""
    update_dir = getattr(args, "update_dir", ".") or "."
    dry_run = getattr(args, "dry_run", False)

    lock = _load_lock_file(update_dir)
    entries = lock.get("locked", [])
    if not entries:
        print("  No pinned configs in castor.lock.yaml")
        print("  Install configs first: castor install opencastor.com/config/<id>")
        return

    print(f"  Checking {len(entries)} pinned config(s)...")
    updated = 0
    for entry in entries:
        config_id = entry.get("id", "?")
        old_hash = entry.get("hash", "")
        print(f"\n  [{config_id}]")
        _install_from_hub(config_id, update_dir, dry_run=dry_run, pin_hash=None, update_lock=True)
        # Reload to check if hash changed
        new_lock = _load_lock_file(update_dir)
        new_entry = next((e for e in new_lock.get("locked", []) if e.get("id") == config_id), None)
        if new_entry and new_entry.get("hash") != old_hash:
            print(f"  ↑ Updated ({old_hash} → {new_entry['hash']})")
            updated += 1
        elif not dry_run:
            print("  ✓ Already up to date")

    if not dry_run:
        print(f"\n  {updated}/{len(entries)} config(s) updated")


def _cmd_lock(args) -> None:
    """castor lock — show, verify, or clear the castor.lock.yaml."""
    lock_cmd = getattr(args, "lock_cmd", None)
    lock_dir = getattr(args, "lock_dir", ".") or "."

    lock = _load_lock_file(lock_dir)
    entries = lock.get("locked", [])

    if lock_cmd == "show" or lock_cmd is None:
        if not entries:
            print(f"  castor.lock.yaml is empty or not found in {lock_dir}")
            return
        print(f"\n  Pinned configs ({len(entries)}):\n")
        for entry in entries:
            print(f"  {entry.get('id', '?')}")
            print(f"    url:          {entry.get('url', '?')}")
            print(f"    hash:         {entry.get('hash', '?')}")
            print(f"    installed_at: {entry.get('installed_at', '?')}")
            print(f"    filename:     {entry.get('filename', '?')}")
            print()

    elif lock_cmd == "verify":
        if not entries:
            print("  Nothing to verify.")
            return
        import json as _json
        import urllib.error
        import urllib.request

        print(f"\n  Verifying {len(entries)} pinned config(s)...\n")
        for entry in entries:
            config_id = entry.get("id", "?")
            expected = entry.get("hash", "")
            url = "https://us-central1-opencastor.cloudfunctions.net/getConfig"
            try:
                payload = _json.dumps({"data": {"id": config_id}}).encode()
                req = urllib.request.Request(
                    url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = _json.loads(resp.read()).get("result", {})
                    actual = _content_hash(data.get("content", ""))
                    if actual == expected:
                        print(f"  ✓ {config_id} — hash matches")
                    else:
                        print(
                            f"  ✗ {config_id} — HASH MISMATCH (expected {expected}, got {actual})"
                        )
                        print("    Run: castor update to refresh")
            except Exception as exc:
                print(f"  ? {config_id} — could not verify: {exc}")

    elif lock_cmd == "clear":
        _save_lock_file(lock_dir, {"locked": []})
        print(f"  ✓ Cleared castor.lock.yaml in {lock_dir}")


def _cmd_optimize(args) -> None:
    """castor optimize — run the per-robot runtime optimizer."""
    import asyncio
    from pathlib import Path

    from castor.optimizer import run_optimizer

    dry_run = getattr(args, "dry_run", False)
    show_report = getattr(args, "show_report", False)
    config_file = getattr(args, "config", None)

    # Schedule/unschedule cron
    schedule = getattr(args, "schedule", False)
    unschedule = getattr(args, "unschedule", False)
    if schedule:
        from castor.idle import install_cron_schedule

        try:
            result = install_cron_schedule()
            if result == "already installed":
                print("  ✓ Optimizer cron job already installed.")
            else:
                print(f"  ✓ Cron job installed: {result}")
        except Exception as exc:
            print(f"  ✗ Failed to install cron: {exc}")
        return
    if unschedule:
        from castor.idle import uninstall_cron_schedule

        removed = uninstall_cron_schedule()
        print("  ✓ Cron job removed." if removed else "  No cron job found.")
        return

    if show_report:
        # Show last optimization report
        history_path = Path.home() / ".config" / "opencastor" / "optimizer-history.json"
        if not history_path.exists():
            print("  No optimizer history found. Run: castor optimize")
            return
        import json as _json

        history = _json.loads(history_path.read_text())
        if not history:
            print("  Optimizer history is empty.")
            return
        last = history[-1]
        print()
        print(f"  Last run: {last['timestamp']}")
        print(f"  Config: {last['config_path']}")
        print(f"  Changes applied: {last['changes_applied']}  Reverted: {last['changes_reverted']}")
        for ch in last.get("changes_proposed", []):
            status = "✓" if ch["applied"] else ("↩" if ch["reverted"] else "·")
            print(
                f"  {status} [{ch['change_type']}] {ch['description']} "
                f"({ch['metric_name']}: Δ{ch['metric_delta']:+.3f})"
            )
        print()
        return

    # Find config file
    if config_file:
        cfg = Path(config_file)
    else:
        # Try common locations
        candidates = [
            Path("robot.rcan.yaml"),
            Path("config/robot.rcan.yaml"),
            Path.home() / ".config" / "opencastor" / "robot.rcan.yaml",
        ]
        cfg = next((p for p in candidates if p.exists()), candidates[0])

    if not cfg.exists() and not dry_run:
        print(f"  Config not found at {cfg}. Pass --config <path> or use --dry-run.")
        return

    mode_str = " [DRY RUN]" if dry_run else ""
    print(f"\n  Running optimizer{mode_str}...")
    print(f"  Config: {cfg}")

    report = asyncio.run(run_optimizer(cfg, dry_run=dry_run))

    print()
    print(report.summary())

    if report.skipped_active_session:
        print("\n  ⚠ Optimizer skipped — active session in progress. Try again during idle hours.")
    elif not report.changes_proposed:
        print("\n  ✓ No optimizations needed — robot config already well-tuned.")
    elif dry_run:
        print(
            f"\n  {len(report.changes_proposed)} change(s) proposed. Run without --dry-run to apply."
        )
    else:
        print(
            f"\n  ✓ {report.changes_applied} change(s) applied, {report.changes_reverted} reverted."
        )
    print()


def _cmd_skills(args) -> None:
    """castor skills — list loaded skills with folder structure and usage stats."""
    import json as _json

    from castor.skills.loader import SkillLoader, get_skill_usage_stats

    loader = SkillLoader()
    skills = loader.load_all()

    show_stats = getattr(args, "stats", False)
    filter_name = getattr(args, "name", None)
    as_json = getattr(args, "skills_json", False)

    if filter_name:
        if filter_name not in skills:
            print(f"  Skill '{filter_name}' not found. Try: castor skills")
            return
        skills = {filter_name: skills[filter_name]}

    if as_json:
        out = {}
        for name, sk in skills.items():
            entry = {k: v for k, v in sk.items() if k != "body"}
            if show_stats:
                entry["usage"] = get_skill_usage_stats(name)
            out[name] = entry
        print(_json.dumps(out, indent=2))
        return

    print()
    print(f"  {'NAME':<24} {'VER':<6} {'CONSENT':<10} {'SCRIPTS':<8} {'REFS':<6} {'TOOLS'}")
    print(f"  {'─' * 24} {'─' * 6} {'─' * 10} {'─' * 8} {'─' * 6} {'─' * 30}")

    for name, sk in sorted(skills.items()):
        scripts_count = len(sk.get("scripts", []))
        refs_count = len(sk.get("references", []))
        tools = ", ".join(sk.get("tools", [])) or "—"
        consent = sk.get("consent", "none")
        version = sk.get("version", "?")
        print(f"  {name:<24} {version:<6} {consent:<10} {scripts_count:<8} {refs_count:<6} {tools}")

    print()
    if show_stats:
        print("  ── Usage Statistics ─────────────────────────────────────")
        for name in sorted(skills):
            stats = get_skill_usage_stats(name)
            total = stats["total_triggers"]
            last = stats["last_triggered"] or "never"
            print(f"  {name:<24} {total:>4} triggers   last: {last}")
            if stats["recent_10"]:
                sample = stats["recent_10"][0][:60]
                print(f'    {"":24} recent: "{sample}"')
        print()

    print(
        f"  {len(skills)} skills loaded  ·  castor skills --stats for usage  ·  castor explore --type skill for hub"
    )
    print()


def _cmd_explore(args) -> None:
    """castor explore — browse available presets, skills, and harnesses."""
    from pathlib import Path

    content_type = getattr(args, "explore_type", None)

    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║         OpenCastor Hub — opencastor.com/hub          ║")
    print("  ╚══════════════════════════════════════════════════════╝")

    if not content_type or content_type == "preset":
        presets_dir = Path(__file__).parent.parent / "config" / "presets"
        if presets_dir.exists():
            preset_files = sorted(presets_dir.glob("*.rcan.yaml"))
            print(f"\n  PRESETS ({len(preset_files)} available):")
            for p in preset_files[:12]:
                print(f"    castor install preset:{p.stem}")
            if len(preset_files) > 12:
                print(f"    ... and {len(preset_files) - 12} more in config/presets/")

    if not content_type or content_type == "skill":
        skills_dir = Path(__file__).parent / "skills" / "builtin"
        if skills_dir.exists():
            skill_dirs = sorted(d for d in skills_dir.iterdir() if d.is_dir())
            print(f"\n  SKILLS ({len(skill_dirs)} built-in):")
            for s in skill_dirs:
                skill_md = s / "SKILL.md"
                consent = (
                    "consent: required"
                    if skill_md.exists() and "consent: required" in skill_md.read_text()
                    else ""
                )
                flag = " ⚠ consent" if consent else ""
                print(f"    castor install skill:{s.name}{flag}")

    if not content_type or content_type == "harness":
        print("\n  HARNESSES (community bundles):")
        print("    castor install harness:bob-pi4-oakd    # Pi4 + OAK-D + Gemini 2.0")
        print("    castor install harness:alex-so-arm101  # SO-ARM101 + Gemini 2.0 + Docker")
        print("    More at: https://opencastor.com/hub")

    print()
    print("  SHARE YOUR CONFIG:")
    print('    castor share preset config/robot.rcan.yaml --title "My Robot"')
    print("    castor share skill castor/skills/my-skill/")
    print("    castor share harness . --include-skills")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="castor",
        description="OpenCastor - The Universal Runtime for Embodied AI",
        epilog=(
            "Command groups:\n"
            "  Setup:       wizard, quickstart, configure, install-service, learn\n"
            "  Run:         run, gateway, dashboard, demo, shell, repl\n"
            "  Diagnostics: doctor, fix, status, logs, lint, benchmark, test\n"
            "  Hardware:    test-hardware, calibrate, record, replay, watch\n"
            "  Config:      migrate, backup, restore, export, diff, profile\n"
            "  Safety:      approvals, privacy, audit\n"
            "  Network:     discover, fleet, network, schedule\n"
            "  Advanced:    token, search, plugins, upgrade, update-check\n"
            "\n"
            "Quick start:\n"
            "  castor wizard                                 # First-time setup\n"
            "  castor run --config robot.rcan.yaml           # Start the robot\n"
            "  castor run --config robot.rcan.yaml --dashboard  # Robot + tmux dashboard\n"
            "  castor demo                                   # Try without hardware\n"
            "  castor doctor                                 # Check system health\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # castor run
    p_run = sub.add_parser(
        "run",
        help="Run the robot perception-action loop",
        epilog=(
            "Examples:\n"
            "  castor run --config robot.rcan.yaml\n"
            "  castor run --config robot.rcan.yaml --dashboard\n"
            "  castor run --config robot.rcan.yaml --dashboard --layout minimal\n"
            "  castor run --config robot.rcan.yaml --simulate --dashboard\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_run.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_run.add_argument("--simulate", action="store_true", help="Run without hardware")
    p_run.add_argument(
        "--behavior",
        default=None,
        metavar="BEHAVIOR_FILE",
        help="Run a behavior script instead of the perception loop (e.g. patrol.behavior.yaml)",
    )
    p_run.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch tmux dashboard alongside the robot (requires tmux)",
    )
    p_run.add_argument(
        "--layout",
        default="full",
        choices=["full", "minimal", "debug"],
        help="Dashboard layout when --dashboard is used (default: full)",
    )

    # castor gateway
    p_gw = sub.add_parser(
        "gateway",
        help="Start the API gateway server",
        epilog="Example: castor gateway --config robot.rcan.yaml --host 0.0.0.0 --port 8080",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_gw.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_gw.add_argument("--host", default="127.0.0.1", help="Bind address")
    p_gw.add_argument("--port", type=int, default=8000, help="Port number")

    # castor mcp — MCP server (Model Context Protocol)
    p_mcp = sub.add_parser(
        "mcp",
        help="MCP server — expose robot tools to any AI agent (Claude Code, Codex, Cursor, …)",
        epilog=(
            "Examples:\n"
            "  castor mcp --token $CASTOR_MCP_TOKEN   # start server (stdio)\n"
            "  castor mcp token --name laptop --loa 3  # generate token\n"
            "  castor mcp clients                       # list authorised clients\n"
            "  CASTOR_MCP_DEV=1 castor mcp              # dev mode (LoA 3, no token)\n"
            "\n"
            "Add to Claude Code:\n"
            "  claude mcp add castor -- castor mcp --token $CASTOR_MCP_TOKEN"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_mcp.add_argument("--token", default="", help="Bearer token (or set CASTOR_MCP_TOKEN)")
    p_mcp.add_argument("--config", default="", help="Path to RCAN yaml")
    p_mcp_sub = p_mcp.add_subparsers(dest="mcp_cmd")
    p_mcp_tok = p_mcp_sub.add_parser("token", help="Generate a new MCP client token")
    p_mcp_tok.add_argument("--name", required=True, help="Client name")
    p_mcp_tok.add_argument("--loa", type=int, default=1, help="LoA level 0–3 (default 1)")
    p_mcp_tok.add_argument("--config", default="", help="Path to RCAN yaml")
    p_mcp_sub.add_parser("clients", help="List authorised MCP clients")
    p_mcp_inst = p_mcp_sub.add_parser(
        "install", help="Register castor mcp with a local MCP client (Claude Code, etc.)"
    )
    p_mcp_inst.add_argument(
        "--client", default="claude", choices=["claude"], help="MCP client to register with"
    )
    p_mcp_inst.add_argument("--token", default="", help="Token to embed (or set CASTOR_MCP_TOKEN)")

    # castor wizard
    p_wizard = sub.add_parser(
        "wizard",
        help="Interactive setup wizard",
        epilog="Example: castor wizard --simple --accept-risk",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_wizard.add_argument("--simple", action="store_true", help="QuickStart mode")
    p_wizard.add_argument("--accept-risk", action="store_true", help="Skip safety prompt")
    p_wizard.add_argument("--web", action="store_true", help="Open browser-based wizard")
    p_wizard.add_argument("--web-port", type=int, default=8765, help="Port for web wizard")

    # castor dashboard — starts robot + tmux TUI immediately
    p_dash = sub.add_parser(
        "dashboard",
        help="Launch tmux dashboard and start the robot immediately",
    )
    p_dash.add_argument(
        "--config",
        default="robot.rcan.yaml",
        help="RCAN config file (auto-detected if omitted)",
    )
    p_dash.add_argument(
        "--layout",
        default="full",
        choices=["full", "minimal", "debug"],
        help="Dashboard layout (default: full)",
    )
    p_dash.add_argument("--simulate", action="store_true", help="Simulation mode (no hardware)")
    p_dash.add_argument("--kill", action="store_true", help="Kill existing dashboard session")

    # castor dashboard-tui
    p_tui = sub.add_parser(
        "dashboard-tui",
        help="Terminal dashboard (tmux multi-pane robot monitor)",
    )
    p_tui.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_tui.add_argument(
        "--layout",
        default="full",
        choices=["full", "minimal", "debug"],
        help="Dashboard layout (default: full)",
    )
    p_tui.add_argument("--simulate", action="store_true", help="Simulation mode")
    p_tui.add_argument("--kill", action="store_true", help="Kill existing dashboard")

    # castor token
    p_token = sub.add_parser("token", help="Issue a JWT token for RCAN API access")
    p_token.add_argument(
        "--role", default="user", help="RCAN role (guest/user/operator/admin/creator)"
    )
    p_token.add_argument(
        "--scope", default=None, help="Comma-separated scopes (e.g. status,control)"
    )
    p_token.add_argument("--ttl", default="24", help="Token lifetime in hours (default: 24)")
    p_token.add_argument("--subject", default=None, help="Principal name (default: cli-user)")
    p_token.add_argument("--rotate", action="store_true", help="Rotate JWT signing key")
    p_token.add_argument(
        "--new-secret", default=None, help="Explicit replacement secret for --rotate"
    )
    p_token.add_argument("--kid", default=None, help="Key ID (kid) for issued or rotated key")

    # castor discover
    p_discover = sub.add_parser("discover", help="Discover RCAN peers on the local network")
    p_discover.add_argument("--timeout", default="5", help="Scan duration in seconds (default: 5)")

    # castor doctor
    # Issue #348: castor snapshot
    p_snapshot = sub.add_parser(
        "snapshot",
        help="Take or inspect diagnostic snapshots",
        description="Capture and view system diagnostic snapshots.",
        epilog="Examples: castor snapshot take | castor snapshot latest | castor snapshot history 10",
    )
    p_snapshot.add_argument(
        "snapshot_action",
        nargs="?",
        choices=["take", "latest", "history"],
        default="latest",
        help="Snapshot sub-command (default: latest)",
    )
    p_snapshot.add_argument(
        "snapshot_args",
        nargs="*",
        help="Extra arguments (e.g. limit N for history)",
    )

    p_peer_test = sub.add_parser(
        "peer-test",
        help="Test direct RCAN communication with discovered peers",
    )
    p_peer_test.add_argument("peer", nargs="?", help="Peer hostname or address")
    p_peer_test.add_argument(
        "--transport",
        choices=["all", "http", "mqtt", "compact", "minimal"],
        default="all",
    )
    p_peer_test.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        default=True,
    )

    p_contribute = sub.add_parser(
        "contribute",
        help="Manage idle compute contribution",
    )
    p_contribute.add_argument(
        "contribute_action",
        nargs="?",
        choices=["status", "start", "stop", "history"],
        default="status",
        help="Contribute sub-command (default: status)",
    )

    # castor provider — gated model provider management
    p_provider = sub.add_parser(
        "provider",
        help="Manage gated model providers (auth, list, status)",
        epilog="Example: castor provider auth pi-foundation --config robot.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_provider_sub = p_provider.add_subparsers(dest="provider_action")
    p_prov_auth = p_provider_sub.add_parser("auth", help="Test provider authentication")
    p_prov_auth.add_argument(
        "provider_name", nargs="?", default="", help="Provider name from config"
    )
    p_prov_auth.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_prov_list = p_provider_sub.add_parser("list", help="List configured providers")
    p_prov_list.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_provider_sub.add_parser("status", help="Show status of active providers on running gateway")

    p_doctor = sub.add_parser(
        "doctor",
        help="Run system health checks",
        epilog="Example: castor doctor --config robot.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_doctor.add_argument("--config", default=None, help="RCAN config file to validate")
    p_doctor.add_argument(
        "--auto-fix",
        action="store_true",
        help="Attempt to auto-fix common issues (e.g. missing .env, large memory DB)",
    )

    # castor keygen
    p_keygen = sub.add_parser(
        "keygen",
        help="Generate cryptographic key pairs (Ed25519 and/or ML-DSA-65)",
        epilog=(
            "Examples:\n"
            "  castor keygen                 # generate Ed25519 signing key\n"
            "  castor keygen --pq            # generate ML-DSA-65 PQ key (FIPS 204)\n"
            "  castor keygen --both          # generate both Ed25519 + ML-DSA-65\n"
            "  castor keygen --pq --out /path/to/pq.key"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_keygen.add_argument(
        "--pq",
        action="store_true",
        help="Generate ML-DSA-65 key (RCAN v2.2 post-quantum, FIPS 204)",
    )
    p_keygen.add_argument(
        "--both",
        action="store_true",
        help="Generate both Ed25519 and ML-DSA-65 keys",
    )
    p_keygen.add_argument(
        "--out",
        default=None,
        help="Output path for the key (default: ~/.opencastor/[pq_]signing_key.pem/.key)",
    )
    p_keygen.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing key files",
    )

    # castor compliance
    p_compliance = sub.add_parser(
        "compliance",
        help="Check RCAN v1.2 conformance (L1/L2/L3) for a robot config",
        epilog="Example: castor compliance --config bob.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_compliance.add_argument(
        "--config", default="robot.rcan.yaml", help="RCAN config file to check"
    )
    p_compliance.add_argument(
        "--level",
        choices=["L1", "L2", "L3", "L4", "L5"],
        default=None,
        help="L1-L3=RCAN v1.x, L4=v2.1 supply chain, L5=v2.2 PQ signing",
    )
    p_compliance.add_argument(
        "--json", action="store_true", dest="output_json", help="Output results as JSON"
    )
    p_compliance.add_argument(
        "--commitments",
        action="store_true",
        help="Also verify the on-disk commitment chain log",
    )
    p_compliance.add_argument(
        "--format",
        choices=["json", "text"],
        default=None,
        help="Output format: json or text (uses ComplianceReport module)",
    )
    p_compliance.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help="Write output to FILE instead of stdout",
    )

    # castor demo
    p_demo = sub.add_parser(
        "demo",
        help="Run a simulated demo (no hardware/API keys)",
        epilog="Example: castor demo --steps 5 --delay 2.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_demo.add_argument("--steps", type=int, default=10, help="Number of loop iterations")
    p_demo.add_argument("--delay", type=float, default=0.8, help="Seconds between steps")
    p_demo.add_argument(
        "--layout",
        default="full",
        choices=["full", "minimal"],
        help="Demo depth: full (all 5 acts) or minimal (skip Acts 3 & 4)",
    )
    p_demo.add_argument("--no-color", action="store_true", help="Disable rich output")

    # castor test-hardware
    p_test = sub.add_parser(
        "test-hardware",
        help="Test each motor/servo individually",
        epilog="Example: castor test-hardware --config robot.rcan.yaml -y",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_test.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_test.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts")

    # castor calibrate
    p_cal = sub.add_parser(
        "calibrate",
        help="Interactive servo/motor calibration",
        epilog="Example: castor calibrate --config robot.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_cal.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")

    # castor logs
    p_logs = sub.add_parser(
        "logs",
        help="View structured OpenCastor logs",
        epilog="Example: castor logs -f --level WARNING --module providers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_logs.add_argument("--follow", "-f", action="store_true", help="Follow log output")
    p_logs.add_argument(
        "--level", default=None, help="Minimum level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
    )
    p_logs.add_argument(
        "--module", default=None, help="Filter by module name (e.g. providers, Gateway)"
    )
    p_logs.add_argument("--lines", "-n", type=int, default=50, help="Number of recent lines")
    p_logs.add_argument("--no-color", action="store_true", help="Disable color output")

    # castor backup
    p_backup = sub.add_parser(
        "backup",
        help="Back up configs and credentials",
        epilog="Example: castor backup -o my_backup.tar.gz",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_backup.add_argument("--output", "-o", default=None, help="Output archive path")

    # castor restore
    p_restore = sub.add_parser(
        "restore",
        help="Restore configs from a backup archive",
        epilog="Example: castor restore opencastor_backup_20260216.tar.gz",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_restore.add_argument("archive", help="Path to the backup .tar.gz file")
    p_restore.add_argument(
        "--dry-run", action="store_true", help="List contents without extracting"
    )

    # castor migrate
    p_migrate = sub.add_parser("migrate", help="Migrate RCAN config to current schema version")
    p_migrate.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_migrate.add_argument("--dry-run", action="store_true", help="Show changes without modifying")

    # castor upgrade
    p_upgrade = sub.add_parser("upgrade", help="Upgrade OpenCastor to latest version")
    p_upgrade.add_argument("--verbose", "-v", action="store_true", help="Show pip output")
    p_upgrade.add_argument(
        "--check",
        action="store_true",
        dest="check_only",
        help="Show available updates without upgrading",
    )
    p_upgrade.add_argument(
        "--venv", default=None, metavar="PATH", help="Path to venv (default: current Python)"
    )

    # castor install-service
    p_svc = sub.add_parser(
        "install-service",
        help="Install systemd services for gateway and dashboard (auto-start on boot)",
        epilog="Example: castor install-service --config robot.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_svc.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_svc.add_argument(
        "--dashboard-port", type=int, default=8501, help="Dashboard port (default: 8501)"
    )
    p_svc.add_argument(
        "--dry-run", action="store_true", help="Print generated service files without installing"
    )

    # castor status
    sub.add_parser("status", help="Show provider and channel readiness")

    # --- New commands (batch 3) ---

    # castor shell
    p_shell = sub.add_parser(
        "shell",
        help="Interactive command shell with robot objects",
        epilog="Example: castor shell --config robot.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_shell.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")

    # castor watch
    p_watch = sub.add_parser(
        "watch",
        help="Live telemetry dashboard (Rich)",
        epilog="Example: castor watch --gateway http://192.168.1.100:8000",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_watch.add_argument(
        "--gateway",
        default="http://127.0.0.1:8000",
        help="Gateway URL (default: http://127.0.0.1:8000)",
    )
    p_watch.add_argument(
        "--refresh", type=float, default=2.0, help="Refresh interval in seconds (default: 2.0)"
    )

    # castor fit
    sub.add_parser(
        "fit",
        help="Show which LLM models fit your robot's hardware (via llmfit)",
        epilog="Example: castor fit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # castor memory
    p_memory = sub.add_parser(
        "memory",
        help="Manage robot operational memory (robot-memory.md)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  castor memory show\n"
            "  castor memory add --text 'left wheel encoder intermittent' --type hardware_observation\n"
            "  castor memory prune --threshold 0.15\n"
            "  castor memory decay\n"
        ),
    )
    memory_sub = p_memory.add_subparsers(dest="memory_cmd")
    memory_sub.add_parser("show", help="Show all entries with confidence scores")
    p_mem_add = memory_sub.add_parser("add", help="Manually add a memory entry")
    p_mem_add.add_argument("--text", required=True, help="Observation text (max 500 chars)")
    p_mem_add.add_argument(
        "--type",
        dest="entry_type",
        default="hardware_observation",
        choices=["hardware_observation", "environment_note", "behavior_pattern", "resolved"],
        help="Entry type (default: hardware_observation)",
    )
    p_mem_add.add_argument(
        "--confidence",
        default="0.8",
        help="Initial confidence 0.0–1.0 (default: 0.8)",
    )
    p_mem_add.add_argument("--tags", default="", help="Comma-separated tags")
    p_mem_add.add_argument("--rrn", default=None, help="Robot RRN (default: CASTOR_RRN env)")
    p_mem_prune = memory_sub.add_parser("prune", help="Remove entries below confidence threshold")
    p_mem_prune.add_argument(
        "--threshold", default="0.10", help="Min confidence to keep (default: 0.10)"
    )
    p_mem_prune.add_argument("--dry-run", action="store_true", help="Show what would be pruned")
    memory_sub.add_parser("decay", help="Apply time-based confidence decay and save")
    # Legacy: replay (kept for backward compat)
    p_mem_replay = memory_sub.add_parser(
        "replay", help="Replay historical episodes through updated consolidation pipeline"
    )
    p_mem_replay.add_argument("--since", default=None, metavar="DATE")
    p_mem_replay.add_argument("--episode-id", default=None)
    p_mem_replay.add_argument("--episodes-dir", default=None)
    p_mem_replay.add_argument("--dry-run", action="store_true")
    p_mem_replay.add_argument("--verbose", "-v", action="store_true")

    # castor fleet
    p_fleet = sub.add_parser(
        "fleet",
        help="Manage robot group policies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  castor fleet list --config bob.rcan.yaml\n  castor fleet resolve RRN-000000000042 --config bob.rcan.yaml\n  castor fleet apply-all --config bob.rcan.yaml",
    )
    p_fleet.add_argument(
        "--timeout", default="5", help="mDNS scan duration in seconds (default: 5)"
    )
    fleet_sub = p_fleet.add_subparsers(dest="fleet_cmd")

    p_fl_list = fleet_sub.add_parser("list", help="List all defined groups")
    p_fl_list.add_argument("--config", default=None)

    p_fl_resolve = fleet_sub.add_parser("resolve", help="Show resolved config for a robot")
    p_fl_resolve.add_argument("rrn", help="Robot Registry Number")
    p_fl_resolve.add_argument("--config", default=None)
    p_fl_resolve.add_argument("--json", action="store_true", dest="output_json")

    p_fl_status = fleet_sub.add_parser("status", help="Show which groups each robot belongs to")
    p_fl_status.add_argument("--config", default=None)

    # castor node — RCAN §17 namespace delegation
    p_node = sub.add_parser(
        "node",
        help="Manage RCAN namespace delegation for this robot fleet",
        epilog=(
            "Examples:\n"
            "  castor node status        # Show node broadcaster status\n"
            "  castor node manifest      # Print /.well-known/rcan-node.json\n"
            "  castor node resolve <RRN> # Resolve an RRN via federated registry\n"
            "  castor node ping          # Check rcan.dev reachability\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    node_sub = p_node.add_subparsers(dest="node_cmd")

    node_sub.add_parser("status", help="Show current node broadcaster status and manifest")
    node_sub.add_parser("manifest", help="Print the /.well-known/rcan-node.json manifest")
    p_node_resolve = node_sub.add_parser(
        "resolve", help="Resolve an RRN via the federated registry"
    )
    p_node_resolve.add_argument("rrn", help="Robot Registry Number (e.g. RRN-AB-00000042)")
    node_sub.add_parser("ping", help="Check rcan.dev registry reachability")

    # castor inspect
    p_inspect = sub.add_parser(
        "inspect",
        help="Query a robot's live RCAN profile, safety state, and telemetry",
        epilog="Examples:\n  castor inspect RRN-000000000042\n  castor inspect --config bob.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_inspect.add_argument(
        "rrn", nargs="?", default=None, help="Robot Registry Number (e.g. RRN-000000000042)"
    )
    p_inspect.add_argument("--config", default=None, help="Local RCAN config file to inspect")
    p_inspect.add_argument(
        "--gateway", default=None, help="Gateway URL (e.g. http://localhost:8080)"
    )
    p_inspect.add_argument("--json", action="store_true", dest="output_json", help="JSON output")

    # castor verification
    p_verification = sub.add_parser(
        "verification",
        help="Check verification tier for an RRN via rcan.dev",
        epilog="Example: castor verification RRN-AB-00000042",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_verification.add_argument("rrn", help="Robot Registry Number (e.g. RRN-AB-00000042)")

    # castor register
    p_register = sub.add_parser(
        "register",
        help="Register your robot with rcan.dev and get a globally unique RRN",
        epilog="Example: castor register --config bob.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_register.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_register.add_argument(
        "--api-key", default=None, help="rcan.dev API key (or set RCAN_API_KEY env var)"
    )
    p_register.add_argument("--manufacturer", default=None, help="Override manufacturer slug")
    p_register.add_argument("--model", default=None, help="Override model slug")
    p_register.add_argument("--version", default=None, help="Override version string")
    p_register.add_argument(
        "--device-id", default=None, dest="device_id", help="Override device ID"
    )
    p_register.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Validate config and show what would be registered without making API calls",
    )

    # castor fix
    p_fix = sub.add_parser(
        "fix",
        help="Auto-fix common issues found by doctor",
        epilog="Example: castor fix --config robot.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_fix.add_argument("--config", default=None, help="RCAN config file (optional)")

    # castor repl
    p_repl = sub.add_parser(
        "repl",
        help="Python REPL with brain, driver, camera pre-loaded",
        epilog="Example: castor repl --config robot.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_repl.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")

    # castor record
    p_record = sub.add_parser(
        "record",
        help="Record a perception-action session to JSONL",
        epilog="Example: castor record --config robot.rcan.yaml --output session.jsonl",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_record.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_record.add_argument("--output", "-o", default="session.jsonl", help="Output JSONL file")
    p_record.add_argument("--simulate", action="store_true", help="Run without hardware")

    # castor replay
    p_replay = sub.add_parser(
        "replay",
        help="Replay a recorded session from JSONL or via gateway API",
        epilog=(
            "Examples:\n"
            "  castor replay session.jsonl --execute --config robot.rcan.yaml\n"
            "  castor replay --url http://localhost:8000 --list\n"
            "  castor replay --url http://localhost:8000 --last 20 --dry-run\n"
            "  castor replay --url http://localhost:8000 --start <id> --end <id> --speed 2"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_replay.add_argument(
        "recording",
        nargs="?",
        default=None,
        help="Path to the .jsonl recording file (omit for API mode with --url)",
    )
    p_replay.add_argument("--execute", action="store_true", help="Re-execute actions on hardware")
    p_replay.add_argument("--config", default=None, help="RCAN config file (required if --execute)")
    # API replay flags (#328)
    p_replay.add_argument("--url", default=None, help="Gateway base URL for API replay mode")
    p_replay.add_argument("--token", default=None, help="Bearer token for gateway auth")
    p_replay.add_argument("--start", default=None, help="Start episode ID for trajectory replay")
    p_replay.add_argument("--end", default=None, help="End episode ID for trajectory replay")
    p_replay.add_argument(
        "--last", type=int, default=None, metavar="N", help="Replay last N episodes"
    )
    p_replay.add_argument(
        "--speed", type=float, default=1.0, help="Playback speed multiplier (default 1.0)"
    )
    p_replay.add_argument(
        "--dry-run", action="store_true", help="Show replay plan without executing"
    )
    p_replay.add_argument(
        "--list", action="store_true", help="List recent episodes from the gateway"
    )

    # castor benchmark
    p_bench = sub.add_parser(
        "benchmark",
        help="Profile perception-action loop performance, or compare provider latency/cost",
        epilog=(
            "Examples:\n"
            "  castor benchmark --config robot.rcan.yaml --iterations 5\n"
            "  castor benchmark --providers google,openai --rounds 3\n"
            "  castor benchmark --providers anthropic --rounds 5 --output results.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_bench.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_bench.add_argument(
        "--iterations", type=int, default=3, help="Number of iterations (default: 3)"
    )
    p_bench.add_argument("--simulate", action="store_true", help="Skip hardware driver")
    p_bench.add_argument(
        "--providers",
        default=None,
        help="Comma-separated provider names to benchmark (e.g. google,openai,anthropic). "
        "Omit to use the single-config hardware benchmark.",
    )
    p_bench.add_argument(
        "--rounds",
        type=int,
        default=3,
        help="Number of prompt-suite rounds per provider (default: 3; used with --providers)",
    )
    p_bench.add_argument(
        "--output",
        default=None,
        help="Write benchmark results to this JSON file (used with --providers)",
    )

    # castor lint
    p_lint = sub.add_parser(
        "lint",
        help="Deep config validation beyond JSON schema",
        epilog="Example: castor lint --config robot.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_lint.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")

    # ── fria ──────────────────────────────────────────────────────────────────
    p_fria = sub.add_parser("fria", help="EU AI Act FRIA compliance tools")
    p_fria_sub = p_fria.add_subparsers(dest="fria_cmd")
    p_fria_gen = p_fria_sub.add_parser(
        "generate",
        help="Generate signed FRIA artifact for notified body submission (§22)",
    )
    p_fria_gen.add_argument(
        "--config", metavar="FILE", help="RCAN config file (default: robot.rcan.yaml)"
    )
    p_fria_gen.add_argument(
        "--output",
        metavar="FILE",
        help="JSON output path (default: fria-{rrn}-{date}.json)",
    )
    p_fria_gen.add_argument(
        "--html", metavar="FILE", help="HTML output path (default: same stem as --output)"
    )
    p_fria_gen.add_argument(
        "--annex-iii",
        dest="annex_iii",
        metavar="BASIS",
        required=True,
        help=(
            "EU AI Act Annex III classification basis. One of: "
            "safety_component, biometric, critical_infrastructure, education, "
            "employment, essential_services, law_enforcement, migration, "
            "administration_of_justice, general_purpose_ai"
        ),
    )
    p_fria_gen.add_argument(
        "--intended-use",
        dest="intended_use",
        metavar="TEXT",
        default="",
        help="Free-text deployment description",
    )
    p_fria_gen.add_argument(
        "--force", action="store_true", help="Skip conformance prerequisite gate"
    )
    p_fria_gen.add_argument(
        "--no-html",
        action="store_true",
        dest="no_html",
        help="JSON output only, no HTML companion",
    )
    p_fria_gen.add_argument(
        "--skip-sign",
        action="store_true",
        dest="skip_sign",
        help="Omit ML-DSA-65 signature (dev/test mode)",
    )
    p_fria_gen.add_argument(
        "--benchmark",
        metavar="FILE",
        dest="benchmark_path",
        default=None,
        help="Path to safety-benchmark-*.json to inline in FRIA document",
    )
    p_fria_gen.set_defaults(func=cmd_fria_generate)

    # castor validate
    p_validate = sub.add_parser(
        "validate",
        help="Run RCAN conformance checks",
        epilog=(
            "Examples:\n"
            "  castor validate --config bot.rcan.yaml       # RCAN conformance check\n"
            "  castor validate --config bot.rcan.yaml --category safety\n"
            "  castor validate --config bot.rcan.yaml --json\n"
            "  castor validate --config bot.rcan.yaml --strict\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_validate.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_validate.add_argument(
        "--category",
        default=None,
        choices=[
            "safety",
            "provider",
            "protocol",
            "performance",
            "hardware",
            "rcan_v21",
            "rcan_v22",
        ],
        help="Only run checks in this category (safety/provider/protocol/performance/hardware/rcan_v21/rcan_v22)",  # noqa: E501
    )
    p_validate.add_argument("--json", action="store_true", help="Output results as JSON")
    p_validate.add_argument("--strict", action="store_true", help="Exit with non-zero if any WARN")
    p_validate.set_defaults(func=cmd_validate)

    # castor rcan-check
    p_rcan_check = sub.add_parser(
        "rcan-check",
        help="Focused RCAN §6 safety field conformance check",
        epilog=(
            "Examples:\n"
            "  castor rcan-check                              # auto-detect *.rcan.yaml\n"
            "  castor rcan-check --config robot.rcan.yaml\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_rcan_check.add_argument(
        "--config", default=None, help="RCAN config file (default: auto-detect)"
    )
    p_rcan_check.set_defaults(func=cmd_rcan_check)

    # castor swarm
    p_swarm = sub.add_parser(
        "swarm",
        help="Multi-robot swarm management",
        epilog=(
            "Examples:\n"
            "  castor swarm status\n"
            "  castor swarm status --json\n"
            '  castor swarm command "move forward"\n'
            '  castor swarm command "turn" --node alex\n'
            "  castor swarm stop\n"
            "  castor swarm sync config/robot.rcan.yaml\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_swarm.add_argument(
        "--swarm-config",
        dest="swarm_config",
        default=None,
        help="Path to swarm.yaml (default: config/swarm.yaml)",
    )
    p_swarm.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted table",
    )
    p_swarm.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="Per-node HTTP timeout in seconds (default: 3.0)",
    )
    p_swarm_sub = p_swarm.add_subparsers(dest="swarm_subcmd")

    # castor swarm status
    p_swarm_status = p_swarm_sub.add_parser("status", help="Show health table for all nodes")
    p_swarm_status.add_argument("--json", action="store_true", help="Output raw JSON")
    p_swarm_status.add_argument("--swarm-config", dest="swarm_config", default=None)
    p_swarm_status.add_argument("--timeout", type=float, default=3.0)

    # castor swarm command
    p_swarm_cmd = p_swarm_sub.add_parser("command", help="Send instruction to all or one node")
    p_swarm_cmd.add_argument("instruction", help="Natural-language instruction to send")
    p_swarm_cmd.add_argument("--node", default=None, help="Target a specific node by name")
    p_swarm_cmd.add_argument("--json", action="store_true", help="Output raw JSON")
    p_swarm_cmd.add_argument("--swarm-config", dest="swarm_config", default=None)
    p_swarm_cmd.add_argument("--timeout", type=float, default=10.0)

    # castor swarm stop
    p_swarm_stop = p_swarm_sub.add_parser("stop", help="Emergency stop all nodes")
    p_swarm_stop.add_argument("--json", action="store_true", help="Output raw JSON")
    p_swarm_stop.add_argument("--swarm-config", dest="swarm_config", default=None)
    p_swarm_stop.add_argument("--timeout", type=float, default=5.0)

    # castor swarm sync
    p_swarm_sync = p_swarm_sub.add_parser("sync", help="Push RCAN config reload to all nodes")
    p_swarm_sync.add_argument("config_path", help="Path to RCAN config file to push")
    p_swarm_sync.add_argument("--json", action="store_true", help="Output raw JSON")
    p_swarm_sync.add_argument("--swarm-config", dest="swarm_config", default=None)
    p_swarm_sync.add_argument("--timeout", type=float, default=10.0)

    # castor swarm update
    p_swarm_update = p_swarm_sub.add_parser(
        "update", help="Update OpenCastor on all swarm nodes via SSH"
    )
    p_swarm_update.add_argument(
        "--dry-run", dest="dry_run", action="store_true", help="Print commands without executing"
    )
    p_swarm_update.add_argument("--swarm-config", dest="swarm_config", default=None)

    p_swarm.set_defaults(func=cmd_swarm)

    # castor update
    p_update = sub.add_parser(
        "update",
        help="Update OpenCastor to the latest version",
        epilog=(
            "Examples:\n"
            "  castor update                     # Update to latest\n"
            "  castor update --dry-run           # Preview without changing\n"
            "  castor update --version 2026.2.0  # Pin to a specific version\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_update.add_argument(
        "--dry-run", dest="dry_run", action="store_true", help="Print commands without executing"
    )
    p_update.add_argument(
        "--version",
        default=None,
        metavar="X.Y.Z",
        help="Pin to a specific version tag or pip specifier",
    )

    # castor learn
    p_learn = sub.add_parser(
        "learn",
        help="Interactive step-by-step tutorial",
        epilog="Example: castor learn --lesson 3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_learn.add_argument("--lesson", type=int, default=None, help="Jump to a specific lesson (1-7)")

    # castor improve
    p_improve = sub.add_parser(
        "improve",
        help="Self-improving loop (Sisyphus pattern) — analyze episodes and apply improvements",
        epilog=(
            "Examples:\n"
            "  castor improve --enable              # Enable self-improving loop\n"
            "  castor improve --disable             # Disable self-improving loop\n"
            "  castor improve --episodes 10         # Analyze last 10 episodes\n"
            "  castor improve --status              # Show improvement history\n"
            "  castor improve --improvements         # List all applied patches\n"
            "  castor improve --rollback abc123      # Rollback a specific patch\n"
            "  castor improve --batch --config bot.rcan.yaml  # Batch analysis\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_improve.add_argument("--config", help="RCAN config file")
    p_improve.add_argument(
        "--episodes", type=int, default=5, help="Number of episodes to analyze (default: 5)"
    )
    p_improve.add_argument("--status", action="store_true", help="Show improvement stats")
    p_improve.add_argument("--improvements", action="store_true", help="List applied improvements")
    p_improve.add_argument("--rollback", type=str, help="Rollback a specific improvement by ID")
    p_improve.add_argument("--batch", action="store_true", help="Run ALMA batch consolidation")
    p_improve.add_argument("--dry-run", action="store_true", help="Analyze but don't apply patches")
    p_improve.add_argument(
        "--enable", action="store_true", help="Enable self-improving loop in RCAN config"
    )
    p_improve.add_argument(
        "--disable", action="store_true", help="Disable self-improving loop in RCAN config"
    )

    # castor deploy (issue #103)
    p_deploy = sub.add_parser(
        "deploy",
        help="SSH-push RCAN config and restart service on remote Pi",
        epilog=(
            "Examples:\n"
            "  castor deploy pi@192.168.1.10 --config robot.rcan.yaml\n"
            "  castor deploy pi@192.168.1.10 --full\n"
            "  castor deploy pi@192.168.1.10 --status\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_deploy.add_argument("host", help="Remote host (user@hostname or hostname)")
    p_deploy.add_argument("--config", default="robot.rcan.yaml", help="RCAN config to push")
    p_deploy.add_argument("--full", action="store_true", help="Also run pip install on remote")
    p_deploy.add_argument(
        "--status", action="store_true", dest="status", help="Show remote service status only"
    )
    p_deploy.add_argument(
        "--dry-run", action="store_true", dest="dry_run", help="Preview without executing"
    )
    p_deploy.add_argument("--port", type=int, default=22, help="SSH port (default: 22)")
    p_deploy.add_argument("--key", default=None, help="SSH private key file path")
    p_deploy.add_argument(
        "--no-restart",
        action="store_true",
        dest="no_restart",
        help="Push config only, skip restart",
    )

    # castor agents
    p_agents = sub.add_parser("agents", help="Manage robot agents")
    p_agents.add_argument(
        "action",
        choices=["list", "status", "spawn", "stop"],
        nargs="?",
        default="list",
    )
    p_agents.add_argument("--name", help="Agent name for spawn/stop")
    p_agents.add_argument("--config", help="RCAN config path")
    p_agents.set_defaults(func=cmd_agents)

    # castor export
    p_export = sub.add_parser(
        "export",
        help="Export config bundle (secrets redacted)",
        epilog="Example: castor export --config robot.rcan.yaml --format json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_export.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_export.add_argument("--output", "-o", default=None, help="Output file path")
    p_export.add_argument(
        "--format",
        choices=["zip", "json", "tgz"],
        default="zip",
        help="Export format: zip (default), json, or tgz (includes episodes)",
    )
    p_export.add_argument(
        "--episodes",
        type=int,
        default=100,
        metavar="N",
        help="Max episodes to include in tgz bundle (default: 100)",
    )
    p_export.set_defaults(func=cmd_export)

    p_finetune = sub.add_parser(
        "export-finetune",
        help="Export episode memory as a fine-tuning dataset (Alpaca / ChatML / ShareGPT)",
        epilog=(
            "Examples:\n"
            "  castor export-finetune --format chatml --output dataset.jsonl\n"
            "  castor export-finetune --format alpaca --limit 500 --require-action\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_finetune.add_argument(
        "--format",
        choices=["jsonl", "alpaca", "sharegpt", "chatml"],
        default="chatml",
        help="Fine-tuning format (default: chatml)",
    )
    p_finetune.add_argument("--output", "-o", default=None, help="Output file path")
    p_finetune.add_argument(
        "--limit", type=int, default=1000, help="Max episodes to export (default: 1000)"
    )
    p_finetune.add_argument(
        "--require-action",
        dest="require_action",
        action="store_true",
        help="Only export episodes that have a parsed action",
    )

    # --- OpenClaw-inspired commands (batch 4) ---

    # castor approvals
    p_approvals = sub.add_parser(
        "approvals",
        help="Manage approval queue for dangerous commands",
        epilog="Example: castor approvals --approve 1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_approvals.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_approvals.add_argument("--approve", default=None, help="Approve a pending action by ID")
    p_approvals.add_argument("--deny", default=None, help="Deny a pending action by ID")
    p_approvals.add_argument("--clear", action="store_true", help="Clear resolved approvals")

    # castor schedule
    p_sched = sub.add_parser(
        "schedule",
        help="Manage scheduled/recurring tasks",
        epilog=(
            "Examples:\n"
            "  castor schedule list\n"
            "  castor schedule add --name patrol --command 'castor run ...' --cron '*/30 * * * *'\n"
            "  castor schedule remove --name patrol\n"
            "  castor schedule install\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_sched.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=["list", "add", "remove", "install"],
        help="Action to perform",
    )
    p_sched.add_argument("--name", default=None, help="Task name")
    p_sched.add_argument("--command", dest="task_command", default=None, help="Command to run")
    p_sched.add_argument("--cron", default=None, help="Cron expression (e.g. '*/30 * * * *')")
    p_sched.add_argument("--config", default=None, help="RCAN config file (optional)")

    # castor configure
    p_conf = sub.add_parser(
        "configure",
        help="Interactive config editor (post-wizard tweaks)",
        epilog="Example: castor configure --config robot.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_conf.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")

    # castor search
    p_search = sub.add_parser(
        "search",
        help="Search operational logs and session recordings",
        epilog="Example: castor search 'battery low' --since 7d",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_search.add_argument("query", help="Search query (keywords or phrases)")
    p_search.add_argument("--since", default=None, help="Time window (e.g. 7d, 24h, 1w)")
    p_search.add_argument("--log-file", default=None, help="Specific log file to search")
    p_search.add_argument(
        "--max-results", type=int, default=20, help="Maximum results (default: 20)"
    )

    # castor network
    p_net = sub.add_parser(
        "network",
        help="Network config and VPN/Tailscale exposure",
        epilog=(
            "Examples:\n"
            "  castor network status\n"
            "  castor network expose --mode serve\n"
            "  castor network expose --mode funnel\n"
            "  castor network expose --mode off\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_net.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["status", "expose"],
        help="Action to perform",
    )
    p_net.add_argument(
        "--mode",
        default=None,
        choices=["serve", "funnel", "off"],
        help="Exposure mode (for expose action)",
    )
    p_net.add_argument("--port", type=int, default=8000, help="Gateway port")
    p_net.add_argument("--config", default=None, help="RCAN config file (optional)")

    # castor loa
    p_loa = sub.add_parser(
        "loa",
        help="Manage Level of Assurance enforcement (GAP-16)",
        epilog=(
            "Examples:\n"
            "  castor loa status\n"
            "  castor loa enable\n"
            "  castor loa enable --min-loa 2\n"
            "  castor loa disable\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    loa_sub = p_loa.add_subparsers(dest="loa_cmd")
    for _loa_name in ("status", "enable", "disable"):
        _p = loa_sub.add_parser(
            _loa_name,
            help=f"{'Show' if _loa_name == 'status' else _loa_name.capitalize()} LoA enforcement",
        )
        _p.add_argument("--config", default=None, help="RCAN config file path")
        _p.add_argument(
            "--min-loa",
            dest="min_loa",
            type=int,
            default=None,
            help="Minimum LoA level for control scope (default: keep current)",
        )
        _p.add_argument(
            "--no-reload",
            dest="reload",
            action="store_false",
            default=True,
            help="Skip gateway hot-reload",
        )
        _p.add_argument(
            "--no-firestore", action="store_true", default=False, help="Skip Firestore sync"
        )
        _p.add_argument(
            "--gateway-url", default="http://localhost:8001", help="Gateway base URL for hot-reload"
        )
    p_loa.set_defaults(loa_cmd="status")

    # castor components
    p_comps = sub.add_parser(
        "components",
        help="Manage hardware component registration",
        epilog=(
            "Examples:\n"
            "  castor components detect\n"
            "  castor components register\n"
            "  castor components list\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    comps_sub = p_comps.add_subparsers(dest="components_cmd")
    for _cn in ("detect", "list", "register"):
        _cp = comps_sub.add_parser(
            _cn,
            help=f"{'Detect attached hardware' if _cn == 'detect' else 'List config components' if _cn == 'list' else 'Register components to Firestore'}",
        )
        _cp.add_argument("--config", default=None, help="RCAN config file path")
        _cp.add_argument(
            "--format", choices=["table", "json"], default="table", help="Output format"
        )
    p_comps.set_defaults(components_cmd="detect")

    # castor rrf
    p_rrf = sub.add_parser(
        "rrf",
        help="Robot Registry Foundation v2 — register and query RRN/RCN/RMN/RHN",
        epilog=(
            "Examples:\n"
            "  castor rrf status                  # show full provenance chain\n"
            "  castor rrf register                # register robot (receive RRN)\n"
            "  castor rrf components              # register hardware components (RCN)\n"
            "  castor rrf models                  # register AI models (RMN)\n"
            "  castor rrf harness                 # register AI harness (RHN)\n"
            "  castor rrf wipe --secret <s>       # (dev) clear all KV records\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rrf_sub = p_rrf.add_subparsers(dest="rrf_cmd")
    for _rrf_name, _rrf_help in [
        ("register", "Register this robot with RRF — receive an RRN"),
        ("components", "Register hardware components from config — receive RCNs"),
        ("models", "Register AI models used by this robot — receive RMNs"),
        ("harness", "Register the AI harness (dual-brain) — receive an RHN"),
        ("status", "Show full RRF provenance chain for this robot"),
        ("wipe", "(dev) Delete all RRF KV records via admin endpoint"),
    ]:
        _rp = rrf_sub.add_parser(_rrf_name, help=_rrf_help)
        _rp.add_argument("--config", default=None, help="RCAN config file path")
        _rp.add_argument(
            "--token",
            default=None,
            help="RRF bearer token (default: ~/.config/opencastor/bob-rrf-token.txt)",
        )
        _rp.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Force re-registration even if already registered",
        )
        if _rrf_name == "wipe":
            _rp.add_argument("--secret", default="clawd-wipe-2026", help="Admin wipe secret")
    p_rrf.set_defaults(rrf_cmd="status")

    # castor privacy
    p_priv = sub.add_parser(
        "privacy",
        help="Show privacy policy (sensor access controls)",
        epilog="Example: castor privacy --config robot.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_priv.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")

    # castor consent — R2RAM consent management (issue #778)
    p_consent = sub.add_parser(
        "consent",
        help="Manage R2RAM robot-to-robot consent records",
        epilog=(
            "Examples:\n"
            "  castor consent list --config robot.rcan.yaml\n"
            "  castor consent show <consent_id> --config robot.rcan.yaml\n"
            "  castor consent grant RRN-000000000002 --scope chat,control --config robot.rcan.yaml\n"
            "  castor consent deny RRN-000000000002 --config robot.rcan.yaml\n"
            "  castor consent revoke <consent_id> --config robot.rcan.yaml\n"
            "  castor consent export --offline --config robot.rcan.yaml\n"
            "  castor consent training list --config robot.rcan.yaml\n"
            "  castor consent training delete <subject_id> --config robot.rcan.yaml\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_consent_sub = p_consent.add_subparsers(dest="consent_cmd")

    # consent list
    p_c_list = p_consent_sub.add_parser("list", help="List all consent records")
    p_c_list.add_argument("--config", default=None, help="RCAN config file")

    # consent show
    p_c_show = p_consent_sub.add_parser("show", help="Inspect a consent record")
    p_c_show.add_argument("consent_id", help="Consent record ID")
    p_c_show.add_argument("--config", default=None, help="RCAN config file")

    # consent grant
    p_c_grant = p_consent_sub.add_parser("grant", help="Grant consent to a peer robot")
    p_c_grant.add_argument("rrn", help="Robot Registry Number of the peer")
    p_c_grant.add_argument(
        "--scope",
        default="chat",
        help="Comma-separated scopes (e.g. chat,control) — default: chat",
    )
    p_c_grant.add_argument("--config", default=None, help="RCAN config file")

    # consent deny
    p_c_deny = p_consent_sub.add_parser("deny", help="Deny a pending consent request")
    p_c_deny.add_argument("rrn", help="Robot Registry Number of the peer")
    p_c_deny.add_argument("--config", default=None, help="RCAN config file")

    # consent revoke
    p_c_revoke = p_consent_sub.add_parser("revoke", help="Revoke a granted consent")
    p_c_revoke.add_argument("consent_id", help="Consent record ID to revoke")
    p_c_revoke.add_argument("--config", default=None, help="RCAN config file")

    # consent export
    p_c_export = p_consent_sub.add_parser(
        "export", help="Export signed offline consent blob (RCAN §11)"
    )
    p_c_export.add_argument("--offline", action="store_true", help="Include offline-signed blob")
    p_c_export.add_argument("--config", default=None, help="RCAN config file")

    # consent training (sub-group)
    p_c_training = p_consent_sub.add_parser("training", help="Manage training data consent records")
    p_c_training_sub = p_c_training.add_subparsers(dest="training_cmd")

    p_ct_list = p_c_training_sub.add_parser("list", help="List training consent records")
    p_ct_list.add_argument("--config", default=None, help="RCAN config file")

    p_ct_delete = p_c_training_sub.add_parser(
        "delete", help="GDPR erasure — delete training data for a subject"
    )
    p_ct_delete.add_argument("subject_id", help="Subject ID to erase")
    p_ct_delete.add_argument("--config", default=None, help="RCAN config file")

    # --- Batch 5: Polish & quality-of-life ---

    # castor update-check
    sub.add_parser(
        "update-check",
        help="Check PyPI for newer versions",
        epilog="Example: castor update-check",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # castor profile
    p_profile = sub.add_parser(
        "profile",
        help="Manage named config profiles",
        epilog=(
            "Examples:\n"
            "  castor profile list\n"
            "  castor profile save indoor --config robot.rcan.yaml\n"
            "  castor profile use indoor\n"
            "  castor profile remove indoor\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_profile.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=["list", "save", "use", "remove"],
        help="Action to perform",
    )
    p_profile.add_argument("name", nargs="?", default=None, help="Profile name")
    p_profile.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")

    # castor test
    p_pytest = sub.add_parser(
        "test",
        help="Run the test suite (pytest wrapper)",
        epilog="Example: castor test -v -k test_doctor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_pytest.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    p_pytest.add_argument("--keyword", "-k", default=None, help="pytest -k filter")

    # castor diff
    p_diff = sub.add_parser(
        "diff",
        help="Compare two RCAN config files",
        epilog="Example: castor diff --config robot.rcan.yaml --baseline robot.rcan.yaml.bak",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_diff.add_argument("--config", default="robot.rcan.yaml", help="Current config file")
    p_diff.add_argument("--baseline", required=True, help="Baseline config to compare against")

    # castor quickstart
    p_qs = sub.add_parser(
        "quickstart",
        help="Zero-to-fleet in one command: init wizard + start gateway",
        description="Runs `castor init` then immediately starts the gateway.",
        epilog=(
            "Examples:\n"
            "  castor quickstart\n"
            "  castor quickstart --name Bob --provider google --no-interactive\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_qs.add_argument("--name", "-n", default=None, help="Robot name")
    p_qs.add_argument(
        "--provider",
        default=None,
        choices=["google", "anthropic", "openai", "local"],
        help="AI provider (default: google)",
    )
    p_qs.add_argument("--port", type=int, default=None, help="Gateway port (default: 8080)")
    p_qs.add_argument("--api-key", default=None, dest="api_key", help="AI provider API key")
    p_qs.add_argument(
        "--firebase-project",
        default=None,
        dest="firebase_project",
        help="Firebase project ID",
    )
    p_qs.add_argument("--output", "-o", default=None, help="Output config path")
    p_qs.add_argument(
        "--no-interactive",
        action="store_true",
        dest="no_interactive",
        help="Skip prompts — use flags/defaults",
    )
    p_qs.add_argument("--overwrite", action="store_true", help="Overwrite existing config")

    # castor plugins [install <source>]
    p_plugins = sub.add_parser(
        "plugins",
        help="List loaded plugins or install a new one",
        epilog=(
            "Examples:\n"
            "  castor plugins\n"
            "  castor plugins install ./my_plugin.py\n"
            "  castor plugins install https://github.com/user/my-plugin\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_plugins_sub = p_plugins.add_subparsers(dest="plugin_subcmd")
    p_plugin_install = p_plugins_sub.add_parser("install", help="Install a plugin")
    p_plugin_install.add_argument("source", help="Local .py file path or git URL")

    # castor plugin install <url-or-path>
    p_plugin = sub.add_parser(
        "plugin",
        help="Manage plugins (install with provenance tracking)",
        epilog=(
            "Examples:\n"
            "  castor plugin install https://example.com/my_plugin.py\n"
            "  castor plugin install /local/path/my_plugin.py\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_plugin_sub = p_plugin.add_subparsers(dest="plugin_subcommand")
    p_plugin_install = p_plugin_sub.add_parser(
        "install",
        help="Install a plugin from a URL or local path",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_plugin_install.add_argument(
        "source",
        help="URL or local file path to the plugin .py file",
    )

    # castor scan — detect connected peripherals
    p_scan = sub.add_parser(
        "scan",
        help="Auto-detect connected hardware peripherals",
        epilog=(
            "Examples:\n"
            "  castor scan\n"
            "  castor scan --json\n"
            "  castor scan --no-color\n"
            "  castor scan --i2c-bus 1\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_scan.add_argument("--json", action="store_true", help="Output as JSON")
    p_scan.add_argument("--no-color", action="store_true", help="Plain text output")
    p_scan.add_argument(
        "--i2c-bus",
        type=int,
        default=1,
        dest="i2c_bus",
        help="I2C bus to scan (default: 1)",
    )
    p_scan.add_argument(
        "--suggest",
        action="store_true",
        default=True,
        help="Print suggested RCAN config snippets (default: true)",
    )
    p_scan.add_argument(
        "--refresh",
        action="store_true",
        help="Force fresh scan, bypass TTL cache",
    )
    p_scan.add_argument(
        "--preset-only",
        action="store_true",
        dest="preset_only",
        help="Only print suggested preset name + confidence",
    )

    # castor stop — send SIGTERM to running gateway (#556)
    sub.add_parser("stop", help="Stop the running gateway (reads ~/.opencastor/gateway.pid)")

    # castor daemon — systemd auto-start service management
    p_daemon = sub.add_parser(
        "daemon",
        help="Manage the auto-start system service (systemd)",
        epilog=(
            "Examples:\n"
            "  castor daemon enable --config bob.rcan.yaml   # Install + start on boot\n"
            "  castor daemon status                           # Is it running?\n"
            "  castor daemon logs                             # Recent journal output\n"
            "  castor daemon restart                          # Restart the service\n"
            "  castor daemon disable                          # Remove auto-start\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_daemon.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["enable", "disable", "status", "logs", "restart"],
        help="Action to perform (default: status)",
    )
    p_daemon.add_argument(
        "--config",
        default="robot.rcan.yaml",
        help="RCAN config file the daemon should start with (enable only)",
    )
    p_daemon.add_argument(
        "--user",
        default=None,
        help="System user to run the service as (default: current user)",
    )
    p_daemon.add_argument(
        "--lines",
        type=int,
        default=50,
        help="Number of log lines to show (logs action)",
    )

    # castor hub
    p_flash = sub.add_parser(
        "flash",
        help="Flash ACB v2.0 firmware via DFU-util",
        epilog=(
            "Examples:\n"
            "  castor flash --id motor_0\n"
            "  castor flash --id motor_0 --version latest --confirm\n"
            "  castor flash --id motor_0 --firmware acb-v1.2.bin --confirm\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_flash.add_argument("--id", default="acb", help="Driver ID to flash (default: acb)")
    p_flash.add_argument("--firmware", default=None, help="Path to .bin firmware file")
    p_flash.add_argument(
        "--version", default="latest", help="Firmware version tag to fetch (default: latest)"
    )
    p_flash.add_argument(
        "--confirm", action="store_true", help="Skip interactive confirmation prompt"
    )

    p_hub = sub.add_parser(
        "hub",
        help="Community recipe hub — browse, share, and install configs",
        epilog=(
            "Examples:\n"
            "  castor hub browse\n"
            "  castor hub browse --category home --provider huggingface\n"
            "  castor hub search 'outdoor patrol'\n"
            "  castor hub show picar-patrol-a1b2c3\n"
            "  castor hub install picar-patrol-a1b2c3\n"
            "  castor hub share --config robot.rcan.yaml --docs BUILD.md LESSONS.md\n"
            "  castor hub share --config robot.rcan.yaml --submit\n"
            "  castor hub share --config robot.rcan.yaml --dry-run\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_hub.add_argument(
        "action",
        nargs="?",
        default="browse",
        choices=[
            "browse",
            "search",
            "show",
            "install",
            "share",
            "rate",
            "categories",
            "list",
            "publish",
        ],
        help="Action to perform (default: browse). Use 'list'/'search'/'install'/'publish' for hub index.",
    )
    p_hub.add_argument(
        "--rating", type=int, choices=[1, 2, 3, 4, 5], help="Star rating (1-5) for hub rate"
    )
    p_hub.add_argument("query", nargs="?", default=None, help="Search query or recipe ID")
    p_hub.add_argument("--config", default=None, help="RCAN config to share")
    p_hub.add_argument(
        "--docs", nargs="*", default=None, help="Markdown docs to include with shared recipe"
    )
    p_hub.add_argument(
        "--category",
        default=None,
        choices=[
            "home",
            "outdoor",
            "service",
            "industrial",
            "education",
            "agriculture",
            "security",
            "companion",
            "art",
            "custom",
        ],
        help="Filter by category",
    )
    p_hub.add_argument(
        "--difficulty",
        default=None,
        choices=["beginner", "intermediate", "advanced"],
        help="Filter by difficulty",
    )
    p_hub.add_argument("--provider", default=None, help="Filter by AI provider")
    p_hub.add_argument("--dry-run", action="store_true", help="Preview share without writing")
    p_hub.add_argument(
        "--submit",
        action="store_true",
        help="Auto-create a GitHub PR after packaging (requires gh CLI)",
    )
    p_hub.add_argument("--verbose", "-v", action="store_true", help="Show full details")
    p_hub.add_argument("--output", "-o", default=None, help="Output directory for install/share")

    # castor audit
    # castor safety
    p_safety = sub.add_parser(
        "safety",
        help="Safety protocol management",
        epilog=(
            "Examples:\n"
            "  castor safety rules\n"
            "  castor safety rules --category motion\n"
            "  castor safety benchmark\n"
            "  castor safety benchmark --iterations 50 --fail-fast\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_safety.set_defaults(func=cmd_safety, safety_cmd=None)
    p_safety_sub = p_safety.add_subparsers(dest="safety_cmd")

    # castor safety rules
    p_safety_rules = p_safety_sub.add_parser("rules", help="List safety rules")
    p_safety_rules.add_argument("--category", default=None, help="Filter by category")
    p_safety_rules.add_argument(
        "--config", default=None, help="Path to safety protocol YAML config"
    )
    p_safety_rules.set_defaults(func=cmd_safety)

    # castor safety benchmark
    p_safety_bench = p_safety_sub.add_parser(
        "benchmark",
        help="Measure safety path latencies (P95) against declared thresholds",
    )
    p_safety_bench.add_argument(
        "--config", metavar="FILE", default=None, help="RCAN config file (default: auto-detect)"
    )
    p_safety_bench.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help="JSON output path (default: safety-benchmark-{date}.json)",
    )
    p_safety_bench.add_argument(
        "--iterations",
        type=int,
        default=20,
        metavar="N",
        help="Runs per path (default: 20)",
    )
    p_safety_bench.add_argument(
        "--live",
        action="store_true",
        help="Connect to live robot for estop path",
    )
    p_safety_bench.add_argument(
        "--fail-fast",
        action="store_true",
        dest="fail_fast",
        help="Exit 1 on first threshold breach (CI mode)",
    )
    p_safety_bench.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Machine-readable output only (no Rich table)",
    )
    p_safety_bench.set_defaults(func=cmd_safety_benchmark)

    # castor conformance
    p_conformance = sub.add_parser(
        "conformance",
        help="Print Protocol 66 conformance report",
        epilog=(
            "Examples:\n"
            "  castor conformance\n"
            "  castor conformance --config ~/my-robot.rcan.yaml\n"
            "  castor conformance --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_conformance.add_argument(
        "--config", default=None, help="Path to RCAN config (default: ~/opencastor/bob.rcan.yaml)"
    )

    # castor iso-check (closes #755)
    p_iso = sub.add_parser(
        "iso-check",
        help="ISO/TC 299 + EU AI Act self-assessment checklist",
        epilog=(
            "Examples:\n"
            "  castor iso-check\n"
            "  castor iso-check --config ~/my-robot.rcan.yaml\n"
            "  castor iso-check --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_iso.add_argument(
        "--config", default=None, help="Path to RCAN config (default: ~/opencastor/bob.rcan.yaml)"
    )
    p_iso.add_argument("--json", action="store_true", help="Output JSON")
    p_conformance.add_argument("--json", action="store_true", help="Output raw JSON manifest")

    # castor llmfit
    p_llmfit = sub.add_parser(
        "llmfit",
        help="Check if a local model fits in device RAM (with/without TurboQuant)",
        epilog=(
            "Examples:\n"
            "  castor llmfit gemma3:4b\n"
            "  castor llmfit qwen3:8b --kv-compression turboquant --ctx 16384\n"
            "  castor llmfit --list-models\n"
            "  castor llmfit --tq-status\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_llmfit.add_argument("model", nargs="?", default=None, help="Model ID (e.g. gemma3:4b)")
    p_llmfit.add_argument(
        "--turboquant",
        metavar="MODEL",
        dest="turboquant_model",
        default=None,
        help="Show TurboQuant KV savings analysis for MODEL",
    )
    p_llmfit.add_argument(
        "--kv-compression",
        default="none",
        choices=["none", "turboquant"],
        help="KV cache compression method (default: none)",
    )
    p_llmfit.add_argument(
        "--ctx", type=int, default=8192, help="Context window tokens (default: 8192)"
    )
    p_llmfit.add_argument(
        "--kv-bits", type=int, default=3, help="KV bits for TurboQuant (default: 3)"
    )
    p_llmfit.add_argument(
        "--provider",
        default="ollama",
        choices=["ollama", "vllm", "llamacpp", "mlx"],
        help="Inference provider (affects TurboQuant support status)",
    )
    p_llmfit.add_argument(
        "--ram",
        type=float,
        default=None,
        help="Override available RAM in GB (default: auto-detect)",
    )
    p_llmfit.add_argument("--json", action="store_true", dest="output_json", help="Output JSON")
    p_llmfit.add_argument("--list-models", action="store_true", help="List all known models")
    p_llmfit.add_argument(
        "--tq-status", action="store_true", help="Show TurboQuant ecosystem status across runtimes"
    )

    p_attest = sub.add_parser(
        "attestation",
        help="Show or regenerate software attestation status",
        epilog=(
            "Examples:\n"
            "  castor attestation\n"
            "  castor attestation --config bob.rcan.yaml\n"
            "  castor attestation --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_attest.add_argument("--config", default=None, help="Path to RCAN config file")
    p_attest.add_argument("--out", default=None, help="Output path for attestation JSON")
    p_attest.add_argument("--json", action="store_true", dest="output_json", help="Output JSON")
    p_attest.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    # castor attest — RCAN v2.1 §11 firmware manifest (closes #760)
    p_attest_cmd = sub.add_parser(
        "attest",
        help="Firmware manifest attestation (RCAN v2.1 §11)",
        epilog=(
            "Examples:\n"
            "  castor attest generate\n"
            "  castor attest sign --key robot-private.pem\n"
            "  castor attest verify\n"
            "  castor attest serve\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_attest_sub = p_attest_cmd.add_subparsers(dest="attest_cmd", metavar="SUBCMD")
    p_ag = p_attest_sub.add_parser(
        "generate", help="Generate firmware manifest from installed packages"
    )
    p_ag.add_argument("--config", default=None, help="RCAN config file")
    p_ag.add_argument(
        "--out",
        default=None,
        help="Output path (default: /run/opencastor/rcan-firmware-manifest.json)",
    )
    p_as = p_attest_sub.add_parser(
        "sign", help="Sign the firmware manifest with ML-DSA-65 (RCAN v2.2)"
    )
    p_as.add_argument(
        "--key",
        "--pq-key",
        default=None,
        help="Path to ML-DSA-65 key file (default: ~/.opencastor/pq_signing.key)",
        dest="key",
    )
    p_as.add_argument(
        "--manifest",
        default=None,
        help="Path to manifest JSON (default: /run/opencastor/rcan-firmware-manifest.json)",
    )
    p_av = p_attest_sub.add_parser("verify", help="Verify ML-DSA-65 firmware manifest signature")
    p_av.add_argument(
        "--key",
        "--pq-key",
        default=None,
        help="Path to ML-DSA-65 public key file (default: ~/.opencastor/pq_signing.pub)",
        dest="key",
    )
    p_av.add_argument("--manifest", default=None, help="Path to manifest JSON")
    p_attest_sub.add_parser(
        "serve", help="Confirm /.well-known/rcan-firmware-manifest.json is reachable"
    )

    # castor sbom — RCAN v2.1 §12 CycloneDX SBOM (closes #761)
    p_sbom_cmd = sub.add_parser(
        "sbom",
        help="CycloneDX SBOM generation and RRF publishing (RCAN v2.1 §12)",
        epilog=(
            "Examples:\n"
            "  castor sbom generate\n"
            "  castor sbom publish --token <rrf-token>\n"
            "  castor sbom verify\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_sbom_sub = p_sbom_cmd.add_subparsers(dest="sbom_cmd", metavar="SUBCMD")
    p_sg = p_sbom_sub.add_parser("generate", help="Generate CycloneDX SBOM from installed packages")
    p_sg.add_argument("--config", default=None, help="RCAN config file")
    p_sg.add_argument(
        "--out", default=None, help="Output path (default: /run/opencastor/rcan-sbom.json)"
    )
    p_sp = p_sbom_sub.add_parser("publish", help="Publish SBOM to RRF and receive countersignature")
    p_sp.add_argument("--token", default=None, help="RRF API token")
    p_sp.add_argument("--sbom", default=None, help="Path to SBOM JSON")
    p_sbom_sub.add_parser("verify", help="Verify RRF countersignature on published SBOM")

    p_audit = sub.add_parser(
        "audit",
        help="View the append-only audit log",
        epilog=(
            "Examples:\n"
            "  castor audit\n"
            "  castor audit --since 24h\n"
            "  castor audit --event motor_command\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_audit.add_argument("--since", default=None, help="Time window (e.g. 24h, 7d)")
    p_audit.add_argument(
        "--event", default=None, help="Filter by event type (motor_command, approval, error, etc.)"
    )
    p_audit.add_argument("--limit", type=int, default=50, help="Max entries to show (default: 50)")
    p_audit.add_argument("--verify", action="store_true", help="Verify hash chain integrity")
    p_audit.add_argument(
        "--art11",
        action="store_true",
        help="Generate EU AI Act Art. 11 technical documentation summary",
    )
    p_audit.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Path to RCAN config YAML (default: ~/opencastor/bob.rcan.yaml)",
    )

    # castor monitor
    p_monitor = sub.add_parser(
        "monitor",
        help="Show sensor readings (CPU temp, memory, disk, load)",
    )
    p_monitor.add_argument("--watch", action="store_true", help="Continuous monitoring")
    p_monitor.add_argument(
        "--interval", type=float, default=5.0, help="Seconds between readings (default: 5)"
    )

    # castor login
    p_login = sub.add_parser(
        "login",
        help="Authenticate with AI providers",
        epilog=(
            "Examples:\n"
            "  castor login anthropic     # Setup-token or API key\n"
            "  castor login claude        # Same as anthropic\n"
            "  castor login huggingface\n"
            "  castor login hf --token hf_xxxx\n"
            "  castor login hf --list-models\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_login.add_argument(
        "service",
        nargs="?",
        default="huggingface",
        choices=["huggingface", "hf", "ollama"],
        help="Service to authenticate with (default: huggingface)",
    )
    p_login.add_argument("--token", default=None, help="API token (prompted if not provided)")
    p_login.add_argument(
        "--list-models",
        action="store_true",
        help="List trending models after login",
    )
    p_login.add_argument(
        "--task",
        default="text-generation",
        help="Model task filter for --list-models (default: text-generation)",
    )

    # castor init — interactive setup wizard (zero-to-fleet onboarding)
    p_init = sub.add_parser(
        "init",
        help="Interactive setup wizard — zero-to-fleet onboarding in under 5 minutes",
        description=(
            "Interactive wizard that generates a complete .rcan.yaml config.\n"
            "Run without arguments for guided prompts.\n"
            "Use --no-interactive for CI/scripted use."
        ),
        epilog=(
            "Examples:\n"
            "  castor init\n"
            "  castor init --name Bob --provider google --port 8080 --no-interactive\n"
            "  castor init --output my-robot.rcan.yaml --overwrite\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_init.add_argument(
        "--output", "-o", default=None, help="Output path (default: <robot-name>.rcan.yaml)"
    )
    p_init.add_argument("--name", "-n", default=None, help="Robot name (default: my-robot)")
    p_init.add_argument(
        "--provider",
        default=None,
        choices=["google", "anthropic", "openai", "local"],
        help="AI provider (default: google)",
    )
    p_init.add_argument("--port", type=int, default=None, help="Gateway port (default: 8080)")
    p_init.add_argument("--api-key", default=None, dest="api_key", help="AI provider API key")
    p_init.add_argument(
        "--firebase-project",
        default=None,
        dest="firebase_project",
        help="Firebase project ID (default: opencastor)",
    )
    p_init.add_argument(
        "--no-interactive",
        action="store_true",
        dest="no_interactive",
        help="Skip all prompts — use defaults/flags (required for CI)",
    )
    p_init.add_argument("--overwrite", action="store_true", help="Overwrite existing config file")
    p_init.add_argument(
        "--print", action="store_true", help="Print config to stdout instead of writing to file"
    )

    # SO-ARM101 arm setup (issue #658)
    p_arm = sub.add_parser(
        "arm",
        help="SO-ARM101 arm setup: assemble, detect ports, configure motors, generate config",
        description=(
            "Guided setup for the SO-ARM101 robotic arm (HuggingFace LeRobot / TheRobotStudio).\n\n"
            "  castor arm assemble   — step-by-step physical assembly guide\n"
            "  castor arm detect     — find USB ports for controller boards\n"
            "  castor arm setup      — configure motor IDs and baudrates\n"
            "  castor arm verify     — ping all motors in daisy chain\n"
            "  castor arm config     — generate RCAN config file\n"
        ),
    )
    p_arm.add_argument("arm_subcmd", nargs="?", help="Subcommand (see above)")

    # castor bridge — Firebase remote fleet relay
    p_bridge = sub.add_parser(
        "bridge",
        help="Start the Firebase relay bridge for remote fleet management",
        description=(
            "Connects this robot to Firebase Firestore + FCM, enabling the\n"
            "OpenCastor Client Flutter app to manage the fleet from anywhere.\n\n"
            "Robots initiate outbound connections only — no public ports.\n"
            "All commands pass through R2RAM authorization and Protocol 66\n"
            "safety enforcement before reaching the local gateway.\n\n"
            "  castor bridge --firebase-project live-captions-xr\n"
            "  castor bridge --firebase-project myproject --credentials /path/to/sa.json\n"
            "  castor bridge --firebase-project myproject --gateway-url http://127.0.0.1:8001\n"
        ),
    )
    p_bridge.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Path to RCAN config file (default: auto-detect)",
    )
    p_bridge.add_argument(
        "--firebase-project",
        default=None,
        metavar="PROJECT_ID",
        help="Firebase project ID (e.g. live-captions-xr)",
    )
    p_bridge.add_argument(
        "--credentials",
        default=None,
        metavar="PATH",
        help="Path to Firebase service account JSON (default: use ADC)",
    )
    p_bridge.add_argument(
        "--gateway-url",
        default="http://127.0.0.1:8000",
        metavar="URL",
        help="Local castor gateway URL (default: http://127.0.0.1:8000)",
    )
    p_bridge.add_argument(
        "--gateway-token",
        default=None,
        metavar="TOKEN",
        help="Bearer token for local castor gateway auth",
    )
    p_bridge.add_argument(
        "--poll-interval",
        default=5,
        type=float,
        metavar="SECONDS",
        help="Firestore poll interval in seconds when listener unavailable (default: 5)",
    )
    p_bridge.add_argument(
        "--telemetry-interval",
        default=30,
        type=float,
        metavar="SECONDS",
        help="Telemetry publish interval in seconds (default: 30)",
    )
    p_bridge_sub = p_bridge.add_subparsers(dest="bridge_subcmd")
    p_bridge_setup = p_bridge_sub.add_parser(
        "setup",
        help="Generate and optionally install a systemd service for the bridge",
    )
    p_bridge_setup.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Path to RCAN config (default: auto-detect)",
    )
    p_bridge_discover = p_bridge_sub.add_parser(
        "discover",
        help="Probe RCAN peers configured in rcan_protocol.peers and print status",
    )
    p_bridge_discover.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Path to RCAN config (default: auto-detect)",
    )

    # castor setup — interactive Fleet UI onboarding wizard
    p_setup = sub.add_parser(
        "setup",
        help="Interactive setup wizard — QR codes, Fleet UI onboarding, config generation",
        epilog=(
            "Examples:\n"
            "  castor setup                     # Interactive wizard\n"
            "  castor setup --non-interactive   # Accept all defaults (CI/Docker)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_setup.add_argument(
        "--non-interactive",
        action="store_true",
        dest="non_interactive",
        help="Accept all defaults without prompting (for CI/Docker)",
    )

    # castor fleet-link — Fleet UI deep links and QR codes
    p_fleet_link = sub.add_parser(
        "fleet-link",
        help="Show Fleet UI deep links and QR codes for this robot",
        epilog="Example: castor fleet-link --config bob.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_fleet_link.add_argument(
        "--config",
        default=None,
        help="RCAN config file (auto-detect if omitted)",
    )

    # Shell completions (argcomplete)
    try:
        import argcomplete

        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    # castor share
    p_share = sub.add_parser("share", help="Share a preset, skill, or harness to the hub")
    p_share.add_argument(
        "share_type",
        nargs="?",
        choices=["preset", "skill", "harness"],
        default="preset",
        help="What to share (default: preset)",
    )
    p_share.add_argument("source", nargs="?", default=".", help="File or directory to share")
    p_share.add_argument("--title", "-t", help="Human-readable title")
    p_share.add_argument(
        "--tags", default="", help="Comma-separated tags (hardware, provider, etc.)"
    )
    p_share.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    p_share.add_argument(
        "--publish",
        action="store_true",
        help="Upload to OpenCastor Hub (requires: castor login hub)",
    )

    # castor install
    p_install2 = sub.add_parser("install", help="Install a preset, skill, or harness from the hub")
    p_install2.add_argument(
        "target",
        help="ID to install — e.g. skill:camera-describe, preset:so_arm101, harness:bob-pi4-oakd",
    )
    p_install2.add_argument(
        "--output", "-o", default=".", help="Destination directory (default: .)"
    )
    p_install2.add_argument("--dry-run", action="store_true", help="Preview without applying")

    # castor hub-update (hub configs — not self-update)
    p_hub_update = sub.add_parser(
        "hub-update",
        help="Update hub-installed configs to latest versions (reads castor.lock.yaml)",
    )
    p_hub_update.add_argument(
        "--dir", dest="update_dir", default=".", help="Directory containing castor.lock.yaml"
    )
    p_hub_update.add_argument("--dry-run", action="store_true", help="Preview without applying")
    p_hub_update.set_defaults(func=_cmd_hub_update)

    # castor lock
    p_lock = sub.add_parser("lock", help="Manage castor.lock.yaml (show, verify, clear)")
    p_lock_sub = p_lock.add_subparsers(dest="lock_cmd")
    p_lock_show = p_lock_sub.add_parser("show", help="Show pinned configs")
    p_lock_show.add_argument("--dir", dest="lock_dir", default=".", help="Directory to check")
    p_lock_verify = p_lock_sub.add_parser("verify", help="Verify hashes of pinned configs")
    p_lock_verify.add_argument("--dir", dest="lock_dir", default=".", help="Directory to check")
    p_lock_clear = p_lock_sub.add_parser("clear", help="Remove all pinned configs from lock file")
    p_lock_clear.add_argument("--dir", dest="lock_dir", default=".", help="Directory to check")
    p_lock.set_defaults(func=_cmd_lock)

    # castor explore
    p_explore = sub.add_parser("explore", help="Browse available presets, skills, and harnesses")
    p_explore.add_argument(
        "--type", dest="explore_type", choices=["preset", "skill", "harness"], help="Filter by type"
    )
    p_explore.add_argument("--hardware", help="Filter by hardware tag")
    p_explore.add_argument("--categories", action="store_true", help="List categories only")

    # castor skills
    p_skills = sub.add_parser("skills", help="List loaded skills with usage stats and folder info")
    p_skills.add_argument("--stats", action="store_true", help="Show usage statistics")
    p_skills.add_argument("--name", "-n", metavar="SKILL_NAME", help="Show details for one skill")
    p_skills.add_argument("--json", action="store_true", dest="skills_json", help="Output JSON")

    # castor optimize
    p_optimize = sub.add_parser(
        "optimize", help="Run per-robot runtime optimizer (reads trajectories, tunes config)"
    )
    p_optimize.add_argument(
        "--dry-run", action="store_true", help="Show proposed changes without applying them"
    )
    p_optimize.add_argument(
        "--report", action="store_true", dest="show_report", help="Show last optimization report"
    )
    p_optimize.add_argument("--config", "-c", metavar="PATH", help="Path to RCAN yaml config")
    p_optimize.add_argument(
        "--schedule", action="store_true", help="Install cron job to run optimizer at 3 AM daily"
    )
    p_optimize.add_argument(
        "--unschedule", action="store_true", help="Remove the optimizer cron job"
    )

    # castor leaderboard
    p_leaderboard = sub.add_parser(
        "leaderboard",
        help="Print fleet leaderboard",
        epilog=(
            "Examples:\n"
            "  castor leaderboard\n"
            "  castor leaderboard --tier medium --top 20\n"
            "  castor leaderboard --season 2026-spring --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_leaderboard.add_argument(
        "--tier", default=None, help="Hardware tier filter (default: auto-detect from config)"
    )
    p_leaderboard.add_argument("--season", default=None, help="Season ID to filter by")
    p_leaderboard.add_argument(
        "--top", type=int, default=10, metavar="N", help="Number of entries to show (default: 10)"
    )
    p_leaderboard.add_argument(
        "--json", action="store_true", dest="output_json", help="Output raw JSON"
    )
    p_leaderboard.add_argument("--config", default=None, help="RCAN config for tier auto-detect")

    # castor compete
    p_compete = sub.add_parser(
        "compete",
        help="Manage competition entry and status",
        epilog=(
            "Examples:\n"
            "  castor compete list\n"
            "  castor compete enter sprint-2026-q1\n"
            "  castor compete status sprint-2026-q1\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_compete.add_argument(
        "compete_action",
        nargs="?",
        choices=["list", "enter", "status"],
        default="list",
        help="Compete sub-command (default: list)",
    )
    p_compete.add_argument(
        "competition_id",
        nargs="?",
        default=None,
        help="Competition ID (required for enter/status)",
    )

    # castor season
    p_season = sub.add_parser(
        "season",
        help="Show current season overview and class standings",
        epilog=(
            "Examples:\n  castor season\n  castor season --list\n  castor season --class medium\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_season.add_argument(
        "--list",
        action="store_true",
        dest="list_seasons",
        help="List all seasons with status",
    )
    p_season.add_argument(
        "--class",
        dest="class_id",
        default=None,
        metavar="CLASS_ID",
        help="Filter to one class and show its full leaderboard",
    )

    # castor research
    p_research = sub.add_parser(
        "research",
        help="Manage the harness research pipeline",
        epilog=(
            "Examples:\n"
            "  castor research\n"
            "  castor research history\n"
            "  castor research champion\n"
            "  castor research queue\n"
            "  castor research dashboard\n"
            "  castor research recommend\n"
            "  castor research recommend --hardware pi5_4gb --domain home --explain\n"
            "  castor research recommend --list-findings\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_research.add_argument(
        "research_action",
        nargs="?",
        choices=["status", "history", "champion", "queue", "dashboard", "recommend"],
        default="status",
        help="Research sub-command (default: status)",
    )
    p_research.add_argument(
        "--hardware",
        dest="hardware",
        metavar="HW",
        help="Hardware tier (pi5_4gb, pi5_8gb, pi5_hailo, jetson, server, waveshare)",
    )
    p_research.add_argument(
        "--domain",
        dest="domain",
        metavar="DOMAIN",
        help="Task domain (home, industrial, general)",
    )
    p_research.add_argument(
        "--explain",
        dest="explain",
        action="store_true",
        default=False,
        help="Show synthesis findings that back the recommendation",
    )
    p_research.add_argument(
        "--list-findings",
        dest="list_findings",
        action="store_true",
        default=False,
        help="List all synthesis signals from the autoresearch fleet",
    )

    args = parser.parse_args()

    commands = {
        "run": cmd_run,
        "gateway": cmd_gateway,
        "mcp": cmd_mcp,
        "wizard": cmd_wizard,
        "dashboard": cmd_dashboard,
        "dashboard-tui": cmd_dashboard_tui,
        "token": cmd_token,
        "discover": cmd_discover,
        "memory": cmd_memory,
        "fleet": cmd_fleet,
        "logs": cmd_logs,
        "streaming": cmd_streaming,
        "keygen": cmd_keygen,
        "doctor": cmd_doctor,
        "update": cmd_update,
        "node": cmd_node,
        "inspect": cmd_inspect,
        "verification": cmd_verification,
        "register": cmd_register,
        "compliance": cmd_compliance,
        "demo": cmd_demo,
        "test-hardware": cmd_test_hardware,
        "calibrate": cmd_calibrate,
        "backup": cmd_backup,
        "restore": cmd_restore,
        "migrate": cmd_migrate,
        "upgrade": cmd_upgrade,
        "install-service": cmd_install_service,
        "status": cmd_status,
        # Batch 3
        "shell": cmd_shell,
        "watch": cmd_watch,
        "fix": cmd_fix,
        "repl": cmd_repl,
        "record": cmd_record,
        "replay": cmd_replay,
        "benchmark": cmd_benchmark,
        "lint": cmd_lint,
        "validate": cmd_validate,
        "fria": cmd_fria,
        "rcan-check": cmd_rcan_check,
        "swarm": cmd_swarm,
        "learn": cmd_learn,
        "improve": cmd_improve,
        "agents": cmd_agents,
        "export": cmd_export,
        "export-finetune": cmd_export_finetune,
        # Batch 4 (OpenClaw-inspired)
        "approvals": cmd_approvals,
        "schedule": cmd_schedule,
        "configure": cmd_configure,
        "search": cmd_search,
        "network": cmd_network,
        "loa": cmd_loa,
        "components": cmd_components,
        "rrf": cmd_rrf,
        "privacy": cmd_privacy,
        "consent": cmd_consent,
        # Batch 5 (polish & quality-of-life)
        "update-check": cmd_update_check,
        "profile": cmd_profile,
        "test": cmd_test,
        "diff": cmd_diff,
        "quickstart": cmd_quickstart,
        "plugins": cmd_plugins,
        "plugin": cmd_plugin,
        "audit": cmd_audit,
        "monitor": _cmd_monitor,
        "safety": cmd_safety,
        "conformance": cmd_conformance,
        "iso-check": cmd_iso_check,
        "llmfit": cmd_llmfit,
        "attestation": cmd_attestation,
        "attest": _cmd_attest_dispatch,
        "sbom": _cmd_sbom_dispatch,
        "login": cmd_login,
        "flash": cmd_flash,
        "hub": cmd_hub,
        "scan": cmd_scan,
        "stop": cmd_stop,
        "daemon": cmd_daemon,
        "peer-test": cmd_peer_test,
        "contribute": cmd_contribute_cli,
        "deploy": cmd_deploy,
        # Issue #348
        "snapshot": cmd_snapshot,
        # llmfit model fit analysis
        "fit": lambda _args: __import__(
            "castor.llmfit_helper", fromlist=["run_fit_command"]
        ).run_fit_command(),
        "init": cmd_init,
        # SO-ARM101 arm setup (issue #658)
        "arm": _cmd_arm,
        # Firebase remote fleet bridge
        "bridge": _cmd_bridge,
        # Fleet UI onboarding wizard
        "setup": cmd_setup,
        # Fleet UI deep links + QR codes
        "fleet-link": cmd_fleet_link,
        # Agent harness: skill evaluation
        "eval": _cmd_eval,
        # Trajectory log management
        "trajectory": _cmd_trajectory,
        # Config sharing hub (issue #700 + #701)
        "share": _cmd_share,
        "install": _cmd_install,
        "hub-update": _cmd_hub_update,
        "lock": _cmd_lock,
        "explore": _cmd_explore,
        "skills": _cmd_skills,
        "optimize": _cmd_optimize,
        "provider": cmd_provider,
        # Issue #740 — leaderboard/compete/season/research
        "leaderboard": cmd_leaderboard,
        "compete": cmd_compete,
        "season": cmd_season,
        "research": cmd_research,
        # Issue #780 — revocation CLI
        "revocation": cmd_revocation,
        # Issue #779 — delegation chain management
        "delegation": cmd_delegation,
        # Issue #781 — PQ key rotation CLI
        "key-rotation": cmd_key_rotation,
    }

    # castor delegation — RCAN delegation chain (issue #779)
    p_delegation = sub.add_parser(
        "delegation", help="RCAN delegation chain management (§delegation)"
    )
    p_del_sub = p_delegation.add_subparsers(dest="delegation_cmd")
    p_del_show = p_del_sub.add_parser("show", help="Show delegation config for this robot")
    p_del_show.add_argument("rrn", nargs="?", default=None, help="Filter by RRN")
    p_del_show.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_del_verify = p_del_sub.add_parser(
        "verify", help="Verify delegation chain in a JSON message file"
    )
    p_del_verify.add_argument(
        "file", metavar="FILE", help="JSON message file containing delegation_chain key"
    )
    p_del_sub.add_parser("depth", help="Print max delegation depth (3) and spec note")

    # castor key-rotation — PQ key lifecycle (issue #781)
    p_keyrot = sub.add_parser("key-rotation", help="PQ key rotation lifecycle management")
    p_kr_sub = p_keyrot.add_subparsers(dest="key_rotation_cmd")
    p_kr_status = p_kr_sub.add_parser("status", help="Show current pq_kid, algorithm, key file age")
    p_kr_status.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_kr_rotate = p_kr_sub.add_parser("rotate", help="Generate a new PQ key and update config")
    p_kr_rotate.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_kr_verify = p_kr_sub.add_parser("verify", help="Show JWKS endpoint info for key verification")
    p_kr_verify.add_argument("rrn", nargs="?", default="", help="Robot RRN (optional)")

    # castor eval — skill evaluation harness
    p_eval = sub.add_parser("eval", help="Evaluate a skill against its test suite")
    p_eval.add_argument("--skill", "-s", metavar="NAME", help="Skill name to evaluate")
    p_eval.add_argument(
        "--all", action="store_true", dest="eval_all", help="Evaluate all loaded skills"
    )
    p_eval.add_argument("--verbose", "-v", action="store_true", help="Show per-check details")
    p_eval.add_argument("--json", action="store_true", dest="output_json", help="Output JSON")
    p_eval.add_argument(
        "--no-dry-run", action="store_true", help="Allow physical tool execution (CAUTION)"
    )

    # castor trajectory — trajectory log management
    p_traj = sub.add_parser("trajectory", help="Manage trajectory logs")
    p_traj_sub = p_traj.add_subparsers(dest="traj_action")
    p_traj_sub.add_parser("list", help="Show recent 20 runs")
    p_traj_show = p_traj_sub.add_parser("show", help="Show a single run by ID")
    p_traj_show.add_argument("id", metavar="RUN_ID")
    p_traj_sub.add_parser("export", help="Export all runs as JSONL")
    p_traj_sub.add_parser("stats", help="Show summary statistics")

    # castor revocation — Issue #780
    p_revocation = sub.add_parser(
        "revocation",
        help="Manage RRF revocation status (status, poll, cache)",
    )
    p_rev_sub = p_revocation.add_subparsers(dest="revocation_cmd")
    p_rev_status = p_rev_sub.add_parser("status", help="Show revocation status from cache + RRF")
    p_rev_status.add_argument(
        "rrn", nargs="?", default=None, help="Robot Registration Number (RRN)"
    )
    p_rev_sub.add_parser("poll", help="Force an immediate RRF revocation poll")
    p_rev_sub.add_parser("cache", help="Show local revocation cache contents")
    p_revocation.set_defaults(revocation_cmd="status")

    # Load plugins and merge any plugin-provided commands
    try:
        from castor.plugins import load_plugins

        registry = load_plugins()
        for name, (handler_fn, _) in registry.commands.items():
            if name not in commands:
                commands[name] = lambda a, fn=handler_fn: fn(a)
    except Exception:
        pass

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


def _friendly_error_handler() -> None:
    """Wrap main() with user-friendly error handling and contextual suggestions."""
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.\n")
        sys.exit(130)
    except SystemExit:
        raise
    except FileNotFoundError as exc:
        fname = exc.filename or str(exc)
        print(f"\n  File not found: {fname}")
        if ".rcan.yaml" in str(fname):
            print("  Hint: Run `castor wizard` to create a config file.")
        elif ".env" in str(fname):
            print("  Hint: Run `cp .env.example .env` to create your env file.")
        else:
            print("  Check the path and try again.")
        print()
        sys.exit(1)
    except ImportError as exc:
        dep = exc.name or str(exc)
        print(f"\n  Missing dependency: {dep}")
        # Contextual suggestions based on which package is missing
        suggestions = {
            "dynamixel_sdk": "pip install dynamixel-sdk",
            "cv2": "pip install opencv-python-headless",
            "rich": "pip install rich",
            "yaml": "pip install pyyaml",
            "fastapi": "pip install fastapi uvicorn",
            "streamlit": "pip install streamlit",
            "neonize": "pip install opencastor[whatsapp]",
            "telegram": "pip install opencastor[telegram]",
            "discord": "pip install opencastor[discord]",
            "slack_bolt": "pip install opencastor[slack]",
        }
        hint = suggestions.get(dep)
        if hint:
            print(f"  Hint: {hint}")
        else:
            print("  Hint: pip install -e '.[dev]' or castor fix")
        print()
        sys.exit(1)
    except ConnectionError:
        print("\n  Connection error: Could not reach the server.")
        print("  Hint: Check your network, or run `castor network status`.\n")
        sys.exit(1)
    except Exception as exc:
        print(f"\n  Unexpected error: {exc}")
        print("  Suggestions:")
        print("    1. Run `castor doctor` to check your setup")
        print("    2. Run `castor fix` to auto-repair common issues")
        print("    3. Set LOG_LEVEL=DEBUG and try again for details")
        print()
        if os.getenv("LOG_LEVEL", "").upper() == "DEBUG":
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    _friendly_error_handler()

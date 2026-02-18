"""
OpenCastor CLI entry point.
Provides a unified command interface for the OpenCastor runtime.

Usage:
    castor run      --config robot.rcan.yaml          # Run the robot
    castor gateway  --config robot.rcan.yaml          # Start the API gateway
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
    castor learn                                       # Interactive tutorial
    castor fleet status                                # Multi-robot status
    castor export --config robot.rcan.yaml             # Export config bundle
"""

import argparse
import os
import sys
import traceback

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_run(args) -> None:
    """Run the main perception-action loop."""
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


def cmd_wizard(args) -> None:
    """Run the interactive setup wizard."""
    # Web-based wizard
    if getattr(args, "web", False):
        from castor.web_wizard import launch_web_wizard

        launch_web_wizard(port=getattr(args, "web_port", 8080))
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


def cmd_dashboard(args) -> None:
    """Launch the Streamlit dashboard."""
    import subprocess

    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", "castor/dashboard.py"],
        check=True,
    )


def cmd_token(args) -> None:
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
        print(f"\n  RCAN JWT Token (role={role.name}, ttl={args.ttl}h)\n")
        print(f"  {token}\n")
    except ImportError as exc:
        print("Error: PyJWT is not installed. Install with: pip install PyJWT")
        raise SystemExit(1) from exc
    except KeyError as exc:
        print(f"Error: Invalid role '{args.role}'. Valid: GUEST, USER, LEASEE, OWNER, CREATOR")
        raise SystemExit(1) from exc


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


def cmd_doctor(args) -> None:
    """Run system health checks."""
    from castor.doctor import print_report, run_all_checks

    print("\n  OpenCastor Doctor\n")
    results = run_all_checks(config_path=args.config)
    print_report(results)
    print()


def cmd_demo(args) -> None:
    """Run a simulated perception-action loop (no hardware/API keys)."""
    from castor.demo import run_demo

    run_demo(steps=args.steps, delay=args.delay)


def cmd_test_hardware(args) -> None:
    """Test each motor/servo individually."""
    from castor.test_hardware import run_test

    if not os.path.exists(args.config):
        print(f"\n  Config not found: {args.config}")
        print("  Run `castor wizard` to create one first.\n")
        return

    run_test(config_path=args.config, skip_confirm=args.yes)


def cmd_calibrate(args) -> None:
    """Interactive servo/motor calibration."""
    from castor.calibrate import run_calibration

    if not os.path.exists(args.config):
        print(f"\n  Config not found: {args.config}")
        print("  Run `castor wizard` to create one first.\n")
        return

    run_calibration(config_path=args.config)


def cmd_logs(args) -> None:
    """View structured, colored OpenCastor logs."""
    from castor.logs import view_logs

    view_logs(
        follow=args.follow,
        level=args.level,
        module=args.module,
        lines=args.lines,
        no_color=args.no_color,
    )


def cmd_backup(args) -> None:
    """Back up OpenCastor configs and credentials."""
    from castor.backup import create_backup, print_backup_summary

    archive = create_backup(output_path=args.output)
    if archive:
        # Get list of files for summary
        import tarfile

        with tarfile.open(archive, "r:gz") as tar:
            files = [m.name for m in tar.getmembers()]
        print_backup_summary(archive, files)


def cmd_restore(args) -> None:
    """Restore OpenCastor configs from a backup archive."""
    from castor.backup import print_restore_summary, restore_backup

    if args.dry_run:
        restore_backup(args.archive, dry_run=True)
    else:
        restored = restore_backup(args.archive)
        if restored:
            print_restore_summary(restored)


def cmd_migrate(args) -> None:
    """Migrate an RCAN config to the current schema version."""
    from castor.migrate import migrate_file

    migrate_file(args.config, dry_run=args.dry_run)


def cmd_upgrade(args) -> None:
    """Upgrade OpenCastor and run a health check."""
    import subprocess

    print("\n  Upgrading OpenCastor...\n")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "opencastor"],
        capture_output=not args.verbose,
    )

    if result.returncode == 0:
        print("  Upgrade complete. Running health check...\n")
        from castor.doctor import print_report, run_all_checks

        results = run_all_checks()
        print_report(results)
    else:
        print("  Upgrade failed. Check pip output above.\n")
        if not args.verbose:
            print("  Re-run with --verbose to see details.")

    print()


def cmd_install_service(args) -> None:
    """Generate a systemd service unit file for OpenCastor."""
    if sys.platform != "linux":
        print("\n  install-service is only available on Linux (systemd).\n")
        return
    import getpass

    user = getpass.getuser()
    work_dir = os.getcwd()
    config = os.path.abspath(args.config)
    host = args.host
    port = str(args.port)

    # Resolve the castor executable
    exe = os.path.join(os.path.dirname(sys.executable), "castor")
    if not os.path.exists(exe):
        exe = f"{sys.executable} -m castor.cli"

    env_file = os.path.join(work_dir, ".env")

    unit = f"""\
[Unit]
Description=OpenCastor Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={work_dir}
EnvironmentFile={env_file}
ExecStart={exe} gateway --config {config} --host {host} --port {port}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

    out_path = "/tmp/opencastor.service"
    with open(out_path, "w") as f:
        f.write(unit)

    print(f"\n  Service file written to {out_path}\n")
    print("  Contents:\n")
    for line in unit.splitlines():
        print(f"    {line}")
    print()
    print("  Install with:")
    print(f"    sudo cp {out_path} /etc/systemd/system/opencastor.service")
    print("    sudo systemctl daemon-reload")
    print("    sudo systemctl enable opencastor")
    print("    sudo systemctl start opencastor")
    print()


def cmd_status(args) -> None:
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


# ---------------------------------------------------------------------------
# New command handlers (batch 3)
# ---------------------------------------------------------------------------


def cmd_shell(args) -> None:
    """Launch an interactive command shell with robot objects."""
    from castor.shell import launch_shell

    if not os.path.exists(args.config):
        print(f"\n  Config not found: {args.config}")
        print("  Run `castor wizard` to create one first.\n")
        return

    launch_shell(config_path=args.config)


def cmd_watch(args) -> None:
    """Launch a live Rich telemetry dashboard."""
    from castor.watch import launch_watch

    launch_watch(gateway_url=args.gateway, refresh=args.refresh)


def cmd_fix(args) -> None:
    """Run doctor and attempt to auto-fix common issues."""
    from castor.fix import run_fix

    run_fix(config_path=args.config)


def cmd_repl(args) -> None:
    """Drop into a Python REPL with robot objects pre-loaded."""
    from castor.repl import launch_repl

    if not os.path.exists(args.config):
        print(f"\n  Config not found: {args.config}")
        print("  Run `castor wizard` to create one first.\n")
        return

    launch_repl(config_path=args.config)


def cmd_record(args) -> None:
    """Record a perception-action session to a JSONL file."""
    if not os.path.exists(args.config):
        print(f"\n  Config not found: {args.config}")
        print("  Run `castor wizard` to create one first.\n")
        return

    import time

    from castor.main import Camera, get_driver, load_config
    from castor.providers import get_provider
    from castor.record import SessionRecorder

    config = load_config(args.config)
    brain = get_provider(config["agent"])
    camera = Camera(config)
    driver = get_driver(config) if not args.simulate else None

    recorder = SessionRecorder(args.output)
    print(f"\n  Recording to {args.output} (Ctrl+C to stop)...\n")

    try:
        while True:
            t0 = time.time()
            frame = camera.capture_jpeg()
            thought = brain.think(frame, "Describe what you see and decide an action.")

            action = thought.action or {}
            latency = (time.time() - t0) * 1000

            recorder.record_step(
                frame_size=len(frame),
                instruction="Describe what you see and decide an action.",
                thought_text=thought.raw_text,
                action=action,
                latency_ms=latency,
            )

            # Execute action
            if driver and action.get("type") == "move":
                driver.move(action.get("linear", 0), action.get("angular", 0))
                time.sleep(0.5)
                driver.stop()

            print(f"  Step {recorder._step}: {thought.raw_text[:60]}... ({latency:.0f}ms)")
            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\n  Stopping recording...")
    finally:
        recorder.close()
        if driver:
            driver.stop()
            driver.close()
        camera.close()


def cmd_replay(args) -> None:
    """Replay a recorded session from a JSONL file."""
    from castor.record import replay_session

    replay_session(
        recording_path=args.recording,
        execute=args.execute,
        config_path=args.config,
    )


def cmd_benchmark(args) -> None:
    """Profile a single perception-action loop iteration."""
    from castor.benchmark import run_benchmark

    if not os.path.exists(args.config):
        print(f"\n  Config not found: {args.config}")
        print("  Run `castor wizard` to create one first.\n")
        return

    run_benchmark(
        config_path=args.config,
        iterations=args.iterations,
        simulate=args.simulate,
    )


def cmd_lint(args) -> None:
    """Deep config validation beyond JSON schema."""
    from castor.lint import print_lint_report, run_lint

    if not os.path.exists(args.config):
        print(f"\n  Config not found: {args.config}")
        return

    issues = run_lint(args.config)
    print_lint_report(issues, args.config)


def cmd_learn(args) -> None:
    """Interactive step-by-step tutorial."""
    from castor.learn import run_learn

    run_learn(lesson=args.lesson)


def cmd_fleet(args) -> None:
    """Multi-robot fleet management."""
    from castor.fleet import fleet_status

    fleet_status(timeout=float(args.timeout))


def cmd_export(args) -> None:
    """Export config bundle (no secrets)."""
    from castor.export import export_bundle, print_export_summary

    if not os.path.exists(args.config):
        print(f"\n  Config not found: {args.config}")
        return

    output = export_bundle(
        config_path=args.config,
        output_path=args.output,
        fmt=args.format,
    )
    print_export_summary(output, args.format)


# ---------------------------------------------------------------------------
# OpenClaw-inspired command handlers (batch 4)
# ---------------------------------------------------------------------------


def cmd_approvals(args) -> None:
    """Manage approval queue for dangerous commands."""
    from castor.approvals import ApprovalGate, print_approvals

    # Load config to initialize gate
    config = {}
    if args.config and os.path.exists(args.config):
        import yaml

        with open(args.config) as f:
            config = yaml.safe_load(f)

    gate = ApprovalGate(config)

    if args.approve:
        action = gate.approve(int(args.approve))
        if action:
            print(f"\n  Approved action ID {args.approve}: {action}\n")
        else:
            print(f"\n  Approval ID {args.approve} not found or already resolved.\n")
    elif args.deny:
        if gate.deny(int(args.deny)):
            print(f"\n  Denied action ID {args.deny}.\n")
        else:
            print(f"\n  Approval ID {args.deny} not found or already resolved.\n")
    elif args.clear:
        gate.clear()
        print("\n  Cleared all resolved approvals.\n")
    else:
        pending = gate.list_pending()
        print_approvals(pending)


def cmd_schedule(args) -> None:
    """Manage scheduled tasks."""
    from castor.schedule import (
        add_task,
        install_crontab,
        list_tasks,
        print_schedule,
        remove_task,
    )

    action = args.action

    if action == "list":
        tasks = list_tasks(config_path=args.config)
        print_schedule(tasks)
    elif action == "add":
        if not args.name or not args.task_command or not args.cron:
            print("\n  Usage: castor schedule add --name NAME --command CMD --cron EXPR\n")
            return
        task = add_task(args.name, args.task_command, args.cron)
        print(f"\n  Added: {task['name']} ({task['cron']})\n")
    elif action == "remove":
        if not args.name:
            print("\n  Usage: castor schedule remove --name NAME\n")
            return
        if remove_task(args.name):
            print(f"\n  Removed: {args.name}\n")
        else:
            print(f"\n  Task not found: {args.name}\n")
    elif action == "install":
        install_crontab(config_path=args.config)
    else:
        print("\n  Usage: castor schedule {list|add|remove|install}\n")


def cmd_configure(args) -> None:
    """Interactive config editor."""
    from castor.configure import run_configure

    run_configure(config_path=args.config)


def cmd_search(args) -> None:
    """Search operational logs and session recordings."""
    from castor.memory_search import print_search_results, search_logs

    results = search_logs(
        query=args.query,
        log_file=args.log_file,
        since=args.since,
        max_results=args.max_results,
    )
    print_search_results(results, args.query)


def cmd_network(args) -> None:
    """Network configuration and VPN exposure controls."""
    from castor.network import expose, network_status

    action = args.action

    if action == "status":
        network_status(config_path=args.config)
    elif action == "expose":
        mode = args.mode or "serve"
        port = args.port
        expose(mode=mode, port=port)
    else:
        network_status(config_path=args.config)


def cmd_privacy(args) -> None:
    """Show or configure privacy policy."""
    import yaml

    from castor.privacy import print_privacy_policy

    config = {}
    if args.config and os.path.exists(args.config):
        with open(args.config) as f:
            config = yaml.safe_load(f)

    print_privacy_policy(config)


# ---------------------------------------------------------------------------
# Batch 5: Polish & quality-of-life command handlers
# ---------------------------------------------------------------------------


def cmd_update_check(args) -> None:
    """Check PyPI for a newer version of OpenCastor."""
    from castor.update_check import print_update_status

    print_update_status()


def cmd_profile(args) -> None:
    """Manage named config profiles."""
    from castor.profiles import (
        list_profiles,
        print_profiles,
        remove_profile,
        save_profile,
        use_profile,
    )

    action = args.action

    if action == "list":
        profiles = list_profiles()
        print_profiles(profiles)
    elif action == "save":
        if not args.name:
            print("\n  Usage: castor profile save NAME --config FILE\n")
            return
        save_profile(args.name, args.config)
        print(f"\n  Profile '{args.name}' saved from {args.config}\n")
    elif action == "use":
        if not args.name:
            print("\n  Usage: castor profile use NAME\n")
            return
        try:
            use_profile(args.name)
            print(f"\n  Profile '{args.name}' activated -> robot.rcan.yaml\n")
        except FileNotFoundError:
            print(f"\n  Profile not found: {args.name}\n")
    elif action == "remove":
        if not args.name:
            print("\n  Usage: castor profile remove NAME\n")
            return
        if remove_profile(args.name):
            print(f"\n  Profile '{args.name}' removed.\n")
        else:
            print(f"\n  Profile not found: {args.name}\n")
    else:
        print("\n  Usage: castor profile {list|save|use|remove}\n")


def cmd_test(args) -> None:
    """Run the test suite via pytest."""
    import subprocess

    cmd = [sys.executable, "-m", "pytest", "tests/"]
    if args.verbose:
        cmd.append("-v")
    if args.keyword:
        cmd.extend(["-k", args.keyword])

    print("\n  Running OpenCastor tests...\n")
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def cmd_diff(args) -> None:
    """Compare two RCAN config files."""
    from castor.diff import diff_configs, print_diff

    if not os.path.exists(args.config):
        print(f"\n  File not found: {args.config}\n")
        return
    if not os.path.exists(args.baseline):
        print(f"\n  File not found: {args.baseline}\n")
        return

    diffs = diff_configs(args.config, args.baseline)
    print_diff(diffs, args.config, args.baseline)


def cmd_quickstart(args) -> None:
    """One-command setup: wizard -> demo -> dashboard."""
    import subprocess

    print("\n  OpenCastor QuickStart\n")

    # Step 1: Run wizard in simple mode
    print("  Step 1: Running setup wizard...\n")
    wizard_args = [sys.executable, "-m", "castor.cli", "wizard", "--simple", "--accept-risk"]
    result = subprocess.run(wizard_args)
    if result.returncode != 0:
        print("\n  Wizard failed. Run `castor doctor` to diagnose.\n")
        return

    # Step 2: Run demo
    print("\n  Step 2: Running demo...\n")
    demo_args = [sys.executable, "-m", "castor.cli", "demo", "--steps", "3"]
    subprocess.run(demo_args)

    print("\n  QuickStart complete!")
    print("  Next: castor run --config robot.rcan.yaml\n")


def cmd_plugins(args) -> None:
    """List or manage plugins."""
    from castor.plugins import list_plugins, load_plugins, print_plugins

    load_plugins()
    plugins = list_plugins()
    print_plugins(plugins)


def cmd_login(args) -> None:
    """Authenticate with AI providers (Hugging Face, etc.)."""
    service = args.service.lower()

    if service in ("huggingface", "hf"):
        _login_huggingface(args)
    elif service == "ollama":
        _login_ollama(args)
    elif service in ("anthropic", "claude"):
        _login_anthropic(args)
    else:
        print(f"  Unknown service: {service}")
        print("  Supported: anthropic (claude), huggingface (hf), ollama")


def _login_anthropic(args) -> None:
    """Handle Anthropic authentication via setup-token or API key."""
    import getpass
    import shutil
    import subprocess

    print("\n  Anthropic Authentication")
    print("  ========================")
    print()
    print("  [1] Setup-token (uses Claude Max/Pro subscription — no per-token billing)")
    print("  [2] Paste an existing setup-token")
    print("  [3] API key (pay-as-you-go from console.anthropic.com)")
    print()

    choice = input("  Selection [1]: ").strip() or "1"
    token = None

    if choice == "1":
        # Generate a fresh setup-token via Claude CLI
        if not shutil.which("claude"):
            print("  ❌ Claude CLI not found. Install it first:")
            print("     npm install -g @anthropic-ai/claude-code")
            print()
            print("  Or choose [2] to paste a token generated on another machine.")
            return

        print()
        print("  Generating a setup-token via Claude CLI...")
        print("  (This creates a token specific to OpenCastor — won't affect OpenClaw)")
        print()
        try:
            result = subprocess.run(
                ["claude", "setup-token"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = result.stdout.strip()
            # The token is usually the last line of output
            for line in reversed(output.split("\n")):
                line = line.strip()
                if line.startswith("sk-ant-oat01-") and len(line) >= 80:
                    token = line
                    break

            if not token:
                print(f"  ⚠️  Could not extract token from claude output.")
                if output:
                    print(f"  Output: {output[:200]}")
                print("  Try [2] to paste the token manually.")
                return

        except subprocess.TimeoutExpired:
            print("  ⚠️  claude setup-token timed out. Try running it manually.")
            return
        except Exception as e:
            print(f"  ⚠️  Error running claude setup-token: {e}")
            return

    elif choice == "2":
        print()
        print("  Paste a setup-token (starts with sk-ant-oat01-).")
        print("  Generate one with: claude setup-token")
        print()
        token = getpass.getpass("  Setup-token: ").strip()
        if not token:
            print("  Cancelled.")
            return
        if not (token.startswith("sk-ant-oat01-") and len(token) >= 80):
            print("  ⚠️  Token doesn't match expected format (sk-ant-oat01-...).")
            confirm = input("  Save anyway? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                print("  Cancelled.")
                return

    elif choice == "3":
        print()
        token = getpass.getpass("  ANTHROPIC_API_KEY: ").strip()
        if not token:
            print("  Cancelled.")
            return
    else:
        print("  Invalid selection.")
        return

    # Save token to OpenCastor's own store (~/.opencastor/anthropic-token)
    from castor.providers.anthropic_provider import AnthropicProvider

    saved_path = AnthropicProvider.save_token(token)
    is_setup_token = token.startswith("sk-ant-oat01-")
    label = "setup-token (subscription)" if is_setup_token else "API key"
    print(f"  ✅ {label} saved to {saved_path}")

    # Validate
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=token)
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=10,
            messages=[{"role": "user", "content": "hi"}],
        )
        if resp.content:
            print("  ✅ Authentication verified!")
    except Exception as e:
        print(f"  ⚠️  Could not verify: {e}")


def _write_env_key(key: str, value: str) -> None:
    """Write or update a key in the .env file."""
    env_path = os.path.join(os.getcwd(), ".env")
    lines = []
    found = False
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith(f"{key}="):
                    lines.append(f"{key}={value}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"{key}={value}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)


def _login_huggingface(args) -> None:
    """Handle Hugging Face authentication and model discovery."""
    import getpass

    try:
        from huggingface_hub import HfApi
        from huggingface_hub import login as hf_login
    except ImportError:
        print("  Missing dependency: huggingface-hub")
        print("  Install with: pip install huggingface-hub")
        return

    token = args.token or os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")

    if not token:
        print()
        print("  🤗 Hugging Face Login")
        print("  ─────────────────────")
        print("  Get your token at: https://huggingface.co/settings/tokens")
        print("  Recommended: 'Read' scope is sufficient for inference.")
        print()
        token = getpass.getpass("  HF Token: ").strip()

    if not token:
        print("  No token provided. Aborted.")
        return

    try:
        hf_login(token=token, add_to_git_credential=False)
        api = HfApi(token=token)
        user = api.whoami()
        username = user.get("name", user.get("fullname", "unknown"))
        print(f"\n  ✅ Authenticated as: {username}")
        print("     Token saved to: ~/.cache/huggingface/token")

        # Also save to .env if it exists
        env_path = os.path.join(os.getcwd(), ".env")
        _update_env_var(env_path, "HF_TOKEN", token)

    except Exception as e:
        print(f"\n  ❌ Login failed: {e}")
        return

    if args.list_models:
        _list_hf_models(api, args.task)


def _update_env_var(env_path: str, key: str, value: str) -> None:
    """Add or update a variable in a .env file."""
    lines = []
    found = False
    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}\n"
                found = True
                break

    if not found:
        lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(lines)
    print(f"     Also saved to: {env_path}")


def _login_ollama(args) -> None:
    """Handle Ollama connection setup and model discovery."""
    from castor.providers.ollama_provider import (
        DEFAULT_HOST,
        OllamaConnectionError,
        OllamaProvider,
    )

    host = args.token or os.getenv("OLLAMA_HOST") or DEFAULT_HOST

    print()
    print("  🦙 Ollama Setup")
    print("  ───────────────")
    print(f"  Host: {host}")
    print()

    # Test connection
    try:
        provider = OllamaProvider({"provider": "ollama", "ollama_host": host})
        provider._ping()
        print("  ✅ Connected to Ollama")
    except OllamaConnectionError as exc:
        print(f"  ❌ {exc}")
        print()
        print("  To install Ollama: https://ollama.ai/download")
        print("  To start Ollama:   ollama serve")
        return

    # Save host to .env if non-default
    if host != DEFAULT_HOST:
        env_path = os.path.join(os.getcwd(), ".env")
        _update_env_var(env_path, "OLLAMA_HOST", host)

    # List models
    if args.list_models:
        try:
            models = provider.list_models()
            if models:
                print(f"\n  📦 Available models ({len(models)}):\n")
                for m in models:
                    size_gb = m["size"] / (1024**3) if m["size"] else 0
                    print(f"    • {m['name']:<30s} {size_gb:.1f} GB")
            else:
                print("\n  No models found. Pull one with:")
                print("    ollama pull llava:13b")
            print()
        except Exception as e:
            print(f"\n  Error listing models: {e}\n")

    print("  Use in your RCAN config:")
    print("    agent:")
    print('      provider: "ollama"')
    print('      model: "llava:13b"')
    print()


def _list_hf_models(api, task: str, limit: int = 15) -> None:
    """Print trending models for a task."""
    print(f"\n  📦 Trending {task} models on Hugging Face:\n")
    try:
        models = api.list_models(task=task, sort="trending", direction=-1, limit=limit)
        for i, m in enumerate(models, 1):
            downloads = f"{m.downloads:,}" if m.downloads else "?"
            likes = m.likes or 0
            print(f"  {i:>3}. {m.id}")
            print(f"       ↓ {downloads} downloads  ♥ {likes} likes")
        print()
        print("  Use any model ID in your RCAN config:")
        print("    agent:")
        print('      provider: "huggingface"')
        print('      model: "meta-llama/Llama-3.3-70B-Instruct"')
        print()
    except Exception as e:
        print(f"  Error listing models: {e}")


def cmd_hub(args) -> None:
    """Community recipe hub — browse, share, and install configs."""
    from castor.hub import (
        CATEGORIES,
        DIFFICULTY,
        get_recipe,
        install_recipe,
        list_recipes,
        print_recipe_card,
    )

    action = args.action

    if action == "categories":
        print("\n  📂 Recipe Categories:\n")
        for key, label in CATEGORIES.items():
            print(f"     {key:<15} {label}")
        print("\n  🎯 Difficulty Levels:\n")
        for key, label in DIFFICULTY.items():
            print(f"     {key:<15} {label}")
        print()
        return

    if action == "browse":
        recipes = list_recipes(
            category=args.category,
            difficulty=args.difficulty,
            provider=args.provider,
        )
        if not recipes:
            print("\n  No recipes found.")
            print("  Be the first! Run: castor hub share --config your_robot.rcan.yaml\n")
            return
        print(f"\n  🤖 Community Recipes ({len(recipes)} found):\n")
        for r in recipes:
            print_recipe_card(r, verbose=args.verbose)
        print("\n  Install one: castor hub install <recipe-id>\n")
        return

    if action == "search":
        query = args.query
        if not query:
            print("  Usage: castor hub search 'your query'")
            return
        recipes = list_recipes(search=query)
        if not recipes:
            print(f"\n  No recipes matching '{query}'.")
            return
        print(f"\n  🔍 Results for '{query}' ({len(recipes)} found):\n")
        for r in recipes:
            print_recipe_card(r, verbose=args.verbose)
        print()
        return

    if action == "show":
        recipe_id = args.query
        if not recipe_id:
            print("  Usage: castor hub show <recipe-id>")
            return
        recipe = get_recipe(recipe_id)
        if not recipe:
            print(f"  Recipe not found: {recipe_id}")
            return
        print_recipe_card(recipe, verbose=True)

        # Show README if exists
        from pathlib import Path

        readme = Path(recipe["_dir"]) / "README.md"
        if readme.exists():
            print(f"\n  {'─' * 60}")
            print(readme.read_text()[:2000])
        print()
        return

    if action == "install":
        recipe_id = args.query
        if not recipe_id:
            print("  Usage: castor hub install <recipe-id>")
            return
        dest = install_recipe(recipe_id, dest=args.output or ".")
        if dest:
            print(f"\n  ✅ Recipe installed: {dest}")
            print(f"     Run: castor run --config {dest}\n")
        else:
            print(f"  Recipe not found: {recipe_id}")
        return

    if action == "share":
        if not args.config:
            print("  Usage: castor hub share --config robot.rcan.yaml [--docs BUILD.md ...]")
            return
        _interactive_share(args)
        return

    # Fallback
    print("  Usage: castor hub {browse|search|show|install|share|categories}")
    print("  Run: castor hub --help for details")


def _interactive_share(args) -> None:
    """Interactive recipe sharing wizard."""

    from castor.hub import (
        CATEGORIES,
        DIFFICULTY,
        create_recipe_manifest,
        package_recipe,
    )

    print("\n  🤖 Share Your Robot Recipe")
    print("  ─────────────────────────")
    print("  Your config and docs will be scrubbed of PII before sharing.\n")

    name = input("  Recipe name: ").strip() or "my-robot"
    description = input("  Short description: ").strip() or "A robot config that works"
    author = input("  Your name/handle (or Enter for Anonymous): ").strip() or "Anonymous"

    print("\n  Categories:")
    for i, (_key, label) in enumerate(CATEGORIES.items(), 1):
        print(f"    [{i}] {label}")
    cat_choice = input("  Category [10]: ").strip()
    cat_keys = list(CATEGORIES.keys())
    try:
        category = cat_keys[int(cat_choice) - 1]
    except (ValueError, IndexError):
        category = "custom"

    print("\n  Difficulty:")
    for i, (_key, label) in enumerate(DIFFICULTY.items(), 1):
        print(f"    [{i}] {label}")
    diff_choice = input("  Difficulty [2]: ").strip()
    diff_keys = list(DIFFICULTY.keys())
    try:
        difficulty = diff_keys[int(diff_choice) - 1]
    except (ValueError, IndexError):
        difficulty = "intermediate"

    hardware_str = input("  Hardware (comma-separated): ").strip()
    hardware = [h.strip() for h in hardware_str.split(",") if h.strip()] or ["unspecified"]

    ai_provider = input("  AI provider (e.g. anthropic, huggingface): ").strip() or "unknown"
    ai_model = input("  AI model (e.g. claude-opus-4-6): ").strip() or "unknown"

    budget = input("  Approximate budget (e.g. $150, or Enter to skip): ").strip() or None
    tags_str = input("  Tags (comma-separated, e.g. patrol,outdoor,camera): ").strip()
    tags = [t.strip() for t in tags_str.split(",") if t.strip()] or []

    print()
    use_case = input("  Use case (one-liner about what this robot does): ").strip() or None

    manifest = create_recipe_manifest(
        name=name,
        description=description,
        author=author,
        category=category,
        difficulty=difficulty,
        hardware=hardware,
        ai_provider=ai_provider,
        ai_model=ai_model,
        tags=tags,
        budget=budget,
        use_case=use_case,
    )

    recipe_dir = package_recipe(
        config_path=args.config,
        output_dir=args.output or ".",
        docs=args.docs,
        manifest=manifest,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print("\n  ℹ️  Dry run — no files written.")
    elif getattr(args, "submit", False):
        from castor.hub import SubmitError, submit_recipe_pr

        print(f"\n  ✅ Recipe packaged at: {recipe_dir}")
        print("  📤 Submitting PR to GitHub...\n")
        try:
            pr_url = submit_recipe_pr(recipe_dir, manifest)
            print(f"\n  🎉 Pull request created: {pr_url}")
            print("     A maintainer will review your recipe shortly.")
        except SubmitError as exc:
            print(f"\n  ❌ Submission failed: {exc}")
            print(f"\n  Your recipe is still saved at: {recipe_dir}")
            print("  You can submit manually by opening a PR on GitHub.")
    else:
        print(f"\n  ✅ Recipe packaged at: {recipe_dir}")
        print("  Next steps:")
        print(f"    1. Review the scrubbed files in {recipe_dir}/")
        print("    2. Edit README.md with tips, photos, and lessons learned")
        print("    3. Submit a PR: castor hub share --config {args.config} --submit")
        print("       Or manually at https://github.com/craigm26/OpenCastor")
    print()


def cmd_safety(args) -> None:
    """List safety protocol rules."""
    from castor.safety.protocol import SafetyProtocol

    proto = SafetyProtocol(config_path=getattr(args, "config", None))
    rules = proto.list_rules()
    cat_filter = getattr(args, "category", None)
    if cat_filter:
        rules = [r for r in rules if r["category"] == cat_filter]

    if not rules:
        print("No rules found.")
        return

    # Print table
    print(f"{'ID':<15} {'Category':<12} {'Severity':<10} {'Enabled':<8} Description")
    print("-" * 80)
    for r in rules:
        enabled = "✓" if r["enabled"] else "✗"
        print(
            f"{r['rule_id']:<15} {r['category']:<12} {r['severity']:<10} {enabled:<8} {r['description']}"
        )


def _cmd_monitor(args) -> None:
    """Show sensor readings."""
    from castor.safety.monitor import cli_monitor

    cli_monitor(args)


def cmd_audit(args) -> None:
    """View or verify the audit log."""
    from castor.audit import get_audit, print_audit

    audit = get_audit()

    if getattr(args, "verify", False):
        valid, broken_idx = audit.verify_chain()
        if valid:
            print("✅ Audit chain integrity verified — no tampering detected.")
        else:
            print(f"❌ Audit chain BROKEN at entry index {broken_idx}!")
        return

    entries = audit.read(
        since=args.since,
        event=args.event,
        limit=args.limit,
    )
    print_audit(entries)


# ---------------------------------------------------------------------------
# Parser setup
# ---------------------------------------------------------------------------


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
        epilog="Example: castor run --config robot.rcan.yaml --simulate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_run.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_run.add_argument("--simulate", action="store_true", help="Run without hardware")

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
    p_wizard.add_argument("--web-port", type=int, default=8080, help="Port for web wizard")

    # castor dashboard
    sub.add_parser("dashboard", help="Launch the Streamlit web UI")

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

    # castor discover
    p_discover = sub.add_parser("discover", help="Discover RCAN peers on the local network")
    p_discover.add_argument("--timeout", default="5", help="Scan duration in seconds (default: 5)")

    # castor doctor
    p_doctor = sub.add_parser(
        "doctor",
        help="Run system health checks",
        epilog="Example: castor doctor --config robot.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_doctor.add_argument("--config", default=None, help="RCAN config file to validate")

    # castor demo
    p_demo = sub.add_parser(
        "demo",
        help="Run a simulated demo (no hardware/API keys)",
        epilog="Example: castor demo --steps 5 --delay 2.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_demo.add_argument("--steps", type=int, default=10, help="Number of loop iterations")
    p_demo.add_argument("--delay", type=float, default=1.5, help="Seconds between steps")

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
    p_upgrade = sub.add_parser("upgrade", help="Upgrade OpenCastor and run health check")
    p_upgrade.add_argument("--verbose", "-v", action="store_true", help="Show pip output")

    # castor install-service
    p_svc = sub.add_parser(
        "install-service",
        help="Generate a systemd service unit file",
        epilog="Example: castor install-service --config robot.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_svc.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_svc.add_argument("--host", default="127.0.0.1", help="Bind address")
    p_svc.add_argument("--port", type=int, default=8000, help="Port number")

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
        help="Replay a recorded session from JSONL",
        epilog="Example: castor replay session.jsonl --execute --config robot.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_replay.add_argument("recording", help="Path to the .jsonl recording file")
    p_replay.add_argument("--execute", action="store_true", help="Re-execute actions on hardware")
    p_replay.add_argument("--config", default=None, help="RCAN config file (required if --execute)")

    # castor benchmark
    p_bench = sub.add_parser(
        "benchmark",
        help="Profile perception-action loop performance",
        epilog="Example: castor benchmark --config robot.rcan.yaml --iterations 5",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_bench.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")
    p_bench.add_argument(
        "--iterations", type=int, default=3, help="Number of iterations (default: 3)"
    )
    p_bench.add_argument("--simulate", action="store_true", help="Skip hardware driver")

    # castor lint
    p_lint = sub.add_parser(
        "lint",
        help="Deep config validation beyond JSON schema",
        epilog="Example: castor lint --config robot.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_lint.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")

    # castor learn
    p_learn = sub.add_parser(
        "learn",
        help="Interactive step-by-step tutorial",
        epilog="Example: castor learn --lesson 3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_learn.add_argument("--lesson", type=int, default=None, help="Jump to a specific lesson (1-7)")

    # castor fleet
    p_fleet = sub.add_parser(
        "fleet",
        help="Multi-robot fleet management",
        epilog="Example: castor fleet --timeout 10",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_fleet.add_argument(
        "--timeout", default="5", help="mDNS scan duration in seconds (default: 5)"
    )

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
        "--format", choices=["zip", "json"], default="zip", help="Export format (default: zip)"
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

    # castor privacy
    p_priv = sub.add_parser(
        "privacy",
        help="Show privacy policy (sensor access controls)",
        epilog="Example: castor privacy --config robot.rcan.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_priv.add_argument("--config", default="robot.rcan.yaml", help="RCAN config file")

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
    sub.add_parser(
        "quickstart",
        help="One-command setup: wizard + demo",
        epilog="Example: castor quickstart",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # castor plugins
    sub.add_parser(
        "plugins",
        help="List loaded and available plugins",
        epilog="Example: castor plugins",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # castor hub
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
        choices=["browse", "search", "show", "install", "share", "categories"],
        help="Action to perform (default: browse)",
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
        epilog="Examples:\n  castor safety rules\n  castor safety rules --category motion\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_safety.add_argument(
        "safety_action",
        nargs="?",
        default="rules",
        choices=["rules"],
        help="Safety sub-command (default: rules)",
    )
    p_safety.add_argument("--category", default=None, help="Filter by category")
    p_safety.add_argument(
        "--config",
        default=None,
        help="Path to safety protocol YAML config",
    )

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

    # Shell completions (argcomplete)
    try:
        import argcomplete

        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    args = parser.parse_args()

    commands = {
        "run": cmd_run,
        "gateway": cmd_gateway,
        "wizard": cmd_wizard,
        "dashboard": cmd_dashboard,
        "token": cmd_token,
        "discover": cmd_discover,
        "doctor": cmd_doctor,
        "demo": cmd_demo,
        "test-hardware": cmd_test_hardware,
        "calibrate": cmd_calibrate,
        "logs": cmd_logs,
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
        "learn": cmd_learn,
        "fleet": cmd_fleet,
        "export": cmd_export,
        # Batch 4 (OpenClaw-inspired)
        "approvals": cmd_approvals,
        "schedule": cmd_schedule,
        "configure": cmd_configure,
        "search": cmd_search,
        "network": cmd_network,
        "privacy": cmd_privacy,
        # Batch 5 (polish & quality-of-life)
        "update-check": cmd_update_check,
        "profile": cmd_profile,
        "test": cmd_test,
        "diff": cmd_diff,
        "quickstart": cmd_quickstart,
        "plugins": cmd_plugins,
        "audit": cmd_audit,
        "monitor": _cmd_monitor,
        "safety": cmd_safety,
        "login": cmd_login,
        "hub": cmd_hub,
    }

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

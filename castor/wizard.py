"""
OpenCastor Setup Wizard.
Interactively generates an RCAN-compliant configuration file,
collects API keys, and configures messaging channels.

Features:
  - Safety acknowledgment before physical hardware setup
  - QuickStart (sensible defaults) vs Advanced flow
  - Inline API key validation
  - Auto-hardware detection
  - Post-wizard health check
  - Rich terminal output (with fallback)
"""

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone

from castor import __version__

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Run: pip install pyyaml")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Rich console (optional, graceful fallback)
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn

    _console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    _console = None


class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


def _print(text: str = "", style: str = None):
    """Print with Rich if available, otherwise plain print."""
    if HAS_RICH and style:
        _console.print(text, style=style)
    elif HAS_RICH:
        _console.print(text)
    else:
        print(text)


BANNER = f"""{Colors.BLUE}
   ___                   ___         _
  / _ \\ _ __   ___ _ __ / __|__ _ __| |_ ___ _ _
 | (_) | '_ \\ / -_) '_ \\ (__/ _` (_-<  _/ _ \\ '_|
  \\___/| .__/ \\___|_| |_|\\___\\__,_/__/\\__\\___/_|
       |_|
{Colors.ENDC}"""

PROVIDERS = {
    "1": {
        "provider": "anthropic",
        "model": "claude-opus-4-6",
        "label": "Anthropic Claude Opus 4.6",
        "env_var": "ANTHROPIC_API_KEY",
    },
    "2": {
        "provider": "google",
        "model": "gemini-2.5-flash",
        "label": "Google Gemini 2.5 Flash",
        "env_var": "GOOGLE_API_KEY",
    },
    "3": {
        "provider": "google",
        "model": "gemini-3-flash-preview",
        "label": "Google Gemini 3 Flash (Preview)",
        "env_var": "GOOGLE_API_KEY",
    },
    "4": {
        "provider": "openai",
        "model": "gpt-4.1",
        "label": "OpenAI GPT-4.1",
        "env_var": "OPENAI_API_KEY",
    },
    "5": {
        "provider": "huggingface",
        "model": "meta-llama/Llama-3.3-70B-Instruct",
        "label": "Hugging Face (Llama, Qwen, Mistral, etc.)",
        "env_var": "HF_TOKEN",
    },
    "6": {
        "provider": "ollama",
        "model": "llava:13b",
        "label": "Local Llama (Ollama)",
        "env_var": None,
    },
}

PRESETS = {
    "1": None,  # Custom
    "2": "rpi_rc_car",
    "3": "waveshare_alpha",
    "4": "adeept_generic",
    "5": "freenove_4wd",
    "6": "sunfounder_picar",
}

CHANNELS = {
    "1": {
        "name": "whatsapp",
        "label": "WhatsApp (scan QR code)",
        "env_vars": [],
    },
    "2": {
        "name": "whatsapp_twilio",
        "label": "WhatsApp via Twilio (legacy)",
        "env_vars": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_NUMBER"],
    },
    "3": {
        "name": "telegram",
        "label": "Telegram Bot",
        "env_vars": ["TELEGRAM_BOT_TOKEN"],
    },
    "4": {
        "name": "discord",
        "label": "Discord Bot",
        "env_vars": ["DISCORD_BOT_TOKEN"],
    },
    "5": {
        "name": "slack",
        "label": "Slack Bot",
        "env_vars": ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"],
    },
}


def input_default(prompt, default):
    response = input(f"{prompt} [{default}]: ")
    return response if response else default


def input_secret(prompt):
    """Read a secret value (API key / token). Masks nothing but labels it clearly."""
    value = input(f"  {prompt}: ").strip()
    return value if value else None


def choose_provider():
    print(f"\n{Colors.GREEN}--- BRAIN SELECTION ---{Colors.ENDC}")
    print("Which AI provider do you want to use?")
    for key, val in PROVIDERS.items():
        rec = " (Recommended)" if key == "1" else ""
        print(f"  [{key}] {val['label']}{rec}")

    choice = input_default("Selection", "1")
    return PROVIDERS.get(choice, PROVIDERS["1"])


def _validate_api_key(provider: str, api_key: str) -> bool:
    """Make a lightweight test call to validate an API key.

    Returns True if the key is valid, False otherwise.
    """
    if not api_key:
        return False

    try:
        if provider == "anthropic":
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
            client.models.list(limit=1)
            return True
        elif provider == "google":
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            list(genai.list_models())
            return True
        elif provider == "openai":
            import openai

            client = openai.OpenAI(api_key=api_key)
            client.models.list()
            return True
        elif provider == "openrouter":
            import httpx

            resp = httpx.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            return resp.status_code == 200
    except Exception:
        return False

    return False


def collect_api_key(agent_config):
    """Prompt the user for their provider API key and write it to .env."""
    env_var = agent_config.get("env_var")
    if not env_var:
        return  # Ollama doesn't need a key

    # Check if already set in environment
    if os.getenv(env_var):
        print(f"\n  {Colors.GREEN}[OK]{Colors.ENDC} {env_var} already set in environment.")
        return

    print(f"\n{Colors.GREEN}--- API KEY ---{Colors.ENDC}")
    print(f"  Your {agent_config['label']} API key is needed.")
    print(
        f"  It will be saved to your local "
        f"{Colors.BOLD}.env{Colors.ENDC} file (never committed to git)."
    )

    key = input_secret(f"{env_var}")
    if key:
        # Inline validation
        provider = agent_config.get("provider", "")
        if HAS_RICH:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
                console=_console,
            ) as progress:
                progress.add_task(description="Validating API key...", total=None)
                valid = _validate_api_key(provider, key)
        else:
            print("  Validating API key...", end=" ", flush=True)
            valid = _validate_api_key(provider, key)

        if valid:
            _write_env_var(env_var, key)
            print(f"  {Colors.GREEN}[OK]{Colors.ENDC} Key validated and saved to .env")
        else:
            # Save anyway -- validation might fail due to network, but key could be valid
            _write_env_var(env_var, key)
            print(
                f"  {Colors.WARNING}[WARN]{Colors.ENDC} Could not validate key "
                f"(network issue?). Saved to .env anyway."
            )
    else:
        print(f"  {Colors.WARNING}Skipped.{Colors.ENDC} Set {env_var} in .env before running.")


def choose_channels():
    """Ask which messaging channels to enable."""
    print(f"\n{Colors.GREEN}--- MESSAGING CHANNELS ---{Colors.ENDC}")
    print("Connect your robot to messaging platforms (optional).")
    print("You can enable multiple channels. Enter numbers separated by commas.")
    print("  [0] None (skip)")
    for key, val in CHANNELS.items():
        print(f"  [{key}] {val['label']}")

    choice = input_default("Selection (e.g. 1,2)", "0").strip()
    if choice == "0":
        return []

    selected = []
    for c in choice.split(","):
        c = c.strip()
        if c in CHANNELS:
            selected.append(CHANNELS[c])
    return selected


def collect_channel_credentials(channels):
    """Prompt for credentials for each selected channel."""
    if not channels:
        return

    print(f"\n{Colors.GREEN}--- CHANNEL CREDENTIALS ---{Colors.ENDC}")
    print(f"  Credentials will be saved to your local {Colors.BOLD}.env{Colors.ENDC} file.\n")

    for ch in channels:
        print(f"  {Colors.BOLD}{ch['label']}{Colors.ENDC}")
        if not ch["env_vars"]:
            print(
                f"    {Colors.GREEN}[OK]{Colors.ENDC} No credentials needed -- "
                "QR code will appear when you run castor gateway"
            )
            print()
            continue
        for env_var in ch["env_vars"]:
            if os.getenv(env_var):
                print(f"    {Colors.GREEN}[OK]{Colors.ENDC} {env_var} already set")
                continue
            value = input_secret(env_var)
            if value:
                _write_env_var(env_var, value)
                print(f"    {Colors.GREEN}[OK]{Colors.ENDC} Saved")
            else:
                print(f"    {Colors.WARNING}Skipped{Colors.ENDC}")
        print()


def choose_hardware():
    """Select hardware kit, with optional auto-detection."""
    print(f"\n{Colors.GREEN}--- HARDWARE KIT ---{Colors.ENDC}")

    # Try auto-detection first
    try:
        from castor.hardware_detect import detect_hardware, suggest_preset

        if HAS_RICH:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
                console=_console,
            ) as progress:
                progress.add_task(description="Scanning for hardware...", total=None)
                hw = detect_hardware()
        else:
            print("  Scanning for hardware...", end=" ", flush=True)
            hw = detect_hardware()

        preset_name, confidence, reason = suggest_preset(hw)

        if confidence in ("high", "medium"):
            print(f"\n  {Colors.GREEN}[AUTO-DETECT]{Colors.ENDC} {reason}")
            print(f"  Suggested preset: {Colors.BOLD}{preset_name}{Colors.ENDC}")
            use_detected = input_default("Use detected hardware?", "Y").strip().lower()
            if use_detected in ("y", "yes", ""):
                return preset_name
            print()  # Fall through to manual selection
        else:
            print(f"\n  {Colors.WARNING}[AUTO-DETECT]{Colors.ENDC} {reason}")
            print("  Falling back to manual selection.\n")
    except Exception:
        pass  # Auto-detection not available or failed

    print("Select your hardware kit:")
    print("  [1] Custom (Advanced)")
    print("  [2] RPi RC Car + PCA9685 + CSI Camera (Recommended)")
    print("  [3] Waveshare AlphaBot ($45)")
    print("  [4] Adeept RaspTank ($55)")
    print("  [5] Freenove 4WD Car ($49)")
    print("  [6] SunFounder PiCar-X ($60)")

    choice = input_default("Selection", "2")
    return PRESETS.get(choice)


def get_kinematics():
    print(f"\n{Colors.GREEN}--- KINEMATICS SETUP ---{Colors.ENDC}")
    dof = int(input_default("How many Degrees of Freedom (DoF)?", "6"))

    links = []
    print(f"Defining {dof} links (Base -> End Effector)...")

    for i in range(dof):
        print(f"\n{Colors.BOLD}Link {i + 1}{Colors.ENDC}")
        length = input_default("  Length (mm)", "100")
        mass = input_default("  Approx Mass (g)", "50")
        axis = input_default("  Rotation Axis (x/y/z)", "z")

        links.append(
            {
                "id": f"link_{i + 1}",
                "length_mm": float(length),
                "mass_g": float(mass),
                "axis": axis,
            }
        )
    return links


def get_drivers(links):
    print(f"\n{Colors.GREEN}--- DRIVER MAPPING ---{Colors.ENDC}")
    print("Mapping physical motors to kinematic links...")

    drivers = []
    protocol = input_default(
        "Default Protocol (dynamixel/serial/canbus/ros2/pca9685_i2c)", "serial"
    )
    port = input_default("Default Port (e.g., /dev/ttyUSB0)", "/dev/ttyUSB0")

    for i, link in enumerate(links):
        print(f"\nConfiguring motor for {Colors.BOLD}{link['id']}{Colors.ENDC}")
        motor_id = input_default("  Motor ID", str(i + 1))

        drivers.append(
            {
                "link_id": link["id"],
                "protocol": protocol,
                "port": port,
                "hardware_id": int(motor_id),
                "baud_rate": 115200,
            }
        )
    return drivers


def generate_preset_config(preset_name, robot_name, agent_config):
    """Generate config for a known hardware preset."""
    # Try to load from a preset RCAN file
    preset_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "presets", f"{preset_name}.rcan.yaml"
    )
    if os.path.exists(preset_path):
        with open(preset_path) as f:
            config = yaml.safe_load(f)
        # Override name, UUID, and agent with wizard selections
        config["metadata"]["robot_name"] = robot_name
        config["metadata"]["robot_uuid"] = str(uuid.uuid4())
        config["metadata"]["created_at"] = datetime.now(timezone.utc).isoformat()
        config["agent"]["provider"] = agent_config["provider"]
        config["agent"]["model"] = agent_config["model"]
        return config

    # Fallback: generic differential-drive preset
    return {
        "rcan_version": "1.0.0-alpha",
        "metadata": {
            "robot_name": robot_name,
            "robot_uuid": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "author": "OpenCastor Wizard",
            "license": "Apache-2.0",
            "tags": ["mobile", "rover", "amazon_kit"],
        },
        "agent": {
            "provider": agent_config["provider"],
            "model": agent_config["model"],
            "vision_enabled": True,
            "latency_budget_ms": 200,
            "safety_stop": True,
        },
        "physics": {
            "type": "differential_drive",
            "dof": 2,
            "chassis": {
                "wheel_base_mm": 150,
                "wheel_radius_mm": 32,
            },
        },
        "drivers": [
            {
                "id": "motor_driver",
                "protocol": "pca9685_i2c",
                "port": "/dev/i2c-1",
                "address": "0x40",
                "frequency": 50,
                "channels": {
                    "left_front": 0,
                    "left_rear": 1,
                    "right_front": 2,
                    "right_rear": 3,
                },
            }
        ],
        "network": {
            "telemetry_stream": True,
            "sim_to_real_sync": True,
            "allow_remote_override": False,
        },
        "rcan_protocol": {
            "port": 8000,
            "capabilities": ["status", "nav", "teleop", "chat"],
            "enable_mdns": False,
            "enable_jwt": False,
        },
    }


def generate_custom_config(robot_name, agent_config, links, drivers):
    """Generate config for custom hardware."""
    return {
        "rcan_version": "1.0.0-alpha",
        "metadata": {
            "robot_name": robot_name,
            "robot_uuid": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "author": "OpenCastor Wizard",
            "license": "Apache-2.0",
        },
        "agent": {
            "provider": agent_config["provider"],
            "model": agent_config["model"],
            "vision_enabled": True,
            "latency_budget_ms": 200,
            "safety_stop": True,
        },
        "physics": {
            "type": "serial_manipulator",
            "dof": len(links),
            "kinematics": links,
            "dynamics": {
                "gravity": [0, 0, -9.81],
                "payload_capacity_g": 500,
            },
        },
        "drivers": drivers,
        "network": {
            "telemetry_stream": True,
            "sim_to_real_sync": True,
            "allow_remote_override": False,
        },
        "rcan_protocol": {
            "port": 8000,
            "capabilities": ["status", "arm", "chat"],
            "enable_mdns": False,
            "enable_jwt": False,
        },
    }


def _write_env_var(key: str, value: str):
    """Append or update a variable in the local .env file."""
    env_path = ".env"
    lines = []

    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.readlines()

    # Check if the key already exists; if so, update it
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            found = True
            break

    if not found:
        lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(lines)


def _safety_acknowledgment(accept_risk):
    """Show safety warning and require acknowledgment before proceeding."""
    if accept_risk:
        return

    if HAS_RICH:
        _console.print(
            Panel(
                "[bold yellow]SAFETY WARNING[/]\n\n"
                "  OpenCastor controls [bold]PHYSICAL MOTORS[/] and [bold]SERVOS[/].\n"
                "  Before continuing, please ensure:\n\n"
                "    [yellow]-[/] Keep hands and cables clear of moving parts\n"
                "    [yellow]-[/] Have a power switch or kill-cord within reach\n"
                "    [yellow]-[/] Never leave a running robot unattended\n"
                "    [yellow]-[/] Start with low speed/torque settings",
                border_style="yellow",
                title="[bold]Safety First[/]",
            )
        )
    else:
        print(f"{Colors.WARNING}{Colors.BOLD}--- SAFETY WARNING ---{Colors.ENDC}")
        print(f"{Colors.WARNING}")
        print("  OpenCastor controls PHYSICAL MOTORS and SERVOS.")
        print("  Before continuing, please ensure:")
        print()
        print("    - Keep hands and cables clear of moving parts")
        print("    - Have a power switch or kill-cord within reach")
        print("    - Never leave a running robot unattended")
        print("    - Start with low speed/torque settings")
        print(f"{Colors.ENDC}")

    ack = input("  Type 'yes' to acknowledge and continue: ").strip().lower()
    if ack != "yes":
        print(
            f"\n  Setup cancelled.  Re-run with {Colors.BOLD}--accept-risk{Colors.ENDC} "
            "to skip this prompt."
        )
        sys.exit(0)
    print()


def main():
    parser = argparse.ArgumentParser(description="OpenCastor Setup Wizard")
    parser.add_argument(
        "--simple",
        action="store_true",
        help="QuickStart mode: project name + API key only",
    )
    parser.add_argument(
        "--accept-risk",
        action="store_true",
        help="Skip the safety acknowledgment prompt",
    )
    args = parser.parse_args()

    print(BANNER)

    if HAS_RICH:
        _console.print(f"[bold magenta]OpenCastor Setup Wizard v{__version__}[/]")
        _console.print("Generating spec compliant with [bold]rcan.dev/spec[/]\n")
    else:
        print(f"{Colors.HEADER}OpenCastor Setup Wizard v{__version__}{Colors.ENDC}")
        print(f"Generating spec compliant with {Colors.BOLD}rcan.dev/spec{Colors.ENDC}\n")

    # --- Safety Acknowledgment ---
    _safety_acknowledgment(args.accept_risk)

    # --- QuickStart vs Advanced ---
    quickstart = args.simple
    if not quickstart:
        print(f"{Colors.GREEN}--- SETUP MODE ---{Colors.ENDC}")
        print("  [1] QuickStart  (project name + API key, sensible defaults)")
        print("  [2] Advanced    (full hardware, channel, and driver config)")
        mode = input_default("Selection", "1")
        quickstart = mode != "2"
        print()

    # --- Step 1: Project Name ---
    robot_name = input_default("Project Name", "MyRobot")

    if quickstart:
        # -- QuickStart Path --
        agent_config = choose_provider()
        collect_api_key(agent_config)

        # Messaging channel (optional)
        print(f"\n{Colors.GREEN}--- MESSAGING (optional) ---{Colors.ENDC}")
        print("  Connect a messaging app to talk to your robot.")
        print("  [0] Skip for now")
        print("  [1] WhatsApp (scan QR code — no account needed!)")
        print("  [2] Telegram Bot")
        ch_choice = input_default("Selection", "0").strip()
        selected_channels = []
        if ch_choice == "1":
            selected_channels = [CHANNELS["1"]]
        elif ch_choice == "2":
            selected_channels = [CHANNELS["3"]]
        if selected_channels:
            collect_channel_credentials(selected_channels)

        preset = "rpi_rc_car"
        rcan_data = generate_preset_config(preset, robot_name, agent_config)
    else:
        # -- Advanced Path --
        agent_config = choose_provider()
        collect_api_key(agent_config)

        preset = choose_hardware()
        if preset is not None:
            rcan_data = generate_preset_config(preset, robot_name, agent_config)
        else:
            links = get_kinematics()
            drivers = get_drivers(links)
            rcan_data = generate_custom_config(robot_name, agent_config, links, drivers)

        selected_channels = choose_channels()
        collect_channel_credentials(selected_channels)

    # --- Auto-generate Gateway Auth Token ---
    if not os.getenv("OPENCASTOR_API_TOKEN"):
        import secrets

        token = secrets.token_hex(24)  # 48-char hex token
        _write_env_var("OPENCASTOR_API_TOKEN", token)
        print(
            f"\n  {Colors.GREEN}[AUTO]{Colors.ENDC} Gateway auth token generated and saved to .env"
        )
        print(f"  {Colors.BOLD}OPENCASTOR_API_TOKEN{Colors.ENDC}={token[:8]}...")

    # --- Generate Config ---
    filename = f"{robot_name.lower().replace(' ', '_')}.rcan.yaml"

    if HAS_RICH:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
            console=_console,
        ) as progress:
            progress.add_task(description="Writing config file...", total=None)
            with open(filename, "w") as f:
                yaml.dump(rcan_data, f, sort_keys=False, default_flow_style=False)
    else:
        with open(filename, "w") as f:
            yaml.dump(rcan_data, f, sort_keys=False, default_flow_style=False)

    # --- Auto-detect RCAN capabilities ---
    try:
        from castor.rcan.capabilities import CapabilityRegistry

        cap_reg = CapabilityRegistry(rcan_data)
        detected_caps = cap_reg.names
        if detected_caps:
            print(f"\n{Colors.HEADER}Detected RCAN Capabilities:{Colors.ENDC}")
            for cap in detected_caps:
                print(f"  {Colors.GREEN}+{Colors.ENDC} {cap}")
    except Exception:
        detected_caps = []

    # --- Post-Wizard Health Check ---
    try:
        from castor.doctor import print_report, run_post_wizard_checks

        if HAS_RICH:
            _console.print("\n[bold magenta]--- Running Health Check ---[/]")
        else:
            print(f"\n{Colors.HEADER}--- Running Health Check ---{Colors.ENDC}")
        results = run_post_wizard_checks(filename, rcan_data, agent_config["provider"])
        print_report(results, colors_class=Colors)
    except Exception:
        pass  # Health check failure should never block wizard completion

    # --- Summary ---
    if HAS_RICH:
        _console.print(f"\n{'=' * 50}")
        _console.print("[bold green]Setup Complete![/]\n")
        _console.print(f"  Config file:  [cyan]{filename}[/]")
        _console.print(f"  AI Provider:  {agent_config['label']}")
        _console.print(f"  Model:        {agent_config['model']}")

        if selected_channels:
            names = ", ".join(ch["label"] for ch in selected_channels)
            _console.print(f"  Channels:     {names}")

        _console.print("\n[bold]Next Steps:[/]")
        _console.print(f"  1. Run the robot:        [cyan]castor run --config {filename}[/]")
        _console.print(f"  2. Start the gateway:    [cyan]castor gateway --config {filename}[/]")
        _console.print("  3. Open the dashboard:   [cyan]castor dashboard[/]")
        _console.print("  4. Check status:         [cyan]castor status[/]")
        _console.print(
            f"  5. Auto-start on boot:   [cyan]castor install-service --config {filename}[/]"
        )
        _console.print(
            f"  6. Test your hardware:   [cyan]castor test-hardware --config {filename}[/]"
        )
        _console.print(f"  7. Calibrate servos:     [cyan]castor calibrate --config {filename}[/]")
        _console.print("\n  Or with Docker:          [cyan]docker compose up[/]")
        _console.print("  Validate config:         https://rcan.dev/spec/")
    else:
        print(f"\n{Colors.BOLD}{'=' * 50}{Colors.ENDC}")
        print(f"{Colors.GREEN}Setup Complete!{Colors.ENDC}\n")
        print(f"  Config file:  {Colors.BLUE}{filename}{Colors.ENDC}")
        print(f"  AI Provider:  {agent_config['label']}")
        print(f"  Model:        {agent_config['model']}")

        if selected_channels:
            names = ", ".join(ch["label"] for ch in selected_channels)
            print(f"  Channels:     {names}")

        print(f"\n{Colors.BOLD}Next Steps:{Colors.ENDC}")
        print(
            f"  1. Run the robot:        {Colors.BLUE}castor run --config {filename}{Colors.ENDC}"
        )
        print(
            f"  2. Start the gateway:    {Colors.BLUE}castor gateway --config {filename}{Colors.ENDC}"
        )
        print(f"  3. Open the dashboard:   {Colors.BLUE}castor dashboard{Colors.ENDC}")
        print(f"  4. Check status:         {Colors.BLUE}castor status{Colors.ENDC}")
        print(
            f"  5. Auto-start on boot:   "
            f"{Colors.BLUE}castor install-service --config {filename}{Colors.ENDC}"
        )
        print(
            f"  6. Test your hardware:   "
            f"{Colors.BLUE}castor test-hardware --config {filename}{Colors.ENDC}"
        )
        print(
            f"  7. Calibrate servos:     "
            f"{Colors.BLUE}castor calibrate --config {filename}{Colors.ENDC}"
        )
        print(f"\n  Or with Docker:          {Colors.BLUE}docker compose up{Colors.ENDC}")
        print("\n  Validate config:         https://rcan.dev/spec/")

    # --- Offer to start the robot ---
    print()
    try:
        start = input_default("Start your robot now? (y/n)", "y").strip().lower()
        if start in ("y", "yes"):
            print(f"\n{Colors.GREEN}Starting OpenCastor...{Colors.ENDC}\n")
            import subprocess

            subprocess.run([sys.executable, "-m", "castor.cli", "run", "--config", filename])
    except (KeyboardInterrupt, EOFError):
        print(f"\n\n  {Colors.BOLD}To start later:{Colors.ENDC} castor run --config {filename}")


if __name__ == "__main__":
    main()

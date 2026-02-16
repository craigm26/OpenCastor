"""
OpenCastor Setup Wizard.
Interactively generates an RCAN-compliant configuration file,
collects API keys, and configures messaging channels.
"""

import os
import sys
import uuid
import argparse
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Run: pip install pyyaml")
    sys.exit(1)


class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


BANNER = f"""{Colors.BLUE}
   ___                   ___         _
  / _ \\ _ __   ___ _ __ / __|__ _ __| |_ ___ _ _
 | (_) | '_ \\ / -_) '_ \\ (__/ _` (_-<  _/ _ \\ '_|
  \\___/| .__/ \\___|_| |_|\\___\\__,_/__/\\__\\___/_|
       |_|
{Colors.ENDC}"""

PROVIDERS = {
    "1": {"provider": "anthropic", "model": "claude-opus-4-6", "label": "Anthropic Claude Opus 4.6", "env_var": "ANTHROPIC_API_KEY"},
    "2": {"provider": "google", "model": "gemini-2.0-flash", "label": "Google Gemini", "env_var": "GOOGLE_API_KEY"},
    "3": {"provider": "openai", "model": "gpt-4o", "label": "OpenAI GPT-4o", "env_var": "OPENAI_API_KEY"},
    "4": {"provider": "ollama", "model": "llava:13b", "label": "Local Llama (Ollama)", "env_var": None},
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
        "label": "WhatsApp (via Twilio)",
        "env_vars": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_NUMBER"],
    },
    "2": {
        "name": "telegram",
        "label": "Telegram Bot",
        "env_vars": ["TELEGRAM_BOT_TOKEN"],
    },
    "3": {
        "name": "discord",
        "label": "Discord Bot",
        "env_vars": ["DISCORD_BOT_TOKEN"],
    },
    "4": {
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
    print(f"  It will be saved to your local {Colors.BOLD}.env{Colors.ENDC} file (never committed to git).")

    key = input_secret(f"{env_var}")
    if key:
        _write_env_var(env_var, key)
        print(f"  {Colors.GREEN}[OK]{Colors.ENDC} Saved to .env")
    else:
        print(f"  {Colors.WARNING}Skipped.{Colors.ENDC} Set {env_var} in .env before running.")


def choose_channels():
    """Ask which messaging channels to enable."""
    print(f"\n{Colors.GREEN}--- MESSAGING CHANNELS ---{Colors.ENDC}")
    print("Connect your robot to messaging platforms (optional).")
    print("You can enable multiple channels. Enter numbers separated by commas.")
    print(f"  [0] None (skip)")
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
    print(f"\n{Colors.GREEN}--- HARDWARE KIT ---{Colors.ENDC}")
    print("Select your hardware kit:")
    print("  [1] Custom (Advanced)")
    print("  [2] RPi RC Car + PCA9685 + CSI Camera (Recommended)")
    print("  [3] Waveshare AlphaBot ($45)")
    print("  [4] Adeept RaspTank ($55)")
    print("  [5] Freenove 4WD Car ($49)")
    print("  [6] SunFounder PiCar-X ($60)")

    choice = input_default("Selection", "1")
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


def main():
    parser = argparse.ArgumentParser(description="OpenCastor Setup Wizard")
    parser.add_argument(
        "--simple",
        action="store_true",
        help="Run simplified wizard for Amazon kit presets",
    )
    parser.parse_args()

    print(BANNER)
    print(f"{Colors.HEADER}OpenCastor Setup Wizard v0.1.0{Colors.ENDC}")
    print(
        f"Generating spec compliant with {Colors.BOLD}github.com/continuonai/rcan-spec{Colors.ENDC}\n"
    )

    # --- Step 1: Project Name ---
    robot_name = input_default("Project Name", "MyRobot")

    # --- Step 2: Choose AI Provider ---
    agent_config = choose_provider()

    # --- Step 3: Collect API Key ---
    collect_api_key(agent_config)

    # --- Step 4: Choose Hardware ---
    preset = choose_hardware()

    if preset is not None:
        rcan_data = generate_preset_config(preset, robot_name, agent_config)
    else:
        links = get_kinematics()
        drivers = get_drivers(links)
        rcan_data = generate_custom_config(robot_name, agent_config, links, drivers)

    # --- Step 5: Messaging Channels ---
    selected_channels = choose_channels()
    collect_channel_credentials(selected_channels)

    # --- Step 6: Generate Config ---
    filename = f"{robot_name.lower().replace(' ', '_')}.rcan.yaml"

    with open(filename, "w") as f:
        yaml.dump(rcan_data, f, sort_keys=False, default_flow_style=False)

    # --- Step 7: Summary ---
    print(f"\n{Colors.BOLD}{'=' * 50}{Colors.ENDC}")
    print(f"{Colors.GREEN}Setup Complete!{Colors.ENDC}\n")
    print(f"  Config file:  {Colors.BLUE}{filename}{Colors.ENDC}")
    print(f"  AI Provider:  {agent_config['label']}")
    print(f"  Model:        {agent_config['model']}")

    if selected_channels:
        names = ", ".join(ch["label"] for ch in selected_channels)
        print(f"  Channels:     {names}")

    print(f"\n{Colors.BOLD}Next Steps:{Colors.ENDC}")
    print(f"  1. Run the robot:      {Colors.BLUE}castor run --config {filename}{Colors.ENDC}")
    print(f"  2. Start the gateway:  {Colors.BLUE}castor gateway --config {filename}{Colors.ENDC}")
    print(f"  3. Open the dashboard: {Colors.BLUE}castor dashboard{Colors.ENDC}")
    print(f"  4. Check status:       {Colors.BLUE}castor status{Colors.ENDC}")
    print(f"\n  Or with Docker:        {Colors.BLUE}docker compose up{Colors.ENDC}")
    print(f"\n  Validate config:       https://github.com/continuonai/rcan-spec")


if __name__ == "__main__":
    main()

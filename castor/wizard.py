"""
OpenCastor Setup Wizard.
Interactively generates an RCAN-compliant configuration file.
"""

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
    "1": {"provider": "google", "model": "gemini-1.5-flash", "label": "Google Gemini"},
    "2": {"provider": "openai", "model": "gpt-4o", "label": "OpenAI GPT-4o"},
    "3": {"provider": "anthropic", "model": "claude-3-5-sonnet", "label": "Anthropic Claude 3.5"},
    "4": {"provider": "ollama", "model": "llava:13b", "label": "Local Llama (Ollama)"},
}

PRESETS = {
    "1": None,  # Custom
    "2": "waveshare_alpha",
    "3": "adeept_generic",
    "4": "freenove_4wd",
    "5": "sunfounder_picar",
}


def input_default(prompt, default):
    response = input(f"{prompt} [{default}]: ")
    return response if response else default


def choose_provider():
    print(f"\n{Colors.GREEN}--- BRAIN SELECTION ---{Colors.ENDC}")
    print("Which AI provider do you want to use?")
    for key, val in PROVIDERS.items():
        rec = " (Recommended)" if key == "1" else ""
        print(f"  [{key}] {val['label']}{rec}")

    choice = input_default("Selection", "1")
    return PROVIDERS.get(choice, PROVIDERS["1"])


def choose_hardware():
    print(f"\n{Colors.GREEN}--- HARDWARE KIT ---{Colors.ENDC}")
    print("Select your hardware kit:")
    print("  [1] Custom (Advanced)")
    print("  [2] Waveshare AlphaBot ($45)")
    print("  [3] Adeept RaspTank ($55)")
    print("  [4] Freenove 4WD Car ($49)")
    print("  [5] SunFounder PiCar-X ($60)")

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
    """Generate config for a known Amazon kit preset."""
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

    robot_name = input_default("Project Name", "MyRobot")
    agent_config = choose_provider()
    preset = choose_hardware()

    if preset is not None:
        # Use a preset
        rcan_data = generate_preset_config(preset, robot_name, agent_config)
    else:
        # Custom hardware
        links = get_kinematics()
        drivers = get_drivers(links)
        rcan_data = generate_custom_config(robot_name, agent_config, links, drivers)

    filename = f"{robot_name.lower().replace(' ', '_')}.rcan.yaml"

    with open(filename, "w") as f:
        yaml.dump(rcan_data, f, sort_keys=False, default_flow_style=False)

    print(f"\n{Colors.BOLD}Success!{Colors.ENDC} Generated configuration file: {Colors.BLUE}{filename}{Colors.ENDC}")
    print(f"Validate against the spec at: https://github.com/continuonai/rcan-spec")
    print(f"\nNext: Run 'python -m castor.main --config {filename}' to boot the brain.")


if __name__ == "__main__":
    main()

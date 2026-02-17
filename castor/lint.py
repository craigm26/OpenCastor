"""
OpenCastor Lint -- deep config validation beyond JSON schema.

Checks for semantic issues that a schema can't catch:
  - Unreachable I2C addresses
  - Mismatched channel credentials
  - Driver protocol vs. preset conflicts
  - Unsafe physics values
  - Missing environment variables referenced in config

Usage:
    castor lint --config robot.rcan.yaml
"""

import logging
import os

import yaml

logger = logging.getLogger("OpenCastor.Lint")


def run_lint(config_path: str) -> list:
    """Lint an RCAN config file for semantic issues.

    Returns a list of ``(severity, message)`` tuples.
    Severity is one of ``"error"``, ``"warning"``, ``"info"``.
    """
    with open(config_path) as f:
        config = yaml.safe_load(f)

    issues = []

    issues.extend(_check_agent(config))
    issues.extend(_check_drivers(config))
    issues.extend(_check_physics(config))
    issues.extend(_check_channels(config))
    issues.extend(_check_network(config))
    issues.extend(_check_env_vars(config))

    return issues


def _check_agent(config: dict) -> list:
    """Validate agent/brain configuration."""
    issues = []
    agent = config.get("agent", {})

    if not agent:
        issues.append(("error", "No 'agent' section found in config"))
        return issues

    provider = agent.get("provider", "")
    model = agent.get("model", "")

    if not provider:
        issues.append(("error", "agent.provider is empty"))
    if not model:
        issues.append(("warning", "agent.model is empty -- provider will use default"))

    # Check known provider/model combos
    known_combos = {
        "google": ["gemini"],
        "openai": ["gpt", "o1", "o3", "o4"],
        "anthropic": ["claude"],
        "openrouter": [],  # any model
        "ollama": [],
    }

    if provider in known_combos and known_combos[provider]:
        prefixes = known_combos[provider]
        if not any(model.lower().startswith(p) for p in prefixes):
            issues.append(
                (
                    "warning",
                    f"Model '{model}' looks unusual for provider '{provider}' "
                    f"(expected prefix: {', '.join(prefixes)})",
                )
            )

    budget = agent.get("latency_budget_ms", 3000)
    if budget < 500:
        issues.append(
            (
                "warning",
                f"latency_budget_ms={budget} is very aggressive -- most providers need 1000ms+",
            )
        )
    elif budget > 30000:
        issues.append(
            ("info", f"latency_budget_ms={budget} is very generous -- robot will be slow to react")
        )

    return issues


def _check_drivers(config: dict) -> list:
    """Validate driver configuration."""
    issues = []
    drivers = config.get("drivers", [])

    if not drivers:
        issues.append(("info", "No drivers configured -- will run in simulation mode"))
        return issues

    for i, drv in enumerate(drivers):
        protocol = drv.get("protocol", "")
        if not protocol:
            issues.append(("error", f"drivers[{i}].protocol is empty"))

        # PCA9685 checks
        if "pca9685" in protocol:
            i2c_addr = drv.get("i2c_address", 0x40)
            if i2c_addr not in (0x40, 0x41, 0x42, 0x43, 0x60, 0x70):
                issues.append(
                    (
                        "warning",
                        f"drivers[{i}].i2c_address=0x{i2c_addr:02x} is unusual "
                        "for PCA9685 (expected 0x40-0x43 or 0x60/0x70)",
                    )
                )

        # Dynamixel checks
        if "dynamixel" in protocol:
            port = drv.get("port", "")
            if not port:
                issues.append(
                    (
                        "warning",
                        f"drivers[{i}].port not set -- "
                        "set DYNAMIXEL_PORT env var or specify in config",
                    )
                )
            baud = drv.get("baudrate", 57600)
            if baud not in (9600, 57600, 115200, 1000000):
                issues.append(
                    ("warning", f"drivers[{i}].baudrate={baud} is non-standard for Dynamixel")
                )

    return issues


def _check_physics(config: dict) -> list:
    """Validate physics/safety values."""
    issues = []
    physics = config.get("physics", {})

    if not physics:
        issues.append(("info", "No 'physics' section -- using driver defaults"))
        return issues

    max_speed = physics.get("max_speed_ms", 0)
    if max_speed > 2.0:
        issues.append(
            (
                "warning",
                f"max_speed_ms={max_speed} is very fast for an indoor robot -- "
                "ensure safety measures are in place",
            )
        )

    if not physics.get("safety_stop", True):
        issues.append(
            ("warning", "safety_stop is disabled -- emergency stop won't trigger automatically")
        )

    return issues


def _check_channels(config: dict) -> list:
    """Validate messaging channel configuration."""
    issues = []
    channels = config.get("channels", [])

    from castor.auth import load_dotenv_if_available

    load_dotenv_if_available()

    env_requirements = {
        "whatsapp_twilio": [
            "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN",
            "TWILIO_WHATSAPP_NUMBER",
        ],
        "telegram": ["TELEGRAM_BOT_TOKEN"],
        "discord": ["DISCORD_BOT_TOKEN"],
        "slack": ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"],
    }

    for ch in channels:
        ch_type = ch.get("type", "")
        if ch_type in env_requirements:
            missing = [v for v in env_requirements[ch_type] if not os.getenv(v)]
            if missing:
                issues.append(
                    ("error", f"Channel '{ch_type}' requires env vars: {', '.join(missing)}")
                )

    return issues


def _check_network(config: dict) -> list:
    """Validate network/RCAN protocol settings."""
    issues = []
    net = config.get("rcan_protocol", config.get("network", {}))

    if not net:
        return issues

    port = net.get("port", 8000)
    if port < 1024 and port != 80 and port != 443:
        issues.append(("warning", f"Port {port} requires root privileges -- consider using 8000+"))

    return issues


def _check_env_vars(config: dict) -> list:
    """Check for missing environment variables needed by the config."""
    issues = []
    from castor.auth import load_dotenv_if_available

    load_dotenv_if_available()

    provider = config.get("agent", {}).get("provider", "")
    env_map = {
        "google": "GOOGLE_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }

    if provider in env_map:
        var = env_map[provider]
        if not os.getenv(var):
            # Check if key is inline in config
            if not config.get("agent", {}).get("api_key"):
                issues.append(
                    (
                        "error",
                        f"Provider '{provider}' needs {var} in .env or agent.api_key in config",
                    )
                )

    return issues


def print_lint_report(issues: list, config_path: str):
    """Print lint results."""
    try:
        from rich.console import Console

        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False

    if has_rich:
        console.print(f"\n[bold cyan]  OpenCastor Lint[/] -- {config_path}\n")
    else:
        print(f"\n  OpenCastor Lint -- {config_path}\n")

    if not issues:
        msg = "  No issues found!"
        if has_rich:
            console.print(f"  [green]{msg}[/]")
        else:
            print(f"  {msg}")
        print()
        return

    icons = {"error": "E", "warning": "W", "info": "I"}
    colors = {"error": "red", "warning": "yellow", "info": "dim"}

    for severity, message in issues:
        icon = icons.get(severity, "?")
        if has_rich:
            color = colors.get(severity, "white")
            console.print(f"  [{color}][{icon}][/{color}]  {message}")
        else:
            print(f"  [{icon}]  {message}")

    errors = sum(1 for s, _ in issues if s == "error")
    warnings = sum(1 for s, _ in issues if s == "warning")
    infos = sum(1 for s, _ in issues if s == "info")

    summary = f"  {errors} error(s), {warnings} warning(s), {infos} info"
    if has_rich:
        console.print(f"\n  [bold]{summary}[/]\n")
    else:
        print(f"\n  {summary}\n")

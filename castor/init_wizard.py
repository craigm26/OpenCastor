"""
castor.init_wizard — Interactive zero-to-fleet onboarding wizard.

Runs when the user types ``castor init`` (interactive) or
``castor init --no-interactive --name Bob --provider google --port 8080``
(CI / scripted).

Usage:
    castor init
    castor init --output bob.rcan.yaml
    castor init --name Bob --provider google --port 8080 --no-interactive
    castor quickstart
"""

from __future__ import annotations

import re
import secrets
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Provider / hardware lookup tables
# ---------------------------------------------------------------------------

PROVIDER_MODELS: dict[str, str] = {
    "google": "gemini-2.5-flash",
    "anthropic": "claude-3-5-haiku-20241022",
    "openai": "gpt-4o-mini",
    "local": "llama3.2:3b",
}

HARDWARE_CHOICES: dict[str, str] = {
    "1": "raspberry_pi_4",
    "2": "raspberry_pi_5",
    "3": "linux_arm64",
    "4": "macos",
}

PROVIDER_CHOICES: dict[str, str] = {
    "1": "google",
    "2": "anthropic",
    "3": "openai",
    "4": "local",
}

PROVIDER_LABELS: dict[str, str] = {
    "google": "Google Gemini",
    "anthropic": "Anthropic Claude",
    "openai": "OpenAI GPT",
    "local": "Local (Ollama / llama.cpp)",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Convert a robot name to a filesystem/URL-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "my-robot"


def _prompt(question: str, default: str = "", password: bool = False) -> str:
    """Ask the user a question with a default shown in brackets.

    Exits gracefully on Ctrl-C / EOF.
    """
    bracket = f" [{default}]" if default else ""
    try:
        val = input(f"{question}{bracket}: ").strip()
        return val if val else default
    except (EOFError, KeyboardInterrupt):
        print("\n\n  👋 Setup cancelled.  Run 'castor init' again when ready.\n")
        sys.exit(0)


def _generate_ruri(robot_name: str, robot_uuid: str) -> str:
    """Derive a stable RCAN URI from the robot name and UUID prefix."""
    slug = _slugify(robot_name)
    prefix = robot_uuid.replace("-", "")[:8]
    return f"rcan://{slug}.local.{prefix}"


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------


def generate_wizard_config(
    *,
    robot_name: str,
    provider: str,
    api_key: str = "",
    firebase_project: str = "opencastor",
    port: int = 8080,
    hardware: str = "raspberry_pi_4",
) -> tuple[dict, str, str]:
    """Build the full RCAN config dict.

    Returns:
        (config_dict, rrn, output_filename)
    """
    robot_uuid = str(uuid.uuid4())
    rrn_suffix = uuid.uuid4().hex[:12].upper()
    rrn = f"RRN-{rrn_suffix}"
    ruri = _generate_ruri(robot_name, robot_uuid)
    api_token = secrets.token_hex(32)  # 64 hex chars
    model = PROVIDER_MODELS.get(provider, "gemini-2.5-flash")
    firebase_enabled = bool(firebase_project and firebase_project.strip() != "opencastor")

    # Agent block — include API key inline only if provided
    agent_block: dict = {
        "provider": provider,
        "model": model,
        "temperature": 0.7,
    }
    _key_fields = {
        "google": "google_api_key",
        "anthropic": "anthropic_api_key",
        "openai": "openai_api_key",
    }
    if api_key and provider in _key_fields:
        agent_block[_key_fields[provider]] = api_key

    config: dict = {
        "rcan_version": "1.6",
        "metadata": {
            "robot_name": robot_name,
            "robot_uuid": robot_uuid,
            "ruri": ruri,
            "rrn": rrn,
            "hardware_platform": hardware,
        },
        "agent": agent_block,
        "gateway": {
            "host": "0.0.0.0",
            "port": port,
            "api_token": api_token,
        },
        "rcan_protocol": {
            "enable_mdns": True,
            "version": "1.6",
        },
        "skills": {
            "enabled": True,
            "builtin_skills": ["navigator", "vision", "code-reviewer"],
        },
        "memory": {
            "enabled": True,
        },
        "firebase": {
            "project_id": firebase_project or "opencastor",
            "enabled": firebase_enabled,
        },
    }

    slug = _slugify(robot_name)
    filename = f"{slug}.rcan.yaml"
    return config, rrn, filename


# ---------------------------------------------------------------------------
# QR code printing
# ---------------------------------------------------------------------------


def _print_qr(url: str) -> None:
    """Print a scannable QR code, falling back to a text box if qrcode is unavailable."""
    try:
        import qrcode  # type: ignore[import]

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        for row in matrix:
            print("".join("██" if cell else "  " for cell in row))
    except ImportError:
        # Minimal ASCII fallback — two border lines + URL
        width = max(len(url) + 4, 44)
        bar = "─" * width
        print(f"┌{bar}┐")
        padded = url.center(width - 2)
        print(f"│{padded}│")
        print(f"└{bar}┘")


# ---------------------------------------------------------------------------
# Next-steps box
# ---------------------------------------------------------------------------

_BOX_WIDTH = 46  # inner width (chars between ║ … ║)


def _box_line(text: str) -> str:
    """Pad text to fill a box line."""
    return f"║  {text:<{_BOX_WIDTH - 2}}║"


def _print_next_steps(config_filename: str) -> None:
    top = "╔" + "═" * _BOX_WIDTH + "╗"
    bot = "╚" + "═" * _BOX_WIDTH + "╝"
    print(top)
    print(_box_line("Next steps:"))
    print(_box_line(f"1. castor gateway --config {config_filename}"))
    print(_box_line("2. Open app.opencastor.com"))
    print(_box_line("3. Scan this QR code to add your robot:"))
    print(bot)


# ---------------------------------------------------------------------------
# Main wizard entry point
# ---------------------------------------------------------------------------


def run_wizard(
    *,
    name: Optional[str] = None,
    provider: Optional[str] = None,
    port: Optional[int] = None,
    api_key: Optional[str] = None,
    firebase_project: Optional[str] = None,
    output: Optional[str] = None,
    no_interactive: bool = False,
    overwrite: bool = False,
) -> str:
    """Run the init wizard and return the path to the generated config file.

    In interactive mode (default) prompts the user for each setting.
    In ``--no-interactive`` mode every prompt falls back to its default or
    the value passed in as a keyword argument.

    Returns:
        Absolute path to the written ``.rcan.yaml`` config file.

    Raises:
        FileExistsError: If the output file already exists and overwrite=False.
    """
    import yaml  # pyyaml is a core dep

    interactive = not no_interactive

    if interactive:
        print()
        print("🤖 OpenCastor Setup Wizard")
        print("─" * 30)

    # ── Robot name ─────────────────────────────────────────────────────────
    if interactive:
        robot_name = _prompt("Robot name", default=name or "my-robot")
    else:
        robot_name = name or "my-robot"

    # ── Derive default output path from robot name ─────────────────────────
    slug = _slugify(robot_name)
    resolved_output = output or f"{slug}.rcan.yaml"
    output_path = Path(resolved_output)

    # ── Overwrite guard ────────────────────────────────────────────────────
    if output_path.exists() and not overwrite:
        if not interactive:
            raise FileExistsError(
                f"Config already exists: {output_path}. "
                "Use --overwrite to replace it, or choose a different --output path."
            )
        print(f"\n⚠️  Config already exists: {output_path}")
        answer = _prompt("Overwrite?", default="n")
        if answer.lower() not in ("y", "yes"):
            print("  Cancelled — use a different name or pass --overwrite.")
            sys.exit(0)

    # ── Hardware platform ──────────────────────────────────────────────────
    if interactive:
        print()
        print("Hardware platform:")
        print("  1) Raspberry Pi 4")
        print("  2) Raspberry Pi 5")
        print("  3) Other Linux (x86/ARM)")
        print("  4) macOS (dev/sim)")
        hw_choice = _prompt("Select", default="1")
        hardware = HARDWARE_CHOICES.get(hw_choice, "raspberry_pi_4")
    else:
        hardware = "raspberry_pi_4"

    # ── AI provider ────────────────────────────────────────────────────────
    if interactive:
        print()
        print("AI provider:")
        print("  1) Google Gemini (free tier available) ← recommended")
        print("  2) Anthropic Claude")
        print("  3) OpenAI GPT")
        print("  4) Local (Ollama / llama.cpp)")
        prov_choice = _prompt("Select", default="1")
        resolved_provider = PROVIDER_CHOICES.get(prov_choice, "google")
    else:
        # Accept provider name directly (for --no-interactive)
        resolved_provider = provider or "google"
        # Also accept numeric strings
        if resolved_provider in PROVIDER_CHOICES:
            resolved_provider = PROVIDER_CHOICES[resolved_provider]

    # ── API key ────────────────────────────────────────────────────────────
    if interactive and resolved_provider != "local":
        label = PROVIDER_LABELS.get(resolved_provider, resolved_provider)
        resolved_api_key = _prompt(
            f"API key for {label} [leave blank to set later]",
            default=api_key or "",
        )
    else:
        resolved_api_key = api_key or ""

    # ── Firebase project ID ────────────────────────────────────────────────
    if interactive:
        resolved_firebase = _prompt("Firebase project ID", default=firebase_project or "opencastor")
    else:
        resolved_firebase = firebase_project or "opencastor"

    # ── Port ───────────────────────────────────────────────────────────────
    if interactive:
        port_str = _prompt("Robot port", default=str(port or 8080))
        try:
            resolved_port = int(port_str)
        except ValueError:
            resolved_port = 8080
    else:
        resolved_port = port or 8080

    # ── Generate config ─────────────────────────────────────────────────────
    print()
    print(f"✅ Writing config to {output_path.name}...")
    print("✅ Generating robot UUID...")

    config, rrn, _filename = generate_wizard_config(
        robot_name=robot_name,
        provider=resolved_provider,
        api_key=resolved_api_key,
        firebase_project=resolved_firebase,
        port=resolved_port,
        hardware=hardware,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        yaml.dump(config, fh, sort_keys=False, default_flow_style=False, allow_unicode=True)

    # ── Next steps + QR ───────────────────────────────────────────────────
    print()
    _print_next_steps(output_path.name)
    print()

    fleet_url = f"https://app.opencastor.com/fleet?rrn={rrn}"
    _print_qr(fleet_url)
    print()
    print(f"RRN: {rrn}")
    print()

    return str(output_path.resolve())


# ---------------------------------------------------------------------------
# CLI shims — called directly from castor/cli.py
# ---------------------------------------------------------------------------


def cmd_init(args) -> None:
    """castor init — interactive (or --no-interactive) setup wizard."""
    # Legacy --print flag: emit YAML to stdout, no wizard
    if getattr(args, "print", False):
        from castor.init_config import generate_config

        print(generate_config(robot_name=getattr(args, "name", None)))
        return

    no_interactive = getattr(args, "no_interactive", False)

    # If stdin is not a TTY and --no-interactive was not explicitly set,
    # fall back to silent non-interactive mode rather than hanging.
    if not no_interactive and not sys.stdin.isatty():
        no_interactive = True

    try:
        run_wizard(
            name=getattr(args, "name", None),
            provider=getattr(args, "provider", None),
            port=getattr(args, "port", None),
            api_key=getattr(args, "api_key", None),
            firebase_project=getattr(args, "firebase_project", None),
            output=getattr(args, "output", None),
            no_interactive=no_interactive,
            overwrite=getattr(args, "overwrite", False),
        )
    except FileExistsError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def cmd_quickstart(args) -> None:
    """castor quickstart — run init wizard, then immediately start the gateway."""
    no_interactive = getattr(args, "no_interactive", False)
    if not no_interactive and not sys.stdin.isatty():
        no_interactive = True

    print("\n  🚀 OpenCastor QuickStart\n")
    print("  Step 1: Running setup wizard...")

    try:
        config_path = run_wizard(
            name=getattr(args, "name", None),
            provider=getattr(args, "provider", None),
            port=getattr(args, "port", None),
            api_key=getattr(args, "api_key", None),
            firebase_project=getattr(args, "firebase_project", None),
            output=getattr(args, "output", None),
            no_interactive=no_interactive,
            overwrite=getattr(args, "overwrite", False),
        )
    except FileExistsError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except SystemExit:
        raise

    print(f"\n  Step 2: Starting gateway (config: {config_path})...")
    gateway_port = getattr(args, "port", None) or 8080
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "castor",
            "gateway",
            "--config",
            config_path,
            "--host",
            "0.0.0.0",
            "--port",
            str(gateway_port),
        ]
    )
    raise SystemExit(result.returncode)

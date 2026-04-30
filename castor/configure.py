"""
OpenCastor Configure -- interactive config editor.

Post-setup interactive editor for tweaking RCAN config values
without re-running the full wizard. Supports common adjustments:
  - Switch AI provider/model
  - Change latency budget
  - Toggle safety features
  - Add/remove channels
  - Adjust physics parameters

Usage:
    castor configure --config robot.rcan.yaml
"""

import os

import yaml


def run_configure(config_path: str):
    """Interactive config editor."""
    if not os.path.exists(config_path):
        print(f"\n  Config not found: {config_path}")
        print("  Run `castor wizard` to create one first.\n")
        return

    with open(config_path) as f:
        config = yaml.safe_load(f)

    robot_name = config.get("metadata", {}).get("robot_name", "Robot")

    try:
        from rich.console import Console

        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False
        console = None

    if has_rich:
        console.print(f"\n[bold cyan]  OpenCastor Configure[/] -- {robot_name}")
        console.print(f"  Editing: [dim]{config_path}[/]\n")
    else:
        print(f"\n  OpenCastor Configure -- {robot_name}")
        print(f"  Editing: {config_path}\n")

    modified = False

    while True:
        print("  What would you like to change?")
        print("    [1] AI Provider / Model")
        print("    [2] Latency Budget")
        print("    [3] Safety Settings")
        print("    [4] Privacy Settings")
        print("    [5] Network / Port")
        print("    [6] Robot Name")
        print("    [7] View Current Config")
        print("    [0] Save and Exit")
        print()

        try:
            choice = input("  Selection: ").strip()
        except (EOFError, KeyboardInterrupt):
            choice = "0"

        if choice == "0":
            break
        elif choice == "1":
            modified = _edit_provider(config) or modified
        elif choice == "2":
            modified = _edit_latency(config) or modified
        elif choice == "3":
            modified = _edit_safety(config) or modified
        elif choice == "4":
            modified = _edit_privacy(config) or modified
        elif choice == "5":
            modified = _edit_network(config) or modified
        elif choice == "6":
            modified = _edit_name(config) or modified
        elif choice == "7":
            _show_config(config, has_rich, console)
        else:
            print("  Invalid selection.\n")

    if modified:
        # Backup before saving
        backup_path = config_path + ".bak"
        try:
            import shutil

            shutil.copy2(config_path, backup_path)
            print(f"  Backup saved: {backup_path}")
        except Exception:
            pass

        with open(config_path, "w") as f:
            yaml.dump(config, f, sort_keys=False, default_flow_style=False)

        if has_rich:
            console.print(f"\n  [green]Config saved:[/] {config_path}\n")
        else:
            print(f"\n  Config saved: {config_path}\n")
    else:
        print("\n  No changes made.\n")


def _input_default(prompt, default):
    response = input(f"  {prompt} [{default}]: ").strip()
    return response if response else str(default)


def _edit_provider(config: dict) -> bool:
    """Edit AI provider and model."""
    from castor.setup_catalog import get_provider_specs

    agent = config.setdefault("agent", {})
    current_provider = agent.get("provider", "?")
    current_model = agent.get("model", "?")
    provider_names = ", ".join(sorted(get_provider_specs(include_hidden=True).keys()))

    print(f"\n  Current: {current_provider} / {current_model}")
    print(f"  Available providers: {provider_names}")

    new_provider = _input_default("Provider", current_provider)
    new_model = _input_default("Model", current_model)

    if new_provider != current_provider or new_model != current_model:
        agent["provider"] = new_provider
        agent["model"] = new_model
        print(f"  Updated: {new_provider} / {new_model}\n")
        return True

    print("  No changes.\n")
    return False


def _edit_latency(config: dict) -> bool:
    """Edit latency budget."""
    agent = config.setdefault("agent", {})
    current = agent.get("latency_budget_ms", 3000)

    print(f"\n  Current latency budget: {current}ms")
    print("  Recommended: 1000-5000ms for cloud providers, 200-500ms for local")

    new_val = _input_default("Latency budget (ms)", current)
    try:
        new_val = int(new_val)
    except ValueError:
        print("  Invalid value.\n")
        return False

    if new_val != current:
        agent["latency_budget_ms"] = new_val
        print(f"  Updated: {new_val}ms\n")
        return True

    print("  No changes.\n")
    return False


def _edit_safety(config: dict) -> bool:
    """Edit safety settings."""
    agent = config.setdefault("agent", {})
    physics = config.setdefault("physics", {})

    current_stop = agent.get("safety_stop", True)
    current_speed = physics.get("max_speed_ms", 0.5)
    current_approval = agent.get("require_approval", False)

    print(f"\n  safety_stop: {current_stop}")
    print(f"  max_speed_ms: {current_speed}")
    print(f"  require_approval: {current_approval}")

    changed = False

    val = _input_default("safety_stop (true/false)", str(current_stop).lower())
    new_stop = val.lower() in ("true", "1", "yes")
    if new_stop != current_stop:
        agent["safety_stop"] = new_stop
        changed = True

    val = _input_default("max_speed_ms", current_speed)
    try:
        new_speed = float(val)
        if new_speed != current_speed:
            physics["max_speed_ms"] = new_speed
            changed = True
    except ValueError:
        pass

    val = _input_default("require_approval (true/false)", str(current_approval).lower())
    new_approval = val.lower() in ("true", "1", "yes")
    if new_approval != current_approval:
        agent["require_approval"] = new_approval
        changed = True

    if changed:
        print("  Safety settings updated.\n")
    else:
        print("  No changes.\n")
    return changed


def _edit_privacy(config: dict) -> bool:
    """Edit privacy settings."""
    privacy = config.setdefault("privacy", {})

    settings = {
        "camera_streaming": privacy.get("camera_streaming", False),
        "audio_recording": privacy.get("audio_recording", False),
        "location_sharing": privacy.get("location_sharing", False),
    }

    print("\n  Privacy settings (default: DENIED):")
    for key, value in settings.items():
        status = "ALLOWED" if value else "DENIED"
        print(f"    {key}: {status}")

    changed = False
    for key in settings:
        val = _input_default(f"{key} (true/false)", str(settings[key]).lower())
        new_val = val.lower() in ("true", "1", "yes")
        if new_val != settings[key]:
            privacy[key] = new_val
            changed = True

    if changed:
        print("  Privacy settings updated.\n")
    else:
        print("  No changes.\n")
    return changed


def _edit_network(config: dict) -> bool:
    """Edit network settings."""
    rcan_proto = config.setdefault("rcan_protocol", {})
    current_port = rcan_proto.get("port", 8000)
    current_mdns = rcan_proto.get("enable_mdns", False)

    print(f"\n  port: {current_port}")
    print(f"  enable_mdns: {current_mdns}")

    changed = False

    val = _input_default("port", current_port)
    try:
        new_port = int(val)
        if new_port != current_port:
            rcan_proto["port"] = new_port
            changed = True
    except ValueError:
        pass

    val = _input_default("enable_mdns (true/false)", str(current_mdns).lower())
    new_mdns = val.lower() in ("true", "1", "yes")
    if new_mdns != current_mdns:
        rcan_proto["enable_mdns"] = new_mdns
        changed = True

    if changed:
        print("  Network settings updated.\n")
    else:
        print("  No changes.\n")
    return changed


def _edit_name(config: dict) -> bool:
    """Edit robot name."""
    metadata = config.setdefault("metadata", {})
    current = metadata.get("robot_name", "Robot")

    new_name = _input_default("Robot name", current)
    if new_name != current:
        metadata["robot_name"] = new_name
        print(f"  Updated: {new_name}\n")
        return True

    print("  No changes.\n")
    return False


def _show_config(config: dict, has_rich: bool, console):
    """Pretty-print the current config."""
    text = yaml.dump(config, sort_keys=False, default_flow_style=False)

    if has_rich:
        from rich.syntax import Syntax

        console.print()
        console.print(Syntax(text, "yaml", theme="monokai", line_numbers=True))
        console.print()
    else:
        print()
        for line in text.splitlines():
            print(f"    {line}")
        print()


# ---------------------------------------------------------------------------
# Gate config parsing (used by main.py / api.py at startup)
# ---------------------------------------------------------------------------


def parse_confidence_gates(config: dict) -> list:
    """Parse ``agent.confidence_gates`` from a config dict.

    Returns a list of :class:`~castor.confidence_gate.ConfidenceGate` objects.
    Returns an empty list when the key is absent.

    Example RCAN YAML::

        agent:
          confidence_gates:
            - scope: control
              min_confidence: 0.6
              on_fail: escalate
    """
    from castor.confidence_gate import ConfidenceGate

    raw = config.get("agent", {}).get("confidence_gates", []) or []
    gates = []
    for g in raw:
        try:
            gates.append(
                ConfidenceGate(
                    scope=g["scope"],
                    min_confidence=float(g["min_confidence"]),
                    on_fail=g.get("on_fail", "escalate"),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            import logging

            logging.getLogger("OpenCastor.Configure").warning(
                "Skipping malformed confidence_gate entry: %s (%s)", g, exc
            )
    return gates


def parse_hitl_gates(config: dict) -> list:
    """Parse ``agent.hitl_gates`` from a config dict.

    Returns a list of :class:`~castor.hitl_gate.HiTLGate` objects.
    Returns an empty list when the key is absent.

    Example RCAN YAML::

        agent:
          hitl_gates:
            - action_types: [grip]
              require_auth: true
              auth_timeout_ms: 30000
              on_timeout: block
              notify: [whatsapp]
    """
    from castor.hitl_gate import HiTLGate

    raw = config.get("agent", {}).get("hitl_gates", []) or []
    gates = []
    for g in raw:
        try:
            gates.append(
                HiTLGate(
                    action_types=list(g.get("action_types", [])),
                    require_auth=bool(g.get("require_auth", True)),
                    auth_timeout_ms=int(g.get("auth_timeout_ms", 30000)),
                    on_timeout=g.get("on_timeout", "block"),
                    notify=list(g.get("notify", [])),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            import logging

            logging.getLogger("OpenCastor.Configure").warning(
                "Skipping malformed hitl_gate entry: %s (%s)", g, exc
            )
    return gates


# Action types covered when a robot's RCAN config sets `consent.scope_threshold`
# to the named scope. Wider scopes (lower-trust) include narrower ones.
#
# RCAN scope ladder (loosest → strictest):
#   read  →  command  →  control  →  hardware
#
# A robot declaring `scope_threshold: control` is asking for explicit consent
# on any *control-or-stricter* action (arm motion, gripper close, etc.). A
# robot declaring `scope_threshold: command` adds command-level actions on top
# of those.
_CONSENT_SCOPE_ACTION_TYPES: dict[str, list[str]] = {
    "control": ["pick_place", "arm_pose", "grip", "set_joint_positions"],
    "hardware": ["pick_place", "arm_pose", "grip", "set_joint_positions"],
    "command": ["pick_place", "arm_pose", "grip", "set_joint_positions", "command"],
}


def parse_consent_gates(config: dict) -> list:
    """Auto-derive HiTL gates from a ``consent`` block in the RCAN config.

    Bridges the gap between the high-level RCAN consent declaration:

        consent:
          required: true
          mode: explicit
          scope_threshold: control

    …and the per-action-type :class:`~castor.hitl_gate.HiTLGate` infrastructure.
    Returns an empty list when consent is not required or the scope_threshold
    is below ``control`` (sensor-only scopes don't gate motor action).
    """
    from castor.hitl_gate import HiTLGate

    consent = config.get("consent") or {}
    if not consent.get("required"):
        return []

    scope = consent.get("scope_threshold")
    action_types = _CONSENT_SCOPE_ACTION_TYPES.get(scope, [])
    if not action_types:
        return []

    return [
        HiTLGate(
            action_types=action_types,
            require_auth=True,
            auth_timeout_ms=int(consent.get("auth_timeout_ms", 30000)),
            on_timeout=consent.get("on_timeout", "block"),
            notify=list(consent.get("notify", [])),
        )
    ]

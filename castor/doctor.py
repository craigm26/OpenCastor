"""
OpenCastor Doctor -- system health checks.

Validates the local environment: Python version, .env file, API keys,
RCAN config schema, hardware SDKs, and camera availability.

Usage:
    castor doctor
    castor doctor --config robot.rcan.yaml
"""

import os
import sys


def check_python_version():
    """Check Python >= 3.10."""
    ok = sys.version_info >= (3, 10)
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    detail = ver if ok else f"{ver} (requires 3.10+)"
    return ok, "Python version", detail


def check_env_file():
    """Check that a .env file exists."""
    ok = os.path.exists(".env")
    detail = "found" if ok else "missing -- run: cp .env.example .env"
    return ok, ".env file", detail


def check_provider_keys(config=None):
    """Check which AI provider keys are available.

    If *config* is provided and has ``agent.provider``, only check that
    provider.  Otherwise check all known providers.
    """
    from castor.auth import load_dotenv_if_available, list_available_providers

    load_dotenv_if_available()
    providers = list_available_providers()

    # If a specific provider is requested via config, check only that one
    if config:
        agent = config.get("agent", {})
        name = agent.get("provider", "").lower()
        if name and name in providers:
            ok = providers[name]
            detail = "key found" if ok else "no key set"
            return [(ok, f"Provider key ({name})", detail)]

    # Otherwise report all
    results = []
    for name, ready in providers.items():
        detail = "key found" if ready else "no key"
        if name == "ollama":
            detail = "no key needed" if ready else "no key"
        results.append((ready, f"Provider key ({name})", detail))
    return results


def check_rcan_config(config_path):
    """Validate an RCAN config file against the JSON schema."""
    if not config_path:
        return False, "RCAN config", "no config path provided"

    if not os.path.exists(config_path):
        return False, "RCAN config", f"{config_path} not found"

    try:
        import yaml
        import jsonschema

        schema_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "config", "rcan.schema.json"
        )
        if not os.path.exists(schema_path):
            return False, "RCAN config", "schema file not found"

        import json

        with open(schema_path) as f:
            schema = json.load(f)

        with open(config_path) as f:
            data = yaml.safe_load(f)

        jsonschema.validate(data, schema)
        return True, "RCAN config", f"{config_path} valid"

    except jsonschema.ValidationError as exc:
        return False, "RCAN config", f"validation error: {exc.message}"
    except Exception as exc:
        return False, "RCAN config", str(exc)


def check_hardware_sdks():
    """Try importing hardware SDKs and report which are available."""
    sdks = [
        ("dynamixel_sdk", "Dynamixel SDK"),
        ("adafruit_pca9685", "Adafruit PCA9685"),
        ("picamera2", "PiCamera2"),
        ("cv2", "OpenCV"),
    ]
    results = []
    for module, label in sdks:
        try:
            __import__(module)
            results.append((True, f"SDK: {label}", "installed"))
        except ImportError:
            results.append((False, f"SDK: {label}", "not installed"))
    return results


def check_camera():
    """Quick check whether a camera is accessible via OpenCV."""
    try:
        import cv2

        cap = cv2.VideoCapture(0)
        ok = cap.isOpened()
        cap.release()
        detail = "accessible" if ok else "not accessible"
        return ok, "Camera", detail
    except ImportError:
        return False, "Camera", "OpenCV not installed"
    except Exception as exc:
        return False, "Camera", str(exc)


# ── Runner functions ──────────────────────────────────────────────────


def run_all_checks(config_path=None):
    """Run every health check.  Returns a flat list of (ok, name, detail) tuples."""
    results = []

    results.append(check_python_version())
    results.append(check_env_file())

    # Load config if a path was given, for provider-specific checks
    config = None
    if config_path and os.path.exists(config_path):
        try:
            import yaml

            with open(config_path) as f:
                config = yaml.safe_load(f)
        except Exception:
            pass

    provider_results = check_provider_keys(config)
    results.extend(provider_results)

    if config_path:
        results.append(check_rcan_config(config_path))

    results.extend(check_hardware_sdks())
    results.append(check_camera())

    return results


def run_post_wizard_checks(config_path, config, provider_name):
    """Run the subset of checks relevant after wizard completion."""
    results = []

    # Validate the config just written
    results.append(check_rcan_config(config_path))

    # Check the chosen provider key
    stub_config = {"agent": {"provider": provider_name}}
    provider_results = check_provider_keys(stub_config)
    results.extend(provider_results)

    return results


# ── Output ────────────────────────────────────────────────────────────


def print_report(results, colors_class=None):
    """Print a pass/fail report.

    Uses Rich if available for styled output, otherwise falls back to
    ANSI codes via *colors_class* (e.g. the wizard's ``Colors`` class).
    """
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        table.add_column("", width=6)
        table.add_column("Check")
        table.add_column("Detail")

        passed = failed = 0
        for ok, name, detail in results:
            if ok:
                table.add_row("[green]PASS[/]", name, detail)
                passed += 1
            else:
                table.add_row("[red]FAIL[/]", name, detail)
                failed += 1

        console.print(table)
        status_color = "green" if failed == 0 else "yellow"
        console.print(f"\n  [{status_color}]{passed} passed, {failed} failed[/]")
        return failed == 0

    except ImportError:
        pass

    # Fallback: ANSI colors
    green = getattr(colors_class, "GREEN", "")
    red = getattr(colors_class, "FAIL", "")
    end = getattr(colors_class, "ENDC", "")

    passed = failed = 0
    for ok, name, detail in results:
        if ok:
            tag = f"{green}PASS{end}"
            passed += 1
        else:
            tag = f"{red}FAIL{end}"
            failed += 1
        print(f"  [{tag}] {name}: {detail}")

    print(f"\n  {passed} passed, {failed} failed")
    return failed == 0

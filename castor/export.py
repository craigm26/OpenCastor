"""
OpenCastor Export -- export config bundles and send webhook notifications.

Export: Creates a shareable bundle of config + metadata (no secrets).
Webhook: POSTs notifications on events (errors, low battery, status changes).

Usage:
    castor export --config robot.rcan.yaml --output robot-bundle.zip
    castor export --config robot.rcan.yaml --format json
"""

import json
import logging
import os
import zipfile
from datetime import datetime

import yaml

logger = logging.getLogger("OpenCastor.Export")


def export_bundle(config_path: str, output_path: str = None, fmt: str = "zip") -> str:
    """Export a config bundle (config + metadata, no secrets).

    Args:
        config_path: Path to the RCAN config file.
        output_path: Output file path (auto-generated if None).
        fmt: Output format -- ``"zip"`` or ``"json"``.

    Returns:
        Path to the exported file.
    """
    with open(config_path) as f:
        config = yaml.safe_load(f)

    robot_name = config.get("metadata", {}).get("robot_name", "robot")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Build metadata
    bundle_meta = {
        "exported_at": datetime.now().isoformat(),
        "robot_name": robot_name,
        "model": config.get("metadata", {}).get("model", "unknown"),
        "rcan_version": config.get("rcan_version", "unknown"),
        "provider": config.get("agent", {}).get("provider", "unknown"),
        "ai_model": config.get("agent", {}).get("model", "unknown"),
        "drivers": [d.get("protocol", "?") for d in config.get("drivers", [])],
        "channels": [c.get("type", "?") for c in config.get("channels", [])],
    }

    # Sanitize config -- remove any inline API keys
    sanitized = _sanitize_config(config)

    if fmt == "json":
        if not output_path:
            output_path = f"{robot_name}_{timestamp}.json"
        data = {
            "metadata": bundle_meta,
            "config": sanitized,
        }
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
    else:
        if not output_path:
            output_path = f"{robot_name}_{timestamp}.zip"
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("metadata.json", json.dumps(bundle_meta, indent=2))
            zf.writestr("config.rcan.yaml", yaml.dump(sanitized, default_flow_style=False))

            # Include presets if referenced
            for preset_file in _find_preset_files(config_path):
                if os.path.exists(preset_file):
                    zf.write(preset_file, os.path.basename(preset_file))

    logger.info(f"Bundle exported to {output_path}")
    return output_path


def _sanitize_config(config: dict) -> dict:
    """Remove secrets from a config dict (deep copy)."""
    import copy

    sanitized = copy.deepcopy(config)

    # Remove API keys
    agent = sanitized.get("agent", {})
    if "api_key" in agent:
        agent["api_key"] = "<REDACTED>"

    # Remove channel credentials
    for ch in sanitized.get("channels", []):
        for key in list(ch.keys()):
            if "token" in key.lower() or "secret" in key.lower() or "key" in key.lower():
                ch[key] = "<REDACTED>"

    return sanitized


def _find_preset_files(config_path: str) -> list:
    """Find preset files related to the config."""
    config_dir = os.path.dirname(os.path.abspath(config_path))
    preset_dir = os.path.join(config_dir, "config", "presets")
    if not os.path.isdir(preset_dir):
        # Try project root
        preset_dir = os.path.join(os.getcwd(), "config", "presets")

    if not os.path.isdir(preset_dir):
        return []

    return [os.path.join(preset_dir, f) for f in os.listdir(preset_dir) if f.endswith(".rcan.yaml")]


def print_export_summary(output_path: str, fmt: str):
    """Print export results."""
    try:
        from rich.console import Console

        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False

    size = os.path.getsize(output_path)
    size_str = f"{size / 1024:.1f} KB" if size > 1024 else f"{size} bytes"

    if has_rich:
        console.print(f"\n[bold green]  Exported:[/] {output_path} ({size_str})")
        console.print(f"  Format: {fmt}")
        console.print("  Note: API keys have been redacted for safety.\n")
    else:
        print(f"\n  Exported: {output_path} ({size_str})")
        print(f"  Format: {fmt}")
        print("  Note: API keys have been redacted for safety.\n")


# ---------------------------------------------------------------------------
# Webhook notifications
# ---------------------------------------------------------------------------


def send_webhook(url: str, event: str, data: dict = None) -> bool:
    """Send a webhook notification.

    Args:
        url: The webhook endpoint URL.
        event: Event type (e.g. ``"error"``, ``"low_battery"``, ``"status_change"``).
        data: Additional event data.

    Returns:
        True if the webhook was delivered successfully.
    """
    try:
        import httpx
    except ImportError:
        logger.debug("httpx not installed -- webhook disabled")
        return False

    payload = {
        "event": event,
        "timestamp": datetime.now().isoformat(),
        "source": "opencastor",
        "data": data or {},
    }

    try:
        resp = httpx.post(url, json=payload, timeout=5.0)
        if resp.status_code < 300:
            logger.debug(f"Webhook delivered: {event} -> {url}")
            return True
        else:
            logger.warning(f"Webhook failed ({resp.status_code}): {event} -> {url}")
            return False
    except Exception as exc:
        logger.debug(f"Webhook error: {exc}")
        return False


def send_configured_webhooks(config: dict, event: str, data: dict = None):
    """Send webhooks to all URLs configured in the RCAN config.

    RCAN config format::

        webhooks:
          - url: https://example.com/hook
            events: [error, low_battery]
          - url: https://slack.com/webhook
            events: [all]
    """
    webhooks = config.get("webhooks", [])
    for hook in webhooks:
        url = hook.get("url", "")
        events = hook.get("events", [])
        if not url:
            continue
        if "all" in events or event in events:
            send_webhook(url, event, data)

"""
OpenCastor Export -- export config bundles and send webhook notifications.

Export: Creates a shareable bundle of config + metadata (no secrets).
Webhook: POSTs notifications on events (errors, low battery, status changes).

Usage:
    castor export --config robot.rcan.yaml --output robot-bundle.zip
    castor export --config robot.rcan.yaml --format json
    castor export --config robot.rcan.yaml --format tgz --episodes 50
"""

import hashlib
import io
import json
import logging
import os
import tarfile
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
    sanitized, withheld = _sanitize_config_with_record(config)
    bundle_meta["withheld"] = withheld

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


def export_bundle_tgz(
    config_path: str,
    output_path: str = None,
    episodes_limit: int = 100,
) -> str:
    """Export a tar.gz bundle containing sanitized config, episodes, and manifest.

    The archive contains:
    - ``manifest.json``: version, timestamp, file checksums
    - ``config.rcan.yaml``: sanitized RCAN config (API keys redacted)
    - ``episodes.jsonl``: recent episode records from EpisodeMemory
    - ``env_vars.json``: environment variable *names* (values are NOT included)

    Args:
        config_path: Path to the RCAN config file.
        output_path: Output ``.tar.gz`` path (auto-generated if None).
        episodes_limit: Max number of recent episodes to include.

    Returns:
        Path to the exported ``.tar.gz`` file.
    """
    import importlib

    with open(config_path) as f:
        config = yaml.safe_load(f)

    robot_name = config.get("metadata", {}).get("robot_name", "robot")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if not output_path:
        output_path = f"{robot_name}_{timestamp}.tar.gz"

    # Build sanitized config YAML bytes
    sanitized, withheld = _sanitize_config_with_record(config)
    config_bytes = yaml.dump(sanitized, default_flow_style=False).encode()

    # Export episodes to JSONL bytes
    episodes_bytes = b""
    episodes_note: dict = {"unavailable": False, "truncated": False, "limit": episodes_limit}
    try:
        mem_mod = importlib.import_module("castor.memory")
        db_path = os.getenv("CASTOR_MEMORY_DB", os.path.expanduser("~/.castor/memory.db"))
        mem = mem_mod.EpisodeMemory(db_path=db_path)
        recent = mem.query_recent(limit=episodes_limit)
        lines = []
        for ep in recent:
            lines.append(json.dumps(ep, default=str))
        episodes_bytes = "\n".join(lines).encode()
        episodes_note["returned"] = len(lines)
        # A full page back from a limited query may well be a truncation.
        episodes_note["truncated"] = len(lines) >= episodes_limit
    except Exception as exc:
        logger.debug("export_bundle_tgz: could not load episodes: %s", exc)
        episodes_bytes = b""
        episodes_note = {
            "unavailable": True,
            "truncated": False,
            "limit": episodes_limit,
            "returned": 0,
            "error": str(exc),
        }

    # Collect env var names only (no values)
    castor_env_names = [k for k in os.environ if k.startswith(("CASTOR_", "OPENCASTOR_"))]
    env_vars_bytes = json.dumps({"env_var_names": sorted(castor_env_names)}, indent=2).encode()

    # Compute checksums
    checksums = {
        "config.rcan.yaml": hashlib.sha256(config_bytes).hexdigest(),
        "episodes.jsonl": hashlib.sha256(episodes_bytes).hexdigest(),
        "env_vars.json": hashlib.sha256(env_vars_bytes).hexdigest(),
    }

    try:
        from castor import __version__ as _version
    except Exception:
        _version = "unknown"

    manifest = {
        "exported_at": datetime.now().isoformat(),
        "robot_name": robot_name,
        "version": _version,
        "rcan_version": config.get("rcan_version", "unknown"),
        "provider": config.get("agent", {}).get("provider", "unknown"),
        "ai_model": config.get("agent", {}).get("model", "unknown"),
        "episodes_included": len(episodes_bytes.splitlines()),
        "checksums": checksums,
        # Every field removed by sanitisation, named by path and class, so an
        # empty value in config.rcan.yaml is never mistaken for an absent one.
        "withheld": withheld,
        "episodes_note": episodes_note,
    }
    manifest_bytes = json.dumps(manifest, indent=2).encode()

    with tarfile.open(output_path, "w:gz") as tf:
        for name, data in [
            ("manifest.json", manifest_bytes),
            ("config.rcan.yaml", config_bytes),
            ("episodes.jsonl", episodes_bytes),
            ("env_vars.json", env_vars_bytes),
        ]:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

    logger.info("Bundle (tgz) exported to %s", output_path)
    return output_path


def _sanitize_config(config: dict) -> dict:
    """Remove secrets from a config dict (deep copy). Back-compat wrapper."""
    sanitized, _withheld = _sanitize_config_with_record(config)
    return sanitized


def _sanitize_config_with_record(config: dict) -> tuple[dict, list]:
    """Remove secrets from a config dict and return what was removed.

    Returns ``(sanitized_config, withheld)`` where ``withheld`` is a list of
    ``{"path": ..., "class": ...}`` records naming every field whose value was
    replaced. A redaction that leaves no record is indistinguishable from a
    field that was never there, so every removal is named here by path.
    """
    import copy

    sanitized = copy.deepcopy(config)
    withheld: list[dict] = []

    # Remove API keys
    agent = sanitized.get("agent", {})
    if "api_key" in agent:
        agent["api_key"] = "<REDACTED>"
        withheld.append({"path": "agent.api_key", "class": "api_key"})

    # Remove channel credentials
    for idx, ch in enumerate(sanitized.get("channels", [])):
        for key in list(ch.keys()):
            lowered = key.lower()
            if "token" in lowered or "secret" in lowered or "key" in lowered:
                ch[key] = "<REDACTED>"
                if "token" in lowered:
                    cls = "token"
                elif "secret" in lowered:
                    cls = "secret"
                else:
                    cls = "key"
                withheld.append({"path": f"channels[{idx}].{key}", "class": cls})

    return sanitized, withheld


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

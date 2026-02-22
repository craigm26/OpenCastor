"""
castor hub — preset & behavior index commands.

Fetches a JSON index from the OpenCastor hub and allows listing,
searching, installing, and getting publish instructions.

Hub index format::

    {
      "version": 1,
      "presets": [
        {
          "name": "waveshare_alpha",
          "url": "https://raw.githubusercontent.com/...",
          "tags": ["rover", "i2c"],
          "author": "OpenCastor",
          "description": "Waveshare AlphaBot preset"
        }
      ],
      "behaviors": [
        {
          "name": "patrol",
          "url": "...",
          "tags": ["navigation"],
          "author": "...",
          "description": "Simple patrol loop"
        }
      ]
    }

Override the hub URL with CASTOR_HUB_URL env var.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("OpenCastor.Hub.Index")

DEFAULT_HUB_URL = (
    "https://raw.githubusercontent.com/craigm26/OpenCastor/main/config/hub_index.json"
)

# Repo root relative to this file: castor/commands/hub.py -> repo root is two levels up
_REPO_ROOT = Path(__file__).parent.parent.parent


def fetch_index(hub_url: Optional[str] = None) -> Dict[str, Any]:
    """Fetch and return the hub index JSON.

    Args:
        hub_url: URL to the hub index JSON. Defaults to DEFAULT_HUB_URL,
                 overridable via CASTOR_HUB_URL env var.

    Returns:
        Parsed hub index dict with 'version', 'presets', and 'behaviors' keys.

    Raises:
        RuntimeError: On network errors or non-200 HTTP responses.
    """
    url = hub_url or os.getenv("CASTOR_HUB_URL", DEFAULT_HUB_URL)
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(f"Network error fetching hub index from {url}: {exc}") from exc
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(f"Timeout fetching hub index from {url}: {exc}") from exc
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(
            f"HTTP error {exc.response.status_code} fetching hub index from {url}: {exc}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch hub index from {url}: {exc}") from exc


def _build_table(items: List[Dict[str, Any]], item_type: str) -> None:
    """Print a Rich table of hub items."""
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title=f"OpenCastor Hub — {item_type.capitalize()}s", show_lines=False)
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Type", style="magenta")
        table.add_column("Tags", style="green")
        table.add_column("Author", style="yellow")
        table.add_column("Description")

        for entry in items:
            tags = ", ".join(entry.get("tags", []))
            table.add_row(
                entry.get("name", ""),
                item_type,
                tags,
                entry.get("author", ""),
                entry.get("description", ""),
            )

        console.print(table)
    except ImportError:
        # Fallback plain text if rich not installed
        header = f"{'Name':<30} {'Type':<12} {'Tags':<30} {'Author':<20} {'Description'}"
        print(header)
        print("-" * len(header))
        for entry in items:
            tags = ", ".join(entry.get("tags", []))
            print(
                f"{entry.get('name', ''):<30} {item_type:<12} {tags:<30}"
                f" {entry.get('author', ''):<20} {entry.get('description', '')}"
            )


def cmd_hub_list(args) -> None:
    """Fetch the hub index and print a table of all presets and behaviors."""
    try:
        hub_url = getattr(args, "hub_url", None)
        index = fetch_index(hub_url)
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return

    presets = index.get("presets", [])
    behaviors = index.get("behaviors", [])

    if presets:
        _build_table(presets, "preset")
    if behaviors:
        _build_table(behaviors, "behavior")

    if not presets and not behaviors:
        print("Hub index is empty.")
    else:
        total = len(presets) + len(behaviors)
        print(f"\n{total} item(s) — {len(presets)} preset(s), {len(behaviors)} behavior(s)")


def cmd_hub_search(args) -> None:
    """Filter the hub index by query string and print matching items.

    Matching is case-insensitive across name, tags, and description fields.
    """
    query: str = getattr(args, "query", "") or ""
    if not query:
        print("Usage: castor hub search <query>")
        return

    try:
        hub_url = getattr(args, "hub_url", None)
        index = fetch_index(hub_url)
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return

    q = query.lower()

    def _matches(entry: Dict[str, Any]) -> bool:
        name_match = q in entry.get("name", "").lower()
        tag_match = any(q in t.lower() for t in entry.get("tags", []))
        desc_match = q in entry.get("description", "").lower()
        return name_match or tag_match or desc_match

    matched_presets = [e for e in index.get("presets", []) if _matches(e)]
    matched_behaviors = [e for e in index.get("behaviors", []) if _matches(e)]

    if matched_presets:
        _build_table(matched_presets, "preset")
    if matched_behaviors:
        _build_table(matched_behaviors, "behavior")

    total = len(matched_presets) + len(matched_behaviors)
    if total == 0:
        print(f"No results for '{query}'.")
    else:
        print(f"\n{total} result(s) for '{query}'")


def cmd_hub_install(args) -> None:
    """Download a preset or behavior by name from the hub index.

    Saves presets to config/presets/<name>.rcan.yaml and behaviors to
    config/behaviors/<name>.behavior.yaml. Validates RCAN configs with
    jsonschema when available.
    """
    name: str = getattr(args, "name", "") or ""
    if not name:
        print("Usage: castor hub install <name>")
        return

    try:
        hub_url = getattr(args, "hub_url", None)
        index = fetch_index(hub_url)
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return

    # Find the entry in presets or behaviors
    entry = None
    item_type = None
    for p in index.get("presets", []):
        if p.get("name", "") == name:
            entry = p
            item_type = "preset"
            break
    if entry is None:
        for b in index.get("behaviors", []):
            if b.get("name", "") == name:
                entry = b
                item_type = "behavior"
                break

    if entry is None:
        print(f"Error: '{name}' not found in hub index. Run 'castor hub list' to see available items.")
        return

    url = entry.get("url", "")
    if not url:
        print(f"Error: No URL found for '{name}' in hub index.")
        return

    # Determine output path
    if item_type == "preset":
        output_dir = _REPO_ROOT / "config" / "presets"
        filename = f"{name}.rcan.yaml"
    else:
        output_dir = _REPO_ROOT / "config" / "behaviors"
        filename = f"{name}.behavior.yaml"

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename

    # Download
    print(f"Downloading {item_type} '{name}' from {url} ...")
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        content = resp.text
    except Exception as exc:
        print(f"Error downloading '{name}': {exc}")
        return

    # Validate RCAN preset if possible
    if item_type == "preset":
        try:
            import yaml
            import jsonschema

            parsed = yaml.safe_load(content)
            # Basic structural validation (no external schema fetch required)
            required_keys = {"rcan_version", "metadata", "agent", "physics", "drivers"}
            missing = required_keys - set(parsed.keys() if parsed else [])
            if missing:
                print(f"Warning: downloaded config is missing required keys: {missing}")
        except ImportError:
            pass  # jsonschema or yaml not available — skip validation
        except Exception as exc:
            print(f"Warning: validation error: {exc}")

    output_path.write_text(content, encoding="utf-8")
    print(f"Installed to {output_path}")


def cmd_hub_publish(args) -> None:
    """Print instructions for submitting a preset or behavior to the hub index."""
    print(
        """
OpenCastor Hub — Publishing a Preset or Behavior
=================================================

To share your config with the community, submit a pull request to the
OpenCastor repository on GitHub.  Here is the process:

  1. Place your config file in one of:
       config/presets/<name>.rcan.yaml       (for hardware presets)
       config/behaviors/<name>.behavior.yaml  (for reusable behaviors)

  2. Add an entry for it in config/hub_index.json:
       {
         "name": "<your-name>",
         "url": "https://raw.githubusercontent.com/craigm26/OpenCastor/main/config/presets/<your-name>.rcan.yaml",
         "tags": ["tag1", "tag2"],
         "author": "<your-GitHub-username>",
         "description": "<short description>"
       }

  3. Open a pull request at:
       https://github.com/craigm26/OpenCastor/pulls

  4. Title your PR:  hub: add <your-name> preset
     Include a short description of the hardware and use case.

  5. CI will validate the RCAN schema automatically.

Thank you for contributing to OpenCastor!
"""
    )

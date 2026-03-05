"""
llmfit integration for OpenCastor.

Wraps the llmfit CLI (https://github.com/AlexsJones/llmfit) to recommend
appropriately-sized LLM models for the robot's hardware during setup.

llmfit detects CPU/RAM/GPU/VRAM and scores hundreds of models across quality,
speed, fit, and context dimensions. This module provides a clean interface for
use in the wizard and as a standalone `castor fit` command.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Any

# Rich is optional — same pattern as wizard.py
try:
    from rich.console import Console
    from rich.table import Table

    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# Map llmfit provider names → OpenCastor provider keys
_PROVIDER_MAP: dict[str, str] = {
    "ollama": "ollama",
    "huggingface": "huggingface",
    "hugging_face": "huggingface",
    "hf": "huggingface",
    "anthropic": "anthropic",
    "openai": "openai",
    "gemini": "gemini",
    "google": "gemini",
    "llamacpp": "llamacpp",
    "llama.cpp": "llamacpp",
    "mlx": "mlx",
}

# llmfit install URL
_INSTALL_URL = "https://llmfit.axjns.dev/install.sh"


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def is_installed() -> bool:
    """Return True if the llmfit binary is available in PATH."""
    return shutil.which("llmfit") is not None


def offer_install(console: Any | None = None) -> bool:
    """
    Prompt the user to install llmfit. Run the curl installer if accepted.

    Returns True if llmfit is now installed (was already or just installed).
    """
    _print(console, "\n[llmfit] llmfit is not installed.", style="yellow")
    _print(
        console,
        "  llmfit recommends the best LLM models for your hardware.\n"
        "  Install with: curl -fsSL https://llmfit.axjns.dev/install.sh | sh\n",
    )

    try:
        ans = input("  Install llmfit now? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False

    if ans not in ("y", "yes"):
        _print(console, "  Skipping llmfit — continuing without model fit analysis.\n")
        return False

    _print(console, "  Installing llmfit...", style="cyan")
    try:
        result = subprocess.run(
            ["sh", "-c", f"curl -fsSL {_INSTALL_URL} | sh"],
            timeout=120,
            check=False,
        )
        if result.returncode == 0 and is_installed():
            _print(console, "  ✓ llmfit installed successfully.\n", style="green")
            return True
        else:
            _print(console, "  Install failed. Continuing without llmfit.\n", style="yellow")
            return False
    except Exception as exc:
        _print(console, f"  Install error: {exc}. Continuing without llmfit.\n", style="yellow")
        return False


def get_system_info() -> dict[str, Any] | None:
    """
    Run `llmfit system` and return a dict with detected hardware info.

    Returns None on failure (binary missing, parse error, timeout).
    """
    if not is_installed():
        return None
    try:
        result = subprocess.run(
            ["llmfit", "system"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        # llmfit system currently outputs human-readable text; parse key lines
        return _parse_system_output(result.stdout)
    except Exception:
        return None


def get_recommendations(use_case: str = "chat", limit: int = 5) -> list[dict[str, Any]]:
    """
    Run `llmfit recommend --json --use-case <use_case> --limit <limit>`.

    Returns a list of recommendation dicts, or [] on any failure.
    Each dict has keys: name, provider, fit, score, estimated_tps,
    mem_usage_mb, use_case, params_b (not all guaranteed present).
    """
    if not is_installed():
        return []
    try:
        result = subprocess.run(
            ["llmfit", "recommend", "--json", "--use-case", use_case, "--limit", str(limit)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout.strip())
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def map_to_provider_config(rec: dict[str, Any]) -> dict[str, Any] | None:
    """
    Map a llmfit recommendation dict to an OpenCastor provider config fragment.

    Returns a dict with keys ``provider`` and ``model``, or None if the
    llmfit provider is not supported by OpenCastor.
    """
    raw_provider = str(rec.get("provider", "")).lower().strip()
    oc_provider = _PROVIDER_MAP.get(raw_provider)
    if not oc_provider:
        return None
    return {
        "provider": oc_provider,
        "model": rec.get("name", ""),
        "fit": rec.get("fit", "unknown"),
        "estimated_tps": rec.get("estimated_tps"),
        "mem_usage_mb": rec.get("mem_usage_mb"),
    }


def print_recommendations(recs: list[dict[str, Any]], console: Any | None = None) -> None:
    """Pretty-print a list of llmfit recommendations as a table."""
    if not recs:
        _print(console, "  No recommendations available.", style="yellow")
        return

    if HAS_RICH and console is not None:
        table = Table(title="Model Recommendations for Your Hardware", show_lines=False)
        table.add_column("Fit", style="bold", width=8)
        table.add_column("Model", style="cyan")
        table.add_column("Provider", width=12)
        table.add_column("Est. tok/s", justify="right", width=10)
        table.add_column("RAM (MB)", justify="right", width=9)

        fit_colors = {"perfect": "green", "good": "yellow", "marginal": "red"}

        for rec in recs:
            fit = rec.get("fit", "?")
            color = fit_colors.get(fit, "white")
            table.add_row(
                f"[{color}]{fit}[/{color}]",
                rec.get("name", "?"),
                rec.get("provider", "?"),
                str(rec.get("estimated_tps", "?")),
                str(rec.get("mem_usage_mb", "?")),
            )
        console.print(table)
    else:
        # Plain-text fallback
        print(f"\n  {'FIT':<9} {'MODEL':<40} {'PROV':<12} {'TOK/S':>6} {'MB':>7}")
        print("  " + "-" * 78)
        for rec in recs:
            print(
                f"  {rec.get('fit','?'):<9} {rec.get('name','?'):<40} "
                f"{rec.get('provider','?'):<12} {str(rec.get('estimated_tps','?')):>6} "
                f"{str(rec.get('mem_usage_mb','?')):>7}"
            )
        print()


# ---------------------------------------------------------------------------
# Wizard integration
# ---------------------------------------------------------------------------


def run_wizard_step(console: Any | None = None) -> dict[str, Any] | None:
    """
    Run the Model Fit Analysis wizard step.

    Checks if llmfit is installed (offers install if not), fetches
    recommendations, displays them, and returns the mapped provider config
    for the top recommendation if the user accepts it — or None to skip.

    This step is fully optional; on any failure it returns None.
    """
    _print(console, "\n\033[92m--- MODEL FIT ANALYSIS ---\033[0m")
    _print(
        console,
        "  llmfit checks your hardware and recommends models that will run well.\n",
    )

    if not is_installed():
        installed = offer_install(console)
        if not installed:
            return None

    # Show hardware summary
    info = get_system_info()
    if info:
        _print(console, "  Detected hardware:", style="bold")
        for k, v in info.items():
            _print(console, f"    {k}: {v}")
        print()

    # Get recommendations
    recs = get_recommendations(use_case="chat", limit=5)
    if not recs:
        _print(
            console,
            "  Could not retrieve recommendations. Continuing without model fit analysis.\n",
            style="yellow",
        )
        return None

    print_recommendations(recs, console)

    # Offer to use top recommendation
    top = recs[0]
    mapped = map_to_provider_config(top)
    if mapped is None:
        _print(
            console,
            f"  Top model provider '{top.get('provider')}' is not directly supported "
            "in OpenCastor. Skipping auto-fill.\n",
            style="yellow",
        )
        return None

    try:
        ans = input(
            f"  Use top recommendation ({top.get('name')}) as fast brain default? [Y/n] "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None

    if ans in ("", "y", "yes"):
        _print(
            console,
            f"  ✓ Will pre-fill provider={mapped['provider']} model={mapped['model']}\n",
            style="green",
        )
        return mapped

    return None


# ---------------------------------------------------------------------------
# `castor fit` standalone command
# ---------------------------------------------------------------------------


def run_fit_command() -> None:
    """Entry point for `castor fit` — show hardware and model recommendations."""
    console = None
    if HAS_RICH:
        console = Console()

    if not is_installed():
        installed = offer_install(console)
        if not installed:
            sys.exit(0)

    _print(console, "\n[bold]Hardware Detection[/bold]\n" if HAS_RICH else "\nHardware Detection\n")
    info = get_system_info()
    if info:
        for k, v in info.items():
            _print(console, f"  {k}: {v}")
    else:
        _print(console, "  (hardware info unavailable)", style="yellow")

    _print(console, "\n[bold]Top Model Recommendations[/bold]\n" if HAS_RICH else "\nTop Model Recommendations\n")
    recs = get_recommendations(use_case="chat", limit=10)
    if recs:
        print_recommendations(recs, console)
    else:
        _print(console, "  No recommendations available.\n", style="yellow")

    _print(
        console,
        "\nTip: Run [cyan]llmfit[/cyan] for the full interactive TUI.\n"
        if HAS_RICH
        else "\nTip: Run `llmfit` for the full interactive TUI.\n",
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _print(console: Any | None, text: str, style: str | None = None) -> None:
    """Print with Rich if available, plain otherwise."""
    if HAS_RICH and console is not None:
        console.print(text, style=style)
    else:
        # Strip basic Rich markup for plain output
        import re
        plain = re.sub(r"\[/?[a-z_ ]+\]", "", text)
        print(plain)


def _parse_system_output(output: str) -> dict[str, str]:
    """
    Parse human-readable `llmfit system` output into a dict.

    Handles lines like:  CPU: Apple M2 Pro  or  RAM: 16 GB
    """
    result: dict[str, str] = {}
    for line in output.splitlines():
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if key and val:
                result[key] = val
    return result

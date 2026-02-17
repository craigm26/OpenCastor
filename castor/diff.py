"""
OpenCastor Diff -- compare two RCAN config files side-by-side.

Highlights differences in a structured, readable way rather than
a raw text diff. Useful after ``castor migrate`` or ``castor configure``.

Usage:
    castor diff robot.rcan.yaml robot.rcan.yaml.bak
    castor diff --config robot.rcan.yaml --baseline config/presets/rpi_rc_car.rcan.yaml
"""

import os

import yaml


def diff_configs(path_a: str, path_b: str) -> list:
    """Compare two RCAN config files.

    Returns a list of ``(key_path, value_a, value_b)`` tuples for all
    differences found.
    """
    with open(path_a) as f:
        config_a = yaml.safe_load(f) or {}
    with open(path_b) as f:
        config_b = yaml.safe_load(f) or {}

    diffs = []
    _compare(config_a, config_b, "", diffs)

    # Check for keys in B not in A
    _find_removed(config_a, config_b, "", diffs)

    return diffs


def _compare(a, b, prefix: str, diffs: list):
    """Recursively compare two dicts/values."""
    if isinstance(a, dict) and isinstance(b, dict):
        all_keys = set(list(a.keys()) + list(b.keys()))
        for key in sorted(all_keys):
            path = f"{prefix}.{key}" if prefix else key
            if key not in a:
                diffs.append((path, "<missing>", _fmt_val(b[key])))
            elif key not in b:
                diffs.append((path, _fmt_val(a[key]), "<missing>"))
            else:
                _compare(a[key], b[key], path, diffs)
    elif isinstance(a, list) and isinstance(b, list):
        max_len = max(len(a), len(b))
        for i in range(max_len):
            path = f"{prefix}[{i}]"
            if i >= len(a):
                diffs.append((path, "<missing>", _fmt_val(b[i])))
            elif i >= len(b):
                diffs.append((path, _fmt_val(a[i]), "<missing>"))
            else:
                _compare(a[i], b[i], path, diffs)
    else:
        if a != b:
            diffs.append((prefix, _fmt_val(a), _fmt_val(b)))


def _find_removed(a, b, prefix: str, diffs: list):
    """Find keys in b that are not in a (already handled by _compare)."""
    pass  # _compare handles all keys from both sides


def _fmt_val(val) -> str:
    """Format a value for display."""
    if isinstance(val, dict):
        return f"{{...}} ({len(val)} keys)"
    elif isinstance(val, list):
        return f"[...] ({len(val)} items)"
    elif isinstance(val, str) and len(val) > 60:
        return val[:57] + "..."
    return str(val)


def print_diff(diffs: list, path_a: str, path_b: str):
    """Print config differences."""
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False
        console = None

    label_a = os.path.basename(path_a)
    label_b = os.path.basename(path_b)

    if not diffs:
        msg = "  Configs are identical."
        if has_rich:
            console.print(f"\n[green]{msg}[/]\n")
        else:
            print(f"\n{msg}\n")
        return

    if has_rich:
        console.print("\n[bold cyan]  Config Diff[/]")
        console.print(f"  [dim]{path_a} vs {path_b}[/]\n")

        table = Table(show_header=True, box=None, padding=(0, 1))
        table.add_column("Key", style="bold")
        table.add_column(label_a, style="red")
        table.add_column(label_b, style="green")

        for key_path, val_a, val_b in diffs:
            table.add_row(key_path, str(val_a), str(val_b))

        console.print(table)
        console.print(f"\n  [dim]{len(diffs)} difference(s)[/]\n")
    else:
        print(f"\n  Config Diff: {path_a} vs {path_b}\n")
        print(f"  {'Key':<40s} {label_a:<25s} {label_b:<25s}")
        print(f"  {'-'*40} {'-'*25} {'-'*25}")
        for key_path, val_a, val_b in diffs:
            print(f"  {key_path:<40s} {str(val_a):<25s} {str(val_b):<25s}")
        print(f"\n  {len(diffs)} difference(s)\n")

"""
castor/commands/update.py — Self-update and swarm-update commands.

``castor update``         — update this OpenCastor installation
``castor swarm update``   — SSH into each swarm node and update there

Both commands support ``--dry-run`` (print commands without executing).
``castor update`` also accepts ``--version X.Y.Z`` to pin a release.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Update")


# ---------------------------------------------------------------------------
# Install-type detection
# ---------------------------------------------------------------------------


def _repo_dir() -> Optional[Path]:
    """Return the git repo root if OpenCastor was installed as an editable checkout.

    Walks up from this file looking for a ``.git`` directory.
    """
    candidate = Path(__file__).resolve()
    for parent in [candidate, *candidate.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _is_editable_install() -> bool:
    """True when the package is installed with ``pip install -e .``."""
    return _repo_dir() is not None


# ---------------------------------------------------------------------------
# cmd_update
# ---------------------------------------------------------------------------


def cmd_update(args) -> None:
    """Update OpenCastor to the latest (or a specific) version.

    Behaviour
    ---------
    * Editable (git) install:
        1. ``git -C <repo> pull --ff-only``
        2. ``pip install -e . -q``
    * Regular pip install:
        ``pip install --upgrade opencastor``

    Flags
    -----
    --dry-run       Print the commands that would be executed without running them.
    --version X.Y.Z Pin to a specific release tag / pip version specifier.
    """
    dry_run: bool = getattr(args, "dry_run", False)
    pin_version: Optional[str] = getattr(args, "version", None)

    # Print current version
    try:
        from castor import __version__ as before_ver
    except Exception:
        before_ver = "unknown"

    print(f"  OpenCastor version before: {before_ver}")

    if _is_editable_install():
        repo = _repo_dir()
        _update_git(repo, pin_version=pin_version, dry_run=dry_run)
    else:
        _update_pip(pin_version=pin_version, dry_run=dry_run)

    if not dry_run:
        # Re-import to pick up new version (best-effort in same process)
        try:
            import importlib

            import castor as _castor_mod

            importlib.reload(_castor_mod)
            after_ver = _castor_mod.__version__
        except Exception:
            after_ver = "unknown"
        print(f"  OpenCastor version after:  {after_ver}")
    else:
        print("  (dry-run — no changes made)")


def _update_git(
    repo: Path,
    pin_version: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    """Update a git-based editable install."""
    _pip_exe = sys.executable  # use current interpreter's pip via -m

    if pin_version:
        # Checkout a specific git tag
        cmd_checkout = ["git", "-C", str(repo), "checkout", f"v{pin_version}"]
        _run_or_print(cmd_checkout, dry_run=dry_run, label=f"git checkout v{pin_version}")
    else:
        cmd_pull = ["git", "-C", str(repo), "pull", "--ff-only"]
        _run_or_print(cmd_pull, dry_run=dry_run, label="git pull --ff-only")

    cmd_pip = [sys.executable, "-m", "pip", "install", "-e", str(repo), "-q"]
    _run_or_print(cmd_pip, dry_run=dry_run, label="pip install -e .")


def _update_pip(
    pin_version: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    """Update a regular pip install."""
    spec = f"opencastor=={pin_version}" if pin_version else "opencastor"
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", spec]
    _run_or_print(cmd, dry_run=dry_run, label=f"pip install --upgrade {spec}")


# ---------------------------------------------------------------------------
# cmd_swarm_update
# ---------------------------------------------------------------------------


def cmd_swarm_update(args) -> None:
    """SSH into each swarm node and run the OpenCastor update sequence.

    Reads node definitions from ``config/swarm.yaml``.  Each node must have
    at minimum a ``host`` or ``ip`` field.  Optional fields:

    * ``port``     — SSH port (default: 22)
    * ``user``     — SSH username (default: ``pi``)
    * ``password`` — used with ``sshpass`` if available
    * ``key_file`` — path to a private key (passed to ``-i``)

    The update sequence on each node::

        cd ~/OpenCastor && \\
        git pull --ff-only && \\
        source ~/opencastor-env/bin/activate && \\
        pip install -e . -q && \\
        systemctl --user restart opencastor

    Flags
    -----
    --dry-run   Print the SSH commands without executing them.
    """
    dry_run: bool = getattr(args, "dry_run", False)
    swarm_cfg_path: Optional[str] = getattr(args, "swarm_config", None)

    nodes = _load_swarm_nodes(swarm_cfg_path)
    if not nodes:
        print("  No nodes found in swarm.yaml — nothing to update.")
        return

    has_sshpass = shutil.which("sshpass") is not None
    if not has_sshpass:
        print(
            "  Note: sshpass not found. Nodes with a 'password' field will require\n"
            "  manual password entry (or set up SSH key auth)."
        )

    results: List[Dict[str, Any]] = []
    for node in nodes:
        result = _update_node(node, dry_run=dry_run, has_sshpass=has_sshpass)
        results.append(result)

    # Summary
    print()
    print("  Swarm update summary:")
    for r in results:
        status = "OK" if r.get("success") else "FAILED"
        if dry_run:
            status = "DRY-RUN"
        print(f"    [{status}] {r['name']}")
        if not r.get("success") and not dry_run:
            err = r.get("error", "")
            if err:
                print(f"           {err[:120]}")


def _update_node(
    node: Dict[str, Any],
    dry_run: bool,
    has_sshpass: bool,
) -> Dict[str, Any]:
    """Run the update sequence on a single swarm node."""
    name = node.get("name", node.get("ip", node.get("host", "?")))
    host = node.get("ip") or node.get("host", "localhost")
    port = int(node.get("port", 22))
    user = node.get("user", "pi")
    password = node.get("password", "")
    key_file = node.get("key_file", "")

    remote_cmd = (
        "cd ~/OpenCastor && "
        "git pull --ff-only && "
        "source ~/opencastor-env/bin/activate && "
        "pip install -e . -q && "
        "systemctl --user restart opencastor"
    )

    # Build SSH command
    ssh_cmd: List[str] = []

    if password and has_sshpass:
        ssh_cmd += ["sshpass", "-p", password]
    elif password and not has_sshpass:
        # Print manual instructions instead
        print(
            f"\n  Node '{name}' has a password configured but sshpass is not installed.\n"
            f"  Run manually:\n"
            f"    ssh {user}@{host} -p {port} \"{remote_cmd}\"\n"
        )
        return {"name": name, "success": False, "error": "sshpass not available"}

    ssh_cmd += ["ssh"]
    ssh_cmd += ["-p", str(port)]
    if key_file:
        ssh_cmd += ["-i", key_file]
    # Disable host key checking for automated runs (can be overridden)
    ssh_cmd += ["-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]
    ssh_cmd += [f"{user}@{host}", remote_cmd]

    if dry_run:
        print(f"  [DRY-RUN] Would run on '{name}': {' '.join(ssh_cmd)}")
        return {"name": name, "success": True, "dry_run": True}

    print(f"  Updating node '{name}' ({user}@{host}:{port}) …")
    try:
        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            print(f"    Node '{name}' updated successfully.")
            return {"name": name, "success": True}
        else:
            err = (result.stderr or result.stdout or "").strip()[:200]
            print(f"    Node '{name}' update FAILED: {err}")
            return {"name": name, "success": False, "error": err}
    except subprocess.TimeoutExpired:
        err = "SSH command timed out after 120s"
        print(f"    Node '{name}' update FAILED: {err}")
        return {"name": name, "success": False, "error": err}
    except Exception as exc:
        err = str(exc)
        print(f"    Node '{name}' update FAILED: {err}")
        return {"name": name, "success": False, "error": err}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_or_print(cmd: List[str], dry_run: bool, label: str) -> None:
    """Print the command (always), then execute it unless dry_run is True."""
    print(f"  Running: {' '.join(cmd)}")
    if dry_run:
        return
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        logger.warning("Command failed with exit code %d: %s", result.returncode, label)


def _load_swarm_nodes(config_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load the list of nodes from swarm.yaml (delegates to swarm module)."""
    try:
        from castor.commands.swarm import load_swarm_config

        return load_swarm_config(config_path)
    except Exception as exc:
        logger.error("Could not load swarm config: %s", exc)
        return []

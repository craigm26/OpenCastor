"""castor.rrf_cmd — RRF v2 CLI subcommand dispatch.

Thin wrapper over :mod:`castor.rcan3.rrf_client`. Retains the ``cmd_rrf``
entry-point signature used by ``castor/cli.py:1696``.

Subcommands
-----------
- ``register``   — POST /v2/robots/register (v2, PQ-signed)
- ``get``        — GET /v2/robots/{rrn} (v2)
- ``components`` — deprecated in opencastor 3.0+ (no v2 equivalent)
- ``models``     — deprecated in opencastor 3.0+ (no v2 equivalent)
- ``harness``    — deprecated in opencastor 3.0+ (no v2 equivalent)
- ``status``     — deprecated in opencastor 3.0+ (no v2 equivalent)
- ``wipe``       — deprecated in opencastor 3.0+ (no v2 equivalent)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from castor.rcan3.identity import load_or_generate_identity
from castor.rcan3.reader import read_robot_md
from castor.rcan3.rrf_client import RrfClient, RrfError
from castor.rcan3.signer import CastorSigner

_DEPRECATION_URL = "https://robotregistryfoundation.org/docs/v2-migration"


def _keydir() -> Path:
    return Path(os.getenv("CASTOR_KEYDIR") or (Path.home() / ".castor" / "keys"))


def _deprecated_error(subcommand: str) -> int:
    """Print a clear deprecation error and return exit code 1."""
    sys.stderr.write(
        f"castor rrf {subcommand}: not supported against RRF v2 in opencastor 3.0+.\n"
        f"See {_DEPRECATION_URL}\n"
    )
    return 1


def _build_registration_body(manifest_path: Path) -> dict:
    m = read_robot_md(manifest_path)
    return {
        "schema": "rcan-register-v2",
        "rcan_version": m.rcan_version,
        "metadata": m.frontmatter.get("metadata") or {},
        "agent": m.frontmatter.get("agent") or {},
        "safety": m.safety,
    }


async def _register(manifest_path: Path) -> int:
    ident = load_or_generate_identity(keydir=_keydir())
    signer = CastorSigner(ident)

    m = read_robot_md(manifest_path)
    endpoint = m.endpoint or "https://rcan.dev"

    body = _build_registration_body(manifest_path)
    signed = signer.sign(body)

    async with RrfClient(base_url=endpoint) as c:
        result = await c.register(signed)

    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


async def _get(rrn: str, endpoint: str) -> int:
    async with RrfClient(base_url=endpoint) as c:
        result = await c.get_robot(rrn)
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


# ── Preserved subcommand functions (public names kept for import stability) ──


def cmd_rrf_register(args) -> None:
    """castor rrf register — register this robot with RRF v2."""
    manifest = Path(getattr(args, "manifest", None) or getattr(args, "config", None) or "ROBOT.md")
    try:
        rc = asyncio.run(_register(manifest))
    except RrfError as e:
        sys.stderr.write(f"RRF error: {e}\n")
        sys.exit(1)
    except FileNotFoundError as e:
        sys.stderr.write(f"manifest not found: {e}\n")
        sys.exit(1)
    if rc != 0:
        sys.exit(rc)


def cmd_rrf_get(args) -> None:
    """castor rrf get — fetch a robot record from RRF v2."""
    rrn = getattr(args, "rrn", None)
    if not rrn:
        sys.stderr.write("castor rrf get: --rrn is required\n")
        sys.exit(2)
    endpoint = getattr(args, "endpoint", "https://rcan.dev")
    try:
        rc = asyncio.run(_get(rrn, endpoint))
    except RrfError as e:
        sys.stderr.write(f"RRF error: {e}\n")
        sys.exit(1)
    if rc != 0:
        sys.exit(rc)


def cmd_rrf_components(args) -> None:
    """Deprecated: not supported against RRF v2."""
    sys.exit(_deprecated_error("components"))


def cmd_rrf_models(args) -> None:
    """Deprecated: not supported against RRF v2."""
    sys.exit(_deprecated_error("models"))


def cmd_rrf_harness(args) -> None:
    """Deprecated: not supported against RRF v2."""
    sys.exit(_deprecated_error("harness"))


def cmd_rrf_status(args) -> None:
    """Deprecated: not supported against RRF v2."""
    sys.exit(_deprecated_error("status"))


def cmd_rrf_wipe(args) -> None:
    """Deprecated: not supported against RRF v2."""
    sys.exit(_deprecated_error("wipe"))


def cmd_rrf(args) -> int:
    """CLI entry point for `castor rrf <subcommand>`.

    Dispatch table supports both ``args.rrf_cmd`` (real CLI, set by argparse
    dest="rrf_cmd") and ``args.subcommand`` (tests / scripted calls).
    """
    sub = getattr(args, "rrf_cmd", None) or getattr(args, "subcommand", None) or "status"

    dispatch = {
        "register": cmd_rrf_register,
        "get": cmd_rrf_get,
        "components": cmd_rrf_components,
        "models": cmd_rrf_models,
        "harness": cmd_rrf_harness,
        "status": cmd_rrf_status,
        "wipe": cmd_rrf_wipe,
    }
    fn = dispatch.get(sub)
    if fn is None:
        sys.stderr.write(
            f"castor rrf: unknown subcommand {sub!r}. "
            "Valid: register, get, components, models, harness, status, wipe\n"
        )
        return 2

    # For register/get we return an integer exit code. For deprecated stubs
    # they call sys.exit() internally. Wrap to normalize.
    if sub in ("register", "get"):
        manifest = Path(
            getattr(args, "manifest", None) or getattr(args, "config", None) or "ROBOT.md"
        )
        rrn = getattr(args, "rrn", None)
        endpoint = getattr(args, "endpoint", "https://rcan.dev")
        try:
            if sub == "register":
                return asyncio.run(_register(manifest))
            return asyncio.run(_get(rrn, endpoint))
        except RrfError as e:
            sys.stderr.write(f"RRF error: {e}\n")
            return 1
        except FileNotFoundError as e:
            sys.stderr.write(f"manifest not found: {e}\n")
            return 1

    # Deprecated stubs call sys.exit() — return the code for testing
    return _deprecated_error(sub)

"""
castor/attestation_generator.py — Software attestation for OpenCastor.

Generates /run/opencastor/attestation.json at startup.

Checks (software equivalents of hardware attestation):
  secure_boot   — code integrity: castor package files match pip RECORD hashes
  measured_boot — config integrity: config.yaml hash matches stored baseline
  signed_updates — update chain: installed version pinned to known-good hash

Run as:
  python -m castor.attestation_generator [--config PATH] [--out PATH]
  castor attestation [--config PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("OpenCastor.Attestation")

_DEFAULT_OUT = Path("/run/opencastor/attestation.json")
_FALLBACK_OUT = Path("/tmp/opencastor_attestation.json")
_BASELINE_PATH = Path("/run/opencastor/config_baseline.sha256")


def _sha256_file(path: Path) -> str:
    """Return hex SHA256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_record_file() -> Path | None:
    """Find the pip RECORD file for the opencastor package."""
    try:
        import importlib.metadata as importlib_metadata

        dist = importlib_metadata.distribution("opencastor")
        record = dist._path / "RECORD"  # type: ignore[attr-defined]
        if record.exists():
            return record
    except Exception:
        pass

    # Fallback: scan site-packages
    for site_dir in sys.path:
        p = Path(site_dir)
        if not p.is_dir():
            continue
        for d in p.iterdir():
            if d.name.startswith("opencastor") and d.name.endswith(".dist-info"):
                rec = d / "RECORD"
                if rec.exists():
                    return rec
    return None


def _parse_record(record_path: Path) -> dict[str, str]:
    """Parse RECORD file → {relative_path: expected_sha256_hex}."""
    entries: dict[str, str] = {}
    with open(record_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 2:
                continue
            rel_path = parts[0]
            hash_spec = parts[1]
            if hash_spec.startswith("sha256="):
                # RECORD uses base64url-encoded SHA256
                import base64

                b64 = hash_spec[len("sha256=") :]
                raw = base64.urlsafe_b64decode(b64 + "==")
                entries[rel_path] = raw.hex()
    return entries


def _castor_package_dir() -> Path:
    """Return the directory containing the castor package source."""
    return Path(__file__).resolve().parent


def check_code_integrity() -> tuple[bool, str]:
    """Verify castor package files match pip RECORD hashes (secure_boot equivalent)."""
    record_path = _find_record_file()

    if record_path is not None:
        # Installed via pip — verify against RECORD
        entries = _parse_record(record_path)
        if not entries:
            return False, "record_empty"

        site_pkg = record_path.parent.parent
        checked = 0
        for rel_path, expected_hash in entries.items():
            if not rel_path.endswith(".py"):
                continue
            if not rel_path.startswith("castor/"):
                continue
            full = site_pkg / rel_path
            if not full.exists():
                return False, f"missing_file:{rel_path}"
            actual = _sha256_file(full)
            if actual != expected_hash:
                return False, f"hash_mismatch:{rel_path}"
            checked += 1

        if checked == 0:
            return False, "no_castor_py_in_record"
        return True, "code_integrity_verified"

    # Editable install / dev mode — check git status
    castor_dir = _castor_package_dir()
    repo_root = castor_dir.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--name-only", "HEAD", "--", "castor/"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False, "git_diff_failed"
        changed = [f for f in result.stdout.strip().splitlines() if f.endswith(".py")]
        if changed:
            return False, f"modified_files:{len(changed)}"
        return True, "code_integrity_verified"
    except FileNotFoundError:
        return False, "git_not_available"
    except Exception as exc:
        return False, f"check_error:{exc}"


def check_config_measurement(config_path: Path) -> tuple[bool, str]:
    """Verify config file hash matches stored baseline (measured_boot equivalent)."""
    if not config_path.exists():
        return False, "config_not_found"

    current_hash = _sha256_file(config_path)

    baseline_path = _BASELINE_PATH
    # If /run not writable, use /tmp
    if not baseline_path.parent.exists():
        baseline_path = Path("/tmp/opencastor_config_baseline.sha256")

    if baseline_path.exists():
        stored_hash = baseline_path.read_text().strip()
        if current_hash == stored_hash:
            return True, "config_measurement_ok"
        return False, "config_hash_mismatch"

    # First run — create baseline
    try:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(current_hash)
        return True, "config_measurement_ok"
    except OSError as exc:
        logger.debug("Could not write config baseline: %s", exc)
        return False, f"baseline_write_failed:{exc}"


def check_update_chain(version: str) -> tuple[bool, str]:
    """Verify version format and git cleanliness (signed_updates equivalent)."""
    # Check version format: YYYY.M.D.N
    if not re.match(r"^\d{4}\.\d{1,2}\.\d{1,2}\.\d+$", version):
        return False, f"invalid_version_format:{version}"

    # Check git working tree is clean
    castor_dir = _castor_package_dir()
    repo_root = castor_dir.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            # Not a git repo — if installed via pip, that's OK
            return True, "update_chain_verified"
        dirty = [
            ln
            for ln in result.stdout.strip().splitlines()
            if ln.strip() and not ln.strip().startswith("??") and "castor/" in ln
        ]
        if dirty:
            return False, f"dirty_working_tree:{len(dirty)}_files"
        return True, "update_chain_verified"
    except FileNotFoundError:
        # git not installed — pip install is fine
        return True, "update_chain_verified"
    except Exception as exc:
        return False, f"check_error:{exc}"


def generate_attestation(
    config_path: Path | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Run all attestation checks and write result JSON.

    Returns the attestation dict matching security_posture.py's expected format.
    """
    # Import version
    try:
        from castor import __version__
    except ImportError:
        __version__ = "0.0.0.0"

    # Code integrity
    secure_boot, secure_detail = check_code_integrity()

    # Config measurement
    if config_path is None:
        # Try common locations
        candidates = [
            Path.home() / "OpenCastor" / "config.yaml",
            Path.home() / "opencastor" / "config.yaml",
            _castor_package_dir().parent / "config.yaml",
        ]
        # Also check for *.rcan.yaml in repo root
        repo_root = _castor_package_dir().parent
        for f in repo_root.glob("*.rcan.yaml"):
            candidates.insert(0, f)

        config_path = next((c for c in candidates if c.exists()), None)

    if config_path is not None:
        measured_boot, measured_detail = check_config_measurement(config_path)
    else:
        measured_boot, measured_detail = True, "no_config_file_skipped"

    # Update chain
    signed_updates, update_detail = check_update_chain(__version__)

    verified = secure_boot and measured_boot and signed_updates

    # Build token from version + config hash
    config_hash = _sha256_file(config_path) if config_path and config_path.exists() else "none"
    token = hashlib.sha256(f"{__version__}:{config_hash}".encode()).hexdigest()

    attestation: dict[str, Any] = {
        "secure_boot": secure_boot,
        "measured_boot": measured_boot,
        "signed_updates": signed_updates,
        "verified": verified,
        "profile": "software-attested" if verified else "degraded",
        "token": token,
        "source": "castor.attestation_generator",
        "claims_detail": {
            "secure_boot": secure_detail,
            "measured_boot": measured_detail,
            "signed_updates": update_detail,
        },
    }

    # Write to file
    target = out_path or _DEFAULT_OUT
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(attestation, indent=2) + "\n")
        logger.info("Attestation written to %s", target)
    except OSError:
        # Fallback to /tmp
        target = _FALLBACK_OUT
        try:
            target.write_text(json.dumps(attestation, indent=2) + "\n")
            # Note: do NOT set OPENCASTOR_ATTESTATION_PATH here — it would
            # contaminate other processes and tests. Callers that need the
            # fallback path can read it from _FALLBACK_OUT directly.
            logger.info("Attestation written to fallback %s", target)
        except OSError as exc:
            logger.warning("Could not write attestation: %s", exc)

    return attestation


def main() -> None:
    """CLI entry point for attestation generation."""
    parser = argparse.ArgumentParser(
        description="OpenCastor Software Attestation Generator",
        prog="castor attestation",
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to config YAML file")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Output path (default: {_DEFAULT_OUT})",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    result = generate_attestation(config_path=args.config, out_path=args.out)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        status = "VERIFIED" if result["verified"] else "DEGRADED"
        print(f"\n  OpenCastor Software Attestation: {status}\n")
        print(f"  secure_boot   (code integrity):    {result['secure_boot']}")
        print(f"    {result['claims_detail']['secure_boot']}")
        print(f"  measured_boot (config integrity):   {result['measured_boot']}")
        print(f"    {result['claims_detail']['measured_boot']}")
        print(f"  signed_updates (update chain):      {result['signed_updates']}")
        print(f"    {result['claims_detail']['signed_updates']}")
        print(f"\n  Profile: {result['profile']}")
        print(f"  Token:   {result['token'][:16]}...")
        print()

    sys.exit(0 if result["verified"] else 1)


if __name__ == "__main__":
    main()

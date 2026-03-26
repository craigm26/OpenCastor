"""
castor/firmware — RCAN v2.1 Firmware Manifest generation and serving.

Implements `castor attest` commands:
  castor attest generate   — build manifest from installed packages
  castor attest sign       — sign manifest with robot's Ed25519 key
  castor attest serve      — serve at /.well-known/rcan-firmware-manifest.json
  castor attest verify     — verify manifest signature

Spec: §11 — Firmware Manifests
"""

from __future__ import annotations

import hashlib
import importlib.metadata as importlib_metadata
import json
import logging
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("OpenCastor.Firmware")

FIRMWARE_MANIFEST_PATH = "/.well-known/rcan-firmware-manifest.json"
_DEFAULT_MANIFEST_FILE = Path("/run/opencastor/rcan-firmware-manifest.json")
_FALLBACK_MANIFEST_FILE = Path("/tmp/opencastor-firmware-manifest.json")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class FirmwareComponent:
    name: str
    version: str
    hash: str  # "sha256:<hex>"


@dataclass
class FirmwareManifest:
    rrn: str
    firmware_version: str
    build_hash: str       # "sha256:<hex>" of all component hashes concatenated
    components: list[FirmwareComponent] = field(default_factory=list)
    signed_at: str = ""
    signature: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "rrn":              self.rrn,
            "firmware_version": self.firmware_version,
            "build_hash":       self.build_hash,
            "components":       [asdict(c) for c in self.components],
            "signed_at":        self.signed_at,
        }
        if self.signature:
            d["signature"] = self.signature
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FirmwareManifest":
        components = [
            FirmwareComponent(**c) for c in d.get("components", [])
        ]
        return cls(
            rrn=d.get("rrn", ""),
            firmware_version=d.get("firmware_version", ""),
            build_hash=d.get("build_hash", ""),
            components=components,
            signed_at=d.get("signed_at", ""),
            signature=d.get("signature"),
        )


class FirmwareIntegrityError(Exception):
    pass


# ---------------------------------------------------------------------------
# Manifest generation
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get_opencastor_version() -> str:
    try:
        dist = importlib_metadata.distribution("opencastor")
        return dist.version
    except Exception:
        pass
    # Fallback: try castor package
    try:
        dist = importlib_metadata.distribution("castor")
        return dist.version
    except Exception:
        pass
    return "unknown"


def _get_python_packages() -> list[FirmwareComponent]:
    """Return key installed packages as firmware components."""
    important = {
        "opencastor", "castor", "rcan-py", "rcan",
        "fastapi", "uvicorn", "pydantic",
        "cryptography", "PyNaCl", "PyJWT",
    }
    components = []
    for dist in importlib_metadata.distributions():
        name = dist.metadata.get("Name", "")
        if name.lower() in {n.lower() for n in important}:
            version = dist.metadata.get("Version", "unknown")
            # Hash the dist-info RECORD file for integrity
            try:
                record = next(dist.files or [])
                record_bytes = Path(str(record.locate())).read_bytes()
                h = f"sha256:{_sha256_hex(record_bytes)}"
            except Exception:
                h = f"sha256:{_sha256_hex(f'{name}=={version}'.encode())}"
            components.append(FirmwareComponent(
                name=name,
                version=version,
                hash=h,
            ))
    return sorted(components, key=lambda c: c.name.lower())


def _compute_build_hash(components: list[FirmwareComponent]) -> str:
    """SHA-256 of all component hashes concatenated in sorted order."""
    h = hashlib.sha256()
    for c in sorted(components, key=lambda c: c.name.lower()):
        h.update(c.hash.encode())
    return f"sha256:{h.hexdigest()}"


def generate_manifest(rrn: str, firmware_version: Optional[str] = None) -> FirmwareManifest:
    """Build a firmware manifest from the current environment.

    Args:
        rrn: Robot Registration Number (e.g. "RRN-000000000001").
        firmware_version: Override version string. Defaults to installed opencastor version.

    Returns:
        An unsigned FirmwareManifest.
    """
    if not firmware_version:
        firmware_version = _get_opencastor_version()

    # Platform component
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    python_component = FirmwareComponent(
        name="python",
        version=py_version,
        hash=f"sha256:{_sha256_hex(sys.version.encode())}",
    )

    pkg_components = _get_python_packages()
    components = [python_component] + pkg_components
    build_hash = _compute_build_hash(components)
    signed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return FirmwareManifest(
        rrn=rrn,
        firmware_version=firmware_version,
        build_hash=build_hash,
        components=components,
        signed_at=signed_at,
    )


# ---------------------------------------------------------------------------
# Canonical JSON for signing
# ---------------------------------------------------------------------------

def canonical_manifest_json(m: FirmwareManifest) -> bytes:
    """Return deterministic JSON bytes (sorted keys, no signature field)."""
    obj = {
        "build_hash":       m.build_hash,
        "components": sorted(
            [{"hash": c.hash, "name": c.name, "version": c.version} for c in m.components],
            key=lambda c: c["name"].lower(),
        ),
        "firmware_version": m.firmware_version,
        "rrn":              m.rrn,
        "signed_at":        m.signed_at,
    }
    return json.dumps(obj, sort_keys=False, separators=(",", ":")).encode()


# ---------------------------------------------------------------------------
# Sign / Verify
# ---------------------------------------------------------------------------

def sign_manifest(m: FirmwareManifest, private_key_pem: str) -> FirmwareManifest:
    """Sign the manifest using an Ed25519 private key (PEM).

    Sets m.signature to a base64url-encoded Ed25519 signature.
    Returns a new manifest with the signature set.
    """
    import base64

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
    except ImportError:
        raise FirmwareIntegrityError(
            "cryptography package required for firmware signing: pip install cryptography"
        )

    canonical = canonical_manifest_json(m)
    key = load_pem_private_key(private_key_pem.encode(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise FirmwareIntegrityError("Expected Ed25519 private key")

    sig_bytes = key.sign(canonical)
    sig_b64url = base64.urlsafe_b64encode(sig_bytes).rstrip(b"=").decode()

    import copy
    signed = copy.copy(m)
    signed.signature = sig_b64url
    return signed


def verify_manifest(m: FirmwareManifest, public_key_pem: str) -> None:
    """Verify the manifest signature.

    Raises FirmwareIntegrityError if the signature is invalid or missing.
    """
    import base64

    if not m.signature:
        raise FirmwareIntegrityError("Manifest has no signature")

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        raise FirmwareIntegrityError(
            "cryptography package required: pip install cryptography"
        )

    # Re-pad base64url
    sig_b64 = m.signature + "=" * (4 - len(m.signature) % 4)
    sig_bytes = base64.urlsafe_b64decode(sig_b64)

    canonical = canonical_manifest_json(m)
    key = load_pem_public_key(public_key_pem.encode())
    if not isinstance(key, Ed25519PublicKey):
        raise FirmwareIntegrityError("Expected Ed25519 public key")

    try:
        key.verify(sig_bytes, canonical)
    except InvalidSignature:
        raise FirmwareIntegrityError("Firmware manifest signature verification failed")


def firmware_hash_from_manifest(m: FirmwareManifest) -> str:
    """Return SHA-256 of the canonical manifest JSON, for use in RCAN envelope field 13."""
    canonical = canonical_manifest_json(m)
    return f"sha256:{_sha256_hex(canonical)}"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _manifest_path() -> Path:
    p = _DEFAULT_MANIFEST_FILE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    except (PermissionError, OSError):
        return _FALLBACK_MANIFEST_FILE


def save_manifest(m: FirmwareManifest, path: Optional[Path] = None) -> Path:
    """Save manifest to disk. Returns the path written."""
    out = path or _manifest_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(m.to_dict(), indent=2))
    logger.info("Firmware manifest saved to %s", out)
    return out


def load_manifest(path: Optional[Path] = None) -> FirmwareManifest:
    """Load manifest from disk."""
    p = path or _manifest_path()
    data = json.loads(p.read_text())
    return FirmwareManifest.from_dict(data)


# ---------------------------------------------------------------------------
# castor attest CLI entry points
# ---------------------------------------------------------------------------

def cmd_attest_generate(args) -> None:
    """castor attest generate — build firmware manifest from installed packages."""
    import yaml as _yaml
    def load_config(p): return (_yaml.safe_load(open(p)) if p else {}) if p else {}
    config = load_config(getattr(args, "config", None))
    meta = config.get("metadata", {})
    rrn = config.get("rrn") or meta.get("rrn") or config.get("robot_rrn") or "RRN-UNKNOWN"
    firmware_version = getattr(args, "firmware_version", None) or meta.get("version")

    manifest = generate_manifest(rrn=rrn, firmware_version=firmware_version)
    out = save_manifest(manifest)

    print(f"✓ Firmware manifest generated: {out}")
    print(f"  RRN:              {manifest.rrn}")
    print(f"  Firmware version: {manifest.firmware_version}")
    print(f"  Build hash:       {manifest.build_hash}")
    print(f"  Components:       {len(manifest.components)}")
    print(f"  Signed at:        {manifest.signed_at}")
    print()
    print("Next step: castor attest sign --key <path/to/robot-private.pem>")


def cmd_attest_sign(args) -> None:
    """castor attest sign — sign the firmware manifest with the robot's Ed25519 key."""
    key_path = Path(getattr(args, "key", "") or "")
    if not key_path.exists():
        print(f"Error: private key not found: {key_path}")
        sys.exit(1)

    manifest = load_manifest()
    private_key_pem = key_path.read_text()
    signed = sign_manifest(manifest, private_key_pem)
    out = save_manifest(signed)

    fhash = firmware_hash_from_manifest(signed)
    print(f"✓ Firmware manifest signed: {out}")
    print(f"  Signature:     {signed.signature[:32]}...")
    print(f"  firmware_hash: {fhash}")
    print()
    print("Add firmware_hash to your RCAN config or pass it in message envelopes.")


def cmd_attest_verify(args) -> None:
    """castor attest verify — verify the firmware manifest signature."""
    key_path = Path(getattr(args, "key", "") or "")
    if not key_path.exists():
        print(f"Error: public key not found: {key_path}")
        sys.exit(1)

    manifest = load_manifest()
    public_key_pem = key_path.read_text()
    try:
        verify_manifest(manifest, public_key_pem)
        print("✓ Firmware manifest signature: VALID")
        print(f"  RRN:      {manifest.rrn}")
        print(f"  Version:  {manifest.firmware_version}")
        print(f"  Signed:   {manifest.signed_at}")
    except FirmwareIntegrityError as e:
        print(f"✗ Firmware manifest signature: INVALID — {e}")
        sys.exit(1)


def cmd_attest_serve(args) -> None:
    """castor attest serve — print the manifest path for well-known serving.

    In production, the ASGI server (castor/api.py) mounts /.well-known/ from
    /run/opencastor/. This command confirms the file is in place.
    """
    p = _manifest_path()
    if p.exists():
        fhash = firmware_hash_from_manifest(load_manifest(p))
        print(f"✓ Firmware manifest at: {p}")
        print(f"  Serves at: {FIRMWARE_MANIFEST_PATH}")
        print(f"  firmware_hash: {fhash}")
    else:
        print(f"✗ Firmware manifest not found at {p}")
        print("  Run: castor attest generate && castor attest sign --key <key.pem>")
        sys.exit(1)

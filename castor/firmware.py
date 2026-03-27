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
    build_hash: str  # "sha256:<hex>" of all component hashes concatenated
    components: list[FirmwareComponent] = field(default_factory=list)
    signed_at: str = ""
    signature: Optional[str] = None
    pq_sig: Optional[str] = None  # ML-DSA-65 signature (FIPS 204, v2.2+)
    pq_alg: Optional[str] = None  # "ml-dsa-65" when present

    def to_dict(self) -> dict:
        d = {
            "rrn": self.rrn,
            "firmware_version": self.firmware_version,
            "build_hash": self.build_hash,
            "components": [asdict(c) for c in self.components],
            "signed_at": self.signed_at,
        }
        if self.signature:
            d["signature"] = self.signature
        if self.pq_sig:
            d["pq_sig"] = self.pq_sig
            d["pq_alg"] = self.pq_alg or "ml-dsa-65"
        return d

    @classmethod
    def from_dict(cls, d: dict) -> FirmwareManifest:
        components = [FirmwareComponent(**c) for c in d.get("components", [])]
        return cls(
            rrn=d.get("rrn", ""),
            firmware_version=d.get("firmware_version", ""),
            build_hash=d.get("build_hash", ""),
            components=components,
            signed_at=d.get("signed_at", ""),
            signature=d.get("signature"),
            pq_sig=d.get("pq_sig"),
            pq_alg=d.get("pq_alg"),
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
        "opencastor",
        "castor",
        "rcan-py",
        "rcan",
        "fastapi",
        "uvicorn",
        "pydantic",
        "cryptography",
        "PyNaCl",
        "PyJWT",
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
            components.append(
                FirmwareComponent(
                    name=name,
                    version=version,
                    hash=h,
                )
            )
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
        "build_hash": m.build_hash,
        "components": sorted(
            [{"hash": c.hash, "name": c.name, "version": c.version} for c in m.components],
            key=lambda c: c["name"].lower(),
        ),
        "firmware_version": m.firmware_version,
        "rrn": m.rrn,
        "signed_at": m.signed_at,
    }
    return json.dumps(obj, sort_keys=False, separators=(",", ":")).encode()


# ---------------------------------------------------------------------------
# Sign / Verify
# ---------------------------------------------------------------------------


def sign_manifest(
    m: FirmwareManifest,
    pq_key_path: Optional[str] = None,
    private_key_pem: Optional[str] = None,  # deprecated, ignored
) -> FirmwareManifest:
    """Sign the firmware manifest with ML-DSA-65 (FIPS 204).

    RCAN v2.2: ML-DSA-65 is the ONLY signing algorithm. Ed25519 is deprecated.
    ``private_key_pem`` is ignored if passed.

    Loads the ML-DSA-65 key from ``pq_key_path`` or the default location
    (``~/.opencastor/pq_signing.key``). Auto-generates if missing.

    Sets ``signature`` (ML-DSA-65 base64url) and ``pq_alg = "ml-dsa-65"``.
    Returns a new manifest with signature set.
    """
    import base64
    import copy

    _pq_key_path = pq_key_path or _default_pq_key_path()

    try:
        from rcan.signing import MLDSAKeyPair
    except ImportError:
        raise FirmwareIntegrityError(
            "dilithium-py required for firmware signing: pip install rcan[pq]"
        ) from None

    if _pq_key_path and Path(_pq_key_path).exists():
        pq_kp = MLDSAKeyPair.load(_pq_key_path)
    else:
        # Auto-generate (dev/first-run path)
        pq_kp = MLDSAKeyPair.generate()
        out_path = Path(_pq_key_path or (Path.home() / ".opencastor" / "pq_signing.key"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pq_kp.save(str(out_path))

    canonical = canonical_manifest_json(m)
    pq_sig_bytes = pq_kp.sign_bytes(canonical)

    signed = copy.copy(m)
    signed.signature = base64.urlsafe_b64encode(pq_sig_bytes).rstrip(b"=").decode()
    signed.pq_sig = signed.signature  # same field, kept for compat
    signed.pq_alg = "ml-dsa-65"
    return signed


def _default_pq_key_path() -> Optional[str]:
    """Return default ML-DSA key path if it exists."""
    import os

    env = os.environ.get("OPENCASTOR_PQ_KEY_PATH")
    if env:
        return env
    default = Path.home() / ".opencastor" / "pq_signing.key"
    return str(default) if default.exists() else None


def verify_manifest(
    m: FirmwareManifest,
    pq_public_key_path: Optional[str] = None,
    public_key_pem: Optional[str] = None,  # deprecated, ignored
    require_pq: bool = True,
) -> None:
    """Verify the ML-DSA-65 manifest signature (FIPS 204).

    RCAN v2.2: Ed25519 is deprecated. Only ML-DSA-65 signatures are verified.

    Raises:
        FirmwareIntegrityError: If signature is missing or invalid.
    """
    import base64

    if not m.signature:
        raise FirmwareIntegrityError("Manifest has no ML-DSA-65 signature")

    try:
        from rcan.signing import MLDSAKeyPair
    except ImportError:
        raise FirmwareIntegrityError("dilithium-py required: pip install rcan[pq]") from None

    _pq_pub_path = pq_public_key_path or str(Path.home() / ".opencastor" / "pq_signing.pub")
    if not Path(_pq_pub_path).exists():
        raise FirmwareIntegrityError(
            f"ML-DSA-65 public key not found: {_pq_pub_path}. Run: castor keygen --pq"
        )

    canonical = canonical_manifest_json(m)
    sig_b64 = m.signature + "=" * (4 - len(m.signature) % 4)
    sig_bytes = base64.urlsafe_b64decode(sig_b64)
    pq_pub = MLDSAKeyPair.load_public(_pq_pub_path)
    try:
        pq_pub.verify_bytes(canonical, sig_bytes)
    except Exception as e:
        raise FirmwareIntegrityError(f"ML-DSA-65 signature verification failed: {e}") from e


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

    def load_config(p):
        return (_yaml.safe_load(open(p)) if p else {}) if p else {}

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
    """castor attest sign — sign the firmware manifest with ML-DSA-65 (RCAN v2.2)."""
    key_arg = getattr(args, "key", None) or None
    pq_key_path = key_arg or _default_pq_key_path()

    manifest = load_manifest()
    signed = sign_manifest(manifest, pq_key_path=pq_key_path)
    out = save_manifest(signed)

    fhash = firmware_hash_from_manifest(signed)
    print(f"✓ Firmware manifest signed (ML-DSA-65, FIPS 204): {out}")
    print(f"  Signature:     {signed.signature[:32]}...")
    print(f"  firmware_hash: {fhash}")
    print()
    print("Add firmware_hash to your RCAN config or pass it in message envelopes.")


def cmd_attest_verify(args) -> None:
    """castor attest verify — verify the ML-DSA-65 firmware manifest signature."""
    key_arg = getattr(args, "key", None) or None
    pq_pub_path = key_arg or str(Path.home() / ".opencastor" / "pq_signing.pub")

    if not Path(pq_pub_path).exists():
        print(f"Error: ML-DSA-65 public key not found: {pq_pub_path}")
        print("Run: castor keygen --pq")
        sys.exit(1)

    manifest = load_manifest()
    try:
        verify_manifest(manifest, pq_public_key_path=pq_pub_path)
        print("✓ Firmware manifest signature (ML-DSA-65): VALID")
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

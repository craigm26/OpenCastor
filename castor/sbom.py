"""
castor/sbom — RCAN v2.1 SBOM (Software Bill of Materials) generation and publishing.

Generates a CycloneDX v1.5 SBOM with RCAN extensions and serves/publishes it.

Commands:
  castor sbom generate  — generate SBOM from installed packages
  castor sbom publish   — push SBOM to RRF registry
  castor sbom verify    — verify RRF has countersigned the SBOM

Well-known endpoint: /.well-known/rcan-sbom.json

Spec: §12 — Supply Chain Attestation
"""

from __future__ import annotations

import hashlib
import importlib.metadata as importlib_metadata
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("OpenCastor.SBOM")

SBOM_WELL_KNOWN_PATH = "/.well-known/rcan-sbom.json"
_DEFAULT_SBOM_FILE = Path("/run/opencastor/rcan-sbom.json")
_FALLBACK_SBOM_FILE = Path("/tmp/opencastor-rcan-sbom.json")

RRF_SBOM_PUBLISH_URL = "https://robot-registry-foundation.pages.dev/v2/robots/{rrn}/sbom"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SBOMComponent:
    type: str                  # "library", "framework", "operating-system"
    name: str
    version: str
    purl: str                  # package URL, e.g. "pkg:pypi/opencastor@2026.3.26.0"
    bom_ref: str = ""
    hashes: list[dict] = field(default_factory=list)


@dataclass
class RCANSBOMExtensions:
    rrn: str
    spec_version: str = "2.1.0"
    attestation_ref: str = ""  # URL where this SBOM is served (filled in after save)
    rrf_countersig: Optional[str] = None  # set after castor sbom publish


@dataclass
class RCANBOM:
    """CycloneDX 1.5 SBOM with RCAN extensions."""
    bom_format: str = "CycloneDX"
    spec_version: str = "1.5"
    serial_number: str = ""
    version: int = 1
    metadata: dict = field(default_factory=dict)
    components: list[SBOMComponent] = field(default_factory=list)
    rcan: Optional[RCANSBOMExtensions] = None

    def to_dict(self) -> dict:
        d = {
            "bomFormat":    self.bom_format,
            "specVersion":  self.spec_version,
            "serialNumber": self.serial_number,
            "version":      self.version,
            "metadata":     self.metadata,
            "components": [
                {
                    "type":     c.type,
                    "name":     c.name,
                    "version":  c.version,
                    "purl":     c.purl,
                    **({"bom-ref": c.bom_ref} if c.bom_ref else {}),
                    **({"hashes": c.hashes} if c.hashes else {}),
                }
                for c in self.components
            ],
        }
        if self.rcan:
            d["x-rcan"] = {
                "rrn":            self.rcan.rrn,
                "spec_version":   self.rcan.spec_version,
                "attestation_ref": self.rcan.attestation_ref,
                **({"rrf_countersig": self.rcan.rrf_countersig}
                   if self.rcan.rrf_countersig else {}),
            }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> RCANBOM:
        components = []
        for c in d.get("components", []):
            components.append(SBOMComponent(
                type=c.get("type", "library"),
                name=c.get("name", ""),
                version=c.get("version", ""),
                purl=c.get("purl", ""),
                bom_ref=c.get("bom-ref", ""),
                hashes=c.get("hashes", []),
            ))
        rcan_ext = None
        if "x-rcan" in d:
            xr = d["x-rcan"]
            rcan_ext = RCANSBOMExtensions(
                rrn=xr.get("rrn", ""),
                spec_version=xr.get("spec_version", "2.1.0"),
                attestation_ref=xr.get("attestation_ref", ""),
                rrf_countersig=xr.get("rrf_countersig"),
            )
        return cls(
            bom_format=d.get("bomFormat", "CycloneDX"),
            spec_version=d.get("specVersion", "1.5"),
            serial_number=d.get("serialNumber", ""),
            version=d.get("version", 1),
            metadata=d.get("metadata", {}),
            components=components,
            rcan=rcan_ext,
        )


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _serial_number() -> str:
    """Generate a CycloneDX-compliant URN serial number."""
    import uuid
    return f"urn:uuid:{uuid.uuid4()}"


def _make_purl(name: str, version: str, ecosystem: str = "pypi") -> str:
    return f"pkg:{ecosystem}/{name.lower()}@{version}"


def generate_sbom(rrn: str, sbom_url: str = "") -> RCANBOM:
    """Generate a CycloneDX 1.5 SBOM from installed Python packages.

    Args:
        rrn: Robot Registration Number.
        sbom_url: URL where the SBOM will be served (attestation_ref).

    Returns:
        An unsigned RCANBOM.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Get opencastor version
    try:
        oc_version = importlib_metadata.distribution("opencastor").version
    except Exception:
        oc_version = "unknown"

    metadata = {
        "timestamp": now,
        "tools": [{"vendor": "OpenCastor", "name": "castor sbom", "version": oc_version}],
        "component": {
            "type":    "firmware",
            "name":    "opencastor",
            "version": oc_version,
            "purl":    _make_purl("opencastor", oc_version),
        },
    }

    components = []
    for dist in sorted(importlib_metadata.distributions(), key=lambda d: (d.metadata.get("Name") or "").lower()):
        name = dist.metadata.get("Name") or ""
        version = dist.metadata.get("Version") or "unknown"
        if not name:
            continue
        purl = _make_purl(name, version)
        # Hash the dist info Name+Version for a stable component hash
        h = _sha256_hex(f"{name}=={version}".encode())
        components.append(SBOMComponent(
            type="library",
            name=name,
            version=version,
            purl=purl,
            bom_ref=f"{name.lower()}-{version}",
            hashes=[{"alg": "SHA-256", "content": h}],
        ))

    rcan_ext = RCANSBOMExtensions(
        rrn=rrn,
        spec_version="2.1.0",
        attestation_ref=sbom_url,
    )

    return RCANBOM(
        serial_number=_serial_number(),
        metadata=metadata,
        components=components,
        rcan=rcan_ext,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _sbom_path() -> Path:
    p = _DEFAULT_SBOM_FILE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    except (PermissionError, OSError):
        return _FALLBACK_SBOM_FILE


def save_sbom(sbom: RCANBOM, path: Optional[Path] = None) -> Path:
    """Save SBOM to disk. Returns the path written."""
    out = path or _sbom_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(sbom.to_dict(), indent=2))
    logger.info("SBOM saved to %s", out)
    return out


def load_sbom(path: Optional[Path] = None) -> RCANBOM:
    """Load SBOM from disk."""
    p = path or _sbom_path()
    data = json.loads(p.read_text())
    return RCANBOM.from_dict(data)


# ---------------------------------------------------------------------------
# RRF publishing
# ---------------------------------------------------------------------------

def publish_sbom_to_rrf(sbom: RCANBOM, rrf_token: str) -> dict:
    """POST the SBOM to the RRF registry.

    Returns the JSON response (including rrf_countersig if provided).
    Raises RuntimeError on failure.
    """
    publish_url = RRF_SBOM_PUBLISH_URL.format(rrn=(sbom.rcan.rrn if sbom.rcan else "unknown")) if "{rrn}" in RRF_SBOM_PUBLISH_URL else RRF_SBOM_PUBLISH_URL
    payload = json.dumps(sbom.to_dict()).encode()
    req = Request(
        publish_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {rrf_token}",
            "User-Agent": "OpenCastor/2026.3.26.1",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except URLError as e:
        raise RuntimeError(f"Failed to publish SBOM to RRF: {e}") from e


# ---------------------------------------------------------------------------
# castor sbom CLI entry points
# ---------------------------------------------------------------------------

def cmd_sbom_generate(args) -> None:
    """castor sbom generate — build SBOM from installed packages."""
    import yaml as _yaml
    _cp = getattr(args, "config", None)
    config = (_yaml.safe_load(open(_cp)) if _cp else {}) if _cp else {}
    # also check metadata subkey
    config.get("metadata", config)
    rrn = config.get("rrn") or config.get("metadata", {}).get("rrn") or config.get("robot_rrn") or "RRN-UNKNOWN"

    # Derive SBOM URL from robot's RURI or a configured base URL
    config.get("ruri", "")
    sbom_url = getattr(args, "sbom_url", None) or (
        f"https://robot-registry-foundation.pages.dev/v2/robots/{rrn}/sbom" if rrn != "RRN-UNKNOWN" else ""
    )

    sbom = generate_sbom(rrn=rrn, sbom_url=sbom_url)
    out = save_sbom(sbom)

    print(f"✓ SBOM generated: {out}")
    print(f"  RRN:        {rrn}")
    print(f"  Components: {len(sbom.components)}")
    print(f"  Serial:     {sbom.serial_number}")
    print(f"  attestation_ref: {sbom_url or '(not set — pass --sbom-url)'}")
    print()
    print("Next step: castor sbom publish --token <rrf-token>")


def cmd_sbom_publish(args) -> None:
    """castor sbom publish — push SBOM to RRF registry."""
    token = getattr(args, "token", "") or ""
    if not token:
        print("Error: --token is required for RRF publishing")
        sys.exit(1)

    sbom = load_sbom()
    try:
        response = publish_sbom_to_rrf(sbom, token)
        countersig = response.get("rrf_countersig")
        if countersig and sbom.rcan:
            sbom.rcan.rrf_countersig = countersig
            save_sbom(sbom)
            print("✓ SBOM published and countersigned by RRF")
            print(f"  rrf_countersig: {countersig[:32]}...")
        else:
            print("✓ SBOM published to RRF (no countersig in response)")
    except RuntimeError as e:
        print(f"✗ {e}")
        sys.exit(1)


def cmd_sbom_verify(args) -> None:
    """castor sbom verify — check RRF has countersigned the SBOM."""
    sbom = load_sbom()
    if sbom.rcan and sbom.rcan.rrf_countersig:
        print(f"✓ SBOM has RRF countersignature: {sbom.rcan.rrf_countersig[:32]}...")
        print(f"  attestation_ref: {sbom.rcan.attestation_ref}")
    else:
        print("✗ SBOM has no RRF countersignature — run `castor sbom publish`")
        sys.exit(1)

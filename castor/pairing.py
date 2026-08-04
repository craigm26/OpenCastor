"""castor pair — generate the iOS app's pairing QR + wire gateway attestation.

This bridges the robot-md-gateway (T-001 signed receipts) to the OpenCastor iOS
app. `castor pair`:

  1. Generates an Ed25519 attestation identity (the SAME throwaway approach the
     gateway uses for its own signing key: ``Ed25519PrivateKey.generate()`` ->
     PKCS8 PEM, ``NoEncryption``) and writes the private key to disk (0600).
  2. Wires it into the gateway's env config by setting
     ``ROBOT_MD_ATTESTATION_KEY_FILE`` and ``ROBOT_MD_ATTESTATION_KID`` — the exact
     variables ``robot_md_gateway.attestation.load_signing_identity_from_env`` reads
     so that /v1/invoke starts returning signed receipts.
  3. Builds the pairing QR payload
     ``{v, gateway_url, bearer, manifest_path, rrn, estop_url?}``. ``manifest_path``
     MUST ride in the QR: it is a gateway-host-local filesystem path the
     InvokeEnvelope requires and the client cannot guess.
  4. Projects the robot's OWN declared capability surface out of its ROBOT.md into
     ``capability_surface``, so a phone that has never seen this robot renders the
     capabilities this robot declares rather than whichever manifest it shipped
     with. The QR is already the trust root (it carries the gateway attestation
     key), so this keeps the client from having to fetch a manifest at all.

Pure logic lives here (no argparse, no printing) so it is unit-testable; the
``castor pair`` CLI glue in ``castor/cli.py`` calls ``run_pair`` then renders the
QR + JSON.
"""

from __future__ import annotations

import hashlib
import os
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path

# The two env vars the gateway's attestation loader consumes. Kept as module
# constants so the test asserts against the same strings the gateway reads.
ATTESTATION_KEY_FILE_ENV = "ROBOT_MD_ATTESTATION_KEY_FILE"
ATTESTATION_KID_ENV = "ROBOT_MD_ATTESTATION_KID"

# QR payload schema version. The iOS app (T-012) parses on this.
PAIR_PAYLOAD_VERSION = 1

# How many bytes of encoded payload we are willing to ask a phone camera to read.
# A byte-mode QR tops out at 2953 bytes, but that is a 177x177 module grid — the
# kind of QR that scans off a printed page and not off a terminal at arm's length.
# Below this ceiling the capability surface is carried whole; above it the biggest
# and least load-bearing parts are dropped (see _fit_surface) rather than letting
# `castor pair` emit a QR nobody can scan.
PAIR_QR_BYTE_BUDGET = 1400


@dataclass(frozen=True)
class AttestationIdentity:
    """A freshly-generated gateway attestation identity."""

    key_file: Path
    kid: str
    pub_file: Path


@dataclass(frozen=True)
class PairResult:
    """Everything `castor pair` produced — returned so callers/tests can assert."""

    payload: dict
    identity: AttestationIdentity
    env_file: Path


def default_gateway_url(port: int = 8080) -> str:
    """Best-effort LAN URL for the gateway. Falls back to 127.0.0.1.

    Uses the standard connect-a-UDP-socket trick to learn the primary LAN IP
    without sending anything. The client scans this over the local network.
    """
    ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1; no packets actually sent
            ip = s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        pass
    return f"http://{ip}:{port}"


def generate_attestation_identity(
    key_file: Path, *, kid: str | None = None, force: bool = False
) -> AttestationIdentity:
    """Generate an Ed25519 attestation identity and persist it.

    Reuses the gateway's own throwaway-key recipe (PKCS8 PEM, NoEncryption) so
    the key the gateway loads is byte-compatible with
    ``serialization.load_pem_private_key``. Writes ``<key_file>`` (0600) and a
    sibling ``<key_file>.pub`` (0644, SubjectPublicKeyInfo PEM) for the operator
    / app to resolve the kid to a public key.

    kid defaults to ``gw-<sha256(raw pubkey)[:12]>`` — stable per key, unique.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key_file = key_file.expanduser().resolve()
    if key_file.exists() and not force:
        raise FileExistsError(
            f"{key_file} already exists; pass force=True (or --force) to overwrite "
            "(this rotates the attestation identity)."
        )

    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    raw_pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if kid is None:
        kid = f"gw-{hashlib.sha256(raw_pub).hexdigest()[:12]}"

    key_file.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(key_file, priv_pem, mode=0o600)
    pub_file = key_file.with_suffix(key_file.suffix + ".pub")
    _atomic_write_bytes(pub_file, pub_pem, mode=0o644)

    return AttestationIdentity(key_file=key_file, kid=kid, pub_file=pub_file)


def read_bearer_from_bearers_yaml(path: Path, *, prefer_tier: str = "actuate") -> str:
    """Extract a bearer token from a robot-md-gateway bearers.yaml.

    Prefers an entry whose ``tier`` is ``prefer_tier`` (default ``actuate`` — the
    tier the app needs to drive the robot); falls back to the first token.
    """
    import yaml

    data = yaml.safe_load(path.expanduser().read_text()) or []
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a list of bearer entries")
    entries = [e for e in data if isinstance(e, dict) and e.get("token")]
    if not entries:
        raise ValueError(f"{path}: no bearer entries with a token found")
    for entry in entries:
        if entry.get("tier") == prefer_tier:
            return str(entry["token"])
    return str(entries[0]["token"])


def read_rrn_from_manifest(manifest_path: Path) -> str:
    """Parse the robot's RRN out of a ROBOT.md YAML frontmatter block.

    Reads ``metadata.rrn`` (RCAN v3 shape), falling back to a top-level ``rrn``.
    Returns "" when neither is present (the caller may then require --rrn).
    """
    import yaml

    text = manifest_path.expanduser().read_text()
    fm = _extract_frontmatter(text)
    data = yaml.safe_load(fm) if fm else {}
    if not isinstance(data, dict):
        return ""
    metadata = data.get("metadata") or {}
    rrn = (metadata.get("rrn") if isinstance(metadata, dict) else None) or data.get("rrn")
    return str(rrn) if rrn else ""


def capability_surface_from_manifest(manifest_path: Path) -> dict | None:
    """Project a ROBOT.md's declared capability surface into the QR's compact form.

    The client cannot read a gateway-host-local ROBOT.md and (by design) never
    fetches one, so whatever it is going to render about *this* robot has to ride
    in the QR. This is a lossy projection on purpose: only the fields a client
    actually reads off a manifest travel — the declared capabilities, whether a
    software stop is declared, the declared HiTL gates, and the per-capability
    contracts a client validates a drafted action's SHAPE against.

    Shape (all keys optional except ``capabilities``)::

        {"robot_name": str,
         "capabilities": [str],
         "software_stop": bool,
         "gates": [{"scope": str, "require_auth": bool}],
         "object_descriptors": [str],
         "contracts": {cap: {"args": {name: {"kind": str,
                                             "required": bool,
                                             "has_default": bool}},
                             "preconditions": [str]}}}

    ``physics.workspace`` deliberately does NOT travel: it is display prose the
    client never decides anything with, and its free-text ``note`` has no length
    bound — an unlucky manifest would cost the QR its scannability to say
    something the user can read on the robot.

    ``has_default`` says the contract DECLARES a default, not what the default is:
    the client only needs it to know a missing arg is still well-formed.

    Returns ``None`` when the manifest declares no capabilities at all (there is
    then nothing to say, and an empty surface would be a claim of its own) or when
    the file cannot be read/parsed — pairing must not fail because a manifest is
    unusual.
    """
    import yaml

    try:
        text = Path(manifest_path).expanduser().read_text()
        fm = _extract_frontmatter(text)
        data = yaml.safe_load(fm) if fm else {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None

    capabilities = [c for c in (data.get("capabilities") or []) if isinstance(c, str)]
    if not capabilities:
        return None

    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    safety = data.get("safety") if isinstance(data.get("safety"), dict) else {}
    estop = safety.get("estop") if isinstance(safety.get("estop"), dict) else {}

    surface: dict = {"capabilities": capabilities}
    robot_name = metadata.get("robot_name") or data.get("robot_name")
    if robot_name:
        surface["robot_name"] = str(robot_name)
    surface["software_stop"] = bool(estop.get("software"))

    gates = []
    for gate in safety.get("hitl_gates") or []:
        if not isinstance(gate, dict) or not gate.get("scope"):
            continue
        gates.append(
            {"scope": str(gate["scope"]), "require_auth": bool(gate.get("require_auth"))}
        )
    if gates:
        surface["gates"] = gates

    # The targets the robot declares it can SEE. Cheap, and without them a client
    # drafting an action has no idea which object names are real.
    vision = data.get("vision") if isinstance(data.get("vision"), dict) else {}
    descriptors = [
        str(entry["id"])
        for entry in (vision.get("object_descriptors") or [])
        if isinstance(entry, dict) and entry.get("id")
    ]
    if descriptors:
        surface["object_descriptors"] = descriptors

    contracts = _project_contracts(data.get("capability_contracts"))
    if contracts:
        surface["contracts"] = contracts
    return surface


def _project_contracts(raw: object) -> dict:
    """`capability_contracts` → the compact per-capability contract map."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for capability, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        entry: dict = {}
        args: dict = {}
        raw_args = spec.get("args")
        if isinstance(raw_args, dict):
            for name, arg in raw_args.items():
                arg = arg if isinstance(arg, dict) else {}
                projected: dict = {}
                if arg.get("kind"):
                    projected["kind"] = str(arg["kind"])
                if arg.get("required"):
                    projected["required"] = True
                if "default" in arg:
                    projected["has_default"] = True
                args[str(name)] = projected
        if args:
            entry["args"] = args
        preconditions = []
        for pre in spec.get("preconditions") or []:
            if not isinstance(pre, dict) or not pre.get("kind"):
                continue
            kind = str(pre["kind"])
            name = pre.get("name")
            preconditions.append(f"{kind}({name})" if name else kind)
        if preconditions:
            entry["preconditions"] = preconditions
        out[str(capability)] = entry
    return out


def _fit_surface(payload: dict, surface: dict, budget: int) -> dict | None:
    """Trim the capability surface until the encoded payload fits ``budget`` bytes.

    Drops in order of least value per byte: the contracts (by far the largest
    part, and only a shape check on the client), then the declared targets, then
    the declared gates, then the surface entirely. A robot with an unusually rich
    manifest therefore still gets a scannable QR carrying its capability list, and
    only ever loses detail — it never gains a claim.
    """
    import json as _json

    candidate = dict(surface)
    for drop in (None, "contracts", "object_descriptors", "gates"):
        if drop:
            candidate.pop(drop, None)
        probe = dict(payload)
        probe["capability_surface"] = candidate
        if len(_json.dumps(probe, separators=(",", ":")).encode("utf-8")) <= budget:
            return candidate
    return None


def set_env_var(env_file: Path, key: str, value: str) -> None:
    """Idempotently set ``key=value`` in a dotenv-style file, preserving others.

    If ``key`` already appears it is replaced in place; otherwise it is appended.
    All other lines (e.g. the gateway's ROBOT_MD_PATH / ROBOT_MD_BEARERS_FILE) are
    left untouched, so pointing --env-file at the gateway's existing .env is safe.
    """
    env_file = env_file.expanduser()
    env_file.parent.mkdir(parents=True, exist_ok=True)
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    new_line = f"{key}={value}"
    replaced = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped.split("=", 1)[0] == key:
            out.append(new_line)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(new_line)
    content = "\n".join(out) + "\n"
    _atomic_write_bytes(env_file, content.encode("utf-8"), mode=0o644)


def build_pair_payload(
    *,
    gateway_url: str,
    bearer: str,
    manifest_path: str,
    rrn: str,
    estop_url: str | None = None,
    attest_kid: str | None = None,
    attest_pub: str | None = None,
    console_url: str | None = None,
    console_token: str | None = None,
    capability_surface: dict | None = None,
) -> dict:
    """Build the pairing QR payload. estop_url is included only when provided.

    attest_kid/attest_pub (the gateway attestation kid + standard-base64 SPKI DER
    of its Ed25519 verify key) ride along when both are provided, so clients can
    verify this gateway's signed receipts offline. v1 parsers that predate the
    fields ignore them (unknown keys are non-breaking by contract).

    capability_surface — this robot's own declared capabilities (see
    ``capability_surface_from_manifest``) — rides last and is trimmed to fit
    ``PAIR_QR_BYTE_BUDGET``, because a QR too dense to scan pairs nothing.
    """
    payload = {
        "v": PAIR_PAYLOAD_VERSION,
        "gateway_url": gateway_url,
        "bearer": bearer,
        "manifest_path": manifest_path,
        "rrn": rrn,
    }
    if estop_url:
        payload["estop_url"] = estop_url
    if attest_kid and attest_pub:
        payload["attest_kid"] = attest_kid
        payload["attest_pub"] = attest_pub
    if console_url and console_token:
        # Camera feeds and model settings. Carries its OWN read-only token:
        # a feed URL ends up in image tags, screen recordings, and access logs,
        # and the actuate bearer above can move the arm.
        payload["console_url"] = console_url
        payload["console_token"] = console_token
    if capability_surface and capability_surface.get("capabilities"):
        fitted = _fit_surface(payload, capability_surface, PAIR_QR_BYTE_BUDGET)
        if fitted:
            payload["capability_surface"] = fitted
    return payload


def _attest_pub_b64(pub_file: Path) -> str | None:
    """Standard-base64 SPKI DER of the attestation public key, for the QR."""
    try:
        import base64

        from cryptography.hazmat.primitives import serialization

        key = serialization.load_pem_public_key(pub_file.expanduser().read_bytes())
        der = key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return base64.b64encode(der).decode("ascii")
    except Exception:
        return None


def run_pair(
    *,
    manifest_path: Path,
    gateway_url: str,
    bearer: str,
    rrn: str,
    key_file: Path,
    env_file: Path,
    kid: str | None = None,
    estop_url: str | None = None,
    force: bool = False,
) -> PairResult:
    """Generate the identity, wire the env, and build the QR payload.

    This is the testable core of ``castor pair`` — no printing, no argparse. The
    manifest_path is resolved to an absolute gateway-host-local path before it is
    placed in the QR (the client cannot guess it), and the robot's own declared
    capability surface is projected out of that same manifest into the QR.
    """
    identity = generate_attestation_identity(key_file, kid=kid, force=force)

    set_env_var(env_file, ATTESTATION_KEY_FILE_ENV, str(identity.key_file))
    set_env_var(env_file, ATTESTATION_KID_ENV, identity.kid)

    payload = build_pair_payload(
        gateway_url=gateway_url,
        bearer=bearer,
        manifest_path=str(manifest_path.expanduser().resolve()),
        rrn=rrn,
        estop_url=estop_url,
        attest_kid=identity.kid,
        attest_pub=_attest_pub_b64(identity.pub_file),
        capability_surface=capability_surface_from_manifest(manifest_path),
    )
    return PairResult(payload=payload, identity=identity, env_file=env_file.expanduser())


def _extract_frontmatter(text: str) -> str:
    """Return the YAML frontmatter block (between the first two '---' fences)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    body: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            return "\n".join(body)
        body.append(line)
    return ""


def _atomic_write_bytes(path: Path, content: bytes, *, mode: int) -> None:
    """Atomic write with an explicit permission mode (mirrors init_wizard)."""
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise

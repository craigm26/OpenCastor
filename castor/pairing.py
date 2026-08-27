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

THE QR IS A UNIVERSAL LINK
--------------------------

The QR used to encode the raw payload JSON, which only the OpenCastor app's own
in-app scanner understood — a phone camera pointed at it showed a wall of
gibberish, and the person holding the phone had no idea what they were looking
at or what to install. So the QR now encodes a link instead::

    https://opencastor.com/pair#v1.<base64url of the compact payload JSON>

One QR, both audiences:

  * **App installed** — opencastor.com serves an ``apple-app-site-association``
    covering ``/pair`` for ``WYGG3JXWMG.com.opencastor.ios``, so iOS opens the
    app straight into pairing (once the app ships its associated-domains
    entitlement). Nothing is fetched; the app reads the fragment it was handed.
  * **App not installed** — the phone's browser lands on the /pair explainer
    page, which says what this is and ends in an App Store button.

THE PAYLOAD RIDES IN THE FRAGMENT, AND ONLY THERE
-------------------------------------------------

The payload carries live credentials: an actuate-tier gateway bearer that can
move the robot, and a console token. It goes after the ``#`` because **URL
fragments are never sent to the server**. Not in the request line, so not in an
access log; not to a CDN, so not in a cache key; not to an analytics pixel, so
not in someone's funnel dashboard. Putting one byte of it in the path or the
query would publish a live bearer to every hop between the phone and
opencastor.com — and to whoever reads those logs later. ``test_pairing.py`` pins
this: nothing before the ``#`` may contain payload bytes.

The ``v1.`` tag in front of the base64url is a parsing version, separate from
the payload's own ``v``: it lets the app evolve the *envelope* (a different
encoding, a compressed body) without guessing at what it was handed.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import socket
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("castor.pairing")

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
#
# In LINK MODE the budget is measured against the LINK, not the JSON: base64url
# costs ~33% over the raw bytes and the prefix costs another 34, and it is the
# link the camera has to resolve. Measuring the wrong string is how a QR that
# passes its own fit check still fails at arm's length.
PAIR_QR_BYTE_BUDGET = 1400

# The universal link the QR encodes. opencastor.com serves an
# apple-app-site-association covering /pair for WYGG3JXWMG.com.opencastor.ios,
# and /pair is a live explainer page for phones that do not have the app.
PAIR_LINK_BASE = "https://opencastor.com/pair"

# Fragment envelope tag — `v1.<base64url>`. Deliberately NOT the same number as
# PAIR_PAYLOAD_VERSION: this versions how the fragment is *encoded*, so the app
# can be handed a different encoding some day and know it before it parses.
PAIR_LINK_SCHEMA = "v1"


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
        # REUSE, DON'T REFUSE. The most common reason to re-run `castor pair`
        # is not a new robot — it is a QR that went stale: the Pi moved DHCP
        # leases, a console was added, a bearer changed. Refusing here left
        # exactly two paths, both wrong: hand-assemble the payload (which is
        # how the rover shipped a QR pinned to an address the Pi no longer
        # held), or pass --force and ROTATE a live signing identity to change
        # an IP address (which is how a robot on this bench lost the only copy
        # of its attestation key). An existing key is an identity to keep, and
        # re-emitting its public half is exactly what re-pairing means.
        from cryptography.hazmat.primitives import serialization

        priv = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
        pub_file = key_file.with_suffix(key_file.suffix + ".pub")
        if not pub_file.exists():
            _atomic_write_bytes(
                pub_file,
                priv.public_key().public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                ),
                mode=0o644,
            )
        if kid is None:
            raw_pub = priv.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            kid = f"gw-{hashlib.sha256(raw_pub).hexdigest()[:12]}"
        logger.info("reusing existing attestation identity %s (kid %s)", key_file, kid)
        return AttestationIdentity(key_file=key_file, kid=kid, pub_file=pub_file)

    if key_file.exists():
        # FORCE ROTATES A LIVE SIGNING IDENTITY, SO KEEP THE OLD ONE.
        #
        # Written after `--force` destroyed the only copy of a robot's live
        # attestation key on this bench. Everything about that was working as
        # documented — the docstring says force rotates the identity — and it
        # still cost an unrecoverable key, because "rotate" and "delete the only
        # copy of the thing that signs your receipts" are the same operation
        # when nothing keeps a backup.
        #
        # The old key stays resolvable for receipts already signed with it, so
        # a rotation becomes reversible and the existing audit trail stays
        # verifiable. Timestamped rather than a single `.bak`, so a second
        # rotation cannot quietly eat the first one's backup.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = key_file.with_suffix(key_file.suffix + f".rotated-{stamp}")
        _atomic_write_bytes(backup, key_file.read_bytes(), mode=0o600)
        logger.warning("rotating attestation identity; previous key saved to %s", backup)

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

    BOTH FILE SHAPES ARE ACCEPTED, because the two tools that share this file
    disagreed about it. ``robot-md-gateway init`` writes a MAPPING with a
    ``bearers:`` key (and, once a robot has several actuators, an ``actuators:``
    key beside it); this reader only ever accepted a BARE LIST. So the file the
    gateway's own wizard generates could not be read by the pairing command that
    is documented as the next step, and it failed with "expected a list of
    bearer entries" about a file that plainly contains one.

    Reading both is the fix rather than picking a winner: real deployments have
    files in each shape already, and a format flag-day would break whichever
    half was not migrated.
    """
    import yaml

    data = yaml.safe_load(path.expanduser().read_text()) or []
    if isinstance(data, dict):
        # The gateway wizard's shape. Anything else in the mapping (actuators,
        # policy) is not this function's business.
        data = data.get("bearers") or []
    if not isinstance(data, list):
        raise ValueError(
            f"{path}: expected a list of bearer entries, or a mapping with a 'bearers:' list"
        )
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
        gates.append({"scope": str(gate["scope"]), "require_auth": bool(gate.get("require_auth"))})
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


def compact_payload_json(payload: dict) -> str:
    """The payload's canonical compact JSON — the one form everything encodes.

    Compact separators, key order as built. The QR, the byte budget and the link
    fragment all measure and encode THIS string, so a payload that fits is the
    payload that ships.
    """
    return json.dumps(payload, separators=(",", ":"))


def encode_pair_fragment(payload: dict) -> str:
    """``v1.<unpadded base64url of the compact payload JSON>``.

    Unpadded because ``=`` is legal in a fragment but ugly in a QR and in every
    log line a human ever pastes it into; the decoder puts the padding back.
    """
    compact = compact_payload_json(payload).encode("utf-8")
    encoded = base64.urlsafe_b64encode(compact).decode("ascii").rstrip("=")
    return f"{PAIR_LINK_SCHEMA}.{encoded}"


def decode_pair_fragment(fragment: str) -> dict:
    """The inverse of :func:`encode_pair_fragment`. Raises ValueError if it isn't one.

    This is the reference implementation: the runtime verifies its own QR with
    it, and the firmware and app parsers are written against it.
    """
    fragment = fragment.lstrip("#")
    tag, dot, encoded = fragment.partition(".")
    if not dot or tag != PAIR_LINK_SCHEMA:
        raise ValueError(
            f"unknown pairing fragment version {tag!r} (this runtime writes {PAIR_LINK_SCHEMA!r})"
        )
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"pairing fragment is not base64url: {exc}") from exc
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("pairing fragment did not decode to a JSON object")
    return payload


def pair_link(payload: dict) -> str:
    """The full universal link: ``https://opencastor.com/pair#v1.<base64url>``.

    EVERY byte of the payload is after the ``#``. Fragments are not sent to
    servers, and this payload holds a live actuate bearer — see the module
    docstring. Nothing here may ever move a payload field into the path or the
    query, however convenient it looks.
    """
    return f"{PAIR_LINK_BASE}#{encode_pair_fragment(payload)}"


def decode_pair_link(link: str) -> dict:
    """Read a payload back out of a pairing link. Raises ValueError if it isn't one.

    Deliberately indifferent to what precedes the ``#``: the fragment *is* the
    pairing, and the origin in front of it only decides which explainer page a
    phone without the app lands on. A self-hosted /pair page still pairs.
    """
    _, sep, fragment = link.partition("#")
    if not sep:
        # The input is not echoed: a pairing link is a credential, and error
        # text ends up in bug reports.
        raise ValueError("not a pairing link: no '#' fragment")
    return decode_pair_fragment(fragment)


def _encoded_size(payload: dict, *, for_link: bool) -> int:
    """Bytes the QR actually has to carry for this payload."""
    if for_link:
        return len(pair_link(payload).encode("utf-8"))
    return len(compact_payload_json(payload).encode("utf-8"))


def _fit_surface(
    payload: dict, surface: dict, budget: int, *, for_link: bool = False
) -> dict | None:
    """Trim the capability surface until the encoded payload fits ``budget`` bytes.

    Drops in order of least value per byte: the contracts (by far the largest
    part, and only a shape check on the client), then the declared targets, then
    the declared gates, then the surface entirely. A robot with an unusually rich
    manifest therefore still gets a scannable QR carrying its capability list, and
    only ever loses detail — it never gains a claim.

    ``for_link`` measures the LINK the QR will carry rather than the raw JSON,
    because that is the string the camera resolves. Same budget, longer string:
    a rich manifest loses its contracts one step sooner in link mode.
    """
    candidate = dict(surface)
    for drop in (None, "contracts", "object_descriptors", "gates"):
        if drop:
            candidate.pop(drop, None)
        probe = dict(payload)
        probe["capability_surface"] = candidate
        if _encoded_size(probe, for_link=for_link) <= budget:
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


def read_env_file(env_file: Path) -> dict[str, str]:
    """Read a dotenv-style file into a mapping. Absent or unreadable is empty.

    The counterpart to :func:`set_env_var`, and deliberately as forgiving as the
    shell that sources these files: comments and blank lines are skipped, and a
    value's surrounding quotes come back off (they are there so a shell reads a
    value containing ``|`` or a space as one word, not because they are part of
    it). An absent file is an empty mapping rather than an error — every caller
    is asking "did the operator configure this?", and "no" is an answer.
    """
    values: dict[str, str] = {}
    try:
        lines = env_file.expanduser().read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        values[key.strip()] = val
    return values


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
    for_link: bool = False,
) -> dict:
    """Build the pairing QR payload. estop_url is included only when provided.

    attest_kid/attest_pub (the gateway attestation kid + standard-base64 SPKI DER
    of its Ed25519 verify key) ride along when both are provided, so clients can
    verify this gateway's signed receipts offline. v1 parsers that predate the
    fields ignore them (unknown keys are non-breaking by contract).

    capability_surface — this robot's own declared capabilities (see
    ``capability_surface_from_manifest``) — rides last and is trimmed to fit
    ``PAIR_QR_BYTE_BUDGET``, because a QR too dense to scan pairs nothing.

    for_link budgets that trim against the universal LINK (:func:`pair_link`)
    rather than the raw JSON — base64url costs ~33% and the prefix another 34,
    and it is the link the camera resolves. It defaults False so every existing
    caller and every ``--no-link`` run produces byte-identical output to before;
    ``castor pair`` and ``castor up`` pass True.
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
        fitted = _fit_surface(payload, capability_surface, PAIR_QR_BYTE_BUDGET, for_link=for_link)
        if fitted:
            payload["capability_surface"] = fitted
    return payload


def _attest_pub_b64(pub_file: Path) -> str | None:
    """Standard-base64 SPKI DER of the attestation public key, for the QR."""
    try:
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
    console_url: str | None = None,
    console_token: str | None = None,
    force: bool = False,
    for_link: bool = False,
) -> PairResult:
    """Generate the identity, wire the env, and build the QR payload.

    This is the testable core of ``castor pair`` — no printing, no argparse. The
    manifest_path is resolved to an absolute gateway-host-local path before it is
    placed in the QR (the client cannot guess it), and the robot's own declared
    capability surface is projected out of that same manifest into the QR.

    for_link budgets the capability surface against the universal link the QR
    will carry rather than the raw JSON (see :func:`build_pair_payload`).
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
        console_url=console_url,
        console_token=console_token,
        attest_kid=identity.kid,
        attest_pub=_attest_pub_b64(identity.pub_file),
        capability_surface=capability_surface_from_manifest(manifest_path),
        for_link=for_link,
    )
    return PairResult(payload=payload, identity=identity, env_file=env_file.expanduser())


def write_pair_artifacts(payload: dict, out_dir: Path, *, link: bool = False) -> dict[str, Path]:
    """Write pair-payload.json and (when possible) pair-qr.png into out_dir.

    WHY THIS EXISTS AS A FUNCTION AND NOT A RUNBOOK STEP. Every robot on the
    bench ended up with a hand-assembled pair-payload.json plus a qrcode
    two-liner pasted from session notes — and hand assembly is exactly how the
    rover shipped a QR pinned to a DHCP address the Pi no longer held, pointing
    the phone at a machine that wasn't there. The payload builder always knew
    how to include every field; what was missing was one command that put the
    result where the runbooks already said it lives.

    The JSON is always written — it is the payload of record, and pasting it is
    the documented fallback when a screen is too small or too dim to scan. The
    PNG needs the optional ``qrcode`` package; without it this degrades to
    JSON-only rather than failing, because a missing nicety must not block a
    pairing. Returned dict maps artifact name to written path.

    ``link=True`` also writes pair-link.txt and points the PNG at that link
    instead of the raw JSON, so a phone CAMERA — not just the app's in-app
    scanner — does something useful with the QR. pair-link.txt is written before
    the PNG is attempted and is 0600 like the JSON: its fragment carries the same
    live bearer, and it is the artifact you can paste into a phone when there is
    no qrcode package and no scannable screen.
    """
    out_dir = out_dir.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    compact = compact_payload_json(payload)
    json_path = out_dir / "pair-payload.json"
    # Pretty for the file (humans diff it), compact for the QR (bytes matter).
    _atomic_write_bytes(json_path, (json.dumps(payload, indent=1) + "\n").encode(), mode=0o600)
    written["payload"] = json_path

    qr_content = compact
    if link:
        qr_content = pair_link(payload)
        link_path = out_dir / "pair-link.txt"
        _atomic_write_bytes(link_path, (qr_content + "\n").encode(), mode=0o600)
        written["link"] = link_path

    try:
        import qrcode  # noqa: PLC0415 - optional dependency, degrade gracefully
    except ImportError:
        logger.warning(
            "qrcode not installed — wrote %s only; "
            "`pip install qrcode[pil]` to also get pair-qr.png",
            json_path,
        )
        return written

    png_path = out_dir / "pair-qr.png"
    qrcode.make(qr_content).save(str(png_path))
    png_path.chmod(0o600)
    written["qr"] = png_path
    return written


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

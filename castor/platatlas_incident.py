"""castor.platatlas_incident - file an incident-report/1 to a PlatAtlas org.

WHAT THIS IS. One record class, in one shape, built the same way on both sides of the
wire. ``castor incidents report --submit --platatlas`` takes the unfiled incidents out
of the local hash-chained log (``castor/incidents.py``), turns each one into an
``incident-report/1`` object, signs it with THE ROBOT'S OWN attestation key, and posts
the lot as RCAN NDJSON to ``POST https://<org>.platatlas.com/api/traces?source=rcan``.

The rail side parses exactly these bytes. Its parser is
``apps/platatlas-worker/src/routes/record-class-ingest.ts``, the signature check is
``src/crypto/rcan-verify.ts``, and the canonicalisation both sides sign over is
``packages/rcan/src/canonical.ts``. This module carries a Python implementation of that
one canonicalisation and a validator that refuses any value where the two
implementations could disagree, so "byte-identical" is enforced rather than hoped for.
See :func:`canonical_json_string` for the rules and :func:`_assert_canonicalisable` for
the three cases that are refused.

WHAT IT IS NOT. Filing a report is filing a report. Nothing here is verified by
software: the signature establishes that the holder of this robot's attestation key
signed exactly these bytes, and every field INSIDE those bytes is this robot operator's
own account. ``reporter`` is a named human field and nothing checks that the named
person exists or wrote it. ``severity`` is the filer's choice from the published
taxonomy. The deadline the receiving side renders is a countdown from the filer's own
``discovered_at``, using this project's crosswalk of the commonly cited reporting
windows; it is not statutory text and not a legal determination.

PlatAtlas holds the record. It does not stop anything, it refuses nothing, and it has no
path back to this robot.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from castor.incidents import (
    REPORTING_DEADLINES_DAYS,
    normalize_severity,
)

logger = logging.getLogger("OpenCastor.PlatAtlasIncident")

#: The schema string carried inside the signed body. The rail side does not key on it
#: (it keys on ``action_class``), but a reader of a raw NDJSON line should be able to
#: tell what the line is without a crosswalk.
INCIDENT_REPORT_SCHEMA = "incident-report/1"

#: The action_class the rail side's closed vocabulary holds for this record
#: (``apps/platatlas-worker/src/action-classes.ts``). It is a member of that set for one
#: reason: so a record carrying it is RECOGNISED rather than bucketed. It appears in no
#: crosswalk row there and lights no incident-response control.
INCIDENT_ACTION_CLASS = "incident.report"

#: The envelope type the rail ingest parses. An incident report IS an agent-action
#: attestation by storage and by ledger on that side, which is what gives it the
#: append-only chain, the delete refusal and the retention carve-out with no new code.
ENVELOPE_TYPE = "agent_action_attestation"

#: Cap on the free-text summary, matched to the receiving side's SUMMARY_MAX. Applied
#: HERE so the bytes that are signed are the bytes that are stored: truncating on the
#: far side would mean the stored summary is not the summary anybody signed.
SUMMARY_MAX = 2000
#: Cap on every id-shaped field, and on each entry of the three list fields, matched to
#: the receiving side's ID_MAX / LIST_ITEM_MAX.
ID_MAX = 256
#: Cap on the number of entries in each list field, matched to the receiving side.
LIST_MAX = 50
#: ``Number.MAX_SAFE_INTEGER``. An integer beyond this cannot survive the far side's
#: ``JSON.parse``, so it is refused here rather than signed into bytes that would not
#: verify there. See ``_assert_canonicalisable`` case 4.
MAX_SAFE_INTEGER = 2**53 - 1

#: Where the robot's attestation identity lives after ``castor up`` / ``castor pair``.
#: The same key the gateway signs its receipts with, which is the point: one robot, one
#: attestation identity, one kid a reader resolves.
ATTESTATION_KEY_ENV = "ROBOT_MD_ATTESTATION_KEY_FILE"
ATTESTATION_KID_ENV = "ROBOT_MD_ATTESTATION_KID"

#: The dotenv files ``castor up`` (``<robot home>/gateway-attestation.env``, home from
#: ``ROBOT_HOME`` or the ``~/robot`` default) and ``castor pair``
#: (``~/.config/opencastor/gateway-attestation.env``) write, each holding BOTH
#: ``ROBOT_MD_ATTESTATION_KEY_FILE`` and ``ROBOT_MD_ATTESTATION_KID``.
#:
#: READING THE FILE RATHER THAN GUESSING THE PATH is the point. The two tools write the
#: key to different places AND derive the kid differently - ``castor up`` uses
#: ``<name>-gw-attest`` and publishes the public half to the RRF stub under exactly that
#: name, ``castor pair`` uses ``gw-<sha256>`` - so a module that guessed a path and then
#: derived a kid could sign with the right key under a kid that resolves at no registry,
#: which is worse than not filing: the far side stores the record with
#: ``event_verdict = 'unresolvable'`` and nobody can check it. The key and its kid are
#: only ever taken from the same source.
ATTESTATION_ENV_FILES = (
    lambda: Path(os.environ.get("ROBOT_HOME", "") or (Path.home() / "robot")) / "gateway-attestation.env",
    lambda: Path.home() / ".config" / "opencastor" / "gateway-attestation.env",
)

#: The two variables ``castor up`` writes into the robot's env when trace shipping is
#: configured. Both must be set for this module to post anything; with neither, the
#: caller prints the signed object and says where to send it.
INGEST_KEY_ENV = "PLATATLAS_INGEST_KEY"
ORG_SLUG_ENV = "PLATATLAS_ORG_SLUG"


class PlatAtlasIncidentError(RuntimeError):
    """A filing could not be built, signed or posted. Carries an operator line."""


# ---------------------------------------------------------------------------
# canonical JSON - the one shared signing preimage
# ---------------------------------------------------------------------------


def _assert_canonicalisable(value: Any, path: str = "$") -> None:
    """Refuse any value where this canonicaliser and the JS one could disagree.

    There are exactly four such cases and all four are refused rather than handled,
    because a signature that verifies on one side and fails on the other is worse than a
    filing that did not go out:

    1. **Floats.** ``String(1.0)`` is ``"1"`` in JS and ``repr(1.0)`` is ``"1.0"`` in
       Python, and the two diverge again on precision. No field of an incident report is
       a real number, so floats are refused outright rather than approximated.
    2. **Non-ASCII object keys.** JS sorts keys by UTF-16 code unit; Python sorts by code
       point. The two orders differ above the BMP. Every key this module writes is ASCII,
       so the check costs nothing and closes the case.
    3. **Surrogates in a string.** ``JSON.stringify`` escapes a lone surrogate;
       ``json.dumps`` does not, and the bytes then differ. Refused.
    4. **Integers outside the JS safe range.** Python has arbitrary-precision integers
       and JS has one number type. ``JSON.parse`` on the far side turns 2**53 + 1 into
       2**53 before its canonicaliser ever sees the value, and any integer at or above
       10**21 comes back in exponential form (``1e+21``), so the bytes recomputed there
       are not the bytes signed here and no care on that side can fix it. Refused, with
       the same reasoning as the floats: no field of an incident report is a large
       number, so nothing real is being turned away.

    Booleans are checked before integers on purpose: ``isinstance(True, int)`` is true in
    Python, and a bool serialised as ``1`` would not match ``true``.

    The matching side of this pair is pinned in the rail repo, in
    ``apps/platatlas-worker/test/pa18-canonical-conformance.test.ts``, which demonstrates
    each of the four divergences in the other direction. Neither file proves the pair on
    its own.
    """
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, float):
        raise PlatAtlasIncidentError(
            f"{path}: a float cannot be canonicalised identically on both sides; "
            "no incident-report/1 field is a real number"
        )
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise PlatAtlasIncidentError(
                f"{path}: the integer {value} is outside the JS safe range "
                f"(+/-{MAX_SAFE_INTEGER}); the far side would parse it back as a "
                "different number and the signature would not verify there"
            )
        return
    if isinstance(value, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise PlatAtlasIncidentError(f"{path}: the string contains a surrogate, which the two canonicalisers escape differently")
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _assert_canonicalisable(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k in value:
            if not isinstance(k, str):
                raise PlatAtlasIncidentError(f"{path}: object keys must be strings")
            if not k.isascii():
                raise PlatAtlasIncidentError(f"{path}.{k}: a non-ASCII object key sorts differently in the two canonicalisers")
            _assert_canonicalisable(value[k], f"{path}.{k}")
        return
    raise PlatAtlasIncidentError(f"{path}: unsupported type {type(value).__name__}")


def _ser(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(_ser(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            json.dumps(k, ensure_ascii=False) + ":" + _ser(value[k]) for k in sorted(value)
        ) + "}"
    raise PlatAtlasIncidentError(f"unsupported type {type(value).__name__}")


def canonical_json_string(body: Any, exclude: str | None = None) -> str:
    """The Ed25519 signing preimage, byte-identical to @rail/rcan's canonicalJsonString.

    Recursive sorted keys, no whitespace, JSON string escaping. ``exclude`` drops one key
    from the TOP LEVEL only, which is how ``envelope_signature`` is removed before the
    signature is computed and, on the far side, before it is checked.

    Every input is validated first (see :func:`_assert_canonicalisable`), so this either
    produces the same bytes the other implementation would or it raises.
    """
    _assert_canonicalisable(body)
    if exclude is not None and isinstance(body, dict):
        body = {k: v for k, v in body.items() if k != exclude}
    return _ser(body)


# ---------------------------------------------------------------------------
# building the record
# ---------------------------------------------------------------------------


def _cap(value: Any, limit: int) -> str:
    """Truncate to ``limit`` as the RECEIVING side counts, which is UTF-16 code units.

    Python slices by code point and JavaScript's ``String.prototype.slice`` counts UTF-16
    units, so a summary of 2000 astral characters (an emoji is one code point and two
    units) passes a code-point cap untouched here and is cut in half over there. The
    stored summary would then not be the summary anybody signed, and an odd boundary
    would leave a lone high surrogate in the stored value, which is precisely what the
    cap exists to prevent. So the cap is applied in the far side's own units, and it
    never splits a surrogate pair: a character that does not fit whole is dropped whole.
    """
    text = str(value or "")
    units = 0
    for i, ch in enumerate(text):
        width = 2 if ord(ch) > 0xFFFF else 1
        if units + width > limit:
            return text[:i]
        units += width
    return text


def _cap_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or item == "":
            continue
        out.append(_cap(item, ID_MAX))
        if len(out) >= LIST_MAX:
            break
    return out


def build_incident_report(
    incident: dict[str, Any],
    *,
    rrn: str,
    kid: str,
    ts: str,
    rmn: str = "",
    reporter: str = "",
    disposition: str = "",
    notified: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    actor_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build the UNSIGNED incident-report/1 object for one incident-log record.

    The returned dict is the exact preimage the signature will cover, minus the
    ``envelope_signature`` block that :func:`sign_incident_report` adds. Every cap is
    applied HERE, before signing, so the bytes that are signed are the bytes that are
    stored on the far side; a cap applied there instead would leave a stored summary that
    nobody signed.

    ``severity`` goes through ``castor.incidents.normalize_severity``, which is the same
    three-category taxonomy with the same two legacy aliases the rail side publishes, so
    the two sides agree about which reporting window a record carries.

    ``reporter`` is a NAMED HUMAN field and it is declared. Nothing here, and nothing on
    the receiving side, establishes that the named person exists or filed this.
    """
    severity = normalize_severity(incident.get("severity"))
    body = {
        "type": ENVELOPE_TYPE,
        "schema": INCIDENT_REPORT_SCHEMA,
        "attestation_id": _cap(incident.get("id"), ID_MAX),
        "action_class": INCIDENT_ACTION_CLASS,
        "actor_id": _cap(rrn, ID_MAX),
        # 'service' is the honest actor class: the filing is made by a robot's software
        # on an operator's behalf. It is never claimed as 'human', which on the far side
        # would put this record in the human column of an oversight count.
        "actor_class": "service",
        "target_system": "opencastor",
        "target_ref": _cap(rrn, ID_MAX),
        "ts": _cap(ts, ID_MAX),
        "incident": {
            "incident_id": _cap(incident.get("id"), ID_MAX),
            # The reporting clock runs from DISCOVERY, never from the event time, which
            # is why these two are separate fields on both sides.
            "discovered_at": _cap(incident.get("discovered_at"), ID_MAX),
            "occurred_at": _cap(incident.get("timestamp"), ID_MAX),
            "severity": severity,
            "affected_rrn": _cap(rrn, ID_MAX),
            "affected_rmn": _cap(rmn, ID_MAX),
            "affected_actor_ids": _cap_list(actor_ids),
            "summary": _cap(incident.get("description"), SUMMARY_MAX),
            "evidence_refs": _cap_list(evidence_refs),
            "reporter": _cap(reporter, ID_MAX),
            "disposition": _cap(disposition, ID_MAX),
            "notified": _cap_list(notified),
        },
    }
    # `kid` is deliberately NOT copied into the incident sub-object. The stop receipt
    # carries its signing kid inside the signed body because the gateway and the robot
    # can be different keys there and the far side publishes the comparison; here there
    # is one key, it is already in envelope_signature.kid, and a second copy would be a
    # signed field nothing on either side reads.
    _assert_canonicalisable(body)
    return body


def reporting_window_days(severity: Any) -> int:
    """Days from discovery this category's window allows, per the project crosswalk."""
    return REPORTING_DEADLINES_DAYS[normalize_severity(severity)]


# ---------------------------------------------------------------------------
# signing
# ---------------------------------------------------------------------------


def load_attestation_kid(key_file: Path) -> str:
    """The kid for this robot's attestation key.

    ``ROBOT_MD_ATTESTATION_KID`` when the env carries one (``castor up`` writes it into
    ``gateway-attestation.env`` beside the key), otherwise the same
    ``gw-<sha256(raw pubkey)[:12]>`` derivation ``castor pair`` uses, so the value is
    stable per key whether or not the env file was sourced.
    """
    env_kid = os.environ.get(ATTESTATION_KID_ENV, "").strip()
    if env_kid:
        return env_kid
    from cryptography.hazmat.primitives import serialization

    priv = serialization.load_pem_private_key(Path(key_file).read_bytes(), password=None)
    raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return f"gw-{hashlib.sha256(raw).hexdigest()[:12]}"


def sign_incident_report(body: dict[str, Any], key_file: Path, kid: str) -> dict[str, Any]:
    """Attach the detached Ed25519 ``envelope_signature`` block.

    The signature covers ``canonical_json_string(body, exclude="envelope_signature")``,
    which is precisely what the receiving side recomputes before it verifies. The block
    itself is never part of its own preimage.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = serialization.load_pem_private_key(Path(key_file).read_bytes(), password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        raise PlatAtlasIncidentError(f"{key_file}: not an Ed25519 private key")
    preimage = canonical_json_string(body, exclude="envelope_signature").encode("utf-8")
    sig = priv.sign(preimage)
    signed = dict(body)
    signed["envelope_signature"] = {
        "alg": "Ed25519",
        "kid": kid,
        "sig": base64.b64encode(sig).decode("ascii"),
    }
    return signed


def ndjson_line(signed: dict[str, Any]) -> str:
    """One RCAN NDJSON line: ``{"event": <the signed object>}``.

    The wrapper is what the rail ingest reads; the value of ``event`` is the object the
    signature covers, carried unchanged. The line is written with sorted keys and no
    spaces so a fixture committed to a repo is stable byte for byte across runs, which is
    what makes the cross-repo fixture test meaningful.
    """
    return json.dumps({"event": signed}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# posting
# ---------------------------------------------------------------------------


#: What an org slug may be. Checked before it is interpolated into the ingest URL,
#: because that URL is where the ingest key is sent: a slug holding "/", "@", "?" or "#"
#: moves the HOST the bearer token goes to ("evil.com/" yields
#: https://evil.com/.platatlas.com/...). The value comes from the robot's own env, so
#: this is a guard against a typo and a stray edit rather than against an attacker, but
#: the cost of the guard is one regex and the cost of the typo is a leaked key.
_ORG_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def ingest_url(org_slug: str) -> str:
    """The org's trace ingest. ``source=rcan`` is required: the record-class parse on the
    far side runs only for that source, so a body posted without it is stored as a trace
    and never becomes an incident record."""
    if not _ORG_SLUG_RE.match(org_slug or ""):
        raise PlatAtlasIncidentError(
            f"{ORG_SLUG_ENV}={org_slug!r} is not an org slug (lower-case letters, digits "
            "and dashes, up to 63 characters). Refusing to build a URL from it, because "
            "that URL is where the ingest key would be sent."
        )
    return f"https://{org_slug}.platatlas.com/api/traces?source=rcan"


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect instead of following it.

    ``urllib``'s default handler forwards the ``Authorization`` header to the new host
    and, on 301/302/303, rewrites the POST to a GET and drops the body. A redirect would
    therefore send the ingest key somewhere this module never chose, file nothing, and
    return a 200 from the redirect target - which the caller would then stamp into the
    local log as a successful filing. All three of those are worse than a failed filing,
    so a 3xx is an error with the location named and nothing is sent twice.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise urllib.error.HTTPError(
            req.full_url, code,
            f"refusing to follow a redirect to {newurl}: the ingest key is in this "
            "request's headers and urllib would carry it to the new host, and a "
            "redirected POST is silently downgraded to a GET with no body",
            headers, fp,
        )


def submit_incident_ndjson(
    body: str,
    org_slug: str,
    ingest_key: str,
    *,
    timeout: float = 30.0,
    opener: Any = None,
) -> dict[str, Any]:
    """POST the NDJSON body to the org's ingest. Returns the parsed JSON response.

    Raises :class:`PlatAtlasIncidentError` with an operator line on any non-2xx or any
    transport failure, so the caller prints a sentence rather than a traceback and
    stamps nothing in the local log.

    ``opener`` is a seam for tests: anything with ``urlopen(request, timeout=...)``.
    """
    url = ingest_url(org_slug)
    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {ingest_key}",
            "Content-Type": "application/x-ndjson",
        },
    )
    # Redirects are refused rather than followed (see _NoRedirects). The default opener
    # would carry the Authorization header to whatever host the redirect names.
    send = opener.urlopen if opener is not None else urllib.request.build_opener(_NoRedirects).open
    try:
        with send(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = getattr(resp, "status", None) or resp.getcode()
    except urllib.error.HTTPError as exc:
        # The error response is a file object and closing it is ours to do; without this
        # every non-2xx leaks a socket for the life of the process.
        try:
            detail = exc.read().decode("utf-8", "replace")[:500] if hasattr(exc, "read") else ""
        except Exception:
            detail = ""
        finally:
            try:
                exc.close()
            except Exception:
                pass
        raise PlatAtlasIncidentError(f"{url} answered {exc.code}: {detail}") from exc
    except Exception as exc:  # transport, DNS, TLS
        raise PlatAtlasIncidentError(f"{url} could not be reached: {exc}") from exc
    # A response object that reports no status at all gets an operator line, not a
    # TypeError out of int(None): this function's whole contract is that the caller
    # prints a sentence rather than a traceback.
    try:
        code = int(status)
    except (TypeError, ValueError):
        raise PlatAtlasIncidentError(
            f"{url} answered with no readable status code, so this filing cannot be "
            f"treated as sent: {raw[:200]}"
        ) from None
    if not (200 <= code < 300):
        raise PlatAtlasIncidentError(f"{url} answered {code}: {raw[:500]}")
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {"status": code, "body": raw[:500]}


def platatlas_env() -> tuple[str, str]:
    """``(org_slug, ingest_key)`` from the environment; empty strings when unset."""
    return (
        os.environ.get(ORG_SLUG_ENV, "").strip(),
        os.environ.get(INGEST_KEY_ENV, "").strip(),
    )


def _read_attestation_env_file() -> dict[str, str]:
    """The first ``gateway-attestation.env`` that exists, parsed. ``{}`` when there is
    none - this module never writes one and never guesses at its contents."""
    for resolve in ATTESTATION_ENV_FILES:
        try:
            path = resolve()
            if not path.is_file():
                continue
            out: dict[str, str] = {}
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
            if ATTESTATION_KEY_ENV in out:
                return out
        except Exception:
            continue
    return {}


def attestation_identity() -> tuple[Path, str]:
    """``(key_file, kid)`` for this robot's attestation identity, taken from ONE source.

    In order: the process environment (which is what the castor systemd unit gives the
    gateway, via ``EnvironmentFile=``), then the ``gateway-attestation.env`` that
    ``castor up`` or ``castor pair`` wrote. The kid returned is the kid that source
    declares; it is derived from the key only when the source gave a key and no kid, and
    that derived value is the ``castor pair`` form. Nothing here invents a path.
    """
    env_path = os.environ.get(ATTESTATION_KEY_ENV, "").strip()
    env_kid = os.environ.get(ATTESTATION_KID_ENV, "").strip()
    if env_path:
        path = Path(env_path).expanduser()
        return path, (env_kid or load_attestation_kid(path))
    from_file = _read_attestation_env_file()
    if from_file:
        path = Path(from_file[ATTESTATION_KEY_ENV]).expanduser()
        return path, (from_file.get(ATTESTATION_KID_ENV, "").strip() or env_kid or load_attestation_kid(path))
    raise PlatAtlasIncidentError(
        "no attestation identity: set "
        f"{ATTESTATION_KEY_ENV} (and {ATTESTATION_KID_ENV}), or run `castor up` or "
        "`castor pair`, which write both into a gateway-attestation.env this command "
        "reads. Refusing to guess a key path, because a filing signed under a kid that "
        "resolves at no registry is stored on the far side as unresolvable and cannot "
        "be checked by anybody."
    )


def attestation_key_path() -> Path:
    """The robot's attestation key file. Kept for callers that want only the path;
    prefer :func:`attestation_identity`, which returns the key and its kid together."""
    return attestation_identity()[0]


def build_signed_lines(
    incidents: list[dict[str, Any]],
    *,
    rrn: str,
    ts: str,
    key_file: Path | None = None,
    kid: str | None = None,
    rmn: str = "",
    reporter: str = "",
    disposition: str = "",
    notified: list[str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Build and sign one incident-report/1 per incident. Returns (objects, ndjson body).

    ``evidence_refs`` is filled from the incident's own log identity: the record is in a
    hash-chained local log and the reference points at it, which is a pointer and not a
    copy. Nothing about that chain is checked by the receiving side, and nothing here
    claims it is: ``castor incidents verify`` says the links hold, which is not the same
    as an outside party verifying the log, because the process that writes these lines
    can rewrite them all.
    """
    if key_file:
        path = Path(key_file)
        signing_kid = kid or load_attestation_kid(path)
    else:
        # The key and the kid come from the SAME source, always. See attestation_identity.
        path, resolved_kid = attestation_identity()
        signing_kid = kid or resolved_kid
    if not path.exists():
        raise PlatAtlasIncidentError(
            f"no attestation key at {path}; run `castor pair` or `castor up` first, "
            f"or set {ATTESTATION_KEY_ENV}"
        )
    objects: list[dict[str, Any]] = []
    lines: list[str] = []
    for inc in incidents:
        body = build_incident_report(
            inc,
            rrn=rrn,
            kid=signing_kid,
            ts=ts,
            rmn=rmn,
            reporter=reporter,
            disposition=disposition,
            notified=notified,
            evidence_refs=[f"castor-incident-log:{inc.get('id', '')}"],
            actor_ids=[],
        )
        signed = sign_incident_report(body, path, signing_kid)
        objects.append(signed)
        lines.append(ndjson_line(signed))
    return objects, "\n".join(lines) + "\n"

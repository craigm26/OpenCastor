"""A small, loud client for the robot-md-gateway ``/v1/invoke`` endpoint.

Every motion this benchmark makes goes through the gateway, so every motion
leaves an Ed25519-signed receipt. That is the whole reason to prefer this body
over a direct serial driver, and it is why this module keeps the *entire*
response for every call rather than unwrapping it to a return value: the
receipt is the artifact, the telemetry is a detail of it.

Wire shape, from Bob's archived receipts and the receiver's ``InvokeEnvelope``:

* request — ``POST {gateway}/v1/invoke``, ``Authorization: Bearer <token>``,
  body ``{msg_id, type, ruri, scope, tool_name, tool_args, manifest_path,
  actuator_name?, nonce?, timestamp_ms?}``. The client sends no key id; it names
  the manifest by path and the gateway echoes back the ``manifest_kid`` it
  verified, which is what :class:`GatewayClient` asserts against.
* allow — ``200 {"ok": true, "manifest_kid", "telemetry": {...}, "outcome":
  {...}, "envelope_signature": {...}}``. Tool output is the top-level
  ``telemetry``; ``outcome`` carries only its hash.
* deny — ``403 {"detail": {"deny": "<code>", "reason": ..., "outcome": ...,
  "envelope_signature": ...}}``. A deny is a *signed promise that nothing
  moved*, and callers turn it into ``SafetyAbort``.
* fault — ``500 {"detail": {"actuator_error": ...}}``, unsigned, and motion
  state is unknown. That is an ``EmbodimentFault``, never a shrug.

The three defensive seams that matter, because the ``arm.move_to`` / ``arm.state``
tools are being written by someone else right now: tool names are configurable,
telemetry field names are looked up through alias lists, and an unrecognised
success body raises instead of returning zeros.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from inspect_robots.errors import ConfigError, EmbodimentFault, SafetyAbort

#: The envelope ``type`` the receiver expects.
ENVELOPE_TYPE = "rcan/v1/invoke"
#: Scope for tools that move the arm, and for tools that only read it.
MOTION_SCOPE = "MANIPULATE"
OBSERVE_SCOPE = "OBSERVE"
#: The RCAN resource identifier Bob's gateway is configured with.
DEFAULT_RURI = "rcan://demo.local/bob"
#: Where the tool output lives in an allow body, in order of preference.
_TELEMETRY_KEYS = ("telemetry", "result", "output", "data")

#: ``(url, body, headers, timeout) -> (status, bytes)``. The seam tests replace.
Transport = Callable[[str, bytes, "dict[str, str]", float], "tuple[int, bytes]"]

#: Deny codes that are worth a specific remedy rather than a bare echo.
_DENY_REMEDIES = {
    "tool_allowlist": (
        "The gateway's operator allowlist does not carry this tool. Add it to "
        "ROBOT_MD_TOOL_ALLOWLIST and ROBOT_MD_TOOL_MIN_TIER in the gateway's policy env "
        "and restart the gateway. Nothing moved."
    ),
    "tool_tier": (
        "This bearer's tier may not call this tool. Use the actuate-tier token, not the "
        "read-only one. Nothing moved."
    ),
    "tier_policy": (
        "This bearer is not allowed an actuation scope — an unknown or missing token "
        "degrades to the anon tier. Check -E token / -E token_env. Nothing moved."
    ),
    "safety_state": "The robot is in a stop/safety state. Clear it at the console. Nothing moved.",
    "manifest_provenance": (
        "The gateway could not verify the manifest signature at the path given. Check "
        "-E manifest_path (it is a path on the ROBOT, not on this machine). Nothing moved."
    ),
    "hitl_required": "This tool needs a human-in-the-loop approval that did not arrive. Nothing moved.",
    "actuator_policy": "The driver itself refused on policy. Nothing moved.",
    "actuator_name_required": (
        "The gateway hosts several actuators and could not tell which one this tool is for. "
        "Pass -E actuator_name=so-arm101. Nothing moved."
    ),
    "unknown_actuator": "No such actuator on this gateway. Check -E actuator_name. Nothing moved.",
}

#: Sub-codes a cartesian motion tool reports under ``detail.telemetry.deny``. These are
#: the driver's own reasons, and they are the ones an operator can actually fix. Nothing
#: is clamped on the far side, so every one of these means the arm stood still.
_MOVE_DENY_REMEDIES = {
    "bad_args": "The tool could not parse the arguments. Check -E move_args against the tool's spelling.",
    "out_of_workspace": (
        "The target is outside the arm's declared workspace box. Move the sheet closer to the "
        "base, or re-teach the calibration if a corner was mis-taught."
    ),
    "unreachable": (
        "The arm's links cannot span to that point. Move the sheet closer to the base and "
        "re-teach the calibration."
    ),
    "joint_limits": "Reaching that point needs a joint past its safe range. Move or rotate the sheet.",
    "frame_disagreement": (
        "The driver's kinematics and the manifest's frame disagree about this point. That is a "
        "robot configuration bug, not a sacpaint one."
    ),
    "unsafe_pose": (
        "The pose needed to hold the tool as the tool constraint demands is outside the measured "
        "safe joint range. On an SO-ARM101 this denies EVERY target, because pointing the tool "
        "straight down needs wrist_flex about +1.47 rad against a safe ceiling of +0.41 rad. "
        "Use -E move_tool=arm.reach_point -E move_args=reach_point and mount the pen on the "
        "wrist at an angle so its tip is near vertical in the reachable pose."
    ),
    "unsafe_start": "The arm is already in a pose it will not move from. Home it at the console first.",
    "ik_provider_mismatch": "The gateway and the driver disagree about which IK provider to use.",
    "no_kinematics": "This actuator has no kinematics loaded, so it cannot solve a cartesian target.",
}


class GatewayDenied(SafetyAbort):
    """The gateway refused the call. Signed, and a promise that the robot did not move."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        receipt: Mapping[str, Any],
        detail_code: str | None = None,
    ) -> None:
        super().__init__(message)
        #: The gate that refused, e.g. ``tool_allowlist`` or ``actuator_policy``.
        self.code = code
        #: The driver's own sub-code under ``detail.telemetry.deny``, when it gave one —
        #: ``unreachable``, ``unsafe_pose``, ``joint_limits``, ... This is the specific
        #: reason a motion tool said no, and the half worth acting on.
        self.detail_code = detail_code
        self.receipt = dict(receipt)


class GatewayFault(EmbodimentFault):
    """The gateway or the driver failed. Motion state may be unknown; a human is needed."""


class GatewayMiss(GatewayFault):
    """The driver ran the motion to completion but did not get within tolerance.

    Distinct from a fault on purpose: the arm is at a known, measured place
    (the driver walks back to the closest point it reached), it just is not
    the place that was asked for. ``error_mm`` is the driver's final error when
    it said so. The pen medium treats this as a fault under ``strict_reach``;
    the virtual medium can carry on from where the arm actually is.
    """

    def __init__(self, message: str, *, error_mm: float | None = None) -> None:
        super().__init__(message)
        self.error_mm = error_mm


_MISS_MARKERS = ("stopped getting closer", "diverging", "ran out of iterations")
_FINAL_ERROR_RE = re.compile(r"final error ([0-9.]+) mm")


def _miss_from(body: Mapping[str, Any], *, tool: str) -> GatewayMiss | None:
    """A ``GatewayMiss`` when the driver's error text is a reach miss, else None."""
    text = str(_detail(body).get("actuator_error") or "")
    if not any(marker in text for marker in _MISS_MARKERS):
        return None
    match = _FINAL_ERROR_RE.search(text)
    error_mm = float(match.group(1)) if match else None
    return GatewayMiss(
        f"the arm did not reach the {tool!r} target"
        + (f" (final error {error_mm} mm)" if error_mm is not None else "")
        + f": {text}",
        error_mm=error_mm,
    )


class InvokeResult:
    """One allowed invocation: its telemetry, its receipt, and where it came from."""

    def __init__(
        self,
        *,
        tool: str,
        args: Mapping[str, Any],
        msg_id: str,
        status: int,
        body: Mapping[str, Any],
        telemetry: Mapping[str, Any],
        elapsed_s: float,
    ) -> None:
        self.tool = tool
        self.args = dict(args)
        self.msg_id = msg_id
        self.status = status
        self.body = dict(body)
        self.telemetry = dict(telemetry)
        self.elapsed_s = elapsed_s

    @property
    def manifest_kid(self) -> str | None:
        """The manifest key id the gateway verified for this call."""
        value = self.body.get("manifest_kid")
        return str(value) if value is not None else None

    @property
    def signature(self) -> Mapping[str, Any] | None:
        """The attestation signature over the outcome, if the gateway signed it."""
        signature = self.body.get("envelope_signature")
        return signature if isinstance(signature, Mapping) else None

    def __repr__(self) -> str:
        return (
            f"InvokeResult(tool={self.tool!r}, msg_id={self.msg_id!r}, kid={self.manifest_kid!r})"
        )


def _urllib_transport(
    url: str, body: bytes, headers: dict[str, str], timeout_s: float
) -> tuple[int, bytes]:
    """Default transport: stdlib POST that returns error bodies instead of raising on 4xx/5xx."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        # A deny is an HTTP error with a body we very much want to read and keep.
        return int(exc.code), exc.read()


def load_pair_payload(path: str | Path) -> dict[str, Any]:
    """Read a pairing payload, the one file that carries endpoint + bearer + manifest path.

    Passing ``-E pair_payload=<path>`` is the short road through setup: it fills
    the gateway URL, the manifest path, the actuate bearer, and the console
    token for the cameras in one flag.
    """
    src = Path(path)
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"no pairing payload at {src}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"pairing payload at {src} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"pairing payload at {src} must be a JSON object")
    return data


class GatewayClient:
    """Invoke gateway tools, keep every receipt, and translate every failure honestly."""

    def __init__(
        self,
        gateway_url: str,
        *,
        token: str | None = None,
        manifest_path: str | None = None,
        manifest_kid: str | None = None,
        ruri: str = DEFAULT_RURI,
        actuator_name: str | None = None,
        timeout_s: float = 30.0,
        transport: Transport | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not gateway_url:
            raise ConfigError("gateway_url is required (e.g. -E gateway_url=http://127.0.0.1:8080)")
        if not manifest_path:
            raise ConfigError(
                "manifest_path is required: the absolute path of ROBOT.md *on the robot*, "
                "e.g. -E manifest_path=/home/<user>/bob/ROBOT.md, or supply it with "
                "-E pair_payload=<pair-payload.json>"
            )
        self.gateway_url = gateway_url.rstrip("/")
        self.invoke_url = f"{self.gateway_url}/v1/invoke"
        self.manifest_path = manifest_path
        self.expected_manifest_kid = manifest_kid
        self.ruri = ruri
        self.actuator_name = actuator_name
        self.timeout_s = float(timeout_s)
        self._token = token
        self._transport: Transport = transport if transport is not None else _urllib_transport
        self._clock: Callable[[], float] = clock if clock is not None else time.time
        #: Every call this client made, allowed or denied, newest last.
        self.receipts: list[dict[str, Any]] = []

    # -- envelope ----------------------------------------------------------

    def build_envelope(self, tool: str, args: Mapping[str, Any], *, scope: str) -> dict[str, Any]:
        """Build one invoke envelope. ``msg_id`` is the replay key, so it is fresh each call."""
        envelope: dict[str, Any] = {
            "msg_id": str(uuid.uuid4()),
            "type": ENVELOPE_TYPE,
            "ruri": self.ruri,
            "scope": scope,
            "tool_name": tool,
            "tool_args": dict(args),
            "manifest_path": self.manifest_path,
            "nonce": uuid.uuid4().hex,
            "timestamp_ms": int(self._clock() * 1000),
        }
        if self.actuator_name:
            envelope["actuator_name"] = self.actuator_name
        return envelope

    # -- invoke ------------------------------------------------------------

    def invoke(
        self, tool: str, args: Mapping[str, Any] | None = None, *, scope: str = MOTION_SCOPE
    ) -> InvokeResult:
        """Call one gateway tool, retaining the receipt whatever the answer is.

        Raises :class:`GatewayDenied` (a ``SafetyAbort``) on a structured refusal
        and :class:`GatewayFault` (an ``EmbodimentFault``) on anything else that
        is not a well-formed allow.
        """
        envelope = self.build_envelope(tool, args or {}, scope=scope)
        payload = json.dumps(envelope).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        started = self._clock()
        try:
            status, raw = self._transport(self.invoke_url, payload, headers, self.timeout_s)
        except (urllib.error.URLError, OSError) as exc:
            self._record(envelope, status=0, body={"transport_error": str(exc)}, elapsed_s=0.0)
            raise GatewayFault(
                f"the gateway at {self.invoke_url} is unreachable ({exc}). The arm's motion "
                f"state after {tool!r} is unknown; check the robot before re-running."
            ) from exc
        elapsed_s = self._clock() - started

        body = self._decode(raw, status=status, tool=tool)
        self._record(envelope, status=status, body=body, elapsed_s=elapsed_s)

        deny = _deny_code(status, body)
        if deny is not None:
            reason = _deny_reason(body)
            detail_code = _deny_detail_code(body)
            # The driver's sub-code is the specific one; the gate's code is the general one.
            label = f"{deny}/{detail_code}" if detail_code else deny
            remedy = (
                _MOVE_DENY_REMEDIES.get(detail_code, "") if detail_code else ""
            ) or _DENY_REMEDIES.get(deny, "Nothing moved.")
            raise GatewayDenied(
                f"gateway denied {tool!r}: {label}"
                + (f" — {reason}" if reason else "")
                + f" {remedy}",
                code=deny,
                detail_code=detail_code,
                receipt=self.receipts[-1],
            )
        if status != 200:
            miss = _miss_from(body, tool=tool)
            if miss is not None:
                raise miss
            raise GatewayFault(_fault_message(status, body, tool=tool, url=self.invoke_url))
        if body.get("ok") is False:
            raise GatewayFault(
                f"the gateway reported ok=false for {tool!r} with no deny code: {body}. "
                "Treat the arm's position as unknown."
            )

        self._check_kid(body, tool=tool)
        telemetry = _extract_telemetry(body)
        if telemetry is None:
            raise GatewayFault(
                f"the gateway allowed {tool!r} but the reply carries no telemetry under any of "
                f"{list(_TELEMETRY_KEYS)} (keys: {sorted(body)}). Refusing to guess what the arm did."
            )
        return InvokeResult(
            tool=tool,
            args=envelope["tool_args"],
            msg_id=envelope["msg_id"],
            status=status,
            body=body,
            telemetry=telemetry,
            elapsed_s=elapsed_s,
        )

    def _decode(self, raw: bytes, *, status: int, tool: str) -> dict[str, Any]:
        """Parse a response body, keeping an unparseable one visible rather than swallowing it."""
        text = raw.decode("utf-8", "replace")
        if not text.strip():
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GatewayFault(
                f"the gateway answered {tool!r} with HTTP {status} and a non-JSON body "
                f"({exc}): {text[:200]!r}"
            ) from exc
        if not isinstance(parsed, dict):
            return {"detail": parsed}
        return parsed

    def _check_kid(self, body: Mapping[str, Any], *, tool: str) -> None:
        """Assert the gateway verified the manifest key id we were told to expect."""
        if self.expected_manifest_kid is None:
            return
        seen = body.get("manifest_kid")
        if seen != self.expected_manifest_kid:
            raise GatewayFault(
                f"manifest key id mismatch on {tool!r}: expected {self.expected_manifest_kid!r}, "
                f"the gateway verified {seen!r}. The receipts would attest to a different "
                "manifest than the one this run claims to be driving."
            )

    def _record(
        self, envelope: Mapping[str, Any], *, status: int, body: Mapping[str, Any], elapsed_s: float
    ) -> None:
        """Retain one receipt. The bearer never enters this record."""
        self.receipts.append(
            {
                "n": len(self.receipts),
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "tool": envelope["tool_name"],
                "args": envelope["tool_args"],
                "msg_id": envelope["msg_id"],
                "scope": envelope["scope"],
                "manifest_path": envelope["manifest_path"],
                "http_status": status,
                "elapsed_s": round(elapsed_s, 4),
                "response": dict(body),
            }
        )

    # -- receipts ----------------------------------------------------------

    def write_receipts(self, path: str | Path) -> Path:
        """Write the retained receipts as JSONL, one call per line, creating parents."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as handle:
            for receipt in self.receipts:
                handle.write(json.dumps(receipt) + "\n")
        return out


# -- response readers --------------------------------------------------------


def _detail(body: Mapping[str, Any]) -> Mapping[str, Any]:
    """FastAPI wraps refusals under ``detail``; unwrap when it is an object."""
    detail = body.get("detail")
    return detail if isinstance(detail, Mapping) else {}


def _deny_code(status: int, body: Mapping[str, Any]) -> str | None:
    """The structured refusal code, from either the top level or FastAPI's ``detail``."""
    for source in (_detail(body), body):
        value = source.get("deny")
        if isinstance(value, str) and value:
            return value
    # Belt and braces for a gateway that grows a different refusal shape.
    if status == 403:
        return "denied"
    return None


def _deny_detail_code(body: Mapping[str, Any]) -> str | None:
    """The motion tool's own refusal sub-code, from ``detail.telemetry.deny``.

    An ``actuator_policy`` deny says only "the driver refused"; this says why —
    ``unreachable``, ``unsafe_pose``, ``joint_limits`` and the rest.
    """
    for source in (_detail(body), body):
        telemetry = source.get("telemetry")
        if isinstance(telemetry, Mapping):
            value = telemetry.get("deny")
            if isinstance(value, str) and value:
                return value
    return None


def _deny_reason(body: Mapping[str, Any]) -> str:
    """The human-readable half of a refusal, if the gateway offered one."""
    for source in (_detail(body), body):
        for key in ("reason", "message", "detail"):
            value = source.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _fault_message(status: int, body: Mapping[str, Any], *, tool: str, url: str) -> str:
    """Turn a non-200, non-deny response into a message an operator can act on."""
    detail = _detail(body)
    actuator_error = detail.get("actuator_error")
    if actuator_error:
        kind = detail.get("actuator_error_kind")
        return (
            f"the driver failed {tool!r}: {actuator_error}"
            + (f" ({kind})" if kind else "")
            + ". This reply is unsigned and the arm's position is unknown — check the robot "
            "before continuing. An out-of-reach cartesian target lands here."
        )
    if status == 422:
        return (
            f"the gateway rejected the {tool!r} envelope as malformed (422): "
            f"{json.dumps(body)[:400]}. That is a bug in this adapter's envelope, not a robot "
            "fault; nothing moved."
        )
    if status == 404:
        return (
            f"the gateway at {url} has no {tool!r} (404): {json.dumps(body)[:300]}. "
            f"On a gateway without arm.move_to yet, point -E move_tool at the tool that does "
            "exist (arm.reach_point) with -E move_args=reach_point."
        )
    return f"the gateway answered {tool!r} with HTTP {status}: {json.dumps(body)[:400]}"


def _extract_telemetry(body: Mapping[str, Any]) -> dict[str, Any] | None:
    """Find the tool's own output, tolerating a gateway that renames the envelope around it."""
    for key in _TELEMETRY_KEYS:
        value = body.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    outcome = body.get("outcome")
    if isinstance(outcome, Mapping):
        for key in _TELEMETRY_KEYS:
            value = outcome.get(key)
            if isinstance(value, Mapping):
                return dict(value)
    return None


def read_eef_mm(telemetry: Mapping[str, Any]) -> tuple[float, float, float] | None:
    """Read the end-effector position in base millimetres, or ``None`` if the tool omits it.

    Accepts ``{"eef_mm": {"x": .., "y": .., "z": ..}}``, ``{"eef_mm": [x, y, z]}``,
    and the ``eef``/``eef_pos_mm``/``tcp_mm`` spellings, because the tool that
    will return this is still being written.
    """
    for key in ("eef_mm", "eef", "eef_pos_mm", "tcp_mm", "position_mm"):
        value = telemetry.get(key)
        if isinstance(value, Mapping):
            try:
                return (float(value["x"]), float(value["y"]), float(value["z"]))
            except (KeyError, TypeError, ValueError):
                continue
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 3:
            try:
                return (float(value[0]), float(value[1]), float(value[2]))
            except (TypeError, ValueError):
                continue
    return None


def read_reached(telemetry: Mapping[str, Any]) -> bool | None:
    """Whether the arm says it arrived. ``None`` when the tool does not report it."""
    for key in ("reached", "arrived", "at_target"):
        value = telemetry.get(key)
        if isinstance(value, bool):
            return value
    return None

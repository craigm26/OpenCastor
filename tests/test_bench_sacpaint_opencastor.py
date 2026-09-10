"""The OpenCastor body, proven against fakes: no arm, no camera, no network beyond loopback.

Two in-process HTTP servers stand in for the rig — one speaking the gateway's
``/v1/invoke`` envelope and receipt shape, one serving frames and tapped canvas
corners. Everything the real robot would do to us (deny a tool, refuse a token,
answer 503 with a cold camera, report ``reached: false``, hand back telemetry
under a different key) is something a test here does to us first.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "inspect_robots", reason="castor bench sacpaint needs pip install 'opencastor[paintbench]'"
)

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import cv2
import numpy as np
import pytest
from inspect_robots.embodiment import Embodiment
from inspect_robots.errors import ConfigError, EmbodimentFault, SafetyAbort
from inspect_robots.scene import Scene
from inspect_robots.types import Action

from castor.bench.sacpaint import calibration as calib
from castor.bench.sacpaint.cameras import (
    CornerFetchError,
    FrameFetchError,
    HttpCornerSource,
    HttpFrameSource,
    parse_corner_flag,
    parse_corners,
)
from castor.bench.sacpaint.embodiment import (
    CANONICAL_FLAG,
    CORNERS_KEY,
    OVERHEAD,
    REFERENCE_CAM,
    OpenCastorEmbodiment,
    opencastor_embodiment,
)
from castor.bench.sacpaint.gateway import GatewayClient, GatewayDenied, GatewayFault
from castor.bench.sacpaint.reference import DEFAULT_REFERENCE, get_spec

MANIFEST = "/robot/ROBOT.md"
KID = "bob-manifest-2026"
TOKEN = "rmg_live_testonly"


# -- fake gateway ------------------------------------------------------------


def allow_body(tool: str, telemetry: dict[str, Any]) -> dict[str, Any]:
    """A 200 body shaped like Bob's archived receipts."""
    return {
        "ok": True,
        "manifest_kid": KID,
        "scope": "MANIPULATE",
        "tool_name": tool,
        "actuator_name": "so-arm101",
        "outcome_kind": "executed",
        "telemetry": telemetry,
        "attestation": "attested",
        "outcome": {"corr_id": "c-1", "rrn": "RRN-000000000011", "status": "ok"},
        "envelope_signature": {"kid": "bob-gw-attest-2026", "alg": "Ed25519", "sig": "AAAA=="},
    }


def deny_body(code: str, reason: str) -> dict[str, Any]:
    """A 403 body, FastAPI-wrapped under ``detail`` exactly as the gateway sends it."""
    return {
        "detail": {
            "deny": code,
            "reason": reason,
            "attestation": "attested",
            "outcome": {"corr_id": "c-2", "status": "denied"},
            "envelope_signature": {"kid": "bob-gw-attest-2026", "alg": "Ed25519", "sig": "BBBB=="},
        }
    }


def _move_telemetry(env: dict[str, Any]) -> dict[str, Any]:
    """Echo the commanded point back as ``eef_mm``, like a tool that arrived."""
    args = env.get("tool_args", {})
    if "target_mm" in args:
        x, y, z = args["target_mm"]
    else:
        x, y, z = args.get("x_mm", 0.0), args.get("y_mm", 0.0), args.get("z_mm", 0.0)
    return {
        "reached": True,
        "eef_mm": {"x": x, "y": y, "z": z},
        "elapsed_s": 0.2,
        "final_positions": {},
    }


class _GatewayHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        envelope = json.loads(self.rfile.read(length) or b"{}")
        server: Any = self.server
        server.calls.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "envelope": envelope,
                "tool": envelope.get("tool_name"),
            }
        )
        rule = server.script.get(envelope.get("tool_name"))
        if isinstance(rule, list):
            rule = rule.pop(0) if rule else None
        if rule is None:
            status, body = (
                200,
                allow_body(envelope.get("tool_name", "?"), _move_telemetry(envelope)),
            )
        elif callable(rule):
            status, body = rule(envelope)
        else:
            status, body = rule
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args: Any) -> None:
        """Silence the default stderr access log."""


class _CameraHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server: Any = self.server
        path = self.path.split("?")[0]
        server.hits.append(path)
        if path in ("/frame.jpg", "/overhead"):
            self._send(200, "image/jpeg", server.jpeg())
        elif path == "/frame.json":
            import base64

            payload = {"image_b64": base64.b64encode(server.jpeg()).decode()}
            self._send(200, "application/json", json.dumps(payload).encode())
        elif path == "/frame-url.json":
            payload = {"image": f"http://127.0.0.1:{server.server_address[1]}/frame.jpg"}
            self._send(200, "application/json", json.dumps(payload).encode())
        elif path == "/frame-empty.json":
            self._send(200, "application/json", json.dumps({"status": "warming up"}).encode())
        elif path == "/prime":
            self._send(200, "application/json", b'{"ok": true}')
        elif path == "/cold":
            self._send(503, "application/json", b'{"detail": "no frame from overhead"}')
        elif path == "/corners":
            payload = {"canvas_corners": [[0.1, 0.05], [0.9, 0.07], [0.92, 0.95], [0.08, 0.93]]}
            self._send(200, "application/json", json.dumps(payload).encode())
        else:
            self._send(404, "application/json", b'{"detail": "no such camera"}')

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        """Silence the default stderr access log."""


def _serve(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def gateway():
    """A fake gateway; ``gateway.script[tool]`` overrides one tool's answer."""
    server = _serve(_GatewayHandler)
    server.calls = []  # type: ignore[attr-defined]
    server.script = {}  # type: ignore[attr-defined]
    server.url = f"http://127.0.0.1:{server.server_address[1]}"  # type: ignore[attr-defined]
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def camera():
    """A fake camera/corner service whose frames change so freshness is provable."""
    server = _serve(_CameraHandler)
    server.hits = []  # type: ignore[attr-defined]
    counter = {"n": 0}

    def jpeg() -> bytes:
        counter["n"] += 1
        frame = np.full((40, 30, 3), 255, dtype=np.uint8)
        frame[0, 0] = (counter["n"], counter["n"], counter["n"])  # a serial number in pixel 0
        ok, buf = cv2.imencode(".jpg", frame)
        assert ok
        return buf.tobytes()

    server.jpeg = jpeg  # type: ignore[attr-defined]
    server.url = f"http://127.0.0.1:{server.server_address[1]}"  # type: ignore[attr-defined]
    yield server
    server.shutdown()
    server.server_close()


# -- calibration fixtures ----------------------------------------------------


def _taught(rotation: np.ndarray, translation: np.ndarray) -> list[tuple[Any, Any]]:
    """Four sheet corners pushed through a known rigid transform."""
    return [
        (canvas, tuple(rotation @ np.asarray(canvas, dtype=float) + translation))
        for canvas in calib.CORNER_CANVAS_MM.values()
    ]


#: Sheet lying flat, rotated a quarter turn about base z and offset — a plausible desk pose.
_ROT_90 = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
_OFFSET = np.array([180.0, -150.0, -95.0])

#: The sheet's real size, read from the reference spec rather than written down here.
#: The canvas has already shrunk once (300x400 to 150x200); nothing below may care again.
CANVAS_W_MM, CANVAS_H_MM = get_spec(DEFAULT_REFERENCE).canvas_mm
CANVAS_W_M, CANVAS_H_M = CANVAS_W_MM / 1000.0, CANVAS_H_MM / 1000.0

#: The bottom-right sheet corner, the far end of canvas +x, in canvas metres.
BOTTOM_RIGHT = np.array([CANVAS_W_M, 0.0, 0.002])
#: A point comfortably inside the sheet, wherever the sheet's edges happen to be.
INSIDE = np.array([0.8 * CANVAS_W_M, 0.85 * CANVAS_H_M, 0.01])


def expect_base_mm(canvas_xyz_m) -> dict[str, float]:
    """The ``arm.move_to`` arguments the fixture calibration must produce for a canvas point.

    Written out from the quarter turn and the offset by hand, not by calling the
    calibration, so the test still fails if the transform silently changes:
    canvas +x becomes base +y, canvas +y becomes base -x, z rides the offset.
    """
    x_m, y_m, z_m = canvas_xyz_m
    return {
        "x_mm": _OFFSET[0] - y_m * 1000.0,
        "y_mm": _OFFSET[1] + x_m * 1000.0,
        "z_mm": _OFFSET[2] + z_m * 1000.0,
    }


@pytest.fixture
def calibration() -> calib.CanvasCalibration:
    """A calibration fitted from perfect taught corners: canvas x becomes base y."""
    return calib.fit(_taught(_ROT_90, _OFFSET))


# -- calibration -------------------------------------------------------------


def test_fit_identity_round_trips_metres_to_millimetres():
    cal = calib.fit(_taught(np.eye(3), np.zeros(3)))
    assert cal.canvas_to_base((0.10, 0.20, 0.005)) == pytest.approx([100.0, 200.0, 5.0])
    assert cal.base_to_canvas([100.0, 200.0, 5.0]) == pytest.approx([0.10, 0.20, 0.005])


def test_fit_recovers_a_known_rotation_and_offset(calibration):
    # Canvas (0.30, 0, 0) is the bottom-right corner; the quarter turn sends it to base +y.
    assert calibration.canvas_to_base((0.30, 0.0, 0.0)) == pytest.approx([180.0, 150.0, -95.0])
    assert calibration.rms_residual_mm == pytest.approx(0.0, abs=1e-9)
    assert calibration.base_to_canvas(
        calibration.canvas_to_base((0.1, 0.2, 0.01))
    ) == pytest.approx([0.1, 0.2, 0.01])


def test_fit_reports_the_residual_of_a_mis_taught_corner():
    pairs = _taught(np.eye(3), np.zeros(3))
    canvas, base = pairs[2]
    pairs[2] = (canvas, (base[0] + 6.0, base[1], base[2]))  # 6 mm out on one corner
    cal = calib.fit(pairs)
    assert cal.max_residual_mm > 1.0
    assert cal.rms_residual_mm > 0.5
    # The fit spreads the error over every corner, but the mis-taught one keeps the worst of it.
    residuals = [p["residual_mm"] for p in cal.points]
    assert residuals[2] == max(residuals)
    assert cal.max_residual_mm == pytest.approx(max(residuals))


def test_fit_needs_three_corners():
    with pytest.raises(calib.CalibrationError, match="at least 3"):
        calib.fit(_taught(np.eye(3), np.zeros(3))[:2])


def test_fit_rejects_collinear_corners():
    pairs = [
        ((0.0, 0.0), (0.0, 0.0, 0.0)),
        ((100.0, 0.0), (100.0, 0.0, 0.0)),
        ((200.0, 0.0), (200.0, 0.0, 0.0)),
    ]
    with pytest.raises(calib.CalibrationError, match="collinear"):
        calib.fit(pairs)


def test_fit_rejects_a_downward_canvas_normal():
    # Mirroring y is what swapping two corner labels does; the pen would dig in on a lift.
    mirrored = np.diag([1.0, -1.0, 1.0]) @ np.eye(3)
    pairs = [
        (canvas, tuple(mirrored @ np.asarray(canvas, dtype=float)))
        for canvas in calib.CORNER_CANVAS_MM.values()
    ]
    with pytest.raises(calib.CalibrationError, match="points down"):
        calib.fit(pairs)
    assert calib.fit(pairs, check_up=False) is not None


def test_calibration_save_load_round_trip(tmp_path, calibration):
    path = calib.save(calibration, tmp_path / "canvas.json")
    loaded = calib.load(path)
    assert loaded.canvas_to_base((0.1, 0.2, 0.01)) == pytest.approx(
        calibration.canvas_to_base((0.1, 0.2, 0.01))
    )
    assert len(loaded.points) == 4


def test_calibration_load_says_how_to_make_one(tmp_path):
    with pytest.raises(calib.CalibrationError, match="calibrate"):
        calib.load(tmp_path / "nope.json")


def test_calibration_rejects_a_hand_edited_rotation(tmp_path, calibration):
    path = calib.save(calibration, tmp_path / "canvas.json")
    data = json.loads(path.read_text())
    data["rotation"][2][2] = 2.0  # scales the canvas normal: no longer a rotation
    path.write_text(json.dumps(data))
    with pytest.raises(calib.CalibrationError, match="proper rotation"):
        calib.load(path)


# -- cameras -----------------------------------------------------------------


def test_frame_source_decodes_a_jpeg(camera):
    source = HttpFrameSource(f"{camera.url}/frame.jpg")
    frame = source.fetch()
    assert frame.shape == (40, 30, 3)
    assert frame.dtype == np.uint8


def test_frame_source_primes_before_fetching(camera):
    source = HttpFrameSource(f"{camera.url}/frame.jpg", prime_url=f"{camera.url}/prime")
    source.fetch()
    assert camera.hits == ["/prime", "/frame.jpg"]


def test_frame_source_reads_base64_json(camera):
    assert HttpFrameSource(f"{camera.url}/frame.json").fetch().shape == (40, 30, 3)


def test_frame_source_follows_an_image_url_in_json(camera):
    assert HttpFrameSource(f"{camera.url}/frame-url.json").fetch().shape == (40, 30, 3)


def test_frame_source_says_what_is_wrong_when_json_has_no_image(camera):
    with pytest.raises(FrameFetchError, match="no image in it"):
        HttpFrameSource(f"{camera.url}/frame-empty.json").fetch()


def test_frame_source_reports_a_cold_camera_with_a_remedy(camera):
    with pytest.raises(FrameFetchError) as excinfo:
        HttpFrameSource(f"{camera.url}/cold", name="overhead").fetch()
    assert "503" in str(excinfo.value)
    assert "cold or absent" in str(excinfo.value)
    assert "/cold" in str(excinfo.value)


def test_frame_source_names_the_url_when_the_stream_is_down():
    # Port 1 on loopback: nothing is listening, and nothing will be.
    with pytest.raises(FrameFetchError) as excinfo:
        HttpFrameSource("http://127.0.0.1:1/overhead", name="overhead").fetch()
    assert "http://127.0.0.1:1/overhead" in str(excinfo.value)
    assert "unreachable" in str(excinfo.value)


def test_frame_source_reports_a_404_with_the_camera_list_command(camera):
    with pytest.raises(FrameFetchError, match="camera/list"):
        HttpFrameSource(f"{camera.url}/nope").fetch()


def test_corner_source_reads_tapped_corners(camera):
    corners = HttpCornerSource(f"{camera.url}/corners").fetch()
    assert corners == ((0.1, 0.05), (0.9, 0.07), (0.92, 0.95), (0.08, 0.93))


def test_corner_parsing_accepts_the_shapes_a_phone_might_post():
    expected = ((0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9))
    assert parse_corners([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]) == expected
    assert parse_corners({"corners": [0.1, 0.1, 0.9, 0.1, 0.9, 0.9, 0.1, 0.9]}) == expected
    assert (
        parse_corners(
            {
                "tl": {"x": 0.1, "y": 0.1},
                "tr": {"x": 0.9, "y": 0.1},
                "br": {"x": 0.9, "y": 0.9},
                "bl": {"x": 0.1, "y": 0.9},
            }
        )
        == expected
    )
    assert parse_corner_flag("0.1,0.1,0.9,0.1,0.9,0.9,0.1,0.9") == expected


def test_corner_parsing_normalises_pixels_only_when_told_the_image_size():
    payload = {
        "corners": [[100, 50], [900, 50], [900, 450], [100, 450]],
        "image_width": 1000,
        "image_height": 500,
    }
    assert parse_corners(payload) == ((0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9))
    with pytest.raises(CornerFetchError, match="image size"):
        parse_corners({"corners": [[100, 50], [900, 50], [900, 450], [100, 450]]})


def test_corner_parsing_rejects_the_wrong_number_of_corners():
    with pytest.raises(CornerFetchError, match="expected 8 numbers"):
        parse_corners([[0.1, 0.1], [0.9, 0.1]])


# -- gateway client ----------------------------------------------------------


def _client(gateway, **kwargs: Any) -> GatewayClient:
    params: dict[str, Any] = {
        "token": TOKEN,
        "manifest_path": MANIFEST,
        "actuator_name": "so-arm101",
    }
    params.update(kwargs)
    return GatewayClient(gateway.url, **params)


def test_client_sends_the_envelope_the_receiver_requires(gateway):
    _client(gateway).invoke("arm.home", {})
    call = gateway.calls[0]
    assert call["path"] == "/v1/invoke"
    assert call["auth"] == f"Bearer {TOKEN}"
    envelope = call["envelope"]
    assert envelope["type"] == "rcan/v1/invoke"
    assert envelope["tool_name"] == "arm.home"
    assert envelope["manifest_path"] == MANIFEST
    assert envelope["actuator_name"] == "so-arm101"
    assert envelope["scope"] == "MANIPULATE"
    assert envelope["msg_id"] and envelope["ruri"]


def test_client_gives_every_call_a_fresh_replay_key(gateway):
    client = _client(gateway)
    client.invoke("arm.home", {})
    client.invoke("arm.home", {})
    assert gateway.calls[0]["envelope"]["msg_id"] != gateway.calls[1]["envelope"]["msg_id"]


def test_client_checks_the_manifest_kid_the_gateway_verified(gateway):
    _client(gateway, manifest_kid=KID).invoke("arm.home", {})  # matches: fine
    with pytest.raises(GatewayFault, match="manifest key id mismatch"):
        _client(gateway, manifest_kid="someone-elses-key").invoke("arm.home", {})


def test_client_turns_a_deny_into_a_safety_abort(gateway):
    gateway.script["arm.move_to"] = (
        403,
        deny_body("tool_allowlist", "tool arm.move_to not allowed"),
    )
    with pytest.raises(GatewayDenied) as excinfo:
        _client(gateway).invoke("arm.move_to", {"x_mm": 1.0})
    assert isinstance(excinfo.value, SafetyAbort)
    assert excinfo.value.code == "tool_allowlist"
    assert "ROBOT_MD_TOOL_ALLOWLIST" in str(excinfo.value)
    assert "Nothing moved" in str(excinfo.value)


def test_client_surfaces_the_drivers_own_deny_sub_code(gateway):
    # actuator_policy alone says only "the driver refused"; detail.telemetry.deny says why.
    body = deny_body("actuator_policy", "target refused by the driver")
    body["detail"]["telemetry"] = {"deny": "unreachable", "target_mm": [640.0, 0.0, -95.0]}
    gateway.script["arm.move_to"] = (403, body)
    with pytest.raises(GatewayDenied) as excinfo:
        _client(gateway).invoke("arm.move_to", {"x_mm": 640.0})
    assert isinstance(excinfo.value, SafetyAbort)
    assert excinfo.value.code == "actuator_policy"
    assert excinfo.value.detail_code == "unreachable"
    assert "actuator_policy/unreachable" in str(excinfo.value)
    assert "Move the sheet closer" in str(excinfo.value)


def test_an_unsafe_pose_deny_points_at_the_tool_that_works(gateway):
    # Bob denies every arm.move_to target this way: the straight-down tool constraint
    # needs wrist_flex ~ +1.47 rad against a measured safe ceiling of +0.41 rad.
    body = deny_body("actuator_policy", "wrist_flex 1.47 rad exceeds the safe range")
    body["detail"]["telemetry"] = {"deny": "unsafe_pose"}
    gateway.script["arm.move_to"] = (403, body)
    with pytest.raises(GatewayDenied) as excinfo:
        _client(gateway).invoke("arm.move_to", {"x_mm": 180.0})
    assert excinfo.value.detail_code == "unsafe_pose"
    assert "-E move_tool=arm.reach_point" in str(excinfo.value)


def test_a_deny_without_a_sub_code_still_reads_well(gateway):
    gateway.script["arm.move_to"] = (403, deny_body("actuator_policy", "refused"))
    with pytest.raises(GatewayDenied) as excinfo:
        _client(gateway).invoke("arm.move_to", {"x_mm": 1.0})
    assert excinfo.value.detail_code is None
    assert "actuator_policy" in str(excinfo.value)
    assert "/" not in str(excinfo.value).split(":")[1].split()[0]


def test_client_turns_a_driver_error_into_a_fault_naming_the_unknown_position(gateway):
    gateway.script["arm.move_to"] = (
        500,
        {
            "detail": {
                "actuator_error": "OutOfRangeError: 640mm is beyond this arm's 340mm reach",
                "actuator_error_kind": None,
            }
        },
    )
    with pytest.raises(GatewayFault, match="position is unknown"):
        _client(gateway).invoke("arm.move_to", {"x_mm": 640.0})


def test_client_explains_a_missing_tool_and_points_at_the_fallback(gateway):
    gateway.script["arm.move_to"] = (404, {"detail": {"reason": "no such tool"}})
    with pytest.raises(GatewayFault, match="reach_point"):
        _client(gateway).invoke("arm.move_to", {"x_mm": 1.0})


def test_client_accepts_telemetry_under_an_alias(gateway):
    gateway.script["arm.state"] = (
        200,
        {"ok": True, "manifest_kid": KID, "result": {"eef_mm": [1, 2, 3]}},
    )
    assert _client(gateway).invoke("arm.state", {}).telemetry == {"eef_mm": [1, 2, 3]}


def test_client_refuses_to_guess_when_a_success_body_carries_no_telemetry(gateway):
    gateway.script["arm.home"] = (200, {"ok": True, "manifest_kid": KID})
    with pytest.raises(GatewayFault, match="Refusing to guess"):
        _client(gateway).invoke("arm.home", {})


def test_client_faults_when_the_gateway_is_unreachable():
    client = GatewayClient("http://127.0.0.1:1", token=TOKEN, manifest_path=MANIFEST)
    with pytest.raises(GatewayFault, match="unreachable"):
        client.invoke("arm.home", {})
    assert client.receipts[0]["response"]["transport_error"]


def test_client_keeps_a_receipt_for_denies_too_and_never_the_bearer(gateway):
    gateway.script["arm.move_to"] = (403, deny_body("tier_policy", "anon may not manipulate"))
    client = _client(gateway)
    client.invoke("arm.home", {})
    with pytest.raises(GatewayDenied):
        client.invoke("arm.move_to", {"x_mm": 1.0})
    assert [r["tool"] for r in client.receipts] == ["arm.home", "arm.move_to"]
    assert client.receipts[1]["http_status"] == 403
    assert client.receipts[1]["response"]["detail"]["envelope_signature"]["sig"] == "BBBB=="
    assert TOKEN not in json.dumps(client.receipts)


def test_client_writes_receipts_as_jsonl(gateway, tmp_path):
    client = _client(gateway)
    client.invoke("arm.home", {})
    client.invoke("arm.home", {})
    path = client.write_receipts(tmp_path / "sub" / "receipts.jsonl")
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["tool"] == "arm.home"


# -- embodiment --------------------------------------------------------------


def _embodiment(gateway, camera, calibration, **kwargs: Any) -> OpenCastorEmbodiment:
    params: dict[str, Any] = {
        "gateway_url": gateway.url,
        "manifest_path": MANIFEST,
        "token": TOKEN,
        "calibration": calibration,
        "overhead_url": f"{camera.url}/overhead",
        "state_tool": None,  # today's gateway has no arm.state; prove the fallback works
        "no_prompt": True,
    }
    params.update(kwargs)
    return OpenCastorEmbodiment(**params)


SCENE = Scene(id="sacpaint/line-v0", instruction="Draw the reference image.")


def test_embodiment_satisfies_the_protocol_and_the_benchmark_contract(gateway, camera, calibration):
    body = _embodiment(gateway, camera, calibration)
    assert isinstance(body, Embodiment)
    space = body.info.action_space
    assert space.shape == (3,)
    assert space.semantics.control_mode == "eef_abs_pose"
    assert space.semantics.dim_labels == ("x", "y", "z")
    assert space.low.tolist() == [0.0, 0.0, 0.0]
    assert space.high.tolist() == pytest.approx([CANVAS_W_M, CANVAS_H_M, 0.05])
    assert "reference_drawing" in body.info.supported_target_kinds
    assert {c.name for c in body.info.observation_space.cameras} == {OVERHEAD, REFERENCE_CAM}
    assert body.info.observation_space.state_keys == {"eef_pos"}
    assert body.info.is_simulated is False


def test_reset_homes_the_arm_and_returns_both_images(gateway, camera, calibration):
    body = _embodiment(gateway, camera, calibration)
    observation = body.reset(SCENE)
    assert [c["tool"] for c in gateway.calls] == ["arm.home"]
    assert set(observation.images) == {OVERHEAD, REFERENCE_CAM}
    assert observation.images[OVERHEAD].shape == (40, 30, 3)
    assert observation.images[REFERENCE_CAM].ndim == 3
    assert observation.state["eef_pos"].shape == (3,)
    assert observation.instruction == SCENE.instruction


def test_step_converts_canvas_metres_to_base_millimetres_through_the_calibration(
    gateway, camera, calibration
):
    body = _embodiment(gateway, camera, calibration)
    body.reset(SCENE)
    body.step(Action(data=BOTTOM_RIGHT))
    move = gateway.calls[-1]
    assert move["tool"] == "arm.move_to"
    # The quarter turn: the sheet's far +x edge becomes base +y, and z rides the offset.
    assert move["envelope"]["tool_args"] == pytest.approx(expect_base_mm(BOTTOM_RIGHT))
    assert body.num_steps == 1


def test_step_sends_speed_only_when_configured(gateway, camera, calibration):
    body = _embodiment(gateway, camera, calibration, speed=0.4)
    body.reset(SCENE)
    body.step(Action(data=np.array([0.1, 0.1, 0.01])))
    assert gateway.calls[-1]["envelope"]["tool_args"]["speed"] == 0.4


def test_step_can_speak_the_reach_point_spelling(gateway, camera, calibration):
    body = _embodiment(
        gateway, camera, calibration, move_tool="arm.reach_point", move_args="reach_point"
    )
    body.reset(SCENE)
    body.step(Action(data=BOTTOM_RIGHT))
    args = gateway.calls[-1]["envelope"]["tool_args"]
    expected = expect_base_mm(BOTTOM_RIGHT)
    assert gateway.calls[-1]["tool"] == "arm.reach_point"
    assert args["target_mm"] == pytest.approx(
        [expected["x_mm"], expected["y_mm"], expected["z_mm"]]
    )
    assert args["tolerance_mm"] == 3.0


def test_step_clamps_a_target_off_the_sheet_before_it_reaches_the_arm(gateway, camera, calibration):
    body = _embodiment(gateway, camera, calibration)
    body.reset(SCENE)
    body.step(Action(data=np.array([9.0, -4.0, 99.0])))
    args = gateway.calls[-1]["envelope"]["tool_args"]
    # Clamped to the sheet's far corner at the ceiling height *before* the transform, so
    # the arm is never asked for a point off the sheet or 99 m in the air.
    assert args == pytest.approx(expect_base_mm([CANVAS_W_M, 0.0, 0.05]))


def test_a_deny_stops_the_run_instead_of_drawing_on(gateway, camera, calibration):
    gateway.script["arm.move_to"] = (403, deny_body("safety_state", "software stop engaged"))
    body = _embodiment(gateway, camera, calibration)
    body.reset(SCENE)
    before = len(gateway.calls)
    with pytest.raises(SafetyAbort, match="safety_state"):
        body.step(Action(data=np.array([0.1, 0.1, 0.002])))
    # Exactly one further call: the refused one. Nothing was retried, nothing continued.
    assert len(gateway.calls) == before + 1
    assert body.receipts[-1]["http_status"] == 403


def test_an_arm_that_did_not_reach_halts_rather_than_drawing_from_nowhere(
    gateway, camera, calibration
):
    gateway.script["arm.move_to"] = (
        200,
        allow_body("arm.move_to", {"reached": False, "eef_mm": {"x": 1, "y": 2, "z": 3}}),
    )
    body = _embodiment(gateway, camera, calibration)
    body.reset(SCENE)
    with pytest.raises(EmbodimentFault, match="did not reach"):
        body.step(Action(data=np.array([0.1, 0.1, 0.002])))


def test_strict_reach_false_lets_a_missed_target_through(gateway, camera, calibration):
    gateway.script["arm.move_to"] = [
        (200, allow_body("arm.move_to", {"reached": False, "eef_mm": {"x": 180, "y": 0, "z": -95}}))
    ]
    body = _embodiment(gateway, camera, calibration, strict_reach=False)
    body.reset(SCENE)
    assert body.step(Action(data=np.array([0.1, 0.1, 0.002]))).terminated is False


def test_state_comes_from_the_telemetry_when_the_tool_reports_the_tip(gateway, camera, calibration):
    body = _embodiment(gateway, camera, calibration)
    body.reset(SCENE)
    result = body.step(Action(data=INSIDE))
    assert result.observation.state["eef_pos"] == pytest.approx(INSIDE)


def test_state_falls_back_to_the_commanded_pose_when_telemetry_omits_the_tip(
    gateway, camera, calibration
):
    gateway.script["arm.move_to"] = (
        200,
        allow_body("arm.move_to", {"reached": True, "elapsed_s": 0.2}),
    )
    body = _embodiment(gateway, camera, calibration)
    body.reset(SCENE)
    result = body.step(Action(data=INSIDE))
    assert result.observation.state["eef_pos"] == pytest.approx(INSIDE)


def test_a_state_tool_that_reports_the_tip_is_used_at_reset(gateway, camera, calibration):
    tip = expect_base_mm(BOTTOM_RIGHT)
    gateway.script["arm.state"] = (
        200,
        allow_body(
            "arm.state",
            {
                "joint_positions_rad": {},
                "eef_mm": {"x": tip["x_mm"], "y": tip["y_mm"], "z": tip["z_mm"]},
                "tool": "pen",
            },
        ),
    )
    body = _embodiment(gateway, camera, calibration, state_tool="arm.state")
    observation = body.reset(SCENE)
    assert [c["tool"] for c in gateway.calls] == ["arm.home", "arm.state"]
    assert gateway.calls[-1]["envelope"]["scope"] == "OBSERVE"
    assert observation.state["eef_pos"] == pytest.approx(BOTTOM_RIGHT)


def test_observe_parked_lifts_clear_and_takes_a_fresh_unrectified_frame(
    gateway, camera, calibration
):
    body = _embodiment(gateway, camera, calibration, park_z=0.03)
    body.reset(SCENE)
    body.step(Action(data=np.array([0.1, 0.1, 0.0])))
    frames_before = len([h for h in camera.hits if h == "/overhead"])

    parked = body.observe_parked()

    lift = gateway.calls[-1]["envelope"]["tool_args"]
    assert lift["z_mm"] == pytest.approx(-95.0 + 30.0)  # park_z = 30 mm above the paper
    assert len([h for h in camera.hits if h == "/overhead"]) == frames_before + 1
    # The frame really is new: pixel 0 carries the fake camera's serial number.
    assert parked.images[OVERHEAD][0, 0, 0] != 0
    assert CANONICAL_FLAG not in parked.extra


def test_observations_never_claim_a_photograph_is_canonical(gateway, camera, calibration):
    body = _embodiment(gateway, camera, calibration)
    assert CANONICAL_FLAG not in body.reset(SCENE).extra
    assert (
        CANONICAL_FLAG not in body.step(Action(data=np.array([0.1, 0.1, 0.01]))).observation.extra
    )


def test_tapped_canvas_corners_ride_on_every_observation(gateway, camera, calibration):
    body = _embodiment(gateway, camera, calibration, corners_url=f"{camera.url}/corners")
    observation = body.reset(SCENE)
    assert observation.extra[CORNERS_KEY] == [[0.1, 0.05], [0.9, 0.07], [0.92, 0.95], [0.08, 0.93]]
    stepped = body.step(Action(data=np.array([0.1, 0.1, 0.01])))
    assert stepped.observation.extra[CORNERS_KEY] == observation.extra[CORNERS_KEY]
    # Fetched once per trial, not once per step.
    assert camera.hits.count("/corners") == 1


def test_canvas_corners_can_be_typed_on_the_command_line(gateway, camera, calibration):
    body = _embodiment(
        gateway, camera, calibration, canvas_corners="0.05,0.05,0.95,0.05,0.95,0.95,0.05,0.95"
    )
    assert body.reset(SCENE).extra[CORNERS_KEY] == [
        [0.05, 0.05],
        [0.95, 0.05],
        [0.95, 0.95],
        [0.05, 0.95],
    ]


def test_no_corners_configured_means_no_corner_key(gateway, camera, calibration):
    body = _embodiment(gateway, camera, calibration)
    assert CORNERS_KEY not in body.reset(SCENE).extra


def test_a_dead_camera_halts_the_eval_instead_of_scoring_a_blank_sheet(
    gateway, camera, calibration
):
    body = _embodiment(gateway, camera, calibration, overhead_url=f"{camera.url}/cold")
    with pytest.raises(EmbodimentFault, match="cold or absent"):
        body.reset(SCENE)


def test_the_reference_camera_serves_the_named_reference(gateway, camera, calibration):
    from castor.bench.sacpaint.reference import reference_image

    body = _embodiment(gateway, camera, calibration)
    served = body.reset(SCENE).images[REFERENCE_CAM]
    assert np.array_equal(served, reference_image())


def test_an_unknown_reference_name_fails_before_any_motion(gateway, camera, calibration):
    with pytest.raises(Exception, match="reference"):
        _embodiment(gateway, camera, calibration, reference="not-a-real-reference")
    assert gateway.calls == []


# -- receipts ----------------------------------------------------------------


def test_every_gateway_call_is_retained_on_the_embodiment(gateway, camera, calibration):
    body = _embodiment(gateway, camera, calibration)
    body.reset(SCENE)
    body.step(Action(data=np.array([0.1, 0.1, 0.002])))
    body.observe_parked()
    assert [r["tool"] for r in body.receipts] == ["arm.home", "arm.move_to", "arm.move_to"]
    assert all(r["response"]["envelope_signature"]["kid"] for r in body.receipts)


def test_on_trial_end_writes_the_receipts_beside_the_eval_log(
    gateway, camera, calibration, tmp_path
):
    body = _embodiment(gateway, camera, calibration)
    body.on_trial_start("sacpaint/line-v0", 2, str(tmp_path), "run-42")
    body.reset(SCENE)
    body.step(Action(data=np.array([0.1, 0.1, 0.002])))

    body.on_trial_end(object(), str(tmp_path), "run-42")

    written = tmp_path / "receipts" / "run-42" / "sacpaint_line-v0-epoch2.jsonl"
    lines = written.read_text().strip().splitlines()
    assert [json.loads(line)["tool"] for line in lines] == ["arm.home", "arm.move_to"]


def test_close_flushes_receipts_when_no_trial_hook_ever_fires(
    gateway, camera, calibration, tmp_path
):
    # The core offers embodiments no trial hooks today, so close() is the guarantee.
    body = _embodiment(gateway, camera, calibration, receipts_dir=str(tmp_path / "keep"))
    body.reset(SCENE)
    body.close()
    written = list((tmp_path / "keep").glob("opencastor-*.jsonl"))
    assert len(written) == 1
    assert json.loads(written[0].read_text().strip())["tool"] == "arm.home"


def test_close_does_not_write_twice(gateway, camera, calibration, tmp_path):
    body = _embodiment(gateway, camera, calibration, receipts_dir=str(tmp_path))
    body.reset(SCENE)
    body.on_trial_end(object(), str(tmp_path / "logs"), "run-1")
    body.close()
    assert list(tmp_path.glob("opencastor-*.jsonl")) == []


def test_close_with_nothing_to_write_is_quiet(gateway, camera, calibration, tmp_path):
    body = _embodiment(gateway, camera, calibration, receipts_dir=str(tmp_path))
    body.close()
    assert list(tmp_path.iterdir()) == []


# -- operator gating ---------------------------------------------------------


def test_the_operator_is_asked_before_the_arm_ever_moves(gateway, camera, calibration):
    order: list[str] = []

    def fake_input(prompt: str) -> str:
        order.append("gate")
        assert "Fresh sheet" in prompt
        return ""

    body = _embodiment(
        gateway, camera, calibration, no_prompt=False, input_fn=fake_input, isatty_fn=lambda: True
    )
    body.reset(SCENE)
    order.append(f"first-call:{gateway.calls[0]['tool']}")
    assert order == ["gate", "first-call:arm.home"]


def test_no_prompt_skips_the_gate(gateway, camera, calibration):
    def explode(prompt: str) -> str:
        raise AssertionError("the gate must not run under -E no_prompt=true")

    body = _embodiment(
        gateway, camera, calibration, no_prompt=True, input_fn=explode, isatty_fn=lambda: True
    )
    body.reset(SCENE)
    assert gateway.calls[0]["tool"] == "arm.home"


def test_no_tty_skips_the_gate_rather_than_hanging(gateway, camera, calibration):
    def explode(prompt: str) -> str:
        raise AssertionError("the gate must not run without a TTY")

    body = _embodiment(
        gateway, camera, calibration, no_prompt=False, input_fn=explode, isatty_fn=lambda: False
    )
    body.reset(SCENE)
    assert gateway.calls[0]["tool"] == "arm.home"


def test_a_dead_stdin_at_the_gate_faults_instead_of_raising_eoferror(gateway, camera, calibration):
    def dead(prompt: str) -> str:
        raise EOFError

    body = _embodiment(
        gateway, camera, calibration, no_prompt=False, input_fn=dead, isatty_fn=lambda: True
    )
    with pytest.raises(EmbodimentFault, match="could not read stdin"):
        body.reset(SCENE)
    assert gateway.calls == []


def test_a_connected_operator_session_owns_the_gate_and_the_output(gateway, camera, calibration):
    class FakeSession:
        def __init__(self) -> None:
            self.gates: list[str] = []
            self.lines: list[str] = []

        def gate(self, prompt: str, *, hint: str | None = None) -> None:
            self.gates.append(prompt)

        def write_line(self, text: str) -> None:
            self.lines.append(text)

        def status(self, line: str | None) -> None:
            pass

    session = FakeSession()

    def explode(prompt: str) -> str:
        raise AssertionError("a connected session owns stdin; the adapter must not read it")

    body = _embodiment(gateway, camera, calibration, no_prompt=False, input_fn=explode)
    body.connect_operator_session(session)
    body.reset(SCENE)
    assert len(session.gates) == 1
    assert "Fresh sheet" in session.gates[0]


def test_bind_task_reports_the_horizon_through_the_session(gateway, camera, calibration):
    class FakeSession:
        def __init__(self) -> None:
            self.lines: list[str] = []

        def write_line(self, text: str) -> None:
            self.lines.append(text)

    class FakeEnvelope:
        name = "sacpaint/line-v0"
        max_steps = 240

    session = FakeSession()
    body = _embodiment(gateway, camera, calibration)
    body.connect_operator_session(session)
    body.bind_task(FakeEnvelope())
    assert any("240 arm moves" in line for line in session.lines)


# -- the registry factory ----------------------------------------------------


def test_the_factory_coerces_the_strings_the_cli_hands_it(gateway, camera, calibration, tmp_path):
    path = calib.save(calibration, tmp_path / "canvas.json")
    body = opencastor_embodiment(
        gateway_url=gateway.url,
        manifest_path=MANIFEST,
        calibration=str(path),
        overhead_url=f"{camera.url}/overhead",
        state_tool=None,
        no_prompt="true",
        strict_reach="false",
        park_z="0.04",
        timeout_s="12",
    )
    assert body.no_prompt is True
    assert body.strict_reach is False
    assert body.info.name == "opencastor"


def test_the_factory_rejects_a_nonsense_boolean(gateway, camera, calibration):
    with pytest.raises(Exception, match="must be true or false"):
        opencastor_embodiment(
            gateway_url=gateway.url,
            manifest_path=MANIFEST,
            calibration=calibration,
            no_prompt="maybe",
        )


def test_a_body_without_a_calibration_refuses_to_be_built(gateway, camera):
    with pytest.raises(calib.CalibrationError, match="cannot know where the sheet is"):
        OpenCastorEmbodiment(gateway_url=gateway.url, manifest_path=MANIFEST)


def test_a_body_without_a_manifest_path_refuses_to_be_built(gateway, calibration):
    with pytest.raises(Exception, match="manifest_path is required"):
        OpenCastorEmbodiment(gateway_url=gateway.url, calibration=calibration)


def test_travel_height_must_be_above_the_pen_down_height(gateway, calibration):
    with pytest.raises(Exception, match="must be above pen_down_z"):
        OpenCastorEmbodiment(
            gateway_url=gateway.url,
            manifest_path=MANIFEST,
            calibration=calibration,
            travel_z=0.001,
        )


def test_a_pairing_payload_supplies_the_endpoint_bearer_and_manifest(
    gateway, camera, calibration, tmp_path
):
    payload = tmp_path / "pair-payload.json"
    payload.write_text(
        json.dumps(
            {
                "v": 1,
                "gateway_url": gateway.url,
                "manifest_path": MANIFEST,
                "bearer": TOKEN,
                "console_url": camera.url,
                "console_token": "console-token",
            }
        )
    )
    body = OpenCastorEmbodiment(
        pair_payload=str(payload),
        calibration=calibration,
        overhead_url=f"{camera.url}/overhead",
        state_tool=None,
        no_prompt=True,
    )
    body.reset(SCENE)
    assert gateway.calls[0]["auth"] == f"Bearer {TOKEN}"
    assert gateway.calls[0]["envelope"]["manifest_path"] == MANIFEST


# --- the virtual medium: no paper, no pen -------------------------------------------


def test_easel_leans_a_sheet_against_the_reach_sphere():
    e = calib.easel(
        distance_mm=335.0, elevation_deg=0.0, azimuth_deg=0.0
    )  # upright, straight ahead
    assert np.isclose(np.linalg.det(e.rotation), 1.0)
    # bottom-left as the arm sees it, the other corners, then a lift off the paper towards the base
    assert e.canvas_to_base([0.0, 0.0, 0.0]) == pytest.approx([335.0, 75.0, -100.0])
    assert e.canvas_to_base([0.15, 0.0, 0.0]) == pytest.approx([335.0, -75.0, -100.0])
    assert e.canvas_to_base([0.0, 0.2, 0.0]) == pytest.approx([335.0, 75.0, 100.0])
    assert e.canvas_to_base([0.075, 0.1, 0.005]) == pytest.approx([330.0, 0.0, 0.0])
    assert e.base_to_canvas([335.0, -75.0, 100.0]) == pytest.approx([0.15, 0.2, 0.0])
    tilted = calib.easel(distance_mm=335.0, elevation_deg=15.0)
    centre = tilted.canvas_to_base([0.075, 0.1, 0.0])
    assert np.linalg.norm(centre) == pytest.approx(335.0) and centre[2] == pytest.approx(
        335.0 * np.sin(np.radians(15))
    )
    # every corner stays inside the 300-370 mm shell the arm can actually reach
    for pt in tilted.points:
        assert 310.0 < np.linalg.norm(pt["base_mm"]) < 360.0
    lifted = tilted.canvas_to_base([0.075, 0.1, 0.005])
    assert np.linalg.norm(lifted) == pytest.approx(330.0)  # +z is towards the base
    # the default is the placement measured to fit Bob: upright, 325 mm ahead, centred at base height
    default = calib.easel()
    assert default.canvas_to_base([0.075, 0.1, 0.0]) == pytest.approx([325.0, 0.0, 0.0])
    assert "virtual easel" in (tilted.note or "")


def test_easel_refuses_a_real_pen(gateway, camera):
    with pytest.raises(ConfigError, match="medium=virtual"):
        _embodiment(gateway, camera, "easel")


def test_unknown_medium_is_refused(gateway, camera, calibration):
    with pytest.raises(ConfigError, match="medium"):
        _embodiment(gateway, camera, calibration, medium="chalk")


def test_virtual_medium_inks_from_telemetry_and_needs_no_camera(gateway, camera):
    body = _embodiment(gateway, camera, "easel", medium="virtual", overhead_url=None)
    assert body.info.name == "opencastor-virtual"
    assert "no paper and no pen" in body.info.docs
    obs = body.reset(SCENE)
    assert obs.extra["medium"] == "virtual" and obs.extra["canonical_canvas"] is True
    assert obs.images["overhead"].shape == (800, 600, 3) and obs.images["overhead"].min() == 255
    assert "scene" not in obs.images
    # travel to the start, drop the pen, draw one horizontal stroke, lift, travel away
    body.step(Action(data=np.array([0.02, 0.10, 0.005])))
    body.step(Action(data=np.array([0.02, 0.10, 0.002])))
    after_drop = body.step(Action(data=np.array([0.12, 0.10, 0.002]))).observation
    lifted = body.step(Action(data=np.array([0.12, 0.10, 0.005]))).observation
    travelled = body.step(Action(data=np.array([0.02, 0.05, 0.005]))).observation
    ink = after_drop.images["overhead"][:, :, 0] < 128
    rows, cols = np.nonzero(ink)
    # a 100 mm stroke at y=100 mm: row 800 - 400 = 400, columns 80..480 (4 px/mm)
    assert rows.min() >= 397 and rows.max() <= 403
    assert cols.min() == pytest.approx(80, abs=3) and cols.max() == pytest.approx(480, abs=3)
    assert (lifted.images["overhead"][:, :, 0] < 128).sum() == ink.sum()
    assert (travelled.images["overhead"][:, :, 0] < 128).sum() == ink.sum()
    # every one of those was a real gateway motion at the easel plane
    moves = [c for c in gateway.calls if c["tool"] == "arm.move_to"]
    assert len(moves) == 5
    args = moves[2]["envelope"]["tool_args"]
    expect = calib.easel().canvas_to_base([0.12, 0.10, 0.002])
    assert [args["x_mm"], args["y_mm"], args["z_mm"]] == pytest.approx(list(expect))
    # a fresh trial starts on a clean sheet
    assert body.reset(SCENE).images["overhead"].min() == 255


def test_virtual_medium_keeps_a_real_camera_as_the_scene_when_one_is_given(gateway, camera):
    body = _embodiment(gateway, camera, "easel", medium="virtual")
    obs = body.reset(SCENE)
    assert obs.images["scene"].ndim == 3 and obs.images["overhead"].min() == 255


def _miss_body(error_mm: float) -> tuple[int, dict[str, Any]]:
    return (
        500,
        {
            "detail": {
                "actuator_error": (
                    "stopped getting closer — the arm may be blocked, or this point may not be reachable at this "
                    f"approach angle (final error {error_mm} mm, history [12.1, 9.3, {error_mm}], warm_started=True)"
                ),
                "actuator_error_kind": None,
            }
        },
    )


def test_client_tells_a_miss_from_a_fault(gateway):
    from castor.bench.sacpaint.gateway import GatewayMiss

    gateway.script["arm.reach_point"] = _miss_body(7.4)
    with pytest.raises(GatewayMiss) as info:
        _client(gateway).invoke("arm.reach_point", {"target_mm": [1, 2, 3]})
    assert info.value.error_mm == pytest.approx(7.4)
    assert "did not reach" in str(info.value)
    gateway.script["arm.reach_point"] = (
        500,
        {"detail": {"actuator_error": "SerialException: bus gone", "actuator_error_kind": None}},
    )
    with pytest.raises(GatewayFault, match="position is unknown") as info2:
        _client(gateway).invoke("arm.reach_point", {"target_mm": [1, 2, 3]})
    assert not isinstance(info2.value, GatewayMiss)


def test_pen_medium_treats_a_miss_as_a_fault_under_strict_reach(gateway, camera, calibration):
    body = _embodiment(gateway, camera, calibration)
    body.reset(SCENE)
    gateway.script["arm.move_to"] = _miss_body(6.2)
    with pytest.raises(EmbodimentFault, match="strict_reach=false"):
        body.step(Action(data=BOTTOM_RIGHT))


def test_virtual_medium_carries_on_from_the_measured_pose_after_a_miss(gateway, camera):
    body = _embodiment(
        gateway,
        camera,
        "easel",
        medium="virtual",
        overhead_url=None,
        strict_reach=False,
        state_tool="arm.state",
    )
    e = calib.easel()
    body.reset(SCENE)
    body.step(Action(data=np.array([0.02, 0.10, 0.002])))
    # the next move misses; arm.state then reports the arm 4 mm short of the target
    short = e.canvas_to_base([0.116, 0.10, 0.002])
    gateway.script["arm.move_to"] = _miss_body(4.0)
    gateway.script["arm.state"] = (
        200,
        allow_body("arm.state", {"eef_mm": {"x": short[0], "y": short[1], "z": short[2]}}),
    )
    obs = body.step(Action(data=np.array([0.12, 0.10, 0.002]))).observation
    assert obs.extra["misses"] == 1
    assert obs.state["eef_pos"] == pytest.approx([0.116, 0.10, 0.002], abs=1e-6)
    ink = obs.images["overhead"][:, :, 0] < 128
    cols = np.nonzero(ink)[1]
    assert cols.min() == pytest.approx(80, abs=3) and cols.max() == pytest.approx(
        464, abs=3
    )  # inked to where it got, not where it was sent

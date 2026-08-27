"""Tests for the Pollen Robotics Microduck driver.

Exercises the real robotd wire contract — JSON-RPC 2.0, one object per line
over a Unix socket — against a fake robotd, so no hardware is required.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time

import pytest

from castor.drivers import get_driver, is_supported_protocol
from castor.drivers.microduck_driver import MicroduckDriver


class FakeRobotd:
    """Minimal robotd stand-in: NDJSON JSON-RPC 2.0 over AF_UNIX."""

    def __init__(self, path: str):
        self.path = path
        self.notifications: list[dict] = []
        self.requests: list[dict] = []
        self.healthy = True
        self._conn: socket.socket | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(path)
        self._srv.listen(1)
        self._srv.settimeout(0.2)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    # -- server ------------------------------------------------------

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self._conn = conn
            conn.settimeout(0.2)
            buf = b""
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    if raw.strip():
                        self._handle(json.loads(raw))
            conn.close()
            self._conn = None

    def _handle(self, msg: dict) -> None:
        method = msg.get("method")
        if "id" not in msg:
            with self._lock:
                self.notifications.append(msg)
            return

        with self._lock:
            self.requests.append(msg)

        if method == "robot.subscribe":
            result = {"networks": ["walk.onnx", "sit.onnx"], "status": "ready"}
        elif method == "robot.health":
            result = {
                "healthy": self.healthy,
                "loop": {"hz": 49.8, "missed": 0},
                "battery": {"volts": 7.62, "percent": 64},
                "imu": "ok",
                "bus": "ok",
            }
        elif method == "robot.safeToRestart":
            result = False
        else:
            result = {}
        self.send({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    # -- helpers -----------------------------------------------------

    def send(self, obj: dict) -> None:
        conn = self._conn
        if conn is None:
            return
        try:
            conn.sendall((json.dumps(obj) + "\n").encode())
        except OSError:
            pass

    def push_state(self, params: dict) -> None:
        """Push a robot.state notification, as robotd does after subscribe."""
        self.send({"jsonrpc": "2.0", "method": "robot.state", "params": params})

    def notifications_for(self, method: str) -> list[dict]:
        with self._lock:
            return [n for n in self.notifications if n.get("method") == method]

    def request_methods(self) -> list[str]:
        with self._lock:
            return [r.get("method") for r in self.requests]

    def close(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


@pytest.fixture
def fake_robotd():
    tmpdir = tempfile.mkdtemp(prefix="microduck-test-")
    path = os.path.join(tmpdir, "robotd.sock")
    server = FakeRobotd(path)
    yield server
    server.close()


@pytest.fixture
def driver(fake_robotd):
    drv = MicroduckDriver(
        {
            "transport": "unix",
            "socket": fake_robotd.path,
            "intent_hz": 50,
            "command_ttl_s": 0.3,
            "rpc_timeout_s": 1.0,
        }
    )
    yield drv
    drv.close()


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ── Mock-mode degradation ─────────────────────────────────────────────────────


def test_mock_mode_when_socket_missing():
    drv = MicroduckDriver({"transport": "unix", "socket": "/nonexistent/robotd.sock"})
    assert drv._mode == "mock"
    drv.move(1.0, 0.0)  # must not raise
    drv.stop()
    drv.close()


def test_mock_mode_on_unknown_transport():
    drv = MicroduckDriver({"transport": "carrier-pigeon"})
    assert drv._mode == "mock"
    assert drv.health_check()["mode"] == "mock"
    drv.close()


def test_ssh_transport_without_host_degrades_to_mock():
    drv = MicroduckDriver({"transport": "ssh"})
    assert drv._mode == "mock"
    assert drv._ssh_proc is None
    drv.close()


# ── Connection & handshake ────────────────────────────────────────────────────


def test_connects_and_subscribes(driver, fake_robotd):
    assert driver._mode == "hardware"
    assert _wait_for(lambda: "robot.subscribe" in fake_robotd.request_methods())
    assert driver.get_policies() == ["walk.onnx", "sit.onnx"]


# ── Velocity intents ──────────────────────────────────────────────────────────


def test_move_scales_into_trunk_frame_twist(driver, fake_robotd):
    driver.move(0.5, -1.0)
    assert _wait_for(lambda: fake_robotd.notifications_for("robot.move"))
    params = fake_robotd.notifications_for("robot.move")[0]["params"]
    assert params["vx"] == pytest.approx(0.5 * driver._max_vx)
    assert params["vy"] == 0.0
    assert params["vyaw"] == pytest.approx(-1.0 * driver._max_vyaw)


def test_move_clamps_out_of_range_input(driver, fake_robotd):
    driver.move(5.0, -5.0)
    assert _wait_for(lambda: fake_robotd.notifications_for("robot.move"))
    params = fake_robotd.notifications_for("robot.move")[0]["params"]
    assert params["vx"] == pytest.approx(driver._max_vx)
    assert params["vyaw"] == pytest.approx(-driver._max_vyaw)


def test_intents_repeat_to_feed_robotd_deadman(driver, fake_robotd):
    driver.move(1.0, 0.0)
    # robotd zeroes the twist if intents stop arriving (~0.5 s), so a single
    # OpenCastor move() must be re-sent by the intent loop.
    assert _wait_for(lambda: len(fake_robotd.notifications_for("robot.move")) >= 4)


def test_command_ttl_zeroes_then_goes_quiet(driver, fake_robotd):
    driver.move(1.0, 0.0)
    assert _wait_for(lambda: fake_robotd.notifications_for("robot.move"))
    time.sleep(driver._command_ttl_s + 0.2)

    moves = fake_robotd.notifications_for("robot.move")
    assert moves[-1]["params"] == {"vx": 0.0, "vy": 0.0, "vyaw": 0.0}

    settled = len(fake_robotd.notifications_for("robot.move"))
    time.sleep(0.3)
    assert len(fake_robotd.notifications_for("robot.move")) == settled


def test_intent_hz_floor_beats_robotd_deadman():
    # A config that would under-feed the deadman is raised to a safe floor.
    drv = MicroduckDriver({"transport": "carrier-pigeon", "intent_hz": 0.5})
    assert drv._intent_hz >= 2.0
    drv.close()


def test_strafe_sets_lateral_velocity(driver, fake_robotd):
    driver.strafe(1.0)
    assert _wait_for(lambda: fake_robotd.notifications_for("robot.move"))
    params = fake_robotd.notifications_for("robot.move")[-1]["params"]
    assert params["vy"] == pytest.approx(driver._max_vy)


# ── Stop / bring-up ───────────────────────────────────────────────────────────


def test_stop_zeroes_twist_and_issues_request(driver, fake_robotd):
    driver.move(1.0, 1.0)
    assert _wait_for(lambda: fake_robotd.notifications_for("robot.move"))
    driver.stop()

    assert "robot.stop" in fake_robotd.request_methods()
    assert fake_robotd.notifications_for("robot.move")[-1]["params"] == {
        "vx": 0.0,
        "vy": 0.0,
        "vyaw": 0.0,
    }


def test_init_relax_enable_are_requests(driver, fake_robotd):
    driver.init()
    driver.enable(True)
    driver.relax()
    methods = fake_robotd.request_methods()
    assert "robot.init" in methods
    assert "robot.enable" in methods
    assert "robot.relax" in methods


def test_no_auto_init_by_default(driver, fake_robotd):
    # The duck deliberately does not move on process start.
    assert "robot.init" not in fake_robotd.request_methods()


# ── Head intents ──────────────────────────────────────────────────────────────


def test_head_sends_four_joint_angles(driver, fake_robotd):
    driver.head(neck_pitch=0.35, head_pitch=0.35, head_yaw=0.1, head_roll=0.0)
    assert _wait_for(lambda: fake_robotd.notifications_for("robot.head"))
    params = fake_robotd.notifications_for("robot.head")[0]["params"]
    assert set(params) == {"neck_pitch", "head_pitch", "head_yaw", "head_roll"}
    assert params["neck_pitch"] == pytest.approx(0.35)


# ── Telemetry ─────────────────────────────────────────────────────────────────


def test_state_notifications_are_cached(driver, fake_robotd):
    assert _wait_for(lambda: "robot.subscribe" in fake_robotd.request_methods())
    fake_robotd.push_state(
        {
            "t": 1234.567,
            "move": {
                "requested": [0.4, 0, 0],
                "applied": [0.15, 0, 0],
                "limited_by": ["max_velocity"],
            },
            "policy": "walk",
            "safety": {"fallen": False, "limp": False},
            "battery": {"volts": 7.62, "percent": 64},
            "odom": {"position": [1.0, 2.0], "yaw": 0.5},
        }
    )
    assert _wait_for(lambda: driver.get_state().get("policy") == "walk")
    assert driver.get_battery()["percent"] == 64
    assert driver.get_odometry()["yaw"] == 0.5
    assert driver.get_state()["move"]["limited_by"] == ["max_velocity"]


def test_health_check_maps_robotd_health(driver):
    health = driver.health_check()
    assert health["ok"] is True
    assert health["mode"] == "hardware"
    assert health["error"] is None
    assert health["battery"]["percent"] == 64
    assert health["loop"]["hz"] == pytest.approx(49.8)


def test_health_check_reports_unhealthy(driver, fake_robotd):
    fake_robotd.healthy = False
    health = driver.health_check()
    assert health["ok"] is False
    assert "unhealthy" in health["error"]


def test_safe_to_restart_is_false_while_driving(driver):
    assert driver.safe_to_restart() is False


def test_call_escape_hatch(driver, fake_robotd):
    driver.call("robot.modelApi")
    assert "robot.modelApi" in fake_robotd.request_methods()


# ── SafetyLayer routing ───────────────────────────────────────────────────────


def test_move_is_blocked_by_safety_layer(driver, fake_robotd):
    class DenyingSafetyLayer:
        def write(self, path, data, principal=None):
            return False

        def estop(self, principal=None):
            pass

    driver.set_safety_layer(DenyingSafetyLayer())
    driver.move(1.0, 0.0)
    time.sleep(0.15)
    assert fake_robotd.notifications_for("robot.move") == []


def test_safety_stop_halts_hardware(driver, fake_robotd):
    class RecordingSafetyLayer:
        def __init__(self):
            self.estopped = False

        def write(self, path, data, principal=None):
            return True

        def estop(self, principal=None):
            self.estopped = True

    layer = RecordingSafetyLayer()
    driver.set_safety_layer(layer)
    driver.safety_stop()
    assert layer.estopped is True
    assert "robot.stop" in fake_robotd.request_methods()


# ── Registry ──────────────────────────────────────────────────────────────────


def test_protocol_is_supported():
    assert is_supported_protocol("microduck")
    assert is_supported_protocol("MicroDuck")


def test_get_driver_returns_microduck(fake_robotd):
    drv = get_driver(
        {"drivers": [{"id": "duck", "protocol": "microduck", "socket": fake_robotd.path}]}
    )
    assert isinstance(drv, MicroduckDriver)
    drv.close()


# ── Teardown ──────────────────────────────────────────────────────────────────


def test_close_is_idempotent(fake_robotd):
    drv = MicroduckDriver({"transport": "unix", "socket": fake_robotd.path})
    drv.close()
    drv.close()
    assert drv._mode == "mock"


# ── Skills, voice, mouth, pose ────────────────────────────────────────────────


def test_skills_are_answered_requests(driver, fake_robotd):
    driver.ground_pick()
    driver.kick(left=True)
    driver.kick(left=False)
    driver.sit_toggle()
    driver.roulade()

    skills = [r["params"]["skill"] for r in fake_robotd.requests if r.get("method") == "robot.do"]
    assert skills == ["ground_pick", "kick_left", "kick_right", "sit_toggle", "roulade"]


def test_an_unknown_skill_is_refused_before_it_reaches_the_wire(driver, fake_robotd):
    with pytest.raises(ValueError, match="unknown skill"):
        driver.do_skill("backflip")
    assert "robot.do" not in fake_robotd.request_methods()


def test_quack_plays_the_chirp(driver, fake_robotd):
    driver.quack()
    sounds = [r["params"] for r in fake_robotd.requests if r.get("method") == "robot.sound"]
    assert sounds == [{"tag": "chirp"}]


def test_the_held_ride_carries_its_hold_flag(driver, fake_robotd):
    driver.sound("wheee", hold=True)
    driver.sound("wheee", hold=False)
    sounds = [r["params"] for r in fake_robotd.requests if r.get("method") == "robot.sound"]
    assert sounds == [{"tag": "wheee", "hold": True}, {"tag": "wheee", "hold": False}]


def test_an_unknown_sound_is_refused(driver):
    with pytest.raises(ValueError, match="unknown sound"):
        driver.sound("honk")


def test_the_mouth_is_a_held_intent_that_repeats(driver, fake_robotd):
    driver.mouth(0.8)
    assert _wait_for(lambda: len(fake_robotd.notifications_for("robot.mouth")) >= 3)
    assert fake_robotd.notifications_for("robot.mouth")[0]["params"] == {"open": 0.8}


def test_the_mouth_clamps_to_its_fraction(driver, fake_robotd):
    driver.mouth(5.0)
    assert _wait_for(lambda: fake_robotd.notifications_for("robot.mouth"))
    assert fake_robotd.notifications_for("robot.mouth")[0]["params"] == {"open": 1.0}


def test_pose_is_held_inside_the_trained_envelope(driver, fake_robotd):
    driver.pose(z=-0.5, roll=3.0, pitch=-3.0)
    assert _wait_for(lambda: fake_robotd.notifications_for("robot.pose"))
    params = fake_robotd.notifications_for("robot.pose")[0]["params"]
    assert params == {"z": -0.025, "roll": 0.26, "pitch": -0.26, "active": True}


def test_an_expired_pose_snaps_the_body_back_rather_than_leaving_it_leaning(driver, fake_robotd):
    driver.pose(z=-0.02)
    assert _wait_for(lambda: fake_robotd.notifications_for("robot.pose"))
    time.sleep(driver._command_ttl_s + 0.2)
    assert fake_robotd.notifications_for("robot.pose")[-1]["params"]["active"] is False


def test_look_at_uses_robotd_ik_not_local_trigonometry(driver, fake_robotd):
    driver.look_at(0.5, 0.25, 0.1, neck_pitch=0.2)
    assert _wait_for(lambda: fake_robotd.notifications_for("robot.look"))
    assert fake_robotd.notifications_for("robot.look")[-1]["params"] == {
        "x": 0.5, "y": 0.25, "z": 0.1, "neck_pitch": 0.2,
    }
    assert fake_robotd.notifications_for("robot.head") == []


def test_theremin_and_shutdown_are_requests(driver, fake_robotd):
    driver.theremin(True)
    driver.shutdown()
    methods = fake_robotd.request_methods()
    assert "robot.theremin" in methods
    assert "robot.shutdown" in methods


def test_stop_releases_every_held_slot(driver, fake_robotd):
    driver.mouth(1.0)
    driver.pose(z=-0.02)
    driver.move(1.0, 0.0)
    driver.stop()
    assert driver._mouth is None and driver._pose is None

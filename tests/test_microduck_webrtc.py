"""Tests for the Microduck ``transport: webrtc``.

Everything here is pinned against the Pollen tree rather than against what the
implementation happens to do.  The transcript below is the exchange
``mediad/webclient/index.html`` drives against ``mediad``'s in-process
signalling server, and the tests assert this driver puts the **same bytes** on
the wire in the same order.  A guessed message shape on this path produces
silence rather than an error, which is why the console's own comment says its
shapes were "read off net/webrtc/protocol rather than guessed".

Sources, all in ``pollen-robotics/microduck``:

- ``docs/design/remote-webrtc.md`` §7 "The signalling protocol, for whoever
  writes the bridge" — the frame table.
- ``mediad/webclient/index.html:772-885`` — ``onSignalling``, the switch this
  state machine mirrors.
- ``mediad/src/main.rs:28-36`` — ``--host 0.0.0.0``, ``--port 8443``.
- ``mediad/src/pipeline.rs:1612-1613`` — the robot creates the ``control``
  channel; ``:1624-1651`` — it is a *string* channel.
- ``mediad/src/session.rs:110`` — the robot trims each frame, so one JSON
  object per message with no newline.
- ``duck-ipc-proto/src/lib.rs:304`` — ``API_VERSION = 25``.
- ``robotd-params/src/lib.rs:1608`` — ``deadman_ms: 500``.

No hardware, no duck, no network beyond loopback.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from castor.drivers.microduck_webrtc import (
    API_VERSION,
    CONTROL_CHANNEL_LABEL,
    DEFAULT_SIGNALLING_PORT,
    HANDSHAKE_ID,
    ROBOTD_DEADMAN_S,
    NoProducerError,
    SignallingError,
    SignallingProtocol,
    WebRTCUnavailable,
    _ChannelSocket,
    require_webrtc,
)

# ---------------------------------------------------------------------------
# The transcript, byte for byte
# ---------------------------------------------------------------------------

#: server → peer, in the order ``mediad``'s signalling server sends them.
WELCOME = '{"type":"welcome","peerId":"consumer-7"}'
LIST_ONE = (
    '{"type":"list","producers":[{"id":"producer-1","meta":'
    '{"name":"duck-9f21","serial":"9f21c0de","release":"0.9.1","api_version":"25"}}]}'
)
SESSION_STARTED = '{"type":"sessionStarted","peerId":"producer-1","sessionId":"s-1"}'
OFFER = '{"type":"peer","sessionId":"s-1","sdp":{"type":"offer","sdp":"v=0\\r\\n"}}'
REMOTE_ICE = (
    '{"type":"peer","sessionId":"s-1","ice":'
    '{"candidate":"candidate:1 1 UDP 2013266431 192.168.1.42 45678 typ host",'
    '"sdpMLineIndex":0}}'
)
END_SESSION = '{"type":"endSession","sessionId":"s-1","reason":"producer gone"}'

#: peer → server, exactly what this driver must send.
EXPECT_LIST = '{"type":"list"}'
EXPECT_START = '{"type":"startSession","peerId":"producer-1"}'
EXPECT_ANSWER = '{"type":"peer","sessionId":"s-1","sdp":{"type":"answer","sdp":"v=0\\r\\n"}}'
EXPECT_ENDSESSION = '{"type":"endSession","sessionId":"s-1"}'


def _dump(frame: dict) -> str:
    return json.dumps(frame, separators=(",", ":"))


# ---------------------------------------------------------------------------
# The signalling state machine
# ---------------------------------------------------------------------------


def test_full_exchange_is_byte_for_byte():
    """Replay the console's exchange and pin every frame we emit."""
    proto = SignallingProtocol()
    sent: list[str] = []

    out, event = proto.on_message(json.loads(WELCOME))
    sent += [_dump(f) for f in out]
    assert event is None
    assert proto.peer_id == "consumer-7"

    out, event = proto.on_message(json.loads(LIST_ONE))
    sent += [_dump(f) for f in out]
    assert event[0] == "producer"
    assert event[1]["name"] == "duck-9f21"

    out, event = proto.on_message(json.loads(SESSION_STARTED))
    sent += [_dump(f) for f in out]
    assert event == ("session", "s-1")
    assert proto.starting is False

    out, event = proto.on_message(json.loads(OFFER))
    sent += [_dump(f) for f in out]
    assert event == ("offer", "v=0\r\n")

    # The answer is ours to build once the SDP stack has one.
    sent.append(_dump(proto.answer_frame("v=0\r\n")))

    assert sent == [EXPECT_LIST, EXPECT_START, EXPECT_ANSWER]


def test_list_takes_no_fields():
    """``index.html``: "The server assigns us an id ... `list` takes no fields"."""
    assert _dump(SignallingProtocol.list_frame()) == '{"type":"list"}'


def test_start_session_carries_no_offer():
    """The producer offers and we answer, so ``startSession`` has no ``offer``."""
    frame = SignallingProtocol.start_session_frame("producer-1")
    assert "offer" not in frame
    assert _dump(frame) == EXPECT_START


def test_a_second_listing_does_not_open_a_second_session():
    """The service answers a ``list`` twice over; the ``starting`` guard is why."""
    proto = SignallingProtocol()
    proto.on_message(json.loads(WELCOME))
    first, _ = proto.on_message(json.loads(LIST_ONE))
    second, event = proto.on_message(json.loads(LIST_ONE))
    assert [_dump(f) for f in first] == [EXPECT_START]
    assert second == []
    assert event is None


def test_a_listing_after_a_session_exists_is_ignored():
    proto = SignallingProtocol()
    proto.on_message(json.loads(WELCOME))
    proto.on_message(json.loads(LIST_ONE))
    proto.on_message(json.loads(SESSION_STARTED))
    out, event = proto.on_message(json.loads(LIST_ONE))
    assert out == []
    assert event is None


def test_end_session_clears_both_session_and_wanted():
    """A stale sessionId would ride the next ``peer``; a set ``wanted`` would re-ask."""
    proto = SignallingProtocol()
    proto.on_message(json.loads(WELCOME))
    proto.on_message(json.loads(LIST_ONE))
    proto.on_message(json.loads(SESSION_STARTED))
    out, event = proto.on_message(json.loads(END_SESSION))
    assert out == []
    assert event == ("closed", "producer gone")
    assert proto.session_id is None
    assert proto.wanted is False
    # And a listing after that opens nothing.
    assert proto.on_message(json.loads(LIST_ONE)) == ([], None)


def test_end_session_frame_shape():
    proto = SignallingProtocol()
    assert proto.end_session_frame() is None  # nothing to end
    proto.session_id = "s-1"
    assert _dump(proto.end_session_frame()) == EXPECT_ENDSESSION


def test_ice_frame_shape():
    """Half of the pair whose shape produces silence when it is wrong."""
    proto = SignallingProtocol()
    proto.session_id = "s-1"
    frame = proto.ice_frame("candidate:1 1 UDP 2013266431 10.0.0.1 4444 typ host", 0)
    assert _dump(frame) == (
        '{"type":"peer","sessionId":"s-1","ice":'
        '{"candidate":"candidate:1 1 UDP 2013266431 10.0.0.1 4444 typ host",'
        '"sdpMLineIndex":0}}'
    )


def test_remote_ice_is_surfaced_verbatim():
    proto = SignallingProtocol()
    proto.session_id = "s-1"
    out, event = proto.on_message(json.loads(REMOTE_ICE))
    assert out == []
    assert event[0] == "ice"
    assert event[1]["sdpMLineIndex"] == 0
    assert event[1]["candidate"].startswith("candidate:1 1 UDP")


def test_empty_listing_names_the_pipeline():
    proto = SignallingProtocol()
    with pytest.raises(NoProducerError) as exc:
        proto.on_message(json.loads('{"type":"list","producers":[]}'))
    assert "journalctl -u mediad" in str(exc.value)


def test_server_error_is_raised_with_its_details():
    proto = SignallingProtocol()
    with pytest.raises(SignallingError) as exc:
        proto.on_message(json.loads('{"type":"error","details":"peer not found"}'))
    assert "peer not found" in str(exc.value)


def test_session_rejected_stops_asking():
    proto = SignallingProtocol()
    out, event = proto.on_message(
        json.loads('{"type":"sessionRejected","reason":"robot_busy","activeApp":"padd"}')
    )
    assert out == []
    assert event[0] == "closed"
    assert "robot_busy" in event[1] and "padd" in event[1]
    assert proto.wanted is False


def test_peer_status_changed_is_quiet_on_a_lan():
    proto = SignallingProtocol()
    assert proto.on_message(json.loads('{"type":"peerStatusChanged"}')) == ([], None)


def test_an_answer_where_an_offer_belongs_is_refused():
    proto = SignallingProtocol()
    with pytest.raises(SignallingError):
        proto.on_message(
            json.loads('{"type":"peer","sessionId":"s-1","sdp":{"type":"answer","sdp":"v=0"}}')
        )


def test_a_named_robot_is_chosen_from_two_producers():
    two = json.loads(
        '{"type":"list","producers":['
        '{"id":"p-a","meta":{"name":"duck-aaaa"}},'
        '{"id":"p-b","meta":{"name":"duck-bbbb"}}]}'
    )
    proto = SignallingProtocol(robot="duck-bbbb")
    out, _ = proto.on_message(two)
    assert _dump(out[0]) == '{"type":"startSession","peerId":"p-b"}'

    proto = SignallingProtocol()  # no preference: the first, as the console does
    out, _ = proto.on_message(two)
    assert _dump(out[0]) == '{"type":"startSession","peerId":"p-a"}'

    proto = SignallingProtocol(robot="duck-cccc")
    with pytest.raises(NoProducerError) as exc:
        proto.on_message(two)
    assert "duck-aaaa" in str(exc.value) and "duck-bbbb" in str(exc.value)


# ---------------------------------------------------------------------------
# The control channel's framing
# ---------------------------------------------------------------------------


class FakeDataChannel:
    """A ``control`` datachannel that records strings instead of sending them."""

    label = CONTROL_CHANNEL_LABEL

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)


def _socket_pair() -> tuple[_ChannelSocket, FakeDataChannel]:
    channel = FakeDataChannel()
    sock = _ChannelSocket(send=channel.send, on_close=lambda: None)
    sock.settimeout(0.5)
    return sock, channel


def test_ndjson_becomes_one_message_per_object_with_no_newline():
    """``session.rs:110`` trims; ``pipeline.rs:1613`` is a string channel."""
    sock, channel = _socket_pair()
    sock.sendall(b'{"jsonrpc":"2.0","method":"robot.move"}\n')
    assert channel.sent == ['{"jsonrpc":"2.0","method":"robot.move"}']
    assert "\n" not in channel.sent[0]


def test_two_objects_in_one_write_become_two_messages():
    sock, channel = _socket_pair()
    sock.sendall(b'{"a":1}\n{"b":2}\n')
    assert channel.sent == ['{"a":1}', '{"b":2}']


def test_a_partial_write_is_held_until_its_newline():
    sock, channel = _socket_pair()
    sock.sendall(b'{"jsonrpc":"2.0",')
    assert channel.sent == []
    sock.sendall(b'"method":"robot.stop"}\n')
    assert channel.sent == ['{"jsonrpc":"2.0","method":"robot.stop"}']


def test_inbound_messages_are_re_terminated_for_the_ndjson_reader():
    sock, _ = _socket_pair()
    sock.feed('{"jsonrpc":"2.0","id":1,"result":{}}')
    assert sock.recv() == b'{"jsonrpc":"2.0","id":1,"result":{}}\n'


def test_recv_times_out_the_way_the_driver_expects():
    sock, _ = _socket_pair()
    sock.settimeout(0.05)
    with pytest.raises(TimeoutError):
        sock.recv()


def test_a_closed_channel_reads_as_eof_and_refuses_writes():
    sock, _ = _socket_pair()
    sock.mark_closed()
    assert sock.recv() == b""
    with pytest.raises(OSError):
        sock.sendall(b'{"a":1}\n')


# ---------------------------------------------------------------------------
# The missing extra
# ---------------------------------------------------------------------------


def test_require_webrtc_names_the_install_line(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _no_aiortc(name, *args, **kwargs):
        if name in ("aiortc", "websockets"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_aiortc)
    with pytest.raises(WebRTCUnavailable) as exc:
        require_webrtc()
    text = str(exc.value)
    assert "pip install 'opencastor[microduck-webrtc]'" in text
    assert "transport: ssh" in text  # the escape hatch is named


def test_the_driver_refuses_to_pretend_when_the_extra_is_missing(monkeypatch):
    """A missing extra is a config error, not a duck that is off."""
    from castor.drivers import microduck_webrtc
    from castor.drivers.microduck_driver import MicroduckDriver

    def _boom(_config):
        raise WebRTCUnavailable("nope")

    monkeypatch.setattr(microduck_webrtc, "connect_webrtc", _boom)
    with pytest.raises(WebRTCUnavailable):
        MicroduckDriver({"transport": "webrtc", "host": "duck.local"})


# ---------------------------------------------------------------------------
# The driver, over a fake control channel
# ---------------------------------------------------------------------------


class FakeDuckOverChannel:
    """A robotd-shaped responder wired to a :class:`_ChannelSocket`.

    Replies to requests, records notifications.  Speaks one JSON object per
    message with no newline, the way ``mediad`` does.
    """

    def __init__(self) -> None:
        self.notifications: list[dict] = []
        self.requests: list[dict] = []
        self.sock = _ChannelSocket(send=self._on_client_message, on_close=lambda: None)

    def _on_client_message(self, text: str) -> None:
        assert "\n" not in text, "control frames must not be newline-framed"
        msg = json.loads(text)
        if msg.get("id") is None:
            self.notifications.append(msg)
            return
        self.requests.append(msg)
        method = msg.get("method")
        if method == "hello":
            result = {"api_version": API_VERSION, "daemon_version": "0.9.1", "revision": None}
        elif method == "robot.subscribe":
            # The real keys, from duck-ipc-proto's SubscribeResult — no "networks".
            result = {
                "accepted": True,
                "walk": "walk_v3",
                "stand": "stand_v1",
                "unavailable": [],
                "sitstand": None,
                "ground_pick": None,
                "skills": [],
            }
        elif method == "robot.health":
            result = {"control_loop": {"target_hz": 50.0, "achieved_hz": 49.8, "missed": 0}}
        else:
            result = {}
        self.sock.feed(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}))


def _driver_over(fake: FakeDuckOverChannel, **config):
    import castor.drivers.microduck_webrtc as webrtc_mod
    from castor.drivers.microduck_driver import MicroduckDriver

    saved = webrtc_mod.connect_webrtc
    webrtc_mod.connect_webrtc = lambda _cfg: fake.sock
    try:
        return MicroduckDriver({"transport": "webrtc", "host": "duck.local", **config})
    finally:
        webrtc_mod.connect_webrtc = saved


def test_driver_connects_over_the_channel_and_subscribes():
    fake = FakeDuckOverChannel()
    driver = _driver_over(fake)
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and len(fake.requests) < 1:
            time.sleep(0.02)
        assert driver._mode == "hardware"
        assert [r["method"] for r in fake.requests] == ["robot.subscribe"]
        assert fake.requests[0]["jsonrpc"] == "2.0"
    finally:
        driver.close()


def test_a_move_crosses_the_channel_as_one_unframed_object():
    fake = FakeDuckOverChannel()
    driver = _driver_over(fake, max_vx=0.2, max_vyaw=1.0)
    try:
        driver.move(linear=0.5, angular=0.0)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not fake.notifications:
            time.sleep(0.02)
        moves = [n for n in fake.notifications if n["method"] == "robot.move"]
        assert moves, "no robot.move reached the channel"
        assert moves[0]["params"]["vx"] == pytest.approx(0.1)
        assert moves[0]["params"]["vyaw"] == pytest.approx(0.0)
        assert "id" not in moves[0]  # a continuous intent is a notification
    finally:
        driver.close()


def test_the_drivers_deadman_sends_one_zero_and_then_goes_quiet():
    """Our deadman. robotd's own (500 ms) is what covers losing the channel."""
    fake = FakeDuckOverChannel()
    driver = _driver_over(fake, command_ttl_s=0.2, intent_hz=20.0)
    try:
        driver.move(linear=1.0, angular=0.0)
        time.sleep(0.6)
        moves = [n for n in fake.notifications if n["method"] == "robot.move"]
        assert any(m["params"]["vx"] > 0 for m in moves)
        assert moves[-1]["params"] == {"vx": 0.0, "vy": 0.0, "vyaw": 0.0}
        before = len(moves)
        time.sleep(0.3)
        after = len([n for n in fake.notifications if n["method"] == "robot.move"])
        assert after == before, "the driver kept talking after its own deadman fired"
    finally:
        driver.close()


def test_the_intent_rate_can_never_starve_robotds_deadman():
    """``robotd-params/src/lib.rs:1608`` — ``deadman_ms: 500``."""
    fake = FakeDuckOverChannel()
    driver = _driver_over(fake, intent_hz=0.5)  # absurdly slow, on purpose
    try:
        assert driver._intent_hz >= 1.0 / ROBOTD_DEADMAN_S
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# A fake signalling server, on a real socket
# ---------------------------------------------------------------------------


class FakeSignallingServer:
    """``mediad``'s in-process signalling server, replaying the transcript.

    Runs a real WebSocket server on loopback and records every frame the client
    sends, so the assertion is against bytes that crossed a socket rather than
    against a method call.
    """

    def __init__(self, *, on_start_session=None) -> None:
        self.received: list[str] = []
        self.port: int = 0
        self._on_start_session = on_start_session
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Future | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(10.0), "fake signalling server did not start"

    def _run(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        from websockets.asyncio.server import serve

        self._loop = asyncio.get_running_loop()
        self._stop = self._loop.create_future()
        async with serve(self._handler, "127.0.0.1", 0) as server:
            self.port = next(iter(server.sockets)).getsockname()[1]
            self._ready.set()
            await self._stop

    async def _handler(self, ws) -> None:
        await ws.send(WELCOME)
        async for raw in ws:
            self.received.append(raw)
            msg = json.loads(raw)
            if msg.get("type") == "list":
                await ws.send(LIST_ONE)
            elif msg.get("type") == "startSession":
                await ws.send(SESSION_STARTED)
                if self._on_start_session is not None:
                    await self._on_start_session(ws)
            elif msg.get("type") == "peer" and msg.get("sdp"):
                if self._on_answer is not None:
                    await self._on_answer(msg["sdp"]["sdp"])

    _on_answer = None

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def close(self) -> None:
        if self._loop is not None and self._stop is not None and not self._stop.done():
            self._loop.call_soon_threadsafe(self._stop.set_result, None)
        self._thread.join(timeout=5.0)


def test_the_state_machine_drives_a_real_signalling_server():
    """No aiortc: the SDP stack is stubbed, the socket and the frames are real."""
    websockets = pytest.importorskip("websockets")
    server = FakeSignallingServer()
    try:

        async def drive() -> None:
            proto = SignallingProtocol()
            async with websockets.connect(server.url, open_timeout=5.0) as ws:
                async for raw in ws:
                    out, event = proto.on_message(json.loads(raw))
                    for frame in out:
                        await ws.send(_dump(frame))
                    if event and event[0] == "session":
                        # Where a real SDP stack would take over. This fake
                        # never offers, so the exchange stops here.
                        assert proto.session_id == "s-1"
                        return

        asyncio.run(asyncio.wait_for(drive(), timeout=10.0))
    finally:
        server.close()

    # Exactly two frames, exactly these bytes: the offer never arrives from
    # this fake, so the answer is not among them.
    assert server.received == [EXPECT_LIST, EXPECT_START]


def test_the_link_negotiates_a_control_channel_end_to_end():
    """The whole stack: real WebSocket signalling, real aiortc on both ends.

    The fake plays the producer exactly as ``mediad`` does — it creates the
    ``control`` datachannel (``pipeline.rs:1612``) and it offers
    (``index.html``: "the producer offers and we answer") — then answers
    ``hello`` and ``robot.subscribe`` and records ``robot.move``.
    """
    pytest.importorskip("aiortc")
    from aiortc import RTCPeerConnection, RTCSessionDescription

    from castor.drivers.microduck_webrtc import MicroduckWebRTCLink

    seen: dict = {"notifications": [], "requests": []}
    robot_pc_box: dict = {}

    async def on_start_session(ws) -> None:
        pc = RTCPeerConnection()
        robot_pc_box["pc"] = pc
        channel = pc.createDataChannel(CONTROL_CHANNEL_LABEL)

        @channel.on("message")
        def _on_message(text):
            msg = json.loads(text)
            if msg.get("id") is None:
                seen["notifications"].append(msg)
                return
            seen["requests"].append(msg)
            if msg["method"] == "hello":
                result = {"api_version": API_VERSION, "daemon_version": "0.9.1"}
            elif msg["method"] == "robot.subscribe":
                result = {"accepted": True, "walk": "walk_v3", "skills": []}
            else:
                result = {}
            channel.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}))

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await ws.send(
            _dump(
                {
                    "type": "peer",
                    "sessionId": "s-1",
                    "sdp": {"type": "offer", "sdp": pc.localDescription.sdp},
                }
            )
        )

        async def on_answer(sdp: str) -> None:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))

        server._on_answer = on_answer

    server = FakeSignallingServer(on_start_session=on_start_session)
    link = None
    try:
        link = MicroduckWebRTCLink("127.0.0.1", port=server.port, connect_timeout_s=15.0)
        sock = link.connect()

        # The handshake was consumed by the link, not handed to the driver.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not seen["requests"]:
            time.sleep(0.05)
        assert seen["requests"][0]["method"] == "hello"
        assert seen["requests"][0]["id"] == HANDSHAKE_ID
        assert seen["requests"][0]["params"] == {"api_version": API_VERSION}
        assert link.meta.get("name") == "duck-9f21"

        # And a driver-shaped write crosses as one unframed object.
        sock.settimeout(2.0)
        sock.sendall(b'{"jsonrpc":"2.0","method":"robot.move","params":{"vx":0.1}}\n')
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not seen["notifications"]:
            time.sleep(0.05)
        assert seen["notifications"] == [
            {"jsonrpc": "2.0", "method": "robot.move", "params": {"vx": 0.1}}
        ]
    finally:
        if link is not None:
            link.close()
        # The robot's peer connection belongs to the server's loop; stopping
        # that loop is what tears it down.
        robot_pc_box.clear()
        server.close()

    # The frames the link put on the wire, in order and byte for byte.
    assert server.received[:2] == [EXPECT_LIST, EXPECT_START]
    answers = [json.loads(f) for f in server.received[2:]]
    assert answers, "the link never answered the offer"
    assert answers[0]["type"] == "peer"
    assert answers[0]["sessionId"] == "s-1"
    assert answers[0]["sdp"]["type"] == "answer"
    assert answers[0]["sdp"]["sdp"].startswith("v=0")


def test_a_video_offer_is_answered_inactive_when_video_is_off():
    """The control-only answer, against an offer that really has a video m-line.

    ``mediad`` bundles the datachannel with the video track
    (``a=group:BUNDLE video0 application1``), so a control-only client still
    has to answer the video m-line.  This proves the answer is well-formed and
    marks it ``inactive``.  What it cannot prove without a duck is that
    ``webrtcsink`` is content to be answered that way — see the module
    docstring's "unverified" note and ``webrtc_video: true``.
    """
    pytest.importorskip("aiortc")
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from aiortc.mediastreams import VideoStreamTrack

    from castor.drivers.microduck_webrtc import MicroduckWebRTCLink

    answers: list[str] = []
    box: dict = {}

    async def on_start_session(ws) -> None:
        pc = RTCPeerConnection()
        box["pc"] = pc
        pc.createDataChannel(CONTROL_CHANNEL_LABEL)
        pc.addTrack(VideoStreamTrack())
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await ws.send(
            _dump(
                {
                    "type": "peer",
                    "sessionId": "s-1",
                    "sdp": {"type": "offer", "sdp": pc.localDescription.sdp},
                }
            )
        )

        async def on_answer(sdp: str) -> None:
            answers.append(sdp)
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))

        server._on_answer = on_answer

    server = FakeSignallingServer(on_start_session=on_start_session)
    link = None
    try:
        link = MicroduckWebRTCLink(
            "127.0.0.1", port=server.port, video=False, connect_timeout_s=15.0
        )
        link.connect()
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not answers:
            time.sleep(0.05)
        assert answers, "the link never answered the offer"
        assert "m=video" in answers[0]
        assert "a=inactive" in answers[0]
        assert "a=sendrecv" not in answers[0] and "a=recvonly" not in answers[0]
        assert "m=application" in answers[0]  # the datachannel still arrives
    finally:
        if link is not None:
            link.close()
        box.clear()
        server.close()


def test_defaults_match_the_pollen_tree():
    assert DEFAULT_SIGNALLING_PORT == 8443  # mediad/src/main.rs:36
    assert CONTROL_CHANNEL_LABEL == "control"  # mediad/src/pipeline.rs:1613
    assert API_VERSION == 25  # duck-ipc-proto/src/lib.rs:304
    assert ROBOTD_DEADMAN_S == 0.5  # robotd-params/src/lib.rs:1608

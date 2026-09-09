"""
WebRTC transport for the Pollen Robotics Microduck — the network the duck
already ships.

Why this exists
---------------
Every other off-board route to ``robotd`` costs the owner setup that has
nothing to do with robotics: an SSH key on the duck (``transport: ssh``), or a
relay process the owner installs and a ``robot``-group edit plus the reboot
that makes it take effect (``transport: tcp``).  ``mediad`` is already running
on every duck — the unit is enabled on every install and every update — and it
already carries the whole control surface.  Speaking to it costs nothing but a
hostname.

What it speaks
--------------
Two protocols, both transcribed from the Pollen tree rather than guessed.  A
guessed message shape on this path produces *silence*, not an error, which is
exactly why ``mediad/webclient/index.html`` says its own shapes were "read off
net/webrtc/protocol rather than guessed".

1. **Signalling** — the ``gst-plugins-rs`` 0.15.3 ``net/webrtc/protocol`` wire:
   JSON with a ``type`` tag, camelCase, over a plain WebSocket to
   ``ws://<duck>:8443``.  ``mediad`` runs the server in its own process
   (``mediad/src/main.rs:28-36`` binds ``0.0.0.0:8443`` by default;
   ``docs/design/remote-webrtc.md`` §3).  The exchange this module drives is
   the one ``mediad/webclient/index.html`` drives, message for message:

   ===========================  ==================================================
   direction                    frame
   ===========================  ==================================================
   server → peer                ``{"type":"welcome","peerId":...}``
   peer → server                ``{"type":"list"}``                (takes no fields)
   server → peer                ``{"type":"list","producers":[{"id":...,"meta":{...}}]}``
   peer → server                ``{"type":"startSession","peerId":<producer id>}``
   server → peer                ``{"type":"sessionStarted","peerId":...,"sessionId":...}``
   server → peer                ``{"type":"peer","sessionId":...,"sdp":{"type":"offer","sdp":...}}``
   peer → server                ``{"type":"peer","sessionId":...,"sdp":{"type":"answer","sdp":...}}``
   either                       ``{"type":"peer","sessionId":...,"ice":{"candidate":...,"sdpMLineIndex":...}}``
   peer → server                ``{"type":"endSession","sessionId":...}``
   server → peer                ``{"type":"endSession",...}``, ``{"type":"error","details":...}``
   ===========================  ==================================================

   **The producer offers and we answer.**  ``index.html``: "No ``offer``, so
   the producer offers and we answer.  That is the direction webrtcsink wants:
   it knows what it is sending."  So ``startSession`` carries no ``offer`` key.

2. **Control** — the ``control`` datachannel is JSON-RPC 2.0, *the same*
   JSON-RPC ``robotd``'s Unix socket speaks (``docs/design/remote-webrtc.md``
   §5: "``mediad`` routes a call to the unix socket of the service that owns it
   and pumps replies back", and §5's "replies are not correlated,
   deliberately" — the pipe stays dumb).  So this module hands
   :class:`~castor.drivers.microduck_driver.MicroduckDriver` a *socket-shaped*
   object and the driver's existing NDJSON reader, writer, id correlation,
   intent loop and deadman are used unchanged.

   **The robot creates the channel; we receive it.**
   ``mediad/src/pipeline.rs:1612-1613`` calls ``create-data-channel`` with the
   label ``"control"`` on each consumer's ``webrtcbin``, so a client that opens
   its own would get a second, unrouted channel.  This module therefore waits
   for ``ondatachannel`` and never creates one.

   **One JSON object per datachannel message, no newline.**  The channel is a
   *string* channel (``on-message-string`` / ``send-string``,
   ``pipeline.rs:1624-1651``) and ``mediad/src/session.rs:110`` trims what
   arrives before forwarding it.  The driver's NDJSON framing is translated at
   the boundary by :class:`_ChannelSocket`.

Which calls survive the trip
----------------------------
``mediad/src/route.rs`` is an exhaustive match, per transport, on purpose.
Permitted and used by this driver: ``hello``, ``robot.move``, ``robot.head``,
``robot.look``, ``robot.pose``, ``robot.mouth``, ``robot.do``, ``robot.sound``,
``robot.health``, ``robot.mode``, ``robot.subscribe``, ``robot.enable``,
``robot.init``, ``robot.relax``, ``robot.stop``, ``robot.policies``,
``robot.skills``, ``robot.model`` (``route.rs:52-146``).  Refused over this
transport: ``robot.setMode`` ("a mode switch says *this duck now has wheels on
it*, which is a claim about hardware only somebody in the room can make"),
``system.pairingPin`` / ``system.setPairingPin`` (they authorise BLE, the
recovery path) and the ``update.*`` mutations (they would drop the session).

Security — read this before enabling it
---------------------------------------
**``mediad`` does not authenticate, and it binds all interfaces.**  Verbatim
from ``mediad/src/main.rs:9-13``:

    **It does not authenticate.** Anyone who reaches the signalling port can
    drive the robot and see its camera. That is a decision, not an omission —
    §4 has the reasoning, and the short version is that the pairing PIN is a
    shared ``000000``, so a gate would add a step to every connection and prove
    nothing.

``transport: ssh`` is protected by an SSH key and ``robot`` group membership,
which is real security the owner paid about two and a half minutes for.
Choosing ``transport: webrtc`` removes that cost and that protection together,
on a LAN where ``mediad`` is already listening anyway.  That is defensible and
it is a **stated choice**, not a side effect of making setup faster.  It is
"fine on a bench and in an office. **Not fine in a home**"
(``docs/design/remote-webrtc.md`` §4).  ``docs/hardware/microduck.md`` carries
the same note and what turning ``mediad`` off costs.

Deadman
-------
Three timers, and only two of them are ours:

``robotd``'s
    ``deadman_ms: 500`` (``robotd-params/src/lib.rs:1608``, in
    ``SafetyParams::default``).  Velocity zeroes if intents stop arriving.
    **This is the one that fires when the WebRTC session dies**, because a
    dropped datachannel means the driver's own zero never reaches the robot.
    It is the only deadman that survives losing the transport, which is why the
    transport is allowed to be a network at all.

the driver's ``command_ttl_s``
    1.5 s by default.  Fires when the *brain* goes quiet while the transport is
    healthy: the intent loop sends one explicit zero and then stops talking.

the driver's ``intent_hz``
    20 Hz by default, and floored at ``1 / 0.5 s`` so it can never fall below
    the rate ``robotd``'s deadman needs to stay fed.

Nothing in this module adds a fourth.  A WebRTC-specific deadman would be a
second answer to a question ``robotd`` already answers correctly, and it would
answer it later.

RCAN config::

    drivers:
      - id: duck
        protocol: microduck
        transport: webrtc
        host: radxa-zero3.local   # or the address `duckctl ip` prints
        # port: 8443              # mediad --port default
        # webrtc_video: false     # negotiate control only; see below

Install::

    pip install 'opencastor[microduck-webrtc]'

Video
-----
``webrtc_video`` defaults to ``False`` and the answer marks every media
transceiver ``inactive``.  OpenCastor wants the control channel; decoding a
720p30 H.264 track it never looks at costs real CPU on the machine running the
brain, and the datachannel is bundled with the video track
(``a=group:BUNDLE video0 application1``) so it arrives either way.  Set
``webrtc_video: true`` to accept and drain the track instead.

**Unverified without a duck**: whether ``webrtcsink`` is happy to answer
``inactive`` on its video m-line.  If a real duck refuses the session, set
``webrtc_video: true``; that path is the one the console exercises.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger("OpenCastor.MicroduckWebRTC")

__all__ = [
    "API_VERSION",
    "CONTROL_CHANNEL_LABEL",
    "DEFAULT_CONSOLE_PORT",
    "DEFAULT_SIGNALLING_PORT",
    "MicroduckWebRTCLink",
    "NoProducerError",
    "ROBOTD_DEADMAN_S",
    "SignallingError",
    "SignallingProtocol",
    "WebRTCUnavailable",
    "connect_webrtc",
    "require_webrtc",
]

#: ``duck-ipc-proto``'s API version, from ``duck-ipc-proto/src/lib.rs:304``.
#: Sent in ``hello`` so a skew is reported rather than discovered.
API_VERSION = 25

#: ``mediad --port`` default — ``mediad/src/main.rs:36``.  8443 is what
#: ``webrtcsink``'s own signaller defaults to.
DEFAULT_SIGNALLING_PORT = 8443

#: ``mediad --web-port`` default — ``mediad/src/main.rs:75``.  The console, not
#: the signalling server.  Here so an error message can name it.
DEFAULT_CONSOLE_PORT = 8080

#: The datachannel ``mediad`` opens on every consumer —
#: ``mediad/src/pipeline.rs:1613``.
CONTROL_CHANNEL_LABEL = "control"

#: ``robotd``'s own deadman, ``robotd-params/src/lib.rs:1608``.  The one that
#: fires when this transport dies.
ROBOTD_DEADMAN_S = 0.5

#: The JSON-RPC id this module uses for its own handshake.  A *string* id, so
#: it can never collide with the driver's integer ids — ``duck-ipc-proto``'s
#: ``Id`` is ``Number(u64) | Text(String)`` (``duck-ipc-proto/src/lib.rs:805-810``).
HANDSHAKE_ID = "castor-hello"

_INSTALL_HINT = (
    "transport: webrtc needs aiortc and websockets, which are not installed.\n"
    "\n"
    "    pip install 'opencastor[microduck-webrtc]'\n"
    "\n"
    "They are an optional extra because they are a large dependency for a robot\n"
    "that does not need them.  If you cannot install them, the duck is still\n"
    "reachable over `transport: ssh` (needs an SSH key on the duck) or\n"
    "`transport: tcp` (needs a relay running on the duck)."
)


class WebRTCUnavailable(RuntimeError):
    """Raised when ``transport: webrtc`` is asked for without its dependencies."""


class SignallingError(RuntimeError):
    """The signalling server said no, or said something unexpected."""


class NoProducerError(SignallingError):
    """The signalling server listed no producers.

    ``mediad`` registers as a producer when its pipeline reaches PLAYING, so an
    empty list is a camera pipeline that would not start rather than a duck
    that is off.
    """


def require_webrtc() -> tuple[Any, Any]:
    """Import ``aiortc`` and ``websockets`` or fail with something actionable.

    Returns:
        ``(aiortc, websockets)`` modules.

    Raises:
        WebRTCUnavailable: with the exact install line, never a bare ImportError.
    """
    try:
        import aiortc  # noqa: F401
        import websockets  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised by a monkeypatch test
        raise WebRTCUnavailable(f"{_INSTALL_HINT}\n\n(import failed: {exc})") from exc
    return aiortc, websockets


# ---------------------------------------------------------------------------
# Signalling: the protocol, with no I/O in it
# ---------------------------------------------------------------------------


class SignallingProtocol:
    """The ``gst-plugins-rs`` signalling exchange as a pure state machine.

    No sockets, no asyncio, no aiortc: feed it decoded server frames and it
    returns the frames to send back.  That is what lets a test replay a real
    exchange byte for byte without a duck, an SDP stack or a network.

    The state it keeps is the state ``mediad/webclient/index.html`` keeps, and
    for the same reasons:

    ``wanted``
        Cleared by ``endSession`` so the next listing is not read as an
        invitation to ask again for a session the robot just declined.
    ``starting``
        Set between ``startSession`` and ``sessionStarted``.  The service
        answers a ``list`` twice over — once as a push after the welcome, once
        as the reply to our own — and without this guard the second listing
        opens a second session.
    """

    def __init__(self, *, robot: Optional[str] = None) -> None:
        #: Prefer a producer whose ``meta.name`` or id matches this, when the
        #: server lists more than one.  ``None`` takes the first, which is what
        #: the console does on a LAN where there is exactly one.
        self.robot = robot

        self.peer_id: Optional[str] = None
        self.session_id: Optional[str] = None
        self.starting = False
        self.wanted = True
        self.producer: Optional[dict] = None
        self.meta: dict = {}
        self.last_error: Optional[str] = None

    # -- frames we originate -------------------------------------------

    @staticmethod
    def list_frame() -> dict:
        """``{"type": "list"}`` — takes no fields."""
        return {"type": "list"}

    @staticmethod
    def start_session_frame(producer_id: str) -> dict:
        """``startSession`` with no ``offer``: the producer offers, we answer."""
        return {"type": "startSession", "peerId": producer_id}

    def answer_frame(self, sdp: str) -> dict:
        """``{"type":"peer", sessionId, sdp:{"type":"answer","sdp":...}}``."""
        return {
            "type": "peer",
            "sessionId": self.session_id,
            "sdp": {"type": "answer", "sdp": sdp},
        }

    def ice_frame(self, candidate: str, sdp_mline_index: int) -> dict:
        """``{"type":"peer", sessionId, ice:{"candidate":...,"sdpMLineIndex":...}}``.

        aiortc gathers its candidates before ``setLocalDescription`` returns and
        puts them in the answer SDP, so this module does not currently send
        one.  Kept and tested because the shape is half of the pair above and
        getting it wrong is the mistake that produces silence.
        """
        return {
            "type": "peer",
            "sessionId": self.session_id,
            "ice": {"candidate": candidate, "sdpMLineIndex": sdp_mline_index},
        }

    def end_session_frame(self) -> Optional[dict]:
        if self.session_id is None:
            return None
        return {"type": "endSession", "sessionId": self.session_id}

    # -- frames the server sends ---------------------------------------

    def on_message(self, msg: dict) -> tuple[list[dict], Optional[tuple]]:
        """Advance the state machine.

        Args:
            msg: one decoded server frame.

        Returns:
            ``(outbound, event)``.  ``outbound`` is the list of frames to send,
            in order.  ``event`` is ``None`` or one of:

            - ``("producer", meta)`` — a producer was chosen, before a session
              exists.  ``meta`` names the robot (``mediad/src/producer.rs``).
            - ``("session", session_id)`` — negotiate now.
            - ``("offer", sdp_str)`` — set this remote description and answer.
            - ``("ice", {"candidate":..., "sdpMLineIndex":...})``
            - ``("closed", reason)`` — the robot ended or refused the session.

        Raises:
            NoProducerError: the listing was empty.
            SignallingError: the server sent ``error``.
        """
        kind = msg.get("type")

        if kind == "welcome":
            # The server assigns us an id, then we ask who is producing.
            self.peer_id = msg.get("peerId")
            return [self.list_frame()], None

        if kind == "list":
            return self._on_list(msg)

        if kind == "sessionStarted":
            self.starting = False
            self.session_id = msg.get("sessionId")
            return [], ("session", self.session_id)

        if kind == "peer":
            if msg.get("sdp"):
                sdp = msg["sdp"]
                if sdp.get("type") != "offer":
                    # An answer arriving here would mean we offered, and we
                    # never do.  Named rather than silently ignored.
                    raise SignallingError(
                        f"expected an offer from the producer, got {sdp.get('type')!r}"
                    )
                return [], ("offer", sdp.get("sdp"))
            if msg.get("ice"):
                return [], ("ice", msg["ice"])
            return [], None

        if kind == "endSession":
            # Both, and both matter: a stale sessionId would be sent on the
            # next `peer` message, and leaving `wanted` set turns the next
            # listing into another request the robot has just declined.
            self.session_id = None
            self.wanted = False
            self.starting = False
            return [], ("closed", msg.get("reason"))

        if kind == "sessionRejected":
            # Only the rendezvous sends this; the robot's own signalling server
            # has no notion of another consumer holding the robot.
            self.wanted = False
            self.starting = False
            reason = msg.get("reason") or "no reason given"
            if msg.get("activeApp"):
                reason = f"{reason} ({msg['activeApp']} is driving it)"
            return [], ("closed", reason)

        if kind == "peerStatusChanged":
            # A robot appearing or going away.  On a LAN the one producer is
            # this robot, so there is nothing to redraw.
            return [], None

        if kind == "error":
            self.last_error = msg.get("details")
            raise SignallingError(f"signalling server: {msg.get('details')}")

        return [], None

    def _on_list(self, msg: dict) -> tuple[list[dict], Optional[tuple]]:
        producers = msg.get("producers") or []
        if not producers:
            raise NoProducerError(
                "no producers on the signalling server. mediad registers as one "
                "when its pipeline reaches PLAYING — check `journalctl -u mediad -b` "
                "on the duck for a pipeline that would not start."
            )

        # A session already exists, or nobody asked for one: this listing is a
        # robot appearing or going away, not an invitation to open a session.
        if self.session_id or self.starting or not self.wanted:
            return [], None

        producer = self._choose(producers)
        self.producer = producer
        self.meta = producer.get("meta") or {}
        self.starting = True
        return (
            [self.start_session_frame(producer["id"])],
            ("producer", self.meta),
        )

    def _choose(self, producers: list[dict]) -> dict:
        if self.robot:
            for candidate in producers:
                meta = candidate.get("meta") or {}
                if candidate.get("id") == self.robot or meta.get("name") == self.robot:
                    return candidate
            names = ", ".join(
                (p.get("meta") or {}).get("name") or str(p.get("id")) for p in producers
            )
            raise NoProducerError(
                f"no producer named {self.robot!r} on this duck; the server listed: {names}"
            )
        return producers[0]


# ---------------------------------------------------------------------------
# The control datachannel, shaped like a socket
# ---------------------------------------------------------------------------


class _ChannelSocket:
    """A socket-shaped adapter over the ``control`` datachannel.

    ``MicroduckDriver`` writes newline-terminated JSON with ``sendall`` and
    reads bytes with ``recv``.  ``control`` carries one JSON object per
    datachannel message with **no newline** (it is a string channel:
    ``mediad/src/pipeline.rs:1613``, and ``mediad/src/session.rs:110`` trims
    what arrives).  So the framing is translated here and the driver's reader,
    writer, id correlation, intent loop and deadman are untouched.

    Deliberately not a ``socket.socket`` subclass: only the five methods the
    driver actually calls exist, so a sixth would fail loudly rather than
    silently doing something a datachannel cannot do.
    """

    def __init__(self, send: Callable[[str], None], on_close: Callable[[], None]) -> None:
        self._send = send
        self._on_close = on_close
        self._inbox: queue.Queue[Optional[bytes]] = queue.Queue()
        self._timeout: Optional[float] = None
        self._closed = False
        self._pending = b""

    # -- fed from the WebRTC thread ------------------------------------

    def feed(self, text: str) -> None:
        """A message arrived on the datachannel."""
        if isinstance(text, (bytes, bytearray)):
            text = text.decode("utf-8", "replace")
        # Re-terminate: the driver's reader splits on newlines.  A message that
        # already carries them (nothing does today) still frames correctly.
        self._inbox.put(text.encode("utf-8").rstrip(b"\n") + b"\n")

    def mark_closed(self) -> None:
        """The datachannel or the session went away."""
        if not self._closed:
            self._closed = True
            self._inbox.put(None)

    # -- the socket surface the driver uses ----------------------------

    def settimeout(self, timeout: Optional[float]) -> None:
        self._timeout = timeout

    def sendall(self, data: bytes) -> None:
        if self._closed:
            raise OSError("microduck control channel is closed")
        self._pending += data
        while b"\n" in self._pending:
            raw, self._pending = self._pending.split(b"\n", 1)
            raw = raw.strip()
            if raw:
                self._send(raw.decode("utf-8"))

    def recv(self, _bufsize: int = 65536) -> bytes:
        try:
            item = self._inbox.get(timeout=self._timeout)
        except queue.Empty:
            # The driver's read loop catches TimeoutError and keeps going;
            # socket.timeout is an alias of it since 3.10.
            raise TimeoutError("microduck control channel read timed out") from None
        if item is None:
            return b""  # EOF, which the driver reads as "connection closed"
        return item

    def shutdown(self, _how: int = 0) -> None:
        self.mark_closed()

    def close(self) -> None:
        self.mark_closed()
        self._on_close()


# ---------------------------------------------------------------------------
# The link: signalling + aiortc, on its own thread
# ---------------------------------------------------------------------------


class MicroduckWebRTCLink:
    """Owns one WebRTC session to a duck's ``mediad`` and hands out a socket.

    The asyncio loop and aiortc live on a private daemon thread; the driver
    stays synchronous and never learns that any of this happened.
    """

    def __init__(
        self,
        host: str,
        *,
        port: int = DEFAULT_SIGNALLING_PORT,
        robot: Optional[str] = None,
        video: bool = False,
        connect_timeout_s: float = 20.0,
        api_version: int = API_VERSION,
    ) -> None:
        if not host:
            raise ValueError("transport 'webrtc' requires host")
        self.host = host
        self.port = int(port)
        self.robot = robot
        self.video = bool(video)
        self.connect_timeout_s = float(connect_timeout_s)
        self.api_version = int(api_version)

        self.url = f"ws://{self.host}:{self.port}"
        self.meta: dict = {}
        self.remote_api_version: Optional[int] = None

        self._proto = SignallingProtocol(robot=robot)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None
        self._sock: Optional[_ChannelSocket] = None
        self._pc = None
        self._ws = None
        self._closing = False
        self._handshake: Optional[asyncio.Future] = None

    # -- public --------------------------------------------------------

    def connect(self) -> _ChannelSocket:
        """Negotiate a session and return the control channel as a socket.

        Raises:
            WebRTCUnavailable: aiortc/websockets are not installed.
            SignallingError: the exchange failed, with what the server said.
            TimeoutError: nothing answered inside ``connect_timeout_s``.
        """
        require_webrtc()
        self._thread = threading.Thread(target=self._run, name="microduck-webrtc", daemon=True)
        self._thread.start()
        if not self._ready.wait(self.connect_timeout_s + 2.0):
            self.close()
            raise TimeoutError(
                f"no WebRTC session with {self.url} after {self.connect_timeout_s:.0f}s. "
                f"mediad serves the console on :{DEFAULT_CONSOLE_PORT} too — if "
                f"http://{self.host}:{DEFAULT_CONSOLE_PORT}/ answers and this does not, "
                f"it is a firewall between you and :{self.port}."
            )
        if self._error is not None:
            self.close()
            raise self._error
        assert self._sock is not None
        return self._sock

    def close(self) -> None:
        """Tear the session down: endSession, close the peer connection, stop."""
        self._closing = True
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self._teardown(), loop).result(timeout=3.0)
            except Exception as exc:  # pragma: no cover - best effort
                logger.debug("microduck webrtc teardown: %s", exc)
            loop.call_soon_threadsafe(loop.stop)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        if self._sock is not None:
            self._sock.mark_closed()

    # -- the asyncio side ----------------------------------------------

    def _run(self) -> None:
        try:
            asyncio.run(self._session())
        except Exception as exc:
            self._fail(exc)
        finally:
            self._ready.set()
            if self._sock is not None:
                self._sock.mark_closed()

    def _fail(self, exc: BaseException) -> None:
        if self._error is None and not self._closing:
            self._error = exc
        self._ready.set()

    async def _session(self) -> None:
        import websockets

        self._loop = asyncio.get_running_loop()
        self._handshake = self._loop.create_future()

        async with websockets.connect(self.url, open_timeout=self.connect_timeout_s) as ws:
            self._ws = ws
            async for raw in ws:
                if self._closing:
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("microduck signalling: dropping %r", raw[:200])
                    continue
                await self._handle(ws, msg)
        if not self._ready.is_set():
            self._fail(SignallingError(f"{self.url} closed before a control channel opened"))

    async def _handle(self, ws, msg: dict) -> None:
        outbound, event = self._proto.on_message(msg)
        for frame in outbound:
            await self._send_json(ws, frame)

        if event is None:
            return
        kind, payload = event

        if kind == "producer":
            self.meta = payload or {}
            name = self.meta.get("name") or self.meta.get("serial") or "<unnamed>"
            logger.info(
                "Microduck producer: %s (release %s, api v%s)",
                name,
                self.meta.get("release", "?"),
                self.meta.get("api_version", "?"),
            )
        elif kind == "session":
            logger.debug("microduck session %s", payload)
        elif kind == "offer":
            await self._answer(ws, payload)
        elif kind == "ice":
            await self._add_ice(payload)
        elif kind == "closed":
            self._fail(SignallingError(f"the duck ended the session: {payload or 'no reason'}"))
            if self._sock is not None:
                self._sock.mark_closed()

    async def _send_json(self, ws, frame: dict) -> None:
        line = json.dumps(frame, separators=(",", ":"))
        logger.debug("microduck signalling → %s", line[:200])
        await ws.send(line)

    async def _answer(self, ws, offer_sdp: str) -> None:
        from aiortc import RTCPeerConnection, RTCSessionDescription

        if self._pc is None:
            self._pc = RTCPeerConnection()
            self._wire_peer_connection(self._pc)

        await self._pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))

        if not self.video:
            # Control only.  The datachannel is bundled with the video track, so
            # it still arrives; what this drops is decoding a 720p30 H.264
            # stream nothing looks at.
            for transceiver in self._pc.getTransceivers():
                transceiver.direction = "inactive"

        answer = await self._pc.createAnswer()
        await self._pc.setLocalDescription(answer)
        # aiortc gathers before setLocalDescription returns, so every candidate
        # is already in this SDP and no trickle follows.
        await self._send_json(ws, self._proto.answer_frame(self._pc.localDescription.sdp))

    def _wire_peer_connection(self, pc) -> None:
        @pc.on("datachannel")
        def _on_datachannel(channel):  # pragma: no cover - needs a real pc
            if channel.label != CONTROL_CHANNEL_LABEL:
                logger.debug("microduck: ignoring datachannel %r", channel.label)
                return
            self._attach_control(channel)

        @pc.on("track")
        def _on_track(track):  # pragma: no cover - needs a real pc
            if self.video:
                asyncio.ensure_future(self._drain(track))

        @pc.on("connectionstatechange")
        async def _on_state():  # pragma: no cover - needs a real pc
            logger.debug("microduck webrtc: %s", pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                if self._sock is not None:
                    self._sock.mark_closed()
                self._fail(SignallingError(f"webrtc connection {pc.connectionState}"))

    async def _drain(self, track) -> None:  # pragma: no cover - needs media
        """Consume a track we accepted, so its queue cannot grow without bound."""
        try:
            while True:
                await track.recv()
        except Exception:
            return

    def _attach_control(self, channel) -> None:
        """Wire the ``control`` datachannel to a socket the driver can hold."""
        loop = self._loop

        def _send(text: str) -> None:
            # The driver calls this from its own thread; aiortc's datachannel
            # is not thread-safe and drops the frame without raising when it is
            # touched from the wrong one.  Marshal every frame onto the loop.
            if loop is None or loop.is_closed():
                raise OSError("microduck control channel is closed")
            loop.call_soon_threadsafe(channel.send, text)

        sock = _ChannelSocket(send=_send, on_close=self.close)
        self._sock = sock

        @channel.on("message")
        def _on_message(message):
            if isinstance(message, (bytes, bytearray)):
                message = message.decode("utf-8", "replace")
            # Our own handshake reply is consumed here rather than handed to
            # the driver, which has no pending slot for a string id.
            try:
                parsed = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                sock.feed(message)
                return
            if isinstance(parsed, dict) and parsed.get("id") == HANDSHAKE_ID:
                self._finish_handshake(parsed)
                return
            sock.feed(message)

        @channel.on("close")
        def _on_close():
            sock.mark_closed()

        # `hello` is the version handshake and the first call on a connection
        # (duck-ipc-proto/src/lib.rs:820, route.rs permits it precisely so a
        # client can establish anything).  Sent before the driver gets the
        # socket, so an API skew is reported rather than discovered.
        channel.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": HANDSHAKE_ID,
                    "method": "hello",
                    "params": {"api_version": self.api_version},
                },
                separators=(",", ":"),
            )
        )
        loop = self._loop
        if loop is not None:
            loop.call_later(2.0, self._release)

    def _finish_handshake(self, reply: dict) -> None:
        result = reply.get("result") or {}
        error = reply.get("error")
        if error:
            logger.warning("Microduck hello failed: %s", error)
        else:
            self.remote_api_version = result.get("api_version")
            if (
                self.remote_api_version is not None
                and int(self.remote_api_version) != self.api_version
            ):
                logger.warning(
                    "Microduck API skew: this driver speaks v%s, the duck speaks v%s. "
                    "The duck refuses a call it does not know, not a version it does "
                    "not share.",
                    self.api_version,
                    self.remote_api_version,
                )
            else:
                logger.info(
                    "Microduck control channel open (api v%s, %s)",
                    self.remote_api_version,
                    result.get("daemon_version") or "unknown build",
                )
        self._release()

    def _release(self) -> None:
        """Hand the socket to the caller of :meth:`connect`, once."""
        if self._sock is not None:
            self._ready.set()

    async def _add_ice(self, ice: dict) -> None:
        from aiortc.sdp import candidate_from_sdp

        raw = (ice or {}).get("candidate") or ""
        if not raw.strip():
            return  # end-of-candidates
        if raw.startswith("candidate:"):
            raw = raw[len("candidate:") :]
        try:
            candidate = candidate_from_sdp(raw)
        except Exception as exc:
            logger.debug("microduck: unparseable ICE candidate %r (%s)", raw[:120], exc)
            return
        candidate.sdpMLineIndex = ice.get("sdpMLineIndex")
        if ice.get("sdpMid") is not None:
            candidate.sdpMid = ice["sdpMid"]
        if self._pc is not None:
            await self._pc.addIceCandidate(candidate)

    async def _teardown(self) -> None:
        frame = self._proto.end_session_frame()
        ws, self._ws = self._ws, None
        if frame is not None and ws is not None:
            try:
                await ws.send(json.dumps(frame, separators=(",", ":")))
            except Exception:
                pass
        pc, self._pc = self._pc, None
        if pc is not None:
            try:
                await pc.close()
            except Exception:
                pass
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass


def connect_webrtc(config: dict) -> _ChannelSocket:
    """Build a link from an RCAN driver config and return its control socket.

    The one entry point ``MicroduckDriver`` calls.  The returned object's
    ``close()`` tears the whole session down, so the driver's existing
    ``close()`` needs no new branch.
    """
    link = MicroduckWebRTCLink(
        host=str(config.get("host") or ""),
        port=int(config.get("port") or DEFAULT_SIGNALLING_PORT),
        robot=config.get("robot") or config.get("robot_name"),
        video=bool(config.get("webrtc_video", False)),
        connect_timeout_s=float(config.get("webrtc_timeout_s", 20.0)),
    )
    return link.connect()

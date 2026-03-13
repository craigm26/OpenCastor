"""
castor/channels/mqtt_channel.py — MQTT channel bridge (issue #98, #147, #296).

Connects to an MQTT broker and routes messages between the broker and the
robot brain.  Inbound messages on the subscribe topic are forwarded to the
brain callback; replies are published to the publish topic.

Also supports a bidirectional REST bridge (issue #147): messages published
to ``command_topic`` (default ``castor/command/in``) are forwarded directly
to the robot brain as /api/command instructions; results are published to
``command_response_topic`` (default ``castor/command/out``).

Issue #296 adds ``MQTTActionBridge``: subscribes to a configurable action
topic, translates incoming messages into robot action commands dispatched
through the channel callback, and publishes results back to a result topic.

Required config:
    broker_host:              MQTT broker hostname (default: localhost)

Optional config:
    broker_port:              Broker port (default: 1883)
    subscribe_topic:          Topic to subscribe (default: opencastor/input)
    publish_topic:            Response topic (default: opencastor/output)
    command_topic:            REST bridge inbound topic (default: castor/command/in)
    command_response_topic:   REST bridge outbound topic (default: castor/command/out)
    username:                 MQTT username  (or env MQTT_USERNAME)
    password:                 MQTT password  (or env MQTT_PASSWORD)
    client_id:                MQTT client ID (default: opencastor-<pid>)
    keepalive:                Keepalive in seconds (default: 60)
    qos:                      QoS level 0/1/2 (default: 0)
    tls:                      Enable TLS (default: false)
    publish_telemetry:        Periodically publish robot state (default: false)
    telemetry_hz:             Telemetry publish rate in Hz (default: 1)

Env vars for action bridge (issue #296):
    MQTT_ACTION_TOPIC:        Action command topic (default: opencastor/action)
    MQTT_RESULT_TOPIC:        Result publish topic (default: opencastor/result)

Install::

    pip install opencastor[mqtt]   # or: pip install paho-mqtt

RCAN config example::

    channels:
      - type: mqtt
        broker_host: mqtt.example.com
        subscribe_topic: opencastor/input
        publish_topic: opencastor/output
        command_topic: castor/command/in
        command_response_topic: castor/command/out
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Optional
from collections.abc import Callable

from castor.channels.base import BaseChannel

logger = logging.getLogger("OpenCastor.Channel.MQTT")
_bridge_logger = logging.getLogger("OpenCastor.MQTTActionBridge")

try:
    import paho.mqtt.client as _mqtt_client

    HAS_PAHO = True
except ImportError:
    HAS_PAHO = False


class MQTTActionBridge:
    """Subscribes to MQTT action topic, dispatches commands, publishes results.

    Inbound messages on ``MQTT_ACTION_TOPIC`` (default ``opencastor/action``)
    are parsed as JSON and dispatched through the parent ``MQTTChannel``
    callback.  Results are published as JSON to ``MQTT_RESULT_TOPIC``
    (default ``opencastor/result``).

    Supported payload formats::

        {"instruction": "go forward"}          # text instruction via callback
        {"action": {"type": "move", "linear": 0.5}}  # direct action dict

    When paho-mqtt is unavailable ``enable()``/``disable()`` are no-ops and
    ``publish_result()`` logs a warning rather than raising.
    """

    def __init__(self, channel: MQTTChannel) -> None:
        self._channel = channel
        self._action_topic: str = os.environ.get("MQTT_ACTION_TOPIC", "opencastor/action")
        self._result_topic: str = os.environ.get("MQTT_RESULT_TOPIC", "opencastor/result")
        self._enabled: bool = False

    # ── Public API ─────────────────────────────────────────────────────────────

    def enable(self) -> None:
        """Subscribe to action topic and enable bridge."""
        if not HAS_PAHO:
            _bridge_logger.debug("MQTTActionBridge.enable() skipped — paho-mqtt not installed")
            return
        client = self._channel._client
        if client is None:
            _bridge_logger.warning("MQTTActionBridge.enable() called before client is ready")
            return
        qos = self._channel._qos
        client.subscribe(self._action_topic, qos=qos)
        self._enabled = True
        _bridge_logger.info(
            "MQTTActionBridge enabled: action_topic=%r result_topic=%r",
            self._action_topic,
            self._result_topic,
        )

    def disable(self) -> None:
        """Unsubscribe and disable bridge."""
        if not HAS_PAHO:
            _bridge_logger.debug("MQTTActionBridge.disable() skipped — paho-mqtt not installed")
            return
        client = self._channel._client
        if client is not None:
            try:
                client.unsubscribe(self._action_topic)
            except Exception as exc:
                _bridge_logger.warning("MQTTActionBridge unsubscribe error: %s", exc)
        self._enabled = False
        _bridge_logger.info("MQTTActionBridge disabled")

    def _on_action_message(self, client, userdata, msg) -> None:
        """Parse incoming MQTT message as action command and dispatch.

        Accepted payload formats:

        * ``{"instruction": "go forward"}`` — passed to the channel callback
          as a text instruction.
        * ``{"action": {"type": "move", "linear": 0.5}}`` — the action dict is
          JSON-serialised and passed as the instruction text so the standard
          callback pipeline can interpret it.

        Results (or errors) are published to the result topic via
        :meth:`publish_result`.
        """
        raw = msg.payload
        if not raw:
            _bridge_logger.debug("MQTTActionBridge received empty payload — ignored")
            return

        try:
            payload_str = raw.decode("utf-8", errors="replace").strip()
        except Exception as exc:
            _bridge_logger.warning("MQTTActionBridge payload decode error: %s", exc)
            return

        if not payload_str:
            _bridge_logger.debug("MQTTActionBridge received blank payload — ignored")
            return

        try:
            data = json.loads(payload_str)
        except json.JSONDecodeError as exc:
            _bridge_logger.warning("MQTTActionBridge invalid JSON on %r: %s", msg.topic, exc)
            return

        if not isinstance(data, dict):
            _bridge_logger.warning("MQTTActionBridge payload is not a JSON object — ignored")
            return

        # Determine instruction text from payload format
        if "instruction" in data:
            instruction = str(data["instruction"])
        elif "action" in data:
            instruction = json.dumps(data["action"])
        else:
            _bridge_logger.warning(
                "MQTTActionBridge payload missing 'instruction' or 'action' key — ignored"
            )
            self.publish_result({"error": "missing 'instruction' or 'action' key", "ok": False})
            return

        _bridge_logger.debug("MQTTActionBridge dispatching instruction: %.80s", instruction)

        loop = self._channel._loop
        if loop is None or loop.is_closed():
            _bridge_logger.warning("MQTTActionBridge: event loop unavailable — dropping message")
            return

        chat_id = self._action_topic
        future = asyncio.run_coroutine_threadsafe(
            self._channel.handle_message(chat_id, instruction), loop
        )
        try:
            reply = future.result(timeout=30.0)
            self.publish_result({"ok": True, "reply": reply or "", "instruction": instruction})
        except Exception as exc:
            _bridge_logger.warning("MQTTActionBridge dispatch error: %s", exc)
            self.publish_result({"ok": False, "error": str(exc), "instruction": instruction})

    def publish_result(self, result: dict) -> None:
        """Publish *result* dict as JSON to the result topic."""
        if not HAS_PAHO:
            _bridge_logger.warning(
                "MQTTActionBridge.publish_result() skipped — paho-mqtt not installed"
            )
            return
        client = self._channel._client
        if client is None:
            _bridge_logger.warning("MQTTActionBridge.publish_result() called with no client")
            return
        try:
            payload = json.dumps(result)
            client.publish(self._result_topic, payload.encode(), qos=self._channel._qos)
            _bridge_logger.debug("MQTTActionBridge published result to %r", self._result_topic)
        except Exception as exc:
            _bridge_logger.warning("MQTTActionBridge publish_result error: %s", exc)

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def action_topic(self) -> str:
        """MQTT topic subscribed for action commands."""
        return self._action_topic

    @property
    def result_topic(self) -> str:
        """MQTT topic where results are published."""
        return self._result_topic

    @property
    def is_enabled(self) -> bool:
        """True when the bridge is subscribed and active."""
        return self._enabled


class MQTTChannel(BaseChannel):
    """MQTT channel: subscribe/publish bot powered by paho-mqtt.

    Forwards messages from an MQTT topic to the robot brain and publishes
    replies back to a configurable response topic.
    """

    name = "mqtt"

    def __init__(self, config: dict, on_message: Optional[Callable] = None):
        super().__init__(config, on_message)
        self._config = config
        self._broker_host = config.get("broker_host", os.getenv("MQTT_BROKER_HOST", "localhost"))
        self._broker_port = int(config.get("broker_port", os.getenv("MQTT_BROKER_PORT", "1883")))
        self._subscribe_topic = config.get("subscribe_topic", "opencastor/input")
        self._publish_topic = config.get("publish_topic", "opencastor/output")
        self._command_topic = config.get("command_topic", "castor/command/in")
        self._command_response_topic = config.get("command_response_topic", "castor/command/out")
        self._username = config.get("username", os.getenv("MQTT_USERNAME", ""))
        self._password = config.get("password", os.getenv("MQTT_PASSWORD", ""))
        self._keepalive = int(config.get("keepalive", 60))
        self._qos = int(config.get("qos", 0))
        self._tls = bool(config.get("tls", False))
        self._client_id = config.get("client_id", f"opencastor-{os.getpid()}")
        self._client = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._connected = threading.Event()
        self._running = False
        # Telemetry publisher
        self._publish_telemetry = bool(config.get("publish_telemetry", False))
        self._telemetry_hz = float(config.get("telemetry_hz", 1.0))
        self._telemetry_stop = threading.Event()
        self._telemetry_thread: Optional[threading.Thread] = None
        self._last_action: Optional[dict] = None
        self._start_time: Optional[float] = None
        # Action bridge (issue #296)
        self._action_bridge = MQTTActionBridge(self)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self):
        """Connect to the MQTT broker and begin receiving messages."""
        if not HAS_PAHO:
            raise ImportError("paho-mqtt is not installed. Install with: pip install paho-mqtt")

        self._loop = asyncio.get_event_loop()
        self._running = True

        self._client = _mqtt_client.Client(client_id=self._client_id)
        if self._username:
            self._client.username_pw_set(self._username, self._password)
        if self._tls:
            self._client.tls_set()

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        self._client.connect(self._broker_host, self._broker_port, self._keepalive)

        # Run the paho network loop in a background daemon thread
        self._client.loop_start()

        # Wait up to 10 s for the connection to establish
        await asyncio.to_thread(self._connected.wait, 10.0)
        if not self._connected.is_set():
            raise ConnectionError(
                f"MQTT: Could not connect to {self._broker_host}:{self._broker_port} within 10 s"
            )

        logger.info(
            "MQTT channel connected to %s:%d (sub=%r, pub=%r)",
            self._broker_host,
            self._broker_port,
            self._subscribe_topic,
            self._publish_topic,
        )

        # Enable action bridge (issue #296)
        self._action_bridge.enable()

        if self._publish_telemetry:
            self._start_time = time.time()
            self._telemetry_stop.clear()
            self._telemetry_thread = threading.Thread(
                target=self._telemetry_loop, daemon=True, name="mqtt-telemetry"
            )
            self._telemetry_thread.start()
            logger.info("MQTT telemetry publisher started at %.1f Hz", self._telemetry_hz)

    async def stop(self):
        """Disconnect from the MQTT broker."""
        self._running = False
        # Disable action bridge before disconnecting (issue #296)
        self._action_bridge.disable()
        if self._publish_telemetry and self._telemetry_thread is not None:
            self._telemetry_stop.set()
            self._telemetry_thread.join(timeout=2)
            self._telemetry_thread = None
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        logger.info("MQTT channel disconnected")

    async def send_message(self, chat_id: str, text: str):
        """Publish *text* to the configured publish topic."""
        if self._client and self._connected.is_set():
            payload = text.encode() if isinstance(text, str) else text
            self._client.publish(self._publish_topic, payload, qos=self._qos)

    def get_action_bridge(self) -> MQTTActionBridge:
        """Return the :class:`MQTTActionBridge` instance wired to this channel."""
        return self._action_bridge

    # ── MQTT callbacks (execute in paho's internal thread) ────────────────────

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(self._subscribe_topic, qos=self._qos)
            # Also subscribe to the REST bridge command topic
            client.subscribe(self._command_topic, qos=self._qos)
            self._connected.set()
            logger.debug(
                "MQTT connected (rc=%d), subscribed to %r and command bridge %r",
                rc,
                self._subscribe_topic,
                self._command_topic,
            )
        else:
            logger.error("MQTT connect failed: rc=%d", rc)

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logger.warning("MQTT unexpected disconnect (rc=%d), will auto-reconnect", rc)
        self._connected.clear()

    def _on_message(self, client, userdata, msg):
        """Dispatch an inbound MQTT message to the brain callback.

        Messages arriving on the REST bridge command_topic are forwarded
        directly to the brain as /api/command instructions and results are
        published to command_response_topic.
        """
        if not self._running:
            return

        try:
            payload = msg.payload.decode("utf-8", errors="replace").strip()
        except Exception:
            payload = repr(msg.payload)

        chat_id = msg.topic
        logger.debug("MQTT message on %r: %.80s", chat_id, payload)

        # REST bridge: command_topic → brain → command_response_topic
        if msg.topic == self._command_topic:
            self._handle_command_bridge(payload)
            return

        # Action bridge: action_topic → MQTTActionBridge → result_topic (issue #296)
        if msg.topic == self._action_bridge.action_topic and self._action_bridge.is_enabled:
            self._action_bridge._on_action_message(client, userdata, msg)
            return

        if self._loop and not self._loop.is_closed():
            future = asyncio.run_coroutine_threadsafe(
                self.handle_message(chat_id, payload), self._loop
            )
            try:
                reply = future.result(timeout=30.0)
                if reply and self._client:
                    self._client.publish(self._publish_topic, reply.encode(), qos=self._qos)
                    # Store last action for telemetry publisher
                    try:
                        parsed = json.loads(reply)
                        if isinstance(parsed, dict) and "action" in parsed:
                            self._last_action = parsed["action"]
                        elif isinstance(parsed, dict):
                            self._last_action = parsed
                    except (json.JSONDecodeError, TypeError):
                        self._last_action = {"text": reply[:200]}
            except Exception as exc:
                logger.warning("MQTT message handling error: %s", exc)

    def _telemetry_loop(self) -> None:
        """Background thread: publish robot state to MQTT at ``telemetry_hz`` Hz."""
        interval = 1.0 / max(0.1, self._telemetry_hz)
        robot = self._config.get("metadata", {}).get("robot_name", "robot")
        while not self._telemetry_stop.is_set():
            try:
                uptime = time.time() - (self._start_time or time.time())
                self._client.publish(
                    f"opencastor/{robot}/status",
                    json.dumps({"running": True, "uptime_s": round(uptime, 1)}),
                    qos=self._qos,
                )
                if self._last_action:
                    self._client.publish(
                        f"opencastor/{robot}/action",
                        json.dumps(self._last_action),
                        qos=self._qos,
                    )
            except Exception as exc:
                logger.warning("MQTT telemetry publish error: %s", exc)
            self._telemetry_stop.wait(interval)

    def _handle_command_bridge(self, payload: str) -> None:
        """Forward a command_topic message to the brain and publish the result.

        The payload may be plain text (treated as instruction) or a JSON object
        with an ``instruction`` key.
        """
        try:
            data = json.loads(payload)
            instruction = data.get("instruction", payload) if isinstance(data, dict) else payload
        except (json.JSONDecodeError, TypeError):
            instruction = payload

        if not self._loop or self._loop.is_closed():
            return

        async def _run():
            return await self.handle_message(self._command_topic, instruction)

        future = asyncio.run_coroutine_threadsafe(_run(), self._loop)
        try:
            reply = future.result(timeout=30.0)
            if self._client:
                response_payload = (
                    json.dumps({"response": reply, "instruction": instruction})
                    if reply
                    else json.dumps({"error": "no response"})
                )
                self._client.publish(
                    self._command_response_topic,
                    response_payload.encode(),
                    qos=self._qos,
                )
        except Exception as exc:
            logger.warning("MQTT command bridge error: %s", exc)

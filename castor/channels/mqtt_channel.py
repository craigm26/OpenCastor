"""
castor/channels/mqtt_channel.py — MQTT channel bridge (issue #98).

Connects to an MQTT broker and routes messages between the broker and the
robot brain.  Inbound messages on the subscribe topic are forwarded to the
brain callback; replies are published to the publish topic.

Required config:
    broker_host:     MQTT broker hostname (default: localhost)

Optional config:
    broker_port:     Broker port (default: 1883)
    subscribe_topic: Topic to subscribe (default: opencastor/input)
    publish_topic:   Response topic (default: opencastor/output)
    username:        MQTT username  (or env MQTT_USERNAME)
    password:        MQTT password  (or env MQTT_PASSWORD)
    client_id:       MQTT client ID (default: opencastor-<pid>)
    keepalive:       Keepalive in seconds (default: 60)
    qos:             QoS level 0/1/2 (default: 0)
    tls:             Enable TLS (default: false)

Install::

    pip install opencastor[mqtt]   # or: pip install paho-mqtt

RCAN config example::

    channels:
      - type: mqtt
        broker_host: mqtt.example.com
        subscribe_topic: opencastor/input
        publish_topic: opencastor/output
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Callable, Optional

from castor.channels.base import BaseChannel

logger = logging.getLogger("OpenCastor.Channel.MQTT")

try:
    import paho.mqtt.client as _mqtt_client

    HAS_PAHO = True
except ImportError:
    HAS_PAHO = False


class MQTTChannel(BaseChannel):
    """MQTT channel: subscribe/publish bot powered by paho-mqtt.

    Forwards messages from an MQTT topic to the robot brain and publishes
    replies back to a configurable response topic.
    """

    name = "mqtt"

    def __init__(self, config: dict, on_message: Optional[Callable] = None):
        super().__init__(config, on_message)
        self._broker_host = config.get("broker_host", os.getenv("MQTT_BROKER_HOST", "localhost"))
        self._broker_port = int(config.get("broker_port", os.getenv("MQTT_BROKER_PORT", "1883")))
        self._subscribe_topic = config.get("subscribe_topic", "opencastor/input")
        self._publish_topic = config.get("publish_topic", "opencastor/output")
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

    async def stop(self):
        """Disconnect from the MQTT broker."""
        self._running = False
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

    # ── MQTT callbacks (execute in paho's internal thread) ────────────────────

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(self._subscribe_topic, qos=self._qos)
            self._connected.set()
            logger.debug("MQTT connected (rc=%d), subscribed to %r", rc, self._subscribe_topic)
        else:
            logger.error("MQTT connect failed: rc=%d", rc)

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logger.warning("MQTT unexpected disconnect (rc=%d), will auto-reconnect", rc)
        self._connected.clear()

    def _on_message(self, client, userdata, msg):
        """Dispatch an inbound MQTT message to the brain callback."""
        if not self._running:
            return

        try:
            payload = msg.payload.decode("utf-8", errors="replace").strip()
        except Exception:
            payload = repr(msg.payload)

        chat_id = msg.topic
        logger.debug("MQTT message on %r: %.80s", chat_id, payload)

        if self._loop and not self._loop.is_closed():
            future = asyncio.run_coroutine_threadsafe(
                self.handle_message(chat_id, payload), self._loop
            )
            try:
                reply = future.result(timeout=30.0)
                if reply and self._client:
                    self._client.publish(self._publish_topic, reply.encode(), qos=self._qos)
            except Exception as exc:
                logger.warning("MQTT message handling error: %s", exc)

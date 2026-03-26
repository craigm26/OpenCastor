"""
castor.channels.rcan_mqtt_transport — RCAN-native MQTT carrier.

Delivers RCAN messages between robots over MQTT using compact/minimal encoding.
No HTTP required. Works on local LAN with a Mosquitto broker.

Topic structure:
  rcan/{rrn}/in   — inbound messages (compact encoding)
  rcan/{rrn}/out  — outbound from this robot
  rcan/estop      — global ESTOP broadcast (32-byte minimal, QoS 2)
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable

log = logging.getLogger("OpenCastor.RCANMQTTTransport")

# Try to import rcan transport, fall back to JSON
try:
    from rcan.transport import decode_compact, encode_compact

    _HAS_RCAN = True
except ImportError:
    _HAS_RCAN = False

try:
    from rcan.transport import decode_minimal, encode_minimal

    _HAS_MINIMAL = True
except ImportError:
    _HAS_MINIMAL = False

try:
    import paho.mqtt.client as mqtt  # type: ignore[import-untyped]

    _HAS_PAHO = True
except ImportError:
    _HAS_PAHO = False


class RCANMQTTTransport:
    """RCAN-native MQTT carrier with compact/minimal encoding."""

    def __init__(
        self,
        config: dict[str, Any],
        local_rrn: str,
        on_message: Callable[[dict, bool], None] | None = None,
    ) -> None:
        self._config = config
        self._local_rrn = local_rrn
        self._on_message = on_message
        self._client: Any | None = None
        self._connected = False
        self._lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def _estop_topic(self) -> str:
        return "rcan/estop"

    def _inbound_topic(self, rrn: str) -> str:
        return f"rcan/{rrn}/in"

    def _outbound_topic(self, rrn: str) -> str:
        return f"rcan/{rrn}/out"

    def _encode(self, msg: dict, encoding: str = "compact") -> bytes:
        """Encode a message dict for MQTT payload."""
        if encoding == "compact" and _HAS_RCAN:
            try:
                from rcan import RCANMessage

                rcan_msg = RCANMessage(**msg)
                return encode_compact(rcan_msg)
            except Exception:
                pass
        return json.dumps(msg).encode()

    def _decode(self, payload: bytes) -> dict:
        """Decode an MQTT payload to message dict."""
        if _HAS_RCAN:
            try:
                return decode_compact(payload)
            except Exception:
                pass
        return json.loads(payload)

    def _encode_estop(self, msg: dict) -> bytes:
        """Encode ESTOP using minimal 32-byte format."""
        if _HAS_MINIMAL:
            try:
                from rcan import RCANMessage

                rcan_msg = RCANMessage(cmd="ESTOP", target=msg.get("target", ""))
                return encode_minimal(rcan_msg)
            except Exception:
                pass
        return json.dumps({"cmd": "ESTOP"}).encode()

    def connect(self) -> None:
        """Connect to MQTT broker and subscribe to own topics."""
        if not _HAS_PAHO:
            log.warning("paho-mqtt not installed — RCAN MQTT transport disabled")
            return

        broker = self._config.get("broker_host", "localhost")
        port = self._config.get("broker_port", 1883)

        self._client = mqtt.Client(
            client_id=f"opencastor-rcan-{self._local_rrn}",
            protocol=mqtt.MQTTv311,
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_mqtt_message

        try:
            self._client.connect(broker, port, keepalive=60)
            self._client.loop_start()
        except Exception as exc:
            log.error("RCAN MQTT connect failed: %s", exc)

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: int) -> None:
        if rc == 0:
            self._connected = True
            # Subscribe to own inbound + global ESTOP
            client.subscribe(self._inbound_topic(self._local_rrn), qos=1)
            client.subscribe(self._estop_topic, qos=2)
            log.info(
                "RCAN MQTT connected to broker, subscribed to %s + %s",
                self._inbound_topic(self._local_rrn),
                self._estop_topic,
            )
        else:
            log.error("RCAN MQTT connect failed with rc=%d", rc)

    def _on_mqtt_message(self, client: Any, userdata: Any, mqtt_msg: Any) -> None:
        """Handle incoming MQTT message."""
        is_estop = mqtt_msg.topic == self._estop_topic
        try:
            if is_estop and _HAS_MINIMAL:
                decoded = decode_minimal(mqtt_msg.payload)
            else:
                decoded = self._decode(mqtt_msg.payload)
            if self._on_message:
                self._on_message(decoded, is_estop)
        except Exception as exc:
            log.warning("Failed to decode RCAN MQTT message: %s", exc)

    def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        self._connected = False
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()

    def send(self, msg: dict, peer_rrn: str) -> None:
        """Send an RCAN message to a specific peer."""
        if not self._client or not self._connected:
            log.warning("RCAN MQTT not connected — cannot send to %s", peer_rrn)
            return
        payload = self._encode(msg)
        topic = self._inbound_topic(peer_rrn)
        self._client.publish(topic, payload, qos=1)

    def broadcast_estop(self, msg: dict | None = None) -> None:
        """Broadcast ESTOP on the global estop topic at QoS 2."""
        if not self._client or not self._connected:
            log.warning("RCAN MQTT not connected — cannot broadcast ESTOP")
            return
        payload = self._encode_estop(msg or {"cmd": "ESTOP"})
        self._client.publish(self._estop_topic, payload, qos=2)

    def subscribe_peer(self, peer_rrn: str) -> None:
        """Subscribe to a peer's outbound topic."""
        if self._client and self._connected:
            self._client.subscribe(self._outbound_topic(peer_rrn), qos=1)

"""Tests for castor.channels.rcan_mqtt_transport."""

import json

from castor.channels.rcan_mqtt_transport import RCANMQTTTransport


def test_import():
    """RCANMQTTTransport can be imported."""
    assert RCANMQTTTransport is not None


def test_not_connected_initially():
    """Transport is not connected until connect() is called."""
    t = RCANMQTTTransport({"broker_host": "localhost"}, local_rrn="RRN-000000000001")
    assert not t.is_connected


def test_topic_for_peer():
    """Topic names follow rcan/{rrn}/in pattern."""
    t = RCANMQTTTransport({"broker_host": "localhost"}, local_rrn="RRN-000000000001")
    assert t._inbound_topic("RRN-000000000005") == "rcan/RRN-000000000005/in"
    assert t._outbound_topic("RRN-000000000005") == "rcan/RRN-000000000005/out"


def test_estop_topic():
    """ESTOP topic is 'rcan/estop'."""
    t = RCANMQTTTransport({"broker_host": "localhost"}, local_rrn="RRN-000000000001")
    assert t._estop_topic == "rcan/estop"


def test_encode_produces_bytes():
    """Encoding produces bytes output."""
    t = RCANMQTTTransport({"broker_host": "localhost"}, local_rrn="RRN-000000000001")
    msg = {"cmd": "PING", "msg_id": "test-1", "target": "rcan://r/a/b/v1/x"}
    payload = t._encode(msg)
    assert isinstance(payload, bytes)
    assert len(payload) > 0


def test_compact_smaller_than_json():
    """Compact encoding is smaller than JSON (if rcan available)."""
    t = RCANMQTTTransport({"broker_host": "localhost"}, local_rrn="RRN-000000000001")
    msg = {
        "cmd": "navigate",
        "msg_id": "test-2",
        "target": "rcan://r/a/b/v1/x",
        "body": {"x": 1.0, "y": 2.0},
    }
    payload = t._encode(msg, encoding="compact")
    json_bytes = json.dumps(msg).encode()
    # If rcan is installed, compact should be smaller; if not, they're equal (both JSON)
    assert len(payload) <= len(json_bytes)


def test_encode_estop():
    """ESTOP encoding produces bytes."""
    t = RCANMQTTTransport({"broker_host": "localhost"}, local_rrn="RRN-000000000001")
    payload = t._encode_estop({"cmd": "ESTOP", "target": "rcan://r/a/b/v1/x"})
    assert isinstance(payload, bytes)
    assert len(payload) > 0

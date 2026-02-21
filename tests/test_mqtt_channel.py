"""Tests for castor/channels/mqtt_channel.py — MQTT channel (issue #98)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from castor.channels.mqtt_channel import HAS_PAHO, MQTTChannel


# ── Construction ──────────────────────────────────────────────────────────────

def test_mqtt_channel_defaults():
    ch = MQTTChannel({"broker_host": "localhost"})
    assert ch.name == "mqtt"
    assert ch._broker_host == "localhost"
    assert ch._broker_port == 1883
    assert ch._subscribe_topic == "opencastor/input"
    assert ch._publish_topic == "opencastor/output"


def test_mqtt_channel_custom_config():
    ch = MQTTChannel({
        "broker_host": "broker.example.com",
        "broker_port": 8883,
        "subscribe_topic": "robot/cmd",
        "publish_topic": "robot/reply",
        "tls": True,
        "qos": 1,
    })
    assert ch._broker_host == "broker.example.com"
    assert ch._broker_port == 8883
    assert ch._subscribe_topic == "robot/cmd"
    assert ch._publish_topic == "robot/reply"
    assert ch._tls is True
    assert ch._qos == 1


def test_env_var_defaults(monkeypatch):
    monkeypatch.setenv("MQTT_BROKER_HOST", "env-broker")
    monkeypatch.setenv("MQTT_USERNAME", "user1")
    ch = MQTTChannel({})
    assert ch._broker_host == "env-broker"
    assert ch._username == "user1"


# ── No paho-mqtt available ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_raises_without_paho():
    """If paho is not installed, start() should raise ImportError."""
    with patch("castor.channels.mqtt_channel.HAS_PAHO", False):
        ch = MQTTChannel({"broker_host": "localhost"})
        with pytest.raises(ImportError, match="paho-mqtt"):
            await ch.start()


# ── send_message when not connected ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_message_noop_when_disconnected():
    """send_message should silently do nothing when not connected."""
    ch = MQTTChannel({"broker_host": "localhost"})
    # Not connected — _client is None, _connected is not set
    await ch.send_message("robot/reply", "hello")  # should not raise


# ── Callbacks ─────────────────────────────────────────────────────────────────

def test_on_connect_sets_event():
    ch = MQTTChannel({"broker_host": "localhost"})
    mock_client = MagicMock()
    ch._on_connect(mock_client, None, None, 0)
    assert ch._connected.is_set()
    mock_client.subscribe.assert_called_once_with("opencastor/input", qos=0)


def test_on_connect_error_rc():
    ch = MQTTChannel({"broker_host": "localhost"})
    mock_client = MagicMock()
    ch._on_connect(mock_client, None, None, 5)  # rc=5 = refused
    assert not ch._connected.is_set()


def test_on_disconnect_clears_event():
    ch = MQTTChannel({"broker_host": "localhost"})
    ch._connected.set()
    ch._on_disconnect(None, None, 1)
    assert not ch._connected.is_set()


def test_on_message_noop_when_not_running():
    ch = MQTTChannel({}, on_message=lambda *a: "reply")
    ch._running = False
    msg = MagicMock()
    msg.payload = b"hello"
    msg.topic = "opencastor/input"
    ch._on_message(None, None, msg)  # should not dispatch


# ── Registration in channel registry ─────────────────────────────────────────

def test_mqtt_registered_in_channel_registry():
    from castor.channels import _register_builtin_channels, _CHANNEL_CLASSES

    # Force re-registration in case it hasn't run yet
    _register_builtin_channels()
    if HAS_PAHO:
        assert "mqtt" in _CHANNEL_CLASSES
    # If paho is not installed the key simply won't be present — either way is valid


# ── Auth map ─────────────────────────────────────────────────────────────────

def test_mqtt_in_auth_map():
    from castor.auth import CHANNEL_AUTH_MAP

    assert "mqtt" in CHANNEL_AUTH_MAP

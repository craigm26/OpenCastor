"""Tests for Signal Messenger channel integration (Issue #285)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from castor.channels.signal_channel import SignalChannel


def make_channel(extra=None):
    config = {
        "api_url": "http://localhost:8080",
        "sender": "+12025551234",
        "recipient": "+19995550001",
        **(extra or {}),
    }
    return SignalChannel(config)


# ── Instantiation tests ───────────────────────────────────────────────────────


def test_channel_name():
    ch = make_channel()
    assert ch.name == "signal"


def test_channel_init_default_api_url():
    ch = SignalChannel({"sender": "+1111"})
    assert ch._api_url.startswith("http")


def test_channel_init_custom_api_url():
    ch = SignalChannel({"api_url": "http://custom:9090", "sender": "+1111"})
    assert "custom:9090" in ch._api_url


def test_channel_init_sender_from_config():
    ch = make_channel()
    assert ch._sender == "+12025551234"


def test_channel_init_not_running():
    ch = make_channel()
    assert ch._running is False


def test_channel_init_poll_interval_default():
    ch = make_channel()
    assert ch._poll_interval_s == pytest.approx(1.0)


def test_channel_init_custom_poll_interval():
    ch = make_channel({"poll_interval_s": "2.5"})
    assert ch._poll_interval_s == pytest.approx(2.5)


# ── start/stop tests ──────────────────────────────────────────────────────────


def test_start_sets_running():
    ch = make_channel()
    with patch.object(ch, "_poll_loop"):
        ch.start()
        assert ch._running is True
        ch.stop()


def test_stop_clears_running():
    ch = make_channel()
    ch._running = True
    ch.stop()
    assert ch._running is False


def test_start_is_idempotent():
    ch = make_channel()
    with patch.object(ch, "_poll_loop"):
        ch.start()
        ch.start()  # Second call should not spawn a second thread
        ch.stop()


# ── send_message tests ────────────────────────────────────────────────────────


def test_send_message_calls_post():
    ch = make_channel()
    with patch.object(ch, "_post", return_value={"status": "ok"}) as mock_post:
        result = ch.send_message("Hello robot!", recipient="+19995550001")
    assert result is True
    mock_post.assert_called_once()
    call_args = mock_post.call_args
    assert "/v2/send" in call_args[0]


def test_send_message_returns_false_on_error():
    ch = make_channel()
    with patch.object(ch, "_post", return_value=None):
        result = ch.send_message("Hello!")
    assert result is False


def test_send_message_no_recipient_returns_false():
    ch = SignalChannel({"sender": "+1234", "api_url": "http://localhost:8080"})
    with patch.object(ch, "_post", return_value=None):
        result = ch.send_message("Hello!")
    assert result is False


def test_send_message_with_group_id():
    ch = make_channel()
    with patch.object(ch, "_post", return_value={"status": "ok"}) as mock_post:
        result = ch.send_message("group msg", group_id="group123")
    assert result is True
    payload = mock_post.call_args[0][1]
    assert "group_id" in payload


def test_send_message_payload_contains_message():
    ch = make_channel()
    with patch.object(ch, "_post", return_value={"status": "ok"}) as mock_post:
        ch.send_message("test message")
    payload = mock_post.call_args[0][1]
    assert payload["message"] == "test message"


def test_send_message_payload_contains_sender():
    ch = make_channel()
    with patch.object(ch, "_post", return_value={"status": "ok"}) as mock_post:
        ch.send_message("hello")
    payload = mock_post.call_args[0][1]
    assert payload["number"] == "+12025551234"


# ── Receive and dispatch tests ────────────────────────────────────────────────


def test_receive_messages_returns_empty_when_no_sender():
    ch = SignalChannel({"api_url": "http://localhost:8080"})
    msgs = ch._receive_messages()
    assert msgs == []


def test_receive_messages_returns_list():
    ch = make_channel()
    with patch.object(ch, "_get", return_value=[{"envelope": "test"}]):
        msgs = ch._receive_messages()
    assert isinstance(msgs, list)


def test_dispatch_envelope_calls_callback():
    received = []

    def on_msg(channel, chat_id, text):
        received.append((channel, chat_id, text))
        return "reply"

    ch = make_channel()
    ch._on_message_callback = on_msg

    envelope = {
        "source": "+19001111111",
        "dataMessage": {"message": "hello robot"},
    }
    with patch.object(ch, "send_message"):
        ch._dispatch_envelope(envelope)

    assert len(received) == 1
    assert received[0][2] == "hello robot"


def test_dispatch_envelope_sends_reply():
    def on_msg(channel, chat_id, text):
        return "reply text"

    ch = make_channel()
    ch._on_message_callback = on_msg

    envelope = {
        "source": "+19001111111",
        "dataMessage": {"message": "hello"},
    }
    with patch.object(ch, "send_message") as mock_send:
        ch._dispatch_envelope(envelope)
    mock_send.assert_called_once()
    assert mock_send.call_args[0][0] == "reply text"


def test_dispatch_envelope_skips_empty_message():
    received = []
    ch = make_channel()
    ch._on_message_callback = lambda *a: received.append(a)

    envelope = {"source": "+1111", "dataMessage": {"message": ""}}
    ch._dispatch_envelope(envelope)
    assert not received


def test_channel_registered_in_channels_init():
    # Force re-register
    import castor.channels as cc
    from castor.channels import get_available_channels

    cc._CHANNEL_CLASSES.clear()
    available = get_available_channels()
    assert "signal" in available

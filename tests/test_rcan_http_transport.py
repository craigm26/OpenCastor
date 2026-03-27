"""Tests for castor.rcan.http_transport — RCAN HTTP federation transport."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

from castor.rcan.http_transport import discover_robot, send_message

# ---------------------------------------------------------------------------
# send_message tests
# ---------------------------------------------------------------------------


def test_send_message_unreachable_host_returns_none():
    """send_message returns None when the host is not reachable (URLError)."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        result = send_message(
            "unreachable.local", {"type": 1, "source": "rcan://x", "target": "rcan://y"}
        )
    assert result is None


def test_send_message_http_error_returns_none():
    """send_message returns None on HTTP 4xx/5xx errors."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://example.local:8000/api/rcan/message",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=None,
        )
        result = send_message(
            "example.local", {"type": 2, "source": "rcan://x", "target": "rcan://y"}
        )
    assert result is None


def test_send_message_timeout_returns_none():
    """send_message returns None on timeout (TimeoutError wrapped as URLError)."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = TimeoutError("timed out")
        result = send_message(
            "slow.local", {"type": 1, "source": "rcan://x", "target": "rcan://y"}, timeout_s=0.001
        )
    assert result is None


def test_send_message_success_returns_dict():
    """send_message returns parsed JSON dict on success."""
    response_data = {"status": "received", "type": 1, "source": "rcan://bob"}
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = send_message(
            "alex.local",
            {"type": 1, "source": "rcan://bob", "target": "rcan://alex"},
        )
    assert result == response_data


def test_send_message_includes_auth_header_when_token_provided():
    """send_message includes Authorization header when api_token is set."""
    response_data = {"status": "received"}
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    captured_requests = []

    def capture_urlopen(req, timeout=None):
        captured_requests.append(req)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=capture_urlopen):
        send_message("alex.local", {"type": 1}, api_token="secret-token")

    assert len(captured_requests) == 1
    assert captured_requests[0].get_header("Authorization") == "Bearer secret-token"


# ---------------------------------------------------------------------------
# discover_robot tests
# ---------------------------------------------------------------------------


def test_discover_robot_unreachable_returns_none():
    """discover_robot returns None when the host is unreachable."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urllib.error.URLError("Name or service not known")
        result = discover_robot("ghost.local")
    assert result is None


def test_discover_robot_returns_status_dict():
    """discover_robot returns parsed status dict on success."""
    status = {"ruri": "rcan://local/alex", "version": "2026.3.14.6"}
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(status).encode("utf-8")
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = discover_robot("alex.local")
    assert result == status


def test_discover_robot_timeout_returns_none():
    """discover_robot returns None on any exception (timeout, DNS, etc.)."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = Exception("timeout")
        result = discover_robot("slow.local", timeout_s=0.001)
    assert result is None

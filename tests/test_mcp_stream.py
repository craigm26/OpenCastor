"""Tests for stream_telemetry MCP tool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

STATUS_RESPONSE = {
    "telemetry": {
        "ws_telemetry_url": "ws://192.168.68.88:8001/ws/telemetry",
        "system": {"cpu_temp_c": 48.5, "ram_used_pct": 62.0, "disk_used_pct": 68.5},
        "model_runtime": {"tokens_per_sec": 25.0, "active_model": "claude-opus-4-6"},
    }
}

POLL_FRAME = {
    "cpu_temp_c": 49.0,
    "ram_used_pct": 63.5,
    "tokens_per_sec": 24.0,
}


def _status_mock(*args, **kwargs):
    m = MagicMock()
    m.raise_for_status = MagicMock()
    m.json.return_value = STATUS_RESPONSE
    return m


def _poll_mock(*args, **kwargs):
    m = MagicMock()
    m.raise_for_status = MagicMock()
    m.json.return_value = {"telemetry": POLL_FRAME, "system": POLL_FRAME}
    return m


# ── compute_stats ─────────────────────────────────────────────────────────────


def test_compute_stats_basic():
    from castor.mcp_server import _compute_stats

    frames = [
        {"cpu_temp_c": 48.0, "ram_used_pct": 60.0},
        {"cpu_temp_c": 50.0, "ram_used_pct": 62.0},
    ]
    stats = _compute_stats(frames, None)
    assert stats["cpu_temp_c"]["min"] == 48.0
    assert stats["cpu_temp_c"]["max"] == 50.0
    assert stats["cpu_temp_c"]["mean"] == 49.0
    assert stats["cpu_temp_c"]["samples"] == 2


def test_compute_stats_fields_filter():
    from castor.mcp_server import _compute_stats

    frames = [{"cpu_temp_c": 48.0, "ram_used_pct": 60.0}]
    stats = _compute_stats(frames, ["cpu_temp_c"])
    assert "cpu_temp_c" in stats
    assert "ram_used_pct" not in stats


def test_compute_stats_excludes_bools():
    from castor.mcp_server import _compute_stats

    frames = [{"online": True, "cpu_temp_c": 48.0}]
    stats = _compute_stats(frames, None)
    assert "online" not in stats
    assert "cpu_temp_c" in stats


def test_compute_stats_empty_frames():
    from castor.mcp_server import _compute_stats

    assert _compute_stats([], None) == {}


# ── poll_status_frames ────────────────────────────────────────────────────────


@patch("castor.mcp_server._gateway_url", return_value="http://localhost:8001")
@patch("httpx.get", side_effect=_poll_mock)
def test_poll_frames_returns_data(mock_get, mock_url):
    from castor.mcp_server import _poll_status_frames

    frames = _poll_status_frames(1)
    assert len(frames) >= 1
    assert "cpu_temp_c" in frames[0]


# ── stream_telemetry (fallback path) ─────────────────────────────────────────


@patch("castor.mcp_server._gateway_url", return_value="http://localhost:8001")
@patch("castor.mcp_server._collect_ws_frames", return_value=[])
@patch("castor.mcp_server._poll_status_frames", return_value=[POLL_FRAME, POLL_FRAME])
@patch("castor.mcp_server._default_rrn", return_value="RRN-000000000001")
def test_stream_telemetry_fallback_to_polling(mock_rrn, mock_poll, mock_ws, mock_url):
    from castor.mcp_server import stream_telemetry

    result = stream_telemetry(duration_s=2)
    assert result["frame_count"] == 2
    assert result["source"] == "polling"
    assert "cpu_temp_c" in result["stats"]


@patch("castor.mcp_server._gateway_url", return_value="http://localhost:8001")
@patch("httpx.get", side_effect=_status_mock)
@patch("castor.mcp_server._collect_ws_frames", return_value=[POLL_FRAME, POLL_FRAME, POLL_FRAME])
@patch("castor.mcp_server._default_rrn", return_value="RRN-000000000001")
def test_stream_telemetry_ws_path(mock_rrn, mock_ws, mock_get, mock_url):
    from castor.mcp_server import stream_telemetry

    result = stream_telemetry(duration_s=3)
    assert result["frame_count"] == 3
    assert result["source"] == "websocket"


@patch("castor.mcp_server._gateway_url", return_value="http://localhost:8001")
@patch("castor.mcp_server._collect_ws_frames", return_value=[])
@patch("castor.mcp_server._poll_status_frames", return_value=[])
@patch("castor.mcp_server._default_rrn", return_value="RRN-000000000001")
def test_stream_telemetry_no_data(mock_rrn, mock_poll, mock_ws, mock_url):
    from castor.mcp_server import stream_telemetry

    result = stream_telemetry()
    assert result["frame_count"] == 0
    assert result["stats"] == {}


def test_stream_telemetry_duration_capped():
    """duration_s should be capped at 60."""
    with (
        patch("castor.mcp_server._default_rrn", return_value="RRN-000000000001"),
        patch("castor.mcp_server._gateway_url", return_value="http://localhost:8001"),
        patch("castor.mcp_server._collect_ws_frames", return_value=[]),
        patch("castor.mcp_server._poll_status_frames", return_value=[]),
    ):
        from castor.mcp_server import stream_telemetry

        result = stream_telemetry(duration_s=9999)
        assert result["duration_s"] == 60

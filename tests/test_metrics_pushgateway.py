"""Tests for MetricsRegistry.push_to_gateway — issue #361."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


# ── push_to_gateway function ──────────────────────────────────────────────────


def test_push_to_gateway_returns_false_no_url(monkeypatch):
    monkeypatch.delenv("CASTOR_PROMETHEUS_PUSHGATEWAY", raising=False)
    from castor.metrics import push_to_gateway

    assert push_to_gateway() is False


def test_push_to_gateway_returns_false_empty_url(monkeypatch):
    monkeypatch.setenv("CASTOR_PROMETHEUS_PUSHGATEWAY", "")
    from castor.metrics import push_to_gateway

    assert push_to_gateway() is False


def test_push_to_gateway_uses_env_url(monkeypatch):
    monkeypatch.setenv("CASTOR_PROMETHEUS_PUSHGATEWAY", "http://localhost:9091")
    from castor.metrics import push_to_gateway, get_registry

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = push_to_gateway()
    assert result is True


def test_push_to_gateway_explicit_url():
    from castor.metrics import push_to_gateway

    mock_resp = MagicMock()
    mock_resp.status = 204
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = push_to_gateway(gateway_url="http://pushgateway:9091")
    assert result is True


def test_push_to_gateway_http_error_returns_false():
    from castor.metrics import push_to_gateway
    import urllib.error

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = push_to_gateway(gateway_url="http://bad-host:9091")
    assert result is False


def test_push_to_gateway_status_400_returns_false():
    from castor.metrics import push_to_gateway

    mock_resp = MagicMock()
    mock_resp.status = 400
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = push_to_gateway(gateway_url="http://pushgateway:9091")
    assert result is False


def test_push_to_gateway_custom_job_label():
    from castor.metrics import push_to_gateway

    captured_urls = []

    def _fake_urlopen(req, timeout=None):
        captured_urls.append(req.full_url)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        push_to_gateway(gateway_url="http://gw:9091", job="myrobot")

    assert len(captured_urls) == 1
    assert "myrobot" in captured_urls[0]


def test_push_to_gateway_uses_custom_registry():
    from castor.metrics import MetricsRegistry, push_to_gateway

    reg = MetricsRegistry()
    c = reg.counter("opencastor_loops_total")
    if c:
        c.inc(robot="test_custom")

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    captured_data = []

    def fake_urlopen(req, timeout=None):
        captured_data.append(req.data)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = push_to_gateway(gateway_url="http://gw:9091", registry=reg)

    assert result is True
    assert len(captured_data) == 1
    assert b"opencastor_loops_total" in captured_data[0]


def test_push_to_gateway_content_type_header():
    from castor.metrics import push_to_gateway

    headers_seen = {}

    def _fake_urlopen(req, timeout=None):
        headers_seen.update(dict(req.headers))
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        push_to_gateway(gateway_url="http://gw:9091")

    assert "Content-type" in headers_seen
    assert "text/plain" in headers_seen["Content-type"]


def test_push_to_gateway_timeout_respected():
    from castor.metrics import push_to_gateway

    timeouts_seen = []

    def _fake_urlopen(req, timeout=None):
        timeouts_seen.append(timeout)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        push_to_gateway(gateway_url="http://gw:9091", timeout=2.5)

    assert timeouts_seen == [2.5]


def test_push_to_gateway_url_format():
    from castor.metrics import push_to_gateway

    captured_urls = []

    def _fake_urlopen(req, timeout=None):
        captured_urls.append(req.full_url)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        push_to_gateway(gateway_url="http://gw:9091/", job="opencastor")

    assert captured_urls[0] == "http://gw:9091/metrics/job/opencastor"


# ── Importability ─────────────────────────────────────────────────────────────


def test_push_to_gateway_importable():
    from castor.metrics import push_to_gateway  # noqa: F401

    assert callable(push_to_gateway)

"""Tests for Prometheus / Grafana monitoring stack.

Issue #217 — prometheus.yml, grafana config, push_to_gateway.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Config file existence
# ---------------------------------------------------------------------------


class TestConfigFilesExist:
    def test_prometheus_yml_exists(self):
        p = Path("docker/prometheus/prometheus.yml")
        assert p.exists(), f"Expected {p} to exist"

    def test_grafana_datasource_yaml_exists(self):
        p = Path("docker/grafana/provisioning/datasources/prometheus.yaml")
        assert p.exists(), f"Expected {p} to exist"

    def test_grafana_dashboard_json_exists(self):
        p = Path("docker/grafana/provisioning/dashboards/castor.json")
        assert p.exists(), f"Expected {p} to exist"

    def test_grafana_dashboards_yaml_exists(self):
        p = Path("docker/grafana/provisioning/dashboards/dashboards.yaml")
        assert p.exists(), f"Expected {p} to exist"


# ---------------------------------------------------------------------------
# Prometheus YAML content
# ---------------------------------------------------------------------------


class TestPrometheusYAML:
    def _load(self):
        import yaml

        return yaml.safe_load(Path("docker/prometheus/prometheus.yml").read_text())

    def test_has_global_section(self):
        cfg = self._load()
        assert "global" in cfg

    def test_has_scrape_interval(self):
        cfg = self._load()
        assert "scrape_interval" in cfg["global"]

    def test_has_scrape_configs(self):
        cfg = self._load()
        assert "scrape_configs" in cfg
        assert len(cfg["scrape_configs"]) >= 1

    def test_opencastor_job_configured(self):
        cfg = self._load()
        job_names = [j["job_name"] for j in cfg["scrape_configs"]]
        assert "opencastor" in job_names

    def test_metrics_path_correct(self):
        cfg = self._load()
        oc_job = next(j for j in cfg["scrape_configs"] if j["job_name"] == "opencastor")
        assert oc_job.get("metrics_path") == "/api/metrics"


# ---------------------------------------------------------------------------
# Grafana datasource YAML content
# ---------------------------------------------------------------------------


class TestGrafanaDatasource:
    def _load(self):
        import yaml

        return yaml.safe_load(
            Path("docker/grafana/provisioning/datasources/prometheus.yaml").read_text()
        )

    def test_has_api_version(self):
        cfg = self._load()
        assert cfg.get("apiVersion") == 1

    def test_has_prometheus_datasource(self):
        cfg = self._load()
        names = [d["name"] for d in cfg.get("datasources", [])]
        assert "Prometheus" in names

    def test_prometheus_url_is_prometheus_service(self):
        cfg = self._load()
        prom = next(d for d in cfg["datasources"] if d["name"] == "Prometheus")
        assert "prometheus" in prom["url"]

    def test_prometheus_is_default(self):
        cfg = self._load()
        prom = next(d for d in cfg["datasources"] if d["name"] == "Prometheus")
        assert prom.get("isDefault") is True


# ---------------------------------------------------------------------------
# Grafana dashboard JSON
# ---------------------------------------------------------------------------


class TestGrafanaDashboard:
    def _load(self):
        return json.loads(Path("docker/grafana/provisioning/dashboards/castor.json").read_text())

    def test_has_six_panels(self):
        dash = self._load()
        assert len(dash["panels"]) == 6

    def test_dashboard_has_uid(self):
        dash = self._load()
        assert dash.get("uid") == "opencastor-telemetry"

    def test_panels_have_titles(self):
        dash = self._load()
        titles = [p["title"] for p in dash["panels"]]
        assert all(t for t in titles)

    def test_latency_panel_exists(self):
        dash = self._load()
        titles = [p["title"] for p in dash["panels"]]
        assert any("Latency" in t or "latency" in t for t in titles)

    def test_error_panel_exists(self):
        dash = self._load()
        titles = [p["title"] for p in dash["panels"]]
        assert any("Error" in t or "error" in t for t in titles)


# ---------------------------------------------------------------------------
# push_to_gateway
# ---------------------------------------------------------------------------


class TestPushToGateway:
    def test_returns_false_when_no_url(self, monkeypatch):
        from castor.metrics import push_to_gateway

        monkeypatch.delenv("CASTOR_PROMETHEUS_PUSHGATEWAY", raising=False)
        result = push_to_gateway(gateway_url=None)
        assert result is False

    def test_uses_env_var(self, monkeypatch):
        from castor.metrics import push_to_gateway

        monkeypatch.setenv("CASTOR_PROMETHEUS_PUSHGATEWAY", "http://localhost:9091")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = push_to_gateway()
        assert result is True

    def test_explicit_url_overrides_env(self, monkeypatch):
        from castor.metrics import push_to_gateway

        monkeypatch.setenv("CASTOR_PROMETHEUS_PUSHGATEWAY", "http://wrong:9091")
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            push_to_gateway(gateway_url="http://correct:9091", job="test-job")
            call_url = mock_urlopen.call_args[0][0].full_url
        assert "correct" in call_url
        assert "test-job" in call_url

    def test_network_error_returns_false(self, monkeypatch):
        from castor.metrics import push_to_gateway

        monkeypatch.setenv("CASTOR_PROMETHEUS_PUSHGATEWAY", "http://localhost:9091")
        with patch("urllib.request.urlopen", side_effect=ConnectionError("refused")):
            result = push_to_gateway()
        assert result is False

    def test_custom_job_label(self, monkeypatch):
        from castor.metrics import push_to_gateway

        monkeypatch.setenv("CASTOR_PROMETHEUS_PUSHGATEWAY", "http://gw:9091")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as m:
            push_to_gateway(job="my-custom-job")
            url = m.call_args[0][0].full_url
        assert "my-custom-job" in url

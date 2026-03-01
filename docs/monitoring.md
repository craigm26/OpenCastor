# Monitoring Stack — Prometheus, Grafana & Jaeger

OpenCastor ships a complete observability stack for fleet-scale deployments.

## Quick Start

```bash
# Start gateway + Prometheus + Grafana
docker compose --profile monitoring up

# Start everything + Jaeger (distributed traces)
docker compose --profile monitoring --profile otel up
```

| Service    | URL                         | Default credentials |
|------------|-----------------------------|---------------------|
| Prometheus | http://localhost:9090       | —                   |
| Grafana    | http://localhost:3000       | admin / opencastor  |
| Jaeger UI  | http://localhost:16686      | —                   |

## Metrics Endpoint

Every OpenCastor gateway exposes Prometheus metrics at `GET /api/metrics`:

```
opencastor_loops_total{robot="alex"} 1234
opencastor_action_latency_ms_bucket{...}
opencastor_brain_up{robot="alex"} 1
opencastor_driver_up{robot="alex"} 1
opencastor_uptime_seconds{robot="alex"} 3600
opencastor_safety_score{robot="alex"} 0.95
opencastor_provider_errors_total{provider="google",error_type="timeout"} 2
```

## Grafana Dashboard — 6 Panels

The pre-built dashboard (`docker/grafana/provisioning/dashboards/castor.json`)
includes:

1. **Loop Latency p50 / p95** — histogram quantiles
2. **Commands per Minute** — API command rate
3. **Provider Health** — safety score gauge
4. **Active Driver Mode** — current driver mode stat
5. **Error Rate by Code** — errors grouped by HTTP code / provider
6. **Memory Episodes Count** — total SQLite episode store size

## Configuration

### Prometheus Scrape Config

Edit `docker/prometheus/prometheus.yml` to add your robots:

```yaml
scrape_configs:
  - job_name: "my-robot"
    static_configs:
      - targets: ["my-robot.local:8000"]
    metrics_path: "/api/metrics"
    scrape_interval: 5s
```

### Pushgateway (Short-lived jobs)

Set `CASTOR_PROMETHEUS_PUSHGATEWAY` in `.env` to push metrics from batch jobs:

```bash
CASTOR_PROMETHEUS_PUSHGATEWAY=http://localhost:9091
```

Then call from Python:

```python
from castor.metrics import push_to_gateway
push_to_gateway(job="castor-batch-calibration")
```

### OpenTelemetry Traces

Set these environment variables to enable distributed tracing:

```bash
OTEL_SERVICE_NAME=my-robot
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OPENCASTOR_OTEL_EXPORTER=otlp
```

Then initialise in your startup code:

```python
from castor.telemetry import init_otel
init_otel()
```

## RCAN Config

```yaml
telemetry:
  otel_endpoint: http://localhost:4317
  service_name: my-robot
  prometheus_pushgateway: http://localhost:9091
```

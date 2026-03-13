"""
castor/fleet_telemetry.py — Fleet dashboard with real-time telemetry charts.

Issue #200: Aggregates ``GET /api/metrics`` from all RCAN fleet peers and
exposes them via a single JSON endpoint + a Chart.js HTML dashboard.

API endpoints (registered in castor/api.py):
  GET /api/fleet/telemetry      — JSON {robots: [{name, url, metrics, ok}]}
  GET /api/fleet/dashboard      — HTML fleet dashboard (Chart.js)

Usage::

    from castor.fleet_telemetry import FleetAggregator
    agg = FleetAggregator([
        {"name": "alex", "url": "http://alex.local:8000"},
        {"name": "bob",  "url": "http://bob.local:8000"},
    ])
    snapshot = agg.fetch_all()   # → list[RobotSnapshot]
    payload  = agg.to_dict()     # → JSON-ready dict
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Optional
from urllib import request as _urllib_request

logger = logging.getLogger("OpenCastor.FleetTelemetry")

# Maximum age of a cached snapshot before it is considered stale
_CACHE_TTL_SECONDS: float = float(os.getenv("FLEET_TELEMETRY_CACHE_TTL", "10"))

# HTTP request timeout per robot
_FETCH_TIMEOUT: float = float(os.getenv("FLEET_TELEMETRY_FETCH_TIMEOUT", "5"))


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class RobotSnapshot:
    """A single telemetry snapshot from one fleet robot.

    Attributes:
        name:         Robot friendly name from RCAN config.
        base_url:     HTTP base URL of the robot's API server.
        ok:           True if the fetch succeeded.
        metrics_text: Raw Prometheus text from ``/api/metrics``.
        health:       Parsed dict from ``/health`` (empty if unavailable).
        latency_ms:   Fetch round-trip latency in milliseconds.
        error:        Error string if fetch failed.
        timestamp:    Unix timestamp of this snapshot.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        ok: bool,
        metrics_text: str = "",
        health: Optional[dict] = None,
        latency_ms: float = 0.0,
        error: str = "",
    ) -> None:
        self.name = name
        self.base_url = base_url
        self.ok = ok
        self.metrics_text = metrics_text
        self.health = health or {}
        self.latency_ms = latency_ms
        self.error = error
        self.timestamp = time.time()

    def extract_metric(self, metric_name: str) -> Optional[float]:
        """Extract a scalar metric value from the raw Prometheus text.

        Parses lines of the form ``<metric_name>{...} <value>``
        or ``<metric_name> <value>`` and returns the first match.

        Args:
            metric_name: Prometheus metric name (without labels).

        Returns:
            Float value, or ``None`` if not found.
        """
        for line in self.metrics_text.splitlines():
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            # Check if line starts with the metric name
            if not (line.startswith(metric_name + " ") or line.startswith(metric_name + "{")):
                continue
            # Extract value after the last space
            parts = line.rsplit(" ", 1)
            if len(parts) == 2:
                try:
                    return float(parts[1])
                except ValueError:
                    continue
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict.

        Returns a compact summary suitable for the fleet dashboard.
        """
        return {
            "name": self.name,
            "base_url": self.base_url,
            "ok": self.ok,
            "latency_ms": round(self.latency_ms, 1),
            "error": self.error,
            "timestamp": round(self.timestamp, 1),
            "health": self.health,
            "metrics": {
                "uptime_s": self.extract_metric("opencastor_uptime_seconds"),
                "loops_total": self.extract_metric("opencastor_loops_total"),
                "brain_up": self.extract_metric("opencastor_brain_up"),
                "driver_up": self.extract_metric("opencastor_driver_up"),
                "safety_score": self.extract_metric("opencastor_safety_score"),
                "active_channels": self.extract_metric("opencastor_active_channels"),
            },
        }


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


class FleetAggregator:
    """Fetches and caches telemetry from a list of fleet robots.

    Args:
        robots:  List of ``{"name": str, "url": str}`` dicts.
        threads: Number of parallel fetch threads (default: 8).
        ttl_s:   Cache TTL in seconds (default: from env ``FLEET_TELEMETRY_CACHE_TTL``).
    """

    def __init__(
        self,
        robots: list[dict[str, str]],
        threads: int = 8,
        ttl_s: float = _CACHE_TTL_SECONDS,
    ) -> None:
        self._robots = robots
        self._threads = threads
        self._ttl_s = ttl_s
        self._cache: dict[str, RobotSnapshot] = {}
        self._lock = threading.Lock()
        self._last_fetch: float = 0.0

    @classmethod
    def from_config(cls, config: dict) -> FleetAggregator:
        """Build a :class:`FleetAggregator` from an RCAN config dict.

        Reads the ``fleet.peers`` list:

        .. code-block:: yaml

            fleet:
              peers:
                - name: alex
                  url: http://alex.local:8000
                  token: ${ALEX_TOKEN}

        Args:
            config: Full RCAN config dict.

        Returns:
            New :class:`FleetAggregator`.
        """
        peers = config.get("fleet", {}).get("peers", [])
        robots = []
        for p in peers:
            url = p.get("url", "").rstrip("/")
            name = p.get("name", url)
            token = p.get("token", "")
            if token.startswith("${") and token.endswith("}"):
                token = os.getenv(token[2:-1], "")
            robots.append({"name": name, "url": url, "token": token})
        return cls(robots)

    def fetch_all(self, force: bool = False) -> list[RobotSnapshot]:
        """Fetch telemetry from all robots in parallel.

        Returns cached results if they are younger than *ttl_s*, unless
        *force* is ``True``.

        Args:
            force: Bypass the cache and always re-fetch.

        Returns:
            List of :class:`RobotSnapshot` (one per configured robot).
        """
        now = time.monotonic()
        if not force and (now - self._last_fetch) < self._ttl_s:
            with self._lock:
                return list(self._cache.values())

        snapshots: list[RobotSnapshot] = []
        lock = threading.Lock()

        def _fetch(robot: dict) -> None:
            snap = self._fetch_robot(robot)
            with lock:
                snapshots.append(snap)

        threads = [threading.Thread(target=_fetch, args=(r,), daemon=True) for r in self._robots]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=_FETCH_TIMEOUT + 2)

        with self._lock:
            self._cache = {s.name: s for s in snapshots}
            self._last_fetch = now

        return snapshots

    def to_dict(self, force: bool = False) -> dict[str, Any]:
        """Return a JSON-serialisable aggregate snapshot.

        Args:
            force: Bypass cache.

        Returns:
            ``{"robots": [...], "count": int, "healthy": int, "timestamp": float}``
        """
        snapshots = self.fetch_all(force=force)
        healthy = sum(1 for s in snapshots if s.ok)
        return {
            "robots": [s.to_dict() for s in sorted(snapshots, key=lambda x: x.name)],
            "count": len(snapshots),
            "healthy": healthy,
            "timestamp": round(time.time(), 1),
        }

    def _fetch_robot(self, robot: dict) -> RobotSnapshot:
        """Fetch metrics and health from a single robot.

        Args:
            robot: Dict with ``name``, ``url``, and optional ``token``.

        Returns:
            :class:`RobotSnapshot` regardless of success.
        """
        name = robot.get("name", "unknown")
        url = robot.get("url", "").rstrip("/")
        token = robot.get("token", "")
        t0 = time.monotonic()

        try:
            metrics_text = self._get(f"{url}/api/metrics", token)
        except Exception as exc:
            return RobotSnapshot(
                name=name,
                base_url=url,
                ok=False,
                latency_ms=(time.monotonic() - t0) * 1000,
                error=str(exc),
            )

        # Try health endpoint (non-fatal if missing)
        health: dict = {}
        try:
            health_raw = self._get(f"{url}/health", token)
            health = json.loads(health_raw) if isinstance(health_raw, str) else health_raw
        except Exception:
            pass

        latency_ms = (time.monotonic() - t0) * 1000
        return RobotSnapshot(
            name=name,
            base_url=url,
            ok=True,
            metrics_text=metrics_text if isinstance(metrics_text, str) else "",
            health=health,
            latency_ms=latency_ms,
        )

    def _get(self, url: str, token: str = "", timeout: float = _FETCH_TIMEOUT) -> str:
        """HTTP GET returning the response body as a string.

        Args:
            url:     Full URL.
            token:   Bearer token for Authorization header.
            timeout: Request timeout in seconds.

        Returns:
            Response body as UTF-8 string.
        """
        req = _urllib_request.Request(url)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with _urllib_request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")

    @property
    def robot_count(self) -> int:
        """Number of configured robots."""
        return len(self._robots)

    @property
    def robot_names(self) -> list[str]:
        """Sorted list of robot names."""
        return sorted(r.get("name", "") for r in self._robots)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_fleet_agg: Optional[FleetAggregator] = None
_fleet_lock = threading.Lock()


def get_fleet_aggregator(config: Optional[dict] = None) -> FleetAggregator:
    """Return the global :class:`FleetAggregator` singleton.

    Args:
        config: RCAN config dict (required on first call).

    Returns:
        Global :class:`FleetAggregator`.
    """
    global _fleet_agg
    with _fleet_lock:
        if _fleet_agg is None or config is not None:
            _fleet_agg = FleetAggregator.from_config(config or {})
    return _fleet_agg


def reset_fleet_aggregator() -> None:
    """Reset the global singleton (useful in tests)."""
    global _fleet_agg
    with _fleet_lock:
        _fleet_agg = None


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------

FLEET_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OpenCastor Fleet Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root{--bg:#0f1115;--card:#181c24;--border:#2d3442;--text:#e8edf7;
    --muted:#98a3ba;--accent:#66d9a3;--warn:#e9c46a;--err:#ef476f;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{font-family:'Segoe UI',Verdana,sans-serif;background:var(--bg);
    color:var(--text);padding:16px;}
  h1{color:var(--accent);margin-bottom:16px;font-size:1.4rem;}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px;}
  .card{background:var(--card);border:1px solid var(--border);border-radius:12px;
    padding:14px;}
  .card h3{font-size:0.9rem;margin-bottom:8px;color:var(--accent);}
  .ok{color:var(--accent);}  .err{color:var(--err);}  .warn{color:var(--warn);}
  .metric{font-size:0.8rem;color:var(--muted);margin:3px 0;}
  .metric span{color:var(--text);font-weight:600;}
  canvas{max-height:180px;}
  .last-update{font-size:0.75rem;color:var(--muted);margin-bottom:12px;}
  #summary{font-size:0.85rem;margin-bottom:14px;padding:8px 12px;
    background:var(--card);border-radius:8px;border:1px solid var(--border);}
</style>
</head>
<body>
<h1>🤖 OpenCastor Fleet Dashboard</h1>
<div class="last-update" id="last-update">Loading…</div>
<div id="summary"></div>
<div class="grid" id="grid"></div>

<script>
let charts = {};
let latencyHistory = {};

const POLL_MS = 5000;

async function fetchTelemetry() {
  try {
    const r = await fetch('/api/fleet/telemetry');
    const data = await r.json();
    render(data);
  } catch(e) {
    document.getElementById('last-update').textContent = 'Fetch error: ' + e;
  }
}

function render(data) {
  document.getElementById('last-update').textContent =
    'Last update: ' + new Date().toLocaleTimeString() +
    ' · ' + data.healthy + '/' + data.count + ' healthy';

  document.getElementById('summary').innerHTML =
    `<b>Fleet:</b> ${data.count} robots · ${data.healthy} healthy · ` +
    `<b>Avg latency:</b> ${avgLatency(data.robots).toFixed(0)} ms`;

  const grid = document.getElementById('grid');

  data.robots.forEach(robot => {
    let card = document.getElementById('card-' + robot.name);
    if (!card) {
      card = document.createElement('div');
      card.className = 'card';
      card.id = 'card-' + robot.name;
      card.innerHTML = cardHTML(robot);
      grid.appendChild(card);
      initChart(robot.name);
    } else {
      updateCard(card, robot);
    }
    updateLatencyHistory(robot);
    updateChart(robot.name);
  });
}

function cardHTML(r) {
  const cls = r.ok ? 'ok' : 'err';
  const m = r.metrics || {};
  return `
    <h3 class="${cls}">${r.ok ? '✅' : '❌'} ${r.name}</h3>
    <div class="metric">URL: <span>${r.base_url}</span></div>
    <div class="metric" id="${r.name}-latency">Latency: <span>${r.latency_ms} ms</span></div>
    <div class="metric" id="${r.name}-uptime">Uptime: <span>${fmtUptime(m.uptime_s)}</span></div>
    <div class="metric" id="${r.name}-safety">Safety: <span>${m.safety_score ?? 'N/A'}</span></div>
    <canvas id="chart-${r.name}"></canvas>
  `;
}

function updateCard(card, r) {
  const m = r.metrics || {};
  const set = (id, v) => { const el = document.getElementById(id); if(el) el.innerHTML = v; };
  set(r.name + '-latency', `Latency: <span>${r.latency_ms} ms</span>`);
  set(r.name + '-uptime', `Uptime: <span>${fmtUptime(m.uptime_s)}</span>`);
  set(r.name + '-safety', `Safety: <span>${m.safety_score ?? 'N/A'}</span>`);
}

function initChart(name) {
  latencyHistory[name] = [];
  const ctx = document.getElementById('chart-' + name);
  if (!ctx) return;
  charts[name] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: 'Latency ms',
        data: [],
        borderColor: '#66d9a3',
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.4,
        fill: true,
        backgroundColor: 'rgba(102,217,163,0.08)',
      }]
    },
    options: {
      animation: false,
      scales: { x: { display: false }, y: { min: 0 } },
      plugins: { legend: { display: false } },
    }
  });
}

function updateLatencyHistory(robot) {
  if (!latencyHistory[robot.name]) latencyHistory[robot.name] = [];
  const h = latencyHistory[robot.name];
  h.push({ t: new Date().toLocaleTimeString(), v: robot.latency_ms });
  if (h.length > 30) h.shift();
}

function updateChart(name) {
  const ch = charts[name];
  if (!ch) return;
  const h = latencyHistory[name] || [];
  ch.data.labels = h.map(p => p.t);
  ch.data.datasets[0].data = h.map(p => p.v);
  ch.update('none');
}

function avgLatency(robots) {
  if (!robots.length) return 0;
  return robots.reduce((s, r) => s + (r.latency_ms || 0), 0) / robots.length;
}

function fmtUptime(s) {
  if (s == null) return 'N/A';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? h + 'h ' + m + 'm' : m + 'm';
}

fetchTelemetry();
setInterval(fetchTelemetry, POLL_MS);
</script>
</body>
</html>
"""

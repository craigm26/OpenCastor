#!/usr/bin/env python3
"""Zero-dependency Prometheus & JSON fleet telemetry exporter for OpenCastor nodes.

Polls local/remote robot-md-gateway nodes (e.g. bob + ethan) and exposes:
  - GET /metrics  -> Prometheus text exposition format
  - GET /healthz  -> JSON summary of node reachability, voltage, temp, estop

Run:
    python3 deploy/fleet-ops/fleet_metrics.py --port 9108 --nodes bob=http://127.0.0.1:8001,ethan=http://ethan.local:8001
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


def poll_node(name: str, base_url: str, timeout_s: float = 2.0) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/api/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return {"node": name, "reachable": True, "url": base_url, "status": payload}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return {"node": name, "reachable": False, "url": base_url, "error": str(exc), "status": {}}


def render_prometheus(snapshots: list[dict[str, Any]]) -> str:
    lines = [
        "# HELP castor_node_reachable 1 if gateway status endpoint responded cleanly, else 0.",
        "# TYPE castor_node_reachable gauge",
        "# HELP castor_node_estopped 1 if node e-stop is latched, else 0.",
        "# TYPE castor_node_estopped gauge",
        "# HELP castor_servo_voltage_volts Live servo bus voltage in volts.",
        "# TYPE castor_servo_voltage_volts gauge",
        "# HELP castor_servo_temp_celsius Live servo temperature in Celsius.",
        "# TYPE castor_servo_temp_celsius gauge",
        "# HELP castor_servo_load Live servo present load.",
        "# TYPE castor_servo_load gauge",
    ]
    for snap in snapshots:
        node = snap["node"]
        reachable = 1 if snap.get("reachable") else 0
        lines.append(f'castor_node_reachable{{node="{node}"}} {reachable}')
        st = snap.get("status") or {}
        if "estopped" in st:
            lines.append(f'castor_node_estopped{{node="{node}"}} {1 if st["estopped"] else 0}')
        for joint, val in (st.get("motor_voltages_v") or {}).items():
            lines.append(f'castor_servo_voltage_volts{{node="{node}",joint="{joint}"}} {float(val)}')
        for joint, val in (st.get("motor_temps_c") or {}).items():
            lines.append(f'castor_servo_temp_celsius{{node="{node}",joint="{joint}"}} {float(val)}')
        for joint, val in (st.get("motor_loads") or {}).items():
            lines.append(f'castor_servo_load{{node="{node}",joint="{joint}"}} {float(val)}')
    return "\n".join(lines) + "\n"


def parse_nodes(spec: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        name, url = item.split("=", 1)
        out.append((name.strip(), url.strip()))
    return out


def make_handler(nodes: list[tuple[str, str]]):
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            snaps = [poll_node(n, u) for n, u in nodes]
            if self.path == "/metrics":
                body = render_prometheus(snaps).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/healthz":
                ok = all(s["reachable"] for s in snaps)
                body = json.dumps({"ok": ok, "nodes": snaps}, indent=2).encode("utf-8")
                self.send_response(200 if ok else 503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, fmt: str, *args) -> None:
            return

    return _Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenCastor fleet Prometheus/JSON metrics exporter.")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9108)
    ap.add_argument("--nodes", default="bob=http://127.0.0.1:8001,ethan=http://ethan.local:8001")
    args = ap.parse_args()
    nodes = parse_nodes(args.nodes)
    server = HTTPServer((args.host, args.port), make_handler(nodes))
    print(f"OpenCastor fleet_metrics listening on http://{args.host}:{args.port} (/metrics, /healthz)")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

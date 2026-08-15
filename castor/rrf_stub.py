"""Loopback RRF key resolver — serves the kid lookups a lone robot needs.

The gateway verifies its manifest's ROBOT-MD-SIG footer by resolving the kid
through OPENCASTOR_OPS_RRF_URL. On a fleet that is the Robot Registry; on a
single robot in a kitchen it is this: GET /v2/keys/<kid> answered from PEM
files in a directory. `castor up` installs it as a user unit and drops the
robot's own verify keys in.

Env: RRF_KEY_DIR (directory of <kid>.pem), RRF_PORT (default 8090).
"""
from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

KEY_DIR = Path(os.environ.get("RRF_KEY_DIR", str(Path.home() / "robot" / "keys" / "rrf")))
PORT = int(os.environ.get("RRF_PORT", "8090"))

# Tight on purpose: the kid becomes a filename, and this regex is what stands
# between a URL path and a directory traversal.
KID_RE = re.compile(r"^/v2/keys/([A-Za-z0-9._-]+)$")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
        m = KID_RE.match(self.path)
        pem = KEY_DIR / f"{m.group(1)}.pem" if m else None
        if pem is None or not pem.is_file():
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"kid": m.group(1),
                           "public_key_pem": pem.read_text()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep journald quiet
        pass


def main() -> None:
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()

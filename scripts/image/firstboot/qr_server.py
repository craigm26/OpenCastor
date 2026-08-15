#!/usr/bin/env python3
"""The robot's front door — one page, port 80, no terminal anywhere in sight.

WHAT THIS REPLACES. `castor up` ends by printing a filesystem path:
"Scan /var/lib/opencastor/robot/pair-qr.png with the OpenCastor app". That
sentence is fine for an operator with a shell. For the person this image is
for — a blank card, a Pi, a phone — it is a dead end: there is no terminal to
read it in and no file manager to open it with. The PNG has to become a URL,
and the URL has to be the machine's own name on port 80, because that is the
only address a first-timer can be told over the phone.

IT COMES UP EVEN WHEN NOTHING ELSE DID. This server has no ordering dependency
on the provisioning unit and reads its state from a file, at request time, on
every request. A first boot that failed in the middle still gets a page, and
the page says which half failed and what to do — that is the entire reason
firstboot.sh writes status.json on every exit path. A blank screen is the one
outcome this rail treats as a bug.

NO NEW DEPENDENCIES, DELIBERATELY. Nothing here is outside the standard
library, so the page works before the venv exists, during a half-finished
install, and on an image whose wheelhouse stage was skipped. A pairing kiosk
that needs the thing it is reporting on to be working is not a kiosk.

WHAT IS EXPOSED, STATED PLAINLY. The QR encodes an actuate-tier bearer, and
this server hands the PNG to anyone on the LAN who asks — unauthenticated, on
purpose, because the operator has no credential yet at minute three. The trust
model is a QR sticker on the robot's chassis: physical-ish presence on the home
network. It is NOT a secret. `pair-payload.json` is deliberately NOT served —
the bytes are the same, but a curl-able JSON token is a different class of
exposure from a picture you have to decode, and there is no reason to offer
both. Pair, then read docs/IMAGE.md on turning this page off.
"""
from __future__ import annotations

import html
import json
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATE = Path(os.environ.get("OC_STATE", "/var/lib/opencastor"))
ROBOT_HOME = Path(os.environ.get("ROBOT_HOME", str(STATE / "robot")))
STATUS = STATE / "status.json"
QR = ROBOT_HOME / "pair-qr.png"
PORT = int(os.environ.get("OPENCASTOR_QR_PORT", "80"))
BIND = os.environ.get("OPENCASTOR_QR_BIND", "0.0.0.0")

CSS = """
*{box-sizing:border-box}
body{margin:0;padding:2rem 1.25rem;font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
     background:#11131a;color:#e8eaf0;display:flex;justify-content:center}
main{width:100%;max-width:34rem}
h1{margin:0 0 .15rem;font-size:2rem;letter-spacing:-.02em}
.sub{margin:0 0 1.5rem;color:#9aa3b5;font-size:.95rem}
.card{background:#191c26;border:1px solid #262b3a;border-radius:14px;padding:1.25rem;margin-bottom:1rem}
.qr{background:#fff;border-radius:10px;padding:1rem;display:flex;justify-content:center}
.qr img{width:100%;max-width:20rem;height:auto;image-rendering:pixelated}
ol{margin:1rem 0 0;padding-left:1.2rem}
ol li{margin:.3rem 0}
.pill{display:inline-block;padding:.15rem .55rem;border-radius:999px;font-size:.78rem;
      font-weight:600;letter-spacing:.02em;text-transform:uppercase}
.ready{background:#123524;color:#5ee39a;border:1px solid #1d5a3d}
.work{background:#2a2412;color:#f0c674;border:1px solid #574818}
.bad{background:#361618;color:#ff8f8f;border:1px solid #6b2529}
.deg{margin:.6rem 0 0;padding:.7rem .85rem;background:#20161a;border-left:3px solid #b4494f;
     border-radius:0 8px 8px 0;font-size:.9rem;color:#ffc9c9}
.deg b{color:#ff9b9b;display:block;font-size:.78rem;letter-spacing:.04em;text-transform:uppercase}
code{background:#0d0f15;padding:.1rem .35rem;border-radius:5px;font-size:.85em;color:#a9d6ff}
footer{color:#5f6779;font-size:.8rem;margin-top:1.5rem;text-align:center}
"""


def read_status() -> dict:
    """Whatever the provisioner last managed to write. Never raises, always a dict.

    "Never raises" was only true of the json.loads call. `[]` and `"x"` and
    `null` are all valid JSON and none of them have .get(), so a truncated or
    hand-edited status file took page() down with an AttributeError and the
    operator got a 500 — the blank screen this whole rail exists to prevent,
    arrived at from the one direction nobody checked. The type is part of the
    contract, so it is enforced here rather than assumed by every caller.
    """
    try:
        parsed = json.loads(STATUS.read_text())
    except FileNotFoundError:
        return {"phase": "starting", "ok": False, "degraded": [], "robot_name": ""}
    except Exception as exc:  # noqa: BLE001 - an unreadable status is itself the news
        return {"phase": "unreadable", "ok": False, "robot_name": "",
                "degraded": [f"status: {STATUS} could not be read ({exc})"]}
    if not isinstance(parsed, dict):
        return {"phase": "unreadable", "ok": False, "robot_name": "",
                "degraded": [f"status: {STATUS} is {type(parsed).__name__}, not a JSON "
                             "object — the provisioner was interrupted mid-write, or "
                             "something else wrote the file"]}
    return parsed


def split_degradation(text: str) -> tuple[str, str]:
    """"slug: prose" -> ("SLUG", "prose"). Untagged lines keep the whole string."""
    slug, sep, rest = text.partition(":")
    if sep and " " not in slug.strip():
        return slug.strip().replace("-", " "), rest.strip()
    return "note", text


def page(status: dict) -> bytes:
    # str() around both: html.escape() raises on anything else, and a status
    # file is a file — it can hold whatever the last writer left behind.
    name = str(status.get("robot_name") or "your robot")
    phase = str(status.get("phase") or "starting")
    degraded = status.get("degraded") or []
    if not isinstance(degraded, list):
        # Same reasoning as read_status: a string here would be iterated one
        # character at a time and rendered as forty empty caveat boxes.
        degraded = [str(degraded)]
    qr_ready = QR.is_file()
    done = phase in ("done", "failed")

    if qr_ready and not degraded:
        pill, label = "ready", "ready to pair"
    elif qr_ready:
        pill, label = "work", "ready to pair, with notes"
    elif done:
        pill, label = "bad", "setup did not finish"
    else:
        pill, label = "work", f"setting up ({html.escape(phase)})"

    out = [
        "<!doctype html><html lang=en><meta charset=utf-8>",
        '<meta name=viewport content="width=device-width,initial-scale=1">',
        f"<title>{html.escape(name)} — OpenCastor</title>",
        f"<style>{CSS}</style>",
    ]
    if not done:
        # Only while something is still moving. A finished page that reloads
        # every four seconds is a page the operator cannot read a QR off.
        out.append('<meta http-equiv=refresh content=4>')
    out += [
        "<main>",
        f"<h1>{html.escape(name)}</h1>",
        f'<p class=sub><span class="pill {pill}">{label}</span></p>',
    ]

    if qr_ready:
        out += [
            "<div class=card><div class=qr>",
            '<img src="/pair-qr.png" alt="Pairing QR code">',
            "</div><ol>",
            "<li>Open the <b>OpenCastor</b> app on your phone and tap <b>Pair a robot</b>.</li>",
            "<li>Point the camera at this code. That is the whole setup.</li>",
            "</ol></div>",
        ]
    elif done:
        out += [
            "<div class=card><p>No pairing code was produced on this boot, so there is "
            "nothing to scan yet. The notes below say why.</p>",
            f"<p>Full log: <code>{html.escape(str(status.get('log', STATE / 'firstboot.log')))}</code></p></div>",
        ]
    else:
        out += [
            "<div class=card><p>This robot is setting itself up. The pairing code "
            "appears here on its own — keep this page open, it refreshes every few "
            "seconds. First boot usually takes about a minute.</p></div>",
        ]

    if degraded:
        out.append("<div class=card><p class=sub style='margin:0 0 .5rem'>"
                   "Working, with these caveats:</p>")
        for item in degraded:
            tag, prose = split_degradation(str(item))
            out.append(f"<div class=deg><b>{html.escape(tag)}</b>{html.escape(prose)}</div>")
        out.append("</div>")
    elif done and qr_ready:
        out.append("<div class=card><p style='margin:0'>Everything this Pi has "
                   "hardware for has a driver and a brain. No gaps.</p></div>")

    elapsed = status.get("elapsed_s")
    tail = f" · first boot took {elapsed}s" if isinstance(elapsed, int) else ""
    out += [
        f"<footer>OpenCastor · anyone on this network can see this code "
        f"until you pair{tail}</footer>",
        "</main>",
    ]
    return "\n".join(out).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "opencastor-qr/1"
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib's spelling
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/":
            self._send(200, page(read_status()), "text/html; charset=utf-8")
        elif path == "/pair-qr.png":
            try:
                self._send(200, QR.read_bytes(), "image/png")
            except OSError:
                self._send(404, b"no pairing code yet\n", "text/plain; charset=utf-8")
        elif path == "/status.json":
            body = (json.dumps(read_status(), indent=1) + "\n").encode()
            self._send(200, body, "application/json")
        elif path == "/healthz":
            self._send(200, b"ok\n", "text/plain; charset=utf-8")
        else:
            self._send(404, b"not found\n", "text/plain; charset=utf-8")

    do_HEAD = do_GET

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("qr %s %s\n" % (self.address_string(), fmt % args))


def main() -> int:
    class Server(ThreadingHTTPServer):
        allow_reuse_address = True
        address_family = socket.AF_INET
        daemon_threads = True

    with Server((BIND, PORT), Handler) as httpd:
        print(f"opencastor QR page on http://{BIND}:{PORT}/ "
              f"(status {STATUS}, qr {QR})", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""`python -m castor.console` — serve the console on this robot.

Binds 0.0.0.0 because the whole point is a phone on the same LAN reaching it.
Every endpoint but /console/health is behind the console bearer, and an unset
CONSOLE_TOKEN fails every request closed with a 503 rather than serving the
robot's brains to the network.
"""

from __future__ import annotations

from .app import build_app
from .config import console_port, console_token, robot_home


def main() -> None:
    import uvicorn

    if not console_token():
        # Not fatal — the 503 is the real enforcement — but a console nobody can
        # authenticate to is almost always a missing EnvironmentFile, and that
        # is worth saying once at start rather than once per request.
        print(
            "warning: CONSOLE_TOKEN is not set — every authenticated endpoint "
            "will answer 503 until it is (see <ROBOT_HOME>/console.env)"
        )
    port = console_port()
    print(f"OpenCastor console for {robot_home()} on :{port}")
    # ACCESS LOG OFF, deliberately. The console bearer may ride in the query
    # string (`?token=…`) so an <img src> can carry it, and uvicorn's access log
    # writes the full request line — which on a `castor up` host is journald,
    # readable by anyone in the systemd-journal group and copied verbatim into
    # every `journalctl` paste in a bug report. A request line is not worth a
    # credential; the endpoints report their own failures.
    uvicorn.run(build_app(), host="0.0.0.0", port=port, access_log=False)


if __name__ == "__main__":
    main()

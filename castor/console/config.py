"""Where the console's state lives, and what it is allowed to talk to.

EVERY SETTING IS READ AT CALL TIME, never captured in a module constant at
import. The bench modules this package was ported from read ``ROBOT_HOME`` once,
at import — which is why the rover's console had to poke the variable into the
environment *before* its first import, and why running a second robot on the
same host was a fork rather than a second unit. Reading late costs nothing and
makes one-process-per-robot the boring case.

``ROBOT_MANIFEST`` is deliberately NOT honoured here. The console's whole job is
to describe THIS robot, and the pairing QR describes the robot at
``<ROBOT_HOME>/ROBOT.md``; an env var that quietly pointed the two at different
documents would make the phone's picture of the robot disagree with the robot's
own, which is the exact failure /surface exists to end.
"""
from __future__ import annotations

import os
from pathlib import Path

#: Ollama's own address on this host, when the operator has not said otherwise.
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

#: `castor up`'s port layout is base_port + (0 gateway, 1 runtime, 2 console),
#: so a default-everything host serves the console here.
DEFAULT_CONSOLE_PORT = 8082


def robot_home() -> Path:
    """The robot's state directory — `castor up --home`, default ``~/robot``."""
    return Path(os.environ.get("ROBOT_HOME", str(Path.home() / "robot"))).expanduser()


def manifest_path() -> Path:
    """This robot's signed ROBOT.md. Always inside the robot home (see above)."""
    return robot_home() / "ROBOT.md"


def console_token() -> str:
    """The read-only console bearer. Empty means the console is unconfigured."""
    return os.environ.get("CONSOLE_TOKEN", "")


def console_port() -> int:
    try:
        return int(os.environ.get("CONSOLE_PORT", DEFAULT_CONSOLE_PORT))
    except ValueError:
        return DEFAULT_CONSOLE_PORT


def ollama_url() -> str:
    """Where the model daemon lives. Model management (tags/pull/ps) always goes
    here — those are not conversation, and routing them anywhere else buys
    nothing.

    Read at call time like everything else in this module, and for the same
    reason: the one host on this bench that runs Ollama somewhere other than its
    own loopback (a Pi driving a robot, weights on the workstation next to it)
    could not say so without editing the package. ``OLLAMA_URL`` in
    ``<ROBOT_HOME>/console.env`` is now the whole of that change.
    """
    return os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL)


def chat_upstream() -> str:
    """Where chat turns go. STRAIGHT TO OLLAMA unless told otherwise.

    On the bench this defaulted to a recording proxy on :4141 so reasoning was
    captured as replayable `thinking` blocks. That proxy is a bench extra, not
    something `pip install opencastor` brings, and defaulting to a port nothing
    is listening on turns every first chat into a 504. Operators who run the
    recorder set CHAT_UPSTREAM and get the traces back.
    """
    return os.environ.get("CHAT_UPSTREAM", ollama_url())

"""One ``robot-memory.md``, named the same way by every reader of it.

THE FAILURE THIS ENDS. Three pieces of this runtime open the robot's long-term
memory, and until this module they each named the file themselves:

  * ``castor.brain.robot_context`` — the reader the GATEWAY BRAIN sees, at a
    hardcoded ``~/.opencastor/robot-memory.md``;
  * ``castor.brain.autodream_runner`` — the nightly writer, at
    ``$CASTOR_OPENCASTOR_DIR/robot-memory.md`` (default ``~/.opencastor``);
  * ``castor.brain.memory_recall`` — the CLI and the console, which added
    ``$ROBOT_HOME/robot-memory.md`` for `castor up` hosts.

On a plain host all three agreed. On a `castor up` host they did not, and the
disagreement is invisible from every side: `castor memory add` reports success,
`castor memory show` lists the entry, `/memory/recall` ranks it — and the brain
that is supposed to USE it reads a different file and sees nothing. A healthy
looking useless service. So there is exactly one resolver, here, and all three
call it.

RESOLUTION ORDER, and why the legacy path is first. The gateway brain's reader
is the authority: whatever file it has been reading is the file that holds the
robot's actual history, and a new environment variable must not silently strand
it. ``ROBOT_HOME`` is therefore a FALLBACK for hosts that have no store yet, not
an override for hosts that do.

  1. ``CASTOR_ROBOT_MEMORY_FILE`` — the operator has been explicit; obey.
  2. ``<state dir>/robot-memory.md`` **if it already exists** — the legacy
     store, never stranded.
  3. ``<ROBOT_HOME>/robot-memory.md`` — a `castor up` host with no store yet.
  4. ``<state dir>/robot-memory.md`` — nothing exists anywhere; write where the
     brain has always read.

where ``<state dir>`` is ``CASTOR_OPENCASTOR_DIR`` or ``~/.opencastor``.

Every one of those is read at CALL time, not import time: a test, a wizard, or
a second robot on the same Pi may move ``ROBOT_HOME`` after this module loads.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The file's name, in whichever directory wins. Never varies.
MEMORY_FILENAME = "robot-memory.md"

#: Where OpenCastor kept state before `castor up` existed, and still does for
#: everything that is not per-robot (dream-log.jsonl, health reports).
DEFAULT_STATE_DIRNAME = ".opencastor"


def state_dir() -> Path:
    """``CASTOR_OPENCASTOR_DIR`` or ``~/.opencastor``. The legacy home."""
    configured = os.environ.get("CASTOR_OPENCASTOR_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / DEFAULT_STATE_DIRNAME


def legacy_memory_file() -> Path:
    """The store the gateway brain has always read. The authority."""
    return state_dir() / MEMORY_FILENAME


def memory_file() -> Path:
    """The one ``robot-memory.md`` this robot means. See the module docstring."""
    explicit = os.environ.get("CASTOR_ROBOT_MEMORY_FILE")
    if explicit:
        return Path(explicit).expanduser()
    legacy = legacy_memory_file()
    if legacy.exists():
        return legacy
    robot_home = os.environ.get("ROBOT_HOME")
    if robot_home:
        return Path(robot_home).expanduser() / MEMORY_FILENAME
    return legacy

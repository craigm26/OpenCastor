"""The stop that survives a restart.

WHY THIS FILE EXISTS. Until now every stop this robot could latch lived in one
process' memory: ``SafetyLayer._estop`` is a bool on an object, and the object
dies with the runtime. A robot that e-stopped because its board hit 90 C, got
restarted by systemd twelve seconds later, came back with ``_estop = False``
and would happily take the next motion command. The operator never cleared
anything. The restart did.

So the latch is a FILE, under the robot home, written by the generated runtime
and read back by the next one. ``castor up`` already exports ``ROBOT_HOME`` into
every generated unit, so there is no new environment plumbing, no new prompt and
no hand-edited path: this module resolves itself from what the ten-minute path
already sets.

TWO FLAGS, NOT ONE, and they are different things:

* **estop**  — something decided this robot must not move. It carries the
  SOURCE that set it ('sensor', 'api', 'rcan', 'local', 'swarm'), because a
  latch set by an on-device sensor reading is not clearable by a remote resume.
* **pause**  — a person deliberately stood the robot down. It carries WHO and
  WHY, and the why is load-bearing: a sticky pause nobody can explain is a
  ten-minute failure that looks exactly like broken hardware. ``castor resume``
  prints the principal, the timestamp and the reason before it lifts the pause,
  so the next person always learns what the last one meant.

WHAT THIS IS NOT. It is a best-effort SOFTWARE hold. It stops this robot's own
software from issuing motion and it asks the actuator to stop. It is not a
hardware cut, it does not de-energise anything, and nothing here is safety
rated. If the process is killed -9 between the decision and the write, the
decision is lost; if the actuator is unreachable, the stop does not arrive and
the caller is told ``stop_not_confirmed`` rather than a comfortable 200.

Unset ``ROBOT_HOME`` means NO persistence at all, deliberately: importing
``castor.fs`` in a test or a notebook must not start writing state files into
whatever directory happened to be current.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("OpenCastor.Safety.Latch")

#: Filename under ``ROBOT_HOME``. Not in tokens.env and not in the config: this
#: is mutable state, not a credential and not a setting.
LATCH_FILENAME = "safety-latch.json"

#: Sources that may set the e-stop, mirroring ``SafetyLayer.estop(source=)``.
#: 'sensor' is the one with a special clearing rule; see :func:`clear_blocked_by`.
SENSOR_SOURCE = "sensor"

#: Clearing sources trusted to lift a sensor-set latch. A person standing at
#: the robot (the CLI, on the robot's own host) can see what the sensor saw.
#: A resume arriving over the network cannot.
LOCAL_CLEAR_SOURCES = ("local",)


@dataclass
class LatchState:
    """Everything the next process needs to come back stopped rather than free."""

    estop_engaged: bool = False
    estop_source: str = ""
    estop_principal: str = ""
    estop_reason: str = ""
    estop_at: float = 0.0

    paused: bool = False
    pause_principal: str = ""
    pause_reason: str = ""
    paused_at: float = 0.0

    #: False when :func:`load` could not parse the file it found, and so is
    #: answering "not held" out of ignorance rather than out of knowledge.
    #: A missing file is readable=True: absence is a fact, garbage is not.
    #:
    #: At boot, "not held" is the deliberate answer either way; see load().
    #: A RUNNING process must not treat it as a clear, because then
    #: `echo x > safety-latch.json` lifts a stop that `rm safety-latch.json`
    #: cannot. SafetyLayer.resync_from_latch reads this and stands its ground.
    readable: bool = True

    @property
    def held(self) -> bool:
        """True when this robot is not allowed to move for either reason."""
        return bool(self.estop_engaged or self.paused)

    def describe(self) -> str:
        """One line an operator can act on, or "" when nothing is held."""
        parts = []
        if self.estop_engaged:
            parts.append(
                f"e-stop latched by {self.estop_principal or 'unknown'} "
                f"(source={self.estop_source or 'unknown'}) at "
                f"{_stamp(self.estop_at)}"
                + (f": {self.estop_reason}" if self.estop_reason else "")
            )
        if self.paused:
            parts.append(
                f"paused by {self.pause_principal or 'unknown'} at {_stamp(self.paused_at)}"
                + (f": {self.pause_reason}" if self.pause_reason else "")
            )
        return "; ".join(parts)


def _stamp(at: float) -> str:
    if not at:
        return "an unknown time"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(at))


def robot_home(home: Optional[str | Path] = None) -> Optional[Path]:
    """The robot home, from the argument or ``ROBOT_HOME``. ``None`` when unset."""
    if home:
        return Path(home)
    env = os.environ.get("ROBOT_HOME", "").strip()
    return Path(env) if env else None


def latch_path(home: Optional[str | Path] = None) -> Optional[Path]:
    """Where the latch lives, or ``None`` when there is no robot home."""
    base = robot_home(home)
    return (base / LATCH_FILENAME) if base else None


# ---------------------------------------------------------------------------
# The e-stop clear code, resolved ONCE for every surface that checks it
# ---------------------------------------------------------------------------
# WHY THIS LIVES HERE. There used to be two answers to "what code does this
# robot expect". ``castor/cli.py`` looked in the environment and then in
# ``<robot home>/tokens.env``; ``SafetyLayer.clear_estop``, which
# ``POST /api/estop/clear`` goes through, read only the environment variable of
# the server process. A unit written by `castor up` loads tokens.env, so the
# two agreed on a robot built the ten-minute way. A gateway somebody started by
# hand in a shell without the variable did not: the CLI refused a clear with no
# code while the API took the admin bearer alone, on the same robot, against
# the same provisioned secret. One resolver, used by both, is the whole fix.
#
# It sits in this module and not in ``castor/up.py`` because both callers
# already import it (the latch is what they are clearing), and because
# importing the installer from the hot path of a stop is the wrong dependency.

#: The variable every generated unit carries, via tokens.env. The name is
#: mirrored in ``castor.up.ESTOP_AUTH_VAR``, which is what WRITES it.
ESTOP_AUTH_VAR = "OPENCASTOR_ESTOP_AUTH"

#: The file the generated unit names as its ``EnvironmentFile``. Read directly
#: here so a server started by hand, with a robot home but no exported
#: variable, still finds the code its own robot was provisioned with.
TOKENS_FILENAME = "tokens.env"


def estop_auth_sources(home: Optional[str | Path] = None) -> tuple[str, str]:
    """The code this robot expects, and where it was found.

    Two sources, in order:

    1. ``OPENCASTOR_ESTOP_AUTH`` in the process environment.
    2. ``OPENCASTOR_ESTOP_AUTH=`` in ``<robot home>/tokens.env``, read the same
       way systemd reads it, so nobody has to export anything by hand.

    The robot home comes from *home* when given and from ``ROBOT_HOME``
    otherwise, which is what every generated unit exports. A server started by
    hand with neither has no second source, and that is the honest answer
    rather than a guess at a directory.

    Returns ``("", "")`` when this robot has NO code at all. That is a real
    state and not an error: a gateway-only robot built before `castor up`
    started calling :func:`castor.up.ensure_estop_auth`, or one whose tokens.env
    this process cannot read, has nothing to check a clear against. What no
    caller may do with that answer is treat it as permission. The CLI refuses
    and points at `castor up`; ``SafetyLayer.clear_estop`` keeps the older
    behaviour for the remote path, so a codeless robot is never left with a stop
    nobody on the network can lift, and says so in the log every time.
    """
    code = os.environ.get(ESTOP_AUTH_VAR, "").strip()
    if code:
        return code, f"the {ESTOP_AUTH_VAR} environment variable"
    try:
        base = robot_home(home)
        if base is None:
            return "", ""
        tokens = base / TOKENS_FILENAME
        for line in tokens.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{ESTOP_AUTH_VAR}="):
                found = stripped.split("=", 1)[1].strip()
                if found:
                    return found, str(tokens)
    except Exception:  # noqa: BLE001 - an unreadable file is "no code", not a crash
        pass
    return "", ""


def estop_auth_code(home: Optional[str | Path] = None) -> str:
    """The robot's e-stop clear code, or ``""`` when this robot has none."""
    return estop_auth_sources(home)[0]


def load(home: Optional[str | Path] = None) -> LatchState:
    """Read the latch. A missing, empty or corrupt file reads as "not held".

    A corrupt latch is logged loudly but does NOT fail the boot: a robot that
    refuses to start because a JSON file lost its last byte is a robot nobody
    can use to investigate, and the stop it is failing to remember is
    re-established by the sensor that set it within one monitor interval.

    It comes back with ``readable=False``, though, which is the difference
    between a file that says nothing is held and a file nobody could read. A
    process that is ALREADY holding must not take the second one for a clear;
    see ``SafetyLayer.resync_from_latch``.
    """
    path = latch_path(home)
    if not path or not path.exists():
        return LatchState()
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - never fail a boot on this
        logger.warning("safety latch at %s could not be read (%s); treating as clear", path, exc)
        return LatchState(readable=False)
    if not text.strip():
        # THE LIKELIEST CORRUPTION THERE IS. save() renames a fully written
        # temp file over this one, so a torn write cannot produce a short file,
        # but a power cut before the data behind that rename reaches the disk
        # very much can, and what comes back is zero bytes. An empty file is
        # not a latch saying "nothing is held"; it is a latch nobody can read.
        logger.warning("safety latch at %s is empty; treating as clear", path)
        return LatchState(readable=False)
    try:
        raw = json.loads(text)
    except Exception as exc:  # noqa: BLE001 - never fail a boot on this
        logger.warning("safety latch at %s is unreadable (%s); treating as clear", path, exc)
        return LatchState(readable=False)
    if not isinstance(raw, dict):
        logger.warning("safety latch at %s is not an object; treating as clear", path)
        return LatchState(readable=False)
    if "estop" not in raw and "pause" not in raw:
        # save() always writes both blocks. A JSON object with neither is not
        # something this module wrote, so it is not evidence of anything.
        logger.warning("safety latch at %s has no estop or pause block; treating as clear", path)
        return LatchState(readable=False)
    estop = raw.get("estop") or {}
    pause = raw.get("pause") or {}
    return LatchState(
        estop_engaged=bool(estop.get("engaged")),
        estop_source=str(estop.get("source") or ""),
        estop_principal=str(estop.get("principal") or ""),
        estop_reason=str(estop.get("reason") or ""),
        estop_at=float(estop.get("at") or 0.0),
        paused=bool(pause.get("engaged")),
        pause_principal=str(pause.get("principal") or ""),
        pause_reason=str(pause.get("reason") or ""),
        paused_at=float(pause.get("at") or 0.0),
    )


def save(state: LatchState, home: Optional[str | Path] = None) -> bool:
    """Write the latch atomically, 0600. Returns False when there is no home.

    Atomic because the read side runs at boot: a half-written latch read by the
    next start is exactly the corrupt-file case above, and a robot deciding
    whether it may move should not depend on a partial write.
    """
    path = latch_path(home)
    if not path:
        return False
    payload = {
        "version": 1,
        "estop": {
            "engaged": state.estop_engaged,
            "source": state.estop_source,
            "principal": state.estop_principal,
            "reason": state.estop_reason,
            "at": state.estop_at,
        },
        "pause": {
            "engaged": state.paused,
            "principal": state.pause_principal,
            "reason": state.pause_reason,
            "at": state.paused_at,
        },
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)
        return True
    except Exception as exc:  # noqa: BLE001 - a stop must not fail on a full disk
        logger.warning("could not persist the safety latch to %s: %s", path, exc)
        return False


def record_estop(
    principal: str, source: str, reason: str = "", home: Optional[str | Path] = None
) -> bool:
    """Latch the e-stop on disk, preserving any pause already held."""
    state = load(home)
    state.estop_engaged = True
    state.estop_source = source
    state.estop_principal = principal
    state.estop_reason = reason
    state.estop_at = time.time()
    return save(state, home)


def record_clear(home: Optional[str | Path] = None) -> bool:
    """Lift the e-stop on disk. The pause, if any, is left exactly as it was."""
    state = load(home)
    state.estop_engaged = False
    state.estop_source = ""
    state.estop_principal = ""
    state.estop_reason = ""
    state.estop_at = 0.0
    return save(state, home)


def record_pause(principal: str, reason: str, home: Optional[str | Path] = None) -> LatchState:
    """Latch the pause on disk. ``reason`` is required by the CLI, not by here."""
    state = load(home)
    state.paused = True
    state.pause_principal = principal
    state.pause_reason = reason
    state.paused_at = time.time()
    save(state, home)
    return state


def record_resume(home: Optional[str | Path] = None) -> LatchState:
    """Lift the pause and return the state as it was BEFORE lifting.

    The caller prints that: who paused, when, and why. A resume that says
    nothing teaches the next person nothing.
    """
    previous = load(home)
    state = load(home)
    state.paused = False
    state.pause_principal = ""
    state.pause_reason = ""
    state.paused_at = 0.0
    save(state, home)
    return previous


def clear_blocked_by(latched_source: str, clearing_source: str) -> str:
    """Return a denial reason when this clear must be refused, else "".

    THE RULE THE API ALREADY ADVERTISED. ``POST /api/safety/rcan`` has said in
    its docstring since it was written that "a RESUME is rejected if the local
    e-stop was triggered by an on-device sensor (not a remote STOP)". Nothing
    enforced it. This does.

    A sensor latch means a thermal, load or force reading crossed a critical
    threshold three times running. The remote caller cannot see the robot. The
    clear has to come from someone who can, which here means a clear whose
    source is 'local' (the CLI, on the robot's own host).
    """
    if (latched_source or "").lower() != SENSOR_SOURCE:
        return ""
    if (clearing_source or "").lower() in LOCAL_CLEAR_SOURCES:
        return ""
    return (
        f"e-stop was latched by source={SENSOR_SOURCE}; a clear from "
        f"source={clearing_source or 'unknown'} is refused. Check the robot, then "
        f"clear it at the robot with `castor resume --clear-estop`."
    )


def dump(home: Optional[str | Path] = None) -> dict:
    """The latch as a plain dict, for telemetry and ``/api/fs/estop``."""
    return asdict(load(home))

"""castor up — one command from bare host to paired robot.

THE TEN-MINUTE CONTRACT. OpenCastor's goal is a robot up and running in under
ten minutes, on common hardware, by people who do not administer Linux for a
living. This bench is the counter-example that motivated this file: the rover
took an EXPERIENCED operator three sessions of hand work — hand-written systemd
units, hand-edited env files, a hand-assembled pairing payload that shipped
pinned to a dead DHCP address. Every piece existed; the composition did not.

`castor up` is that composition, and nothing else:

    detect hardware -> pick archetype -> generate the robot home
    -> sign the manifest -> start the services -> print the pairing QR

Design rules, each earned the hard way:

  * NON-INTERACTIVE. The wizard already covers Q&A. A first-timer cannot
    answer questions about tool tiers and oscillator trims; defaults must be
    the safe answers, and every question this could ask is one it can answer
    itself by looking at the machine.
  * IDEMPOTENT. Rerunning `up` on a configured robot refreshes what is stale
    (the QR, the service files) and REUSES what is identity (keys, tokens,
    RRN) — the same reuse-don't-refuse contract `castor pair` learned after a
    --force rotated a live signing key to change an IP address.
  * SAFE BY DEFAULT. Detecting a PCA9685 selects the rc-car archetype but the
    generated config drives SIMULATED wheels: real PWM stays a deliberate,
    documented flip AFTER the on-stand checks, never a side effect of setup.
    An `up` that could make hardware move was rejected outright.
  * TEMPLATES ARE THE PROVEN FILES. The rc-car home is generated from the
    actual configs this bench runs — the ones the 27-check smoke suite passes
    against — with identity substituted. Fresh prose in a generator drifts
    from reality; a template cut from a working robot cannot.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import shutil
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

logger = logging.getLogger("castor.up")

ARCHETYPES = ("rc-car", "sim", "microduck")

#: Which packaged template directory each archetype renders from. `sim` shares
#: the rc-car home deliberately: it is an rc-car whose wheels are not there yet,
#: and giving it its own copy would mean two files to keep true.
TEMPLATE_DIRS = {"rc-car": "rc_car", "sim": "rc_car", "microduck": "microduck"}

#: The Pollen Robotics Microduck. It is the one archetype here that is NOT
#: detected on a bus, and that is not an implementation detail: a duck has its
#: own computer, its own 50 Hz loop and its own policies, and it is reached over
#: the network. `castor up` on a duck is not "make this Pi into a robot", it is
#: "give this robot a brain, a console, a QR and a relay".
MICRODUCK = "microduck"

#: robotd's socket, on the duck. Named here as well as in the driver because
#: this is the file that has to decide whether THIS machine is the duck.
ROBOTD_SOCKET = "/run/robotd.sock"

#: Where the duck bridge unit reads its settings. Its own file, regenerated
#: every run like discovery.env, and for the same reason: it holds this
#: robot's addresses and ports, never a credential. The TOKEN is not in it —
#: only the path to the token file, which is 0600 and outside the robot home.
DUCKBRIDGE_ENV = "duckbridge.env"

#: The one token both clients read, at duck-studio's path unchanged. Outside
#: the robot home deliberately: `bridge/install.sh` wrote it here, the phone's
#: owner has already typed what is in it, and minting a second token somewhere
#: tidier would unpair an app that was working. `up` reuses whatever it finds.
BRIDGE_TOKEN_FILE = "~/.microduck-bridge-token"

#: Port layout relative to --base-port: one robot is three adjacent services.
GATEWAY_OFF, RUNTIME_OFF, CONSOLE_OFF = 0, 1, 2
RRF_STUB_PORT = 8090

#: THE DEFAULT BASE PORT IS PART OF THE DISCOVERY CONTRACT, and it is
#: DELIBERATELY UNCHANGED. When mDNS is blocked — plenty of consumer routers
#: drop multicast between wireless clients — the app falls back to sweeping the
#: /24 for a runtime answering /health, against a fixed list of ports. That
#: list used to be the hand-built bench's (8001, 8003, 8002, 8000) and matched
#: none of these three, so a robot answering perfectly was reported as "no
#: robots found".
#:
#: Moving this to 8000 would have fixed that from this side, and it is the
#: wrong side to fix it from. Three reasons, in order of weight:
#:
#:   1. It is not one number, it is two. A second robot on a host uses
#:      --base-port 8110 (this command's own help text says so), and no single
#:      default can put both layouts in a fixed list. The app has to know the
#:      LAYOUT — base + 0/1/2 — not a set of ports, and once it does, the base
#:      itself stops mattering.
#:   2. 8080 is in every doc, every runbook and both live robots' units. A
#:      default that moves silently relocates services behind QR codes that
#:      have already been scanned, to buy nothing the app cannot buy itself.
#:   3. 8000 is the single most contended port on a developer's machine.
#:
#: The app side is where it landed: CastorKit's `RobotPorts` derives its sweep
#: from `upBases = [8080, 8110]` through the same base + 0/1/2 layout this file
#: defines, and cites these lines as the authority. If this number ever does
#: change, that is the file that changes with it.
DEFAULT_BASE_PORT = 8080

#: The second robot on one host, and the value this command's help text has
#: always printed for it. Named rather than open-coded because the app sweeps
#: this layout too: an operator who takes the collision message below gets a
#: robot the phone can still find.
SECOND_BASE_PORT = 8110

#: Where the discovery unit reads its record from. Its own file, like
#: console.env, and REGENERATED every run: unlike gateway-policy.env it holds
#: no operator decision, only this robot's identity and ports, and a stale copy
#: would advertise ports that moved — a confidently wrong record, which is
#: worse than none.
DISCOVERY_ENV = "discovery.env"

#: Where the console's read-only bearer lives. Its own file, not tokens.env:
#: tokens.env is written once and never touched again, so a robot brought up
#: before the console existed would never have received a token at all.
CONSOLE_ENV = "console.env"


@dataclass
class UpPlan:
    """Everything `up` decided, before it touches the filesystem."""

    name: str
    home: Path
    archetype: str
    rrn: str
    robot_uuid: str
    base_port: int
    detected: list[str] = field(default_factory=list)
    #: Whether the generated gateway policy names the PCA9685 or the simulator.
    #: False is the default everywhere; only an explicit answer sets it True.
    real_wheels: bool = False

    # -- microduck only ----------------------------------------------------
    #: The duck's address, as typed. ``None`` means the duck is THIS machine
    #: (robotd's socket is local), which is the only case where the bridge has
    #: something to relay without an ssh forward.
    duck_host: str | None = None
    #: The login on the duck, for the bridge's ssh forward. ``None`` uses ssh's
    #: own default, which is whatever the operator's ssh config says.
    duck_user: str | None = None
    #: robotd's socket path ON THE DUCK.
    duck_socket: str = ROBOTD_SOCKET
    #: Where the token both clients read lives. Outside the robot home on
    #: purpose: it is duck-studio's path unchanged, so a duck whose owner
    #: already ran `bridge/install.sh` and typed that token into the phone
    #: keeps working, and `up` reuses it rather than minting a second one.
    bridge_token_file: str = ""

    @property
    def is_duck(self) -> bool:
        return self.archetype == MICRODUCK

    @property
    def bridge_port(self) -> int:
        """7788 — the driver's `local_port` default and StudioKit's
        `BridgeHandshake.defaultPort`. NOT derived from --base-port: it is a
        number already written into a shipped app and a shipped driver."""
        from castor.microduck_bridge import DEFAULT_PORT

        return DEFAULT_PORT

    @property
    def bridge_deadman_ms(self) -> int:
        from castor.microduck_bridge import DEFAULT_DEADMAN_MS

        return DEFAULT_DEADMAN_MS

    @property
    def ssh_dest(self) -> str:
        """``user@host`` for the bridge's forward, or "" when the duck is here."""
        if not self.duck_host:
            return ""
        return f"{self.duck_user}@{self.duck_host}" if self.duck_user else self.duck_host

    @property
    def gateway_port(self) -> int:
        return self.base_port + GATEWAY_OFF

    @property
    def runtime_port(self) -> int:
        return self.base_port + RUNTIME_OFF

    @property
    def console_port(self) -> int:
        return self.base_port + CONSOLE_OFF


# ---------------------------------------------------------------------------
# Decisions (pure, testable)
# ---------------------------------------------------------------------------


def pick_archetype(
    i2c_addresses: set[int], duck_evidence: str | None = None
) -> tuple[str, list[str]]:
    """Choose an archetype from what the scan actually found.

    Detection selects the SHAPE of the robot, never whether it can move: an
    rc-car archetype still starts on simulated wheels. 0x40 is the PCA9685's
    default address — the one every hat and breakout ships at.

    THE DUCK COMES FIRST, and not because it is more important. It is because
    the two pieces of evidence are not comparable: a PCA9685 at 0x40 is a chip
    that *might* be wired to a vehicle, while a robotd answering is a whole
    robot that is already standing there. A Pi with a PWM hat AND a duck on the
    network is a Pi somebody is pointing at the duck; there is no reading of
    `castor up --host <duck>` that means "make an rc-car".
    """
    found: list[str] = []
    if duck_evidence:
        found.append(duck_evidence)
        return MICRODUCK, found
    if 0x40 in i2c_addresses:
        found.append("PCA9685 PWM controller at 0x40 (i2c)")
        return "rc-car", found
    return "sim", found


def detect_microduck(
    host: str | None = None,
    *,
    probe=None,
    exists=None,
    socket_path: str = ROBOTD_SOCKET,
    hostnames: tuple[str, ...] | None = None,
) -> str | None:
    """Is there a duck for this `up` to serve? Evidence, or None.

    A DUCK IS NOT FOUND ON A BUS. Every other archetype in this file is decided
    by `scan_i2c`, and applying that habit to a Microduck is how you get an
    archetype that can never be detected: there is nothing of the duck on this
    machine's I2C, because the duck has its own machine.

    Two pieces of evidence, and only two, because only two are honest:

      * ``robotd``'s socket exists HERE. `castor up` is running on the duck
        itself. Rare, and the only case where the bridge needs no forward.
      * A **bridge answers on 7788** at the address given, or at one of the
        hostnames the driver already knows. A TCP listener on that port is a
        `microduck-bridge` (or something pretending to be one, which the token
        will then refuse), and it is the port both the driver and the phone app
        already default to.

    WHAT IS DELIBERATELY NOT EVIDENCE. An open port 22 is not a duck, it is a
    computer. mDNS is not consulted at all: the duck publishes none, and
    Pollen's own scripts say so and route around it. And a duck's stock
    hostname is `radxa-zero3`, not `duck.local` — which is why an explicit
    ``--host`` beats the whole ladder and is what the not-found message says.

    ``host`` given always returns evidence: `--host` on `castor up` names a
    duck and nothing else, so a duck that is merely switched off must still be
    configurable. The returned line says which of the two it was.
    """
    from castor.microduck import CANDIDATE_HOSTNAMES
    from castor.microduck_bridge import DEFAULT_PORT

    exists = exists or _path_exists
    probe = probe or _tcp_answers

    if exists(socket_path):
        return f"robotd socket at {socket_path} — this machine is the duck"
    if host:
        if probe(host, DEFAULT_PORT):
            return f"microduck bridge answering at {host}:{DEFAULT_PORT}"
        return f"--host {host} (nothing answered on {DEFAULT_PORT} yet)"
    for candidate in hostnames if hostnames is not None else CANDIDATE_HOSTNAMES:
        if probe(candidate, DEFAULT_PORT):
            return f"microduck bridge answering at {candidate}:{DEFAULT_PORT}"
    return None


#: The five variables that switch a generated robot from SimulatedDrive to the
#: PCA9685. Kept as data because two things must agree about them: the renderer
#: that writes them out, and the toggler that flips an already-written file.
DRIVE_VARS = (
    "OPENCASTOR_DRIVE",
    "OPENCASTOR_DRIVE_I2C_BUS",
    "OPENCASTOR_DRIVE_I2C_ADDRESS",
    "OPENCASTOR_DRIVE_THROTTLE_CHANNEL",
    "OPENCASTOR_DRIVE_STEERING_CHANNEL",
)

#: Printed next to the question and again after the flip. Motion is the one
#: consequence of this tool that a person cannot undo by rerunning it.
WHEELS_OFF_THE_GROUND = (
    "REAL WHEELS: this robot will be able to MOVE the moment the gateway starts.\n"
    "  Put the vehicle on a stand with its WHEELS OFF THE GROUND first. Constructing\n"
    "  the driver writes NEUTRAL to both channels, but 'neutral' is only neutral once\n"
    "  the per-vehicle trims are measured for YOUR car: an untrimmed neutral is a slow\n"
    "  crawl, often in reverse. Measure them with the wheels up, and fit the e-stop\n"
    "  before the car touches the ground.\n"
    "  Checklist: docs/hardware/pca9685-bringup.md\n"
    "  https://github.com/craigm26/OpenCastor/blob/main/docs/hardware/pca9685-bringup.md"
)


def real_wheels_question(detected: list[str]) -> str:
    """The ONE question `up` is allowed to ask, as text (so a test can read it).

    `up` is otherwise non-interactive on purpose. This question exists because
    the alternative it replaces is worse: a file nobody mentions, in a directory
    nobody named, whose five commented lines are the difference between a robot
    that drives and a robot that signs receipts for motion that never happens.
    The deliberate act stays deliberate; it just happens in the tool that has
    the chip on the bus in front of it, instead of in an unmentioned editor.
    """
    found = detected[0] if detected else "a PCA9685 PWM controller"
    return (
        f"\n  Detected {found}.\n\n"
        f"  {WHEELS_OFF_THE_GROUND}\n\n"
        "  Simulated wheels are the default: everything else works (envelopes,\n"
        "  receipts, the deadman, the app) and the PWM chip is never written to.\n\n"
        "  Enable real wheels now? [y/N] "
    )


def decide_real_wheels(
    *,
    requested: bool | None,
    detected_pwm: bool,
    interactive: bool,
    detected: list[str] | None = None,
    ask=None,
) -> tuple[bool, str]:
    """Resolve the real-wheels decision. Pure but for `ask`; returns (choice, why).

    Precedence, and the order is the safety argument:
      1. An explicit ``--real-wheels`` / ``--simulated-wheels`` always wins.
      2. No chip on the bus means no question and no real wheels, whatever the
         archetype says: an rc-car archetype forced by hand on a bare host must
         not produce a config naming a device that is not there.
      3. A chip plus a terminal gets the question, defaulting to No.
      4. A chip with no terminal (image firstboot, CI, ssh -T) gets simulated
         wheels and a printed line saying how to change that. A machine that
         cannot be asked is never assumed to have said yes.
    """
    if requested is not None:
        return requested, "asked for on the command line"
    if not detected_pwm:
        return False, "no PWM controller detected"
    if not interactive:
        return False, (
            "no terminal to ask — rerun with `castor up --real-wheels` "
            "(wheels off the ground) to enable the PCA9685"
        )
    answer = (ask or input)(real_wheels_question(detected or []))
    if answer.strip().lower() in ("y", "yes"):
        return True, "you answered yes at the prompt"
    return False, "you answered no at the prompt"


def apply_real_wheels(text: str, enable: bool) -> str:
    """Comment or uncomment the drive block of an EXISTING policy file.

    `up` never rewrites `gateway-policy.env` — it carries hand-measured trims
    and an operator's decision. But re-running with an explicit flag has to be
    able to change that one decision, or the answer to "how do I turn the wheels
    on now?" is again "open a file nobody told you about". So this touches
    exactly the five lines in `DRIVE_VARS` and nothing else: trims, tier
    bindings, allowlists and comments all survive byte-for-byte.
    """
    out = []
    for line in text.splitlines(keepends=True):
        bare = line.lstrip("#")
        name = bare.split("=", 1)[0].strip()
        if name not in DRIVE_VARS or "=" not in bare:
            out.append(line)
            continue
        if enable and name == "OPENCASTOR_DRIVE" and bare.split("=", 1)[1].strip() == "simulated":
            # An explicitly simulated robot: enabling means naming a real
            # backend, not uncommenting the word "simulated". Any OTHER value is
            # left alone — `maestro` is also real wheels, and this flag is not
            # the place to overrule a controller choice somebody made on purpose.
            bare = "OPENCASTOR_DRIVE=pca9685\n"
        out.append(("" if enable else "#") + bare.lstrip())
    return "".join(out)


def policy_names_real_wheels(text: str) -> bool:
    """True if an existing policy file has the drive block live (not commented).

    Read rather than remembered: the operator may have edited this file by hand
    between runs, and `up` reporting what it wrote last time instead of what the
    gateway will actually read is exactly the class of lie this file exists to
    stop.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("OPENCASTOR_DRIVE=") and not stripped.endswith("=simulated"):
            return True
    return False


def derive_identity(name: str) -> tuple[str, str]:
    """A locally-derived RRN and uuid for a robot not yet registered.

    Honest about what it is: `RRN-LOCAL-...` cannot be mistaken for a
    registry-issued number, and `castor register` upgrades it later. Derived
    from a random uuid rather than the name so two robots that are both
    called "robot" do not collide.
    """
    robot_uuid = str(uuid.uuid4())
    rrn = f"RRN-LOCAL-{robot_uuid.replace('-', '')[:10]}"
    return rrn, robot_uuid


def render(template_name: str, plan: UpPlan, **extra: str) -> str:
    """Fill one packaged template. Placeholders are {name}-style."""
    folder = TEMPLATE_DIRS.get(plan.archetype, "rc_car")
    text = (resources.files("castor") / "templates" / folder / template_name).read_text()
    mapping = {
        "name": plan.name,
        "rrn": plan.rrn,
        "uuid": plan.robot_uuid,
        "port_runtime": str(plan.runtime_port),
        # -- microduck. Harmless on the rc-car templates, which name none of
        # them; kept in one mapping so there is one place to read what a
        # template may say.
        #
        # `bridge_host` is 127.0.0.1 and NOT the duck's address, which looks
        # wrong the first time and is the whole design: the driver dials the
        # BRIDGE, and the bridge is on this machine holding one connection to
        # the duck. Two processes dialling the duck directly is the two-relays
        # problem this archetype exists to end.
        "bridge_host": "127.0.0.1",
        "bridge_port": str(plan.bridge_port),
        "bridge_token_file": plan.bridge_token_file,
        "duck_socket": plan.duck_socket,
        "duck_host": plan.duck_host or "127.0.0.1",
        "connection_type": "local" if not plan.duck_host else "wifi",
        # The one character that decides whether this robot can move. Empty
        # only when a human answered the real-wheels question; the dataclass
        # default is False, so every other path renders the block commented.
        "drive": "" if plan.real_wheels else "#",
        **extra,
    }
    for key, value in mapping.items():
        text = text.replace("{" + key + "}", value)
    return text


def unit_files(plan: UpPlan, *, python: str, gateway_bin: str) -> dict[str, str]:
    """The systemd user units, rendered from the shapes this bench runs."""
    home, name = plan.home, plan.name
    units = {}
    units[f"{name}-gateway.service"] = f"""[Unit]
Description=robot-md-gateway for {name} — /v1/invoke with Ed25519-signed receipts
After=network-online.target

[Service]
EnvironmentFile={home}/gateway-attestation.env
EnvironmentFile={home}/gateway-policy.env
Environment=ROBOT_MANIFEST={home}/ROBOT.md
Environment=OPENCASTOR_OPS_RRF_URL=http://127.0.0.1:{RRF_STUB_PORT}
ExecStart={gateway_bin} serve --host 0.0.0.0 --port {plan.gateway_port} --bearers {home}/bearers.yaml --robot-md {home}/ROBOT.md
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""
    units[f"{name}-castor.service"] = f"""[Unit]
Description=OpenCastor runtime for {name} — /health /api/stop /ws/telemetry
After=network-online.target

[Service]
EnvironmentFile={home}/tokens.env
Environment=ROBOT_HOME={home}
Environment=ROBOT_NAME={name}
Environment=ROBOT_GATEWAY_URL=http://127.0.0.1:{plan.gateway_port}
Environment=ROBOT_MANIFEST={home}/ROBOT.md
Environment=ROBOT_RUNTIME_PORT={plan.runtime_port}
Environment=ROBOT_CONSOLE_PORT={plan.console_port}
Environment=OPENCASTOR_CONFIG={home}/robot.rcan.yaml
Environment=OPENCASTOR_COMMITMENT_SECRET_FILE={home}/{COMMITMENT_KEY_FILE}
ExecStart={python} {home}/runtime.py
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""
    # EnvironmentFile LAST in this unit, unlike the two above. systemd applies
    # these settings in the order they are written and the last assignment of a
    # variable wins, so a file listed first is silently overridden by every
    # Environment= line under it. console.env is the one file here whose own
    # header invites hand edits — it is where the operator's OLLAMA_URL,
    # CHAT_UPSTREAM, and a moved CONSOLE_PORT live — and an operator who edits a
    # port and watches the console keep answering on the old one has no way to
    # see why. Their edit wins.
    units[f"{name}-console.service"] = f"""[Unit]
Description=OpenCastor console for {name} — chat brains, /surface, /gaps
After=network-online.target

[Service]
Environment=ROBOT_HOME={home}
Environment=ROBOT_NAME={name}
Environment=CONSOLE_PORT={plan.console_port}
EnvironmentFile={home}/{CONSOLE_ENV}
ExecStart={python} -m castor.console
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""
    # THE FIFTH UNIT, and the one whose absence produced "I can't find the
    # robot even though it's on the same network". Everything else `up` writes
    # answers when you know where the robot is; this is the only one that says
    # where it is. It is deliberately last in the order the units are started
    # and first in the order a newcomer notices it missing.
    #
    # EnvironmentFile with NO leading dash, like every other unit here. A `-`
    # would make discovery.env optional, and a missing file would leave a
    # service that starts, reports active (running), and advertises nothing —
    # the exact shape of the failure this unit exists to end. Absent file,
    # failed unit, visible in `systemctl --user status`.
    units[f"{name}-discovery.service"] = f"""[Unit]
Description=mDNS advertisement for {name} — publishes _opencastor._tcp so the app can find it after a DHCP move
After=network-online.target {name}-castor.service

[Service]
EnvironmentFile={home}/{DISCOVERY_ENV}
ExecStart={python} -m castor.discovery
Restart=always
RestartSec=5
# 78 is "no restart will fix this" (zeroconf absent). Looping on it every five
# seconds forever buries the one line that says what to install.
RestartPreventExitStatus=78

[Install]
WantedBy=default.target
"""
    if plan.is_duck:
        # THE SIXTH UNIT, and the one that turns a duck from a robot OpenCastor
        # can describe into a robot OpenCastor can drive.
        #
        # Before it there were two relays for one duck: duck-studio's bridge for
        # the phone, and MicroduckDriver's own `ssh -L 7788:/run/robotd.sock`
        # for `castor run`. Two relays is two deadmen with different numbers,
        # two tokens, two things to install, and a duck being driven by two
        # processes neither of which knows the other exists. This is one of
        # each, and both clients dial the same port.
        #
        # EnvironmentFile with NO leading dash, like every other unit here. A
        # `-` would make duckbridge.env optional, and a bridge started with no
        # settings binds 0.0.0.0:7788 pointed at a /run/robotd.sock that is not
        # there — a service that reports active (running) and relays nothing.
        # Absent file, failed unit, visible in `systemctl --user status`.
        #
        # Restart=always, unlike the gateway's on-failure. The deadman cannot
        # act if this process dies, and the sentence in the bridge's own header
        # says so: "it is a floor, not a guarantee ... which is why the systemd
        # unit restarts it". A bridge that exited cleanly because the duck was
        # rebooting must come back when the duck does.
        units[f"{name}-duckbridge.service"] = f"""[Unit]
Description=robotd relay for {name} — one token, one deadman, the phone and OpenCastor on the same port
After=network-online.target

[Service]
EnvironmentFile={home}/{DUCKBRIDGE_ENV}
ExecStart={python} -m castor.microduck_bridge
Restart=always
RestartSec=5
# 2 is the bridge's own refusal exit: no token file, a token file other users
# can read, or an ssh forward that will not come up. None of those is fixed by
# trying again in five seconds, and looping on it buries the one line that says
# what to do.
RestartPreventExitStatus=2

[Install]
WantedBy=default.target
"""
    units[f"{name}-rrf-stub.service"] = f"""[Unit]
Description=RRF key resolver stub for {name} (loopback kid lookup)

[Service]
Environment=RRF_KEY_DIR={home}/keys/rrf
Environment=RRF_PORT={RRF_STUB_PORT}
ExecStart={python} -m castor.rrf_stub
Restart=on-failure

[Install]
WantedBy=default.target
"""
    return units


def discovery_env(plan: UpPlan) -> str:
    """The environment the discovery unit advertises from.

    Every value here is also written somewhere else — the QR, ROBOT.md, the
    other units — and that is the point: this file is the one place a person
    can read what the robot is TELLING THE NETWORK, without a packet capture.
    Names match `castor/discovery.py`'s reader, which also accepts the
    ROBOT_MANIFEST / ROBOT_RUNTIME_PORT spellings the other units use.
    """
    return (
        "# What this robot publishes on the LAN — written by `castor up`,\n"
        "# regenerated on every run. No credential is in this file and none\n"
        "# belongs here: the record answers \"where is this RRN now?\" and\n"
        "# nothing else. Pairing still happens through the QR.\n"
        f"ROBOT_RRN={plan.rrn}\n"
        f"ROBOT_NAME={plan.name}\n"
        f"ROBOT_HOME={plan.home}\n"
        f"ROBOT_GATEWAY_PORT={plan.gateway_port}\n"
        f"ROBOT_CASTOR_PORT={plan.runtime_port}\n"
        f"ROBOT_CONSOLE_PORT={plan.console_port}\n"
        f"ROBOT_MANIFEST_PATH={plan.home / 'ROBOT.md'}\n"
        "#\n"
        "# Optional. Left unset because the app derives\n"
        "#   http://<host>:$ROBOT_CASTOR_PORT/api/stop\n"
        "# which is exactly what the pairing QR carries. Writing a host in here\n"
        "# would pin an address into the one record whose job is to survive that\n"
        "# address changing. Set it only if your stop lives somewhere else.\n"
        "#ROBOT_ESTOP_URL=\n"
    )


def duckbridge_env(plan: UpPlan) -> str:
    """The settings the sixth unit runs on, as an EnvironmentFile.

    A FILE RATHER THAN AN ExecStart LINE, on purpose. The two things an owner
    most often wants to change on a relay are the deadman and where robotd is,
    and both are one readable line here. Put on the ExecStart they would be one
    `systemctl --user edit` away and would be overwritten by the next
    `castor up`, which regenerates the unit and does not regenerate this.

    NO TOKEN IS IN THIS FILE. Only the path to it. The token lives at 0600
    outside the robot home, the bridge refuses to start if anybody else can
    read it, and a robot home somebody tars up for a bug report carries no
    secret out with it.
    """
    ssh = plan.ssh_dest
    lines = [
        "# The robotd relay for this duck, written by `castor up` and",
        "# regenerated on every run. The TOKEN is not here — only the path to",
        "# it — because this file is safe to read and that file is not.",
        "#",
        "# The three deadmen under this robot, so the number below is read with",
        "# the other two next to it:",
        "#   1500 ms  the driver's command TTL   (robot.rcan.yaml, command_ttl_s)",
        f"#   {plan.bridge_deadman_ms: >4} ms  THIS relay, when the client goes quiet",
        "#    500 ms  robotd's own twist deadman, on the duck, untouchable",
        f"MICRODUCK_BRIDGE_SOCKET={plan.duck_socket}",
        f"MICRODUCK_BRIDGE_PORT={plan.bridge_port}",
        f"MICRODUCK_BRIDGE_DEADMAN_MS={plan.bridge_deadman_ms}",
        f"MICRODUCK_BRIDGE_TOKEN_FILE={plan.bridge_token_file}",
        "#",
        "# The interface to bind. 0.0.0.0 is the robot's own LAN, which is what",
        "# the phone needs to reach. DO NOT PORT-FORWARD IT: the token keeps a",
        "# television out of your robot and does not stop anybody who can read",
        "# the same Wi-Fi.",
        "MICRODUCK_BRIDGE_HOST=0.0.0.0",
    ]
    if ssh:
        lines += [
            "#",
            "# The duck is on the network, so this relay holds ONE `ssh -L` open",
            "# for its whole life and every client dials the local end of it.",
            "# That is what makes one relay serve both the phone and `castor",
            "# run`: unset this only if you move the bridge onto the duck.",
            "#",
            "# It needs key auth (BatchMode: a unit has nowhere to type a",
            "# password) and a login in the duck's `robot` group, or robotd's",
            "# 0660 socket refuses it. `castor duck` prints both fixes.",
            f"MICRODUCK_BRIDGE_SSH={ssh}",
            f"MICRODUCK_BRIDGE_FORWARD_PORT={plan.bridge_port + 1}",
        ]
    else:
        lines += [
            "#",
            "# No MICRODUCK_BRIDGE_SSH: robotd's socket is on THIS machine, so",
            "# there is nothing to forward. Set it to user@duck if you ever move",
            "# this robot home off the duck.",
        ]
    lines += [
        "#",
        "# policy.install is OFF unless you say where. Point POLICY_DIR at the",
        "# directory robotd loads policies from to let the phone put an .onnx",
        "# on the duck's disk; ROBOTD_TOML lets an install point a [policy] key",
        "# at the file. The bridge never restarts robotd, so an install takes",
        "# effect when robotd next starts, and its answer says so.",
        "#MICRODUCK_BRIDGE_POLICY_DIR=/opt/robot/policies/current",
        "#MICRODUCK_BRIDGE_ROBOTD_TOML=/etc/robot/robotd.toml",
        "",
    ]
    return "\n".join(lines)


def resolve_base_port(requested: int | None, stored: int | None) -> int:
    """Which base port this run uses.

    Reuse-don't-refuse, the same contract identity follows. An existing robot's
    ports are pinned in a QR somebody already scanned, so a rerun must not move
    them just because the DEFAULT moved — a rerun is most often triggered by a
    stale QR, and silently relocating the services behind it would be the same
    class of bug as rotating the token inside it.
    """
    if requested is not None:
        return requested
    if stored is not None:
        return stored
    return DEFAULT_BASE_PORT


def occupied_ports(base: int, probe) -> list[int]:
    """Which of this robot's three ports something else already answers on.

    Only meaningful for a robot that does not exist yet: a rerun finds its OWN
    services listening, which is health, not a collision.
    """
    return [
        base + offset
        for offset in (GATEWAY_OFF, RUNTIME_OFF, CONSOLE_OFF)
        if probe(base + offset)
    ]


def ensure_console_token(home: Path) -> tuple[str, bool]:
    """The console's read-only bearer — generated once, NEVER rotated.

    Same reuse-don't-refuse contract the gateway bearers follow, for the same
    reason: this token rides in the pairing QR, so rotating it on a rerun
    un-pairs every phone that ever scanned one. The most common reason to rerun
    `up` is a stale QR, and silently invalidating the credential in the QR you
    are regenerating is the failure that contract exists to prevent.

    Returns (token, reused). Written 0600 in its own file inside the robot home:
    tokens.env is written once and skipped forever after, so a robot brought up
    before the console existed would otherwise never get a token at all.

    THE MODE IS RE-ASSERTED ON REUSE, not just on generation. The reuse path is
    the one a live robot takes every single rerun, and the file it finds may not
    be the file `up` wrote: restored from a backup, copied with `cp` (which
    takes the umask, not the source mode), or edited by an operator following
    the invitation in its own header. A live console bearer left world-readable
    is one `cat` away from anyone with a shell on the host, and nothing about a
    successful `up` would have said so.
    """
    env_file = home / CONSOLE_ENV
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("CONSOLE_TOKEN="):
            existing = stripped.partition("=")[2].strip().strip("\"'")
            if existing:
                env_file.chmod(0o600)
                return existing, True

    token = f"oc_console_{secrets.token_hex(16)}"
    home.mkdir(parents=True, exist_ok=True)
    # Anything else the operator put in this file stays; only an absent or empty
    # CONSOLE_TOKEN is filled in.
    kept = [line for line in lines if not line.strip().startswith("CONSOLE_TOKEN=")]
    if not kept:
        kept = [
            "# Read-only console bearer — generated once by `castor up`.",
            "# It grants viewing and model management, never actuation, and it",
            "# is never rotated: it rides in the pairing QR.",
        ]
    env_file.write_text("\n".join([*kept, f"CONSOLE_TOKEN={token}"]) + "\n")
    env_file.chmod(0o600)
    return token, False


#: Where the runtime looks for the admin bearer's DIGEST. Resolved from
#: ``ROBOT_HOME``, which the castor unit already sets (see :func:`unit_files`),
#: so no new environment plumbing and no new hand-edited file.
ADMIN_TOKEN_DIGEST_FILE = "admin-token.sha256"

#: Where the runtime's commitment chain looks for its HMAC key. Named in the
#: castor unit as ``OPENCASTOR_COMMITMENT_SECRET_FILE`` (see :func:`unit_files`).
COMMITMENT_KEY_FILE = "keys/commitment.key"


def mint_commitment_key(home: Path) -> tuple[Path, bool]:
    """Mint the commitment chain's HMAC key, 0600, under the robot home.

    THIS IS WHY THE CHAIN CAN BE OFF BY DEFAULT. Until 3.5 the chain fell back
    to a literal secret compiled into the wheel, so every robot on earth sealed
    its action records with the same key and any of them could forge another's.
    Removing that literal makes an unprovisioned robot record nothing at all,
    which is only an improvement if provisioning is not a step a human has to
    remember. So it is a generated default: `castor up` writes the key, the
    generated unit names the file, and the ten-minute path never mentions it.

    Identity, not a session credential: REUSED whenever the file already
    exists. Rotating it would orphan every record already in the log, because
    the seals in that log can only be recomputed with the key that made them.
    The mode is re-asserted on reuse for the same reason the console token's
    is: a file restored from a backup or copied with `cp` takes the umask, not
    the source mode, and a world-readable HMAC key is a forgeable chain.

    Returns (path, reused).
    """
    key_file = home / COMMITMENT_KEY_FILE
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if key_file.exists() and key_file.read_bytes().strip():
        key_file.chmod(0o600)
        return key_file, True
    key_file.write_bytes(secrets.token_bytes(32))
    key_file.chmod(0o600)
    return key_file, False


#: The environment variable ``castor/fs/safety.py`` has always read before it
#: allows an e-stop to be cleared, and which nothing ever set. It goes in
#: tokens.env because that is the file every generated unit already names as an
#: EnvironmentFile: no new plumbing, no new prompt, no hand-edited file.
ESTOP_AUTH_VAR = "OPENCASTOR_ESTOP_AUTH"


def ensure_estop_auth(home: Path) -> str:
    """Make sure tokens.env carries an e-stop auth code. Returns it.

    THE ONE PLACE tokens.env IS TOUCHED AFTER CREATION, and only ever by
    APPENDING a line that is absent. The file's rule is that it is written once
    and never rewritten, because rotating what is in it un-pairs the phone.
    Appending a key that was never there rotates nothing. The alternative was a
    second file nobody's unit reads, which is how the console token ended up
    needing its own explanation.

    REUSED, never rotated: this is the code a human wrote down, and a rerun of
    `castor up` that silently changed it would strand every operator who had
    already saved the old one. (The admin bearer is the opposite contract on
    purpose; see :func:`mint_admin_token`.)
    """
    tokens = home / "tokens.env"
    existing = ""
    if tokens.exists():
        existing = tokens.read_text(encoding="utf-8")
        for line in existing.splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{ESTOP_AUTH_VAR}="):
                value = stripped.split("=", 1)[1].strip()
                if value:
                    return value
    code = f"oc_estop_{secrets.token_hex(8)}"
    home.mkdir(parents=True, exist_ok=True)
    suffix = "" if (not existing or existing.endswith("\n")) else "\n"
    with tokens.open("a", encoding="utf-8") as fh:
        fh.write(f"{suffix}{ESTOP_AUTH_VAR}={code}\n")
    tokens.chmod(0o600)
    return code


def mint_admin_token(home: Path) -> str:
    """Mint the admin bearer, store only its digest, and return the secret.

    THE OPPOSITE CONTRACT FROM EVERY OTHER TOKEN `up` WRITES. The bearers and
    the console token are identity: they ride in the pairing QR and rotating
    them un-pairs the phone, so they are reused forever. The admin bearer rides
    in nothing. It is the human's credential for the four or five acts a robot's
    owner performs and its agent must not — authorize a HiTL gate, clear an
    e-stop, mint a key, reboot the host, apply a remote champion config — so:

    * it is minted FRESH on every run, which makes rerunning `castor up` the
      recovery path for an operator who closed the terminal it was printed in;
    * only its SHA-256 lands on disk, in its own 0600 file and NOT in
      tokens.env. Everything running as the runtime uid can read the robot
      home. A digest is not a bearer: reading it does not let you present it.

    Returns the plaintext, for the caller to print once and forget.
    """
    token = f"oc_admin_{secrets.token_hex(16)}"
    home.mkdir(parents=True, exist_ok=True)
    digest_file = home / ADMIN_TOKEN_DIGEST_FILE
    digest_file.write_text(hashlib.sha256(token.encode("utf-8")).hexdigest() + "\n")
    digest_file.chmod(0o600)
    return token


# ---------------------------------------------------------------------------
# Manifest signing
# ---------------------------------------------------------------------------


def sign_manifest(body: str, key_file: Path, kid: str) -> str:
    """Sign ROBOT.md the way the gateway verifies it: Ed25519 over the body,
    a ROBOT-MD-SIG footer, and the kid resolvable through the RRF stub."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if key_file.exists():
        priv = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    else:
        priv = Ed25519PrivateKey.generate()
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(
            priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        key_file.chmod(0o600)
    # SIGN EXACTLY THE BYTES THE GATEWAY WILL VERIFY. Its footer regex starts
    # at the newline BEFORE the comment, and it verifies text[:match.start()] —
    # i.e. the body WITHOUT that final newline. Signing the body with the
    # newline produced a signature that verified beautifully in a bare test
    # and failed as `manifest_provenance` on the live gateway: one byte of
    # framing, two honest implementations, no error message that names it.
    canonical = body.rstrip("\n")
    sig = base64.b64encode(priv.sign(canonical.encode("utf-8"))).decode()
    return f"{canonical}\n<!-- ROBOT-MD-SIG kid={kid} sig={sig} -->\n"


def publish_manifest_key(key_file: Path, kid: str, rrf_dir: Path) -> Path:
    """Drop the verify key where the stub serves it."""
    from cryptography.hazmat.primitives import serialization

    priv = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    rrf_dir.mkdir(parents=True, exist_ok=True)
    out = rrf_dir / f"{kid}.pem"
    out.write_bytes(pub_pem)
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _stdin_is_a_terminal() -> bool:
    """Can this run ask a question at all?

    The image's firstboot runs `castor up` from a systemd unit with no stdin,
    and CI runs it with a closed one. Both must reach the same answer a silent
    machine deserves: simulated wheels, and a printed line saying how to change
    it — never a prompt read from EOF and never a default of yes.
    """
    import sys

    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:  # noqa: BLE001 - a detached stdin is not a terminal
        return False


def _say(step: str, started: float) -> None:
    print(f"  [{time.monotonic() - started:5.1f}s] {step}")


def run_up(
    *,
    home: Path,
    name: str | None = None,
    archetype: str | None = None,
    base_port: int | None = None,
    python: str | None = None,
    start_services: bool = True,
    link: bool = True,
    real_wheels: bool | None = None,
    host: str | None = None,
    user: str | None = None,
    ask=None,
) -> UpPlan:
    """The whole bring-up. Prints progress; returns the plan for callers/tests.

    ``base_port`` of None means "decide": an existing robot keeps the ports its
    QR already pinned, and a new one gets :data:`DEFAULT_BASE_PORT`, which is
    inside the set the app sweeps when mDNS is blocked.

    ``link`` (default on, same as ``castor pair``) makes the pairing QR a
    universal link — a phone camera opens the app, or the /pair explainer page
    if it is not installed. ``link=False`` writes the raw-JSON QR only the app's
    in-app scanner reads.

    ``real_wheels`` is the one decision `up` cannot make for you. ``None`` means
    "ask, once, if there is a chip on the bus and a terminal to ask at";
    ``True``/``False`` come from ``--real-wheels``/``--simulated-wheels`` and
    skip the question. ``ask`` overrides ``input`` for tests.

    ``host`` and ``user`` name a Pollen Microduck on the network. ``host``
    given means the microduck archetype unless an explicit ``archetype`` says
    otherwise: it is a duck-only flag, and a duck that is merely switched off
    must still be configurable. Neither is read by any other archetype.
    """
    started = time.monotonic()
    home = home.expanduser().resolve()
    name = name or home.name

    # -- detect ------------------------------------------------------------
    i2c: set[int] = set()
    try:
        from castor.peripherals import scan_i2c

        i2c = {p.i2c_address for p in scan_i2c() if p.i2c_address is not None}
    except Exception:  # noqa: BLE001 - no bus is a valid machine state
        pass
    # A duck is looked for over the NETWORK, and only when there is a reason
    # to: an explicit --host, an explicit --archetype microduck, or a bus with
    # no PCA9685 on it. A Pi with a PWM hat is a car and does not spend four
    # connection attempts proving it is not a duck.
    duck: str | None = None
    if host or archetype == MICRODUCK or 0x40 not in i2c:
        try:
            duck = detect_microduck(host)
        except Exception:  # noqa: BLE001 - no network is a valid machine state
            duck = f"--host {host}" if host else None
    picked, detected = pick_archetype(i2c, duck_evidence=duck)
    archetype = archetype or picked
    if archetype not in ARCHETYPES:
        raise SystemExit(f"unknown archetype {archetype!r} (know: {ARCHETYPES})")
    if archetype == MICRODUCK and not duck and not host:
        # Asked for by hand with nothing found and no address. Honoured — the
        # duck may be charging — but the one thing that would make it work is
        # printed rather than discovered later from a mock-mode driver.
        _say(
            "warning: --archetype microduck and no duck answered. Give it an "
            "address: `castor up --archetype microduck --host <ip>`. A stock "
            "duck's hostname is `radxa-zero3`, it publishes no mDNS, and "
            "`duckctl ip` over Bluetooth is how Pollen says to find it.",
            started,
        )
    for line in detected:
        _say(f"detected: {line}", started)
    _say(f"archetype: {archetype}", started)

    # -- identity (reused on rerun, generated once) -------------------------
    state_file = home / ".castor-up.json"
    stored_base: int | None = None
    if state_file.exists():
        state = json.loads(state_file.read_text())
        rrn, robot_uuid = state["rrn"], state["uuid"]
        # The ports are identity too, in the sense that matters: they are in a
        # QR somebody scanned. Reused unless the operator asks for others.
        stored_base = state.get("base_port")
        _say("identity: reused existing", started)
    else:
        rrn, robot_uuid = derive_identity(name)
        _say(f"identity: {rrn} (local — `castor register` upgrades it)", started)

    base_port = resolve_base_port(base_port, stored_base)
    if stored_base is None:
        # A fresh robot only: a rerun finds its own services on these ports.
        taken = occupied_ports(base_port, _port_answers)
        if taken:
            raise SystemExit(
                f"ports {', '.join(str(p) for p in taken)} are already answering on "
                "this host, so this robot's services would collide with whatever "
                "owns them.\n"
                "Give it its own range: `castor up --home "
                f"{home} --base-port {SECOND_BASE_PORT if base_port == DEFAULT_BASE_PORT else base_port + 30}` "
                "(gateway, runtime and console are base + 0, 1, 2 — and the app "
                "sweeps that layout, so a robot moved this way is still findable)."
            )
    # -- the one question ---------------------------------------------------
    # Asked BEFORE anything is written, and only when this run would create the
    # policy file: a rerun on a configured robot must not re-litigate a decision
    # its operator already made (and possibly already trimmed for).
    # A duck has no PCA9685 and no question to ask about one. Stated rather
    # than falling out of the detection: `--real-wheels --archetype microduck`
    # must not write a PWM block into a policy file for a robot whose motor bus
    # belongs to another computer entirely.
    detected_pwm = archetype != MICRODUCK and any("PCA9685" in line for line in detected)
    policy = home / "gateway-policy.env"
    if archetype == MICRODUCK:
        wheels, why = False, "a Microduck's servos belong to robotd, not to this host"
    elif real_wheels is None and policy.exists():
        wheels = policy_names_real_wheels(policy.read_text())
        why = "gateway-policy.env already exists — left untouched"
    else:
        wheels, why = decide_real_wheels(
            requested=real_wheels,
            detected_pwm=detected_pwm,
            interactive=_stdin_is_a_terminal(),
            detected=detected,
            ask=ask,
        )
    if real_wheels and not detected_pwm and archetype != MICRODUCK:
        # --real-wheels on a bus with nothing on it. Honoured (the chip may be
        # unpowered while the operator wires it), but never silently: the
        # gateway will refuse to start until the board answers.
        _say("warning: --real-wheels but no PCA9685 answered at 0x40", started)

    plan = UpPlan(
        name=name,
        home=home,
        archetype=archetype,
        rrn=rrn,
        robot_uuid=robot_uuid,
        base_port=base_port,
        detected=detected,
        real_wheels=wheels,
        duck_host=host,
        duck_user=user,
        bridge_token_file=str(Path(BRIDGE_TOKEN_FILE).expanduser()),
    )

    # -- home dir ------------------------------------------------------------
    home.mkdir(parents=True, exist_ok=True)
    (home / "keys" / "rrf").mkdir(parents=True, exist_ok=True)

    manifest_kid = f"{name}-manifest"
    manifest_key = home / "keys" / "manifest-ed25519-private.pem"
    body = render("ROBOT.md.tmpl", plan)
    (home / "ROBOT.md").write_text(sign_manifest(body, manifest_key, manifest_kid))
    _say(f"ROBOT.md written and signed (kid {manifest_kid})", started)

    (home / "robot.rcan.yaml").write_text(render("robot.rcan.yaml.tmpl", plan))
    if not policy.exists():
        # Never overwritten wholesale: this file carries hand-measured trims and
        # an operator's decision, and a rerun of `up` must not silently reverse
        # either. Only the five drive lines are ever touched again, and only on
        # an explicit flag (below).
        policy.write_text(render("gateway-policy.env.tmpl", plan))
    elif real_wheels is not None:
        before = policy.read_text()
        after = apply_real_wheels(before, real_wheels)
        if after != before:
            policy.write_text(after)
            _say(
                f"gateway-policy.env: drive block "
                f"{'UNCOMMENTED (real wheels)' if real_wheels else 'commented out (simulated)'}",
                started,
            )
    _say(
        ("wheels: REAL (PCA9685) — " if wheels else "wheels: simulated — ") + why,
        started,
    )
    if wheels:
        print("\n  " + WHEELS_OFF_THE_GROUND + "\n")
    (home / "runtime.py").write_text(render("runtime.py.tmpl", plan))
    (home / DISCOVERY_ENV).write_text(discovery_env(plan))
    if plan.is_duck:
        from castor.microduck_bridge import mint_token

        (home / DUCKBRIDGE_ENV).write_text(duckbridge_env(plan))
        bridge_token, token_reused = mint_token(plan.bridge_token_file)
        _say(
            ("bridge token: reused " if token_reused else "bridge token: minted at ")
            + plan.bridge_token_file
            + (" (the app already has this one)" if token_reused else ""),
            started,
        )

    # -- bearers + runtime tokens (reused: rotating them un-pairs the phone) --
    bearers = home / "bearers.yaml"
    if bearers.exists():
        from castor.pairing import read_bearer_from_bearers_yaml

        actuate = read_bearer_from_bearers_yaml(bearers)
        read_tok = read_bearer_from_bearers_yaml(bearers, prefer_tier="read")
        _say("bearers: tokens reused", started)
    else:
        actuate = f"rmg_live_{secrets.token_hex(16)}"
        read_tok = f"rmg_read_{secrets.token_hex(16)}"
        _say("bearers: tokens generated (actuate + read)", started)
    # TOKENS are identity and reused (rotating them un-pairs the phone); the
    # ACTUATOR section is re-resolved every run against what is actually
    # installed — so `pip install rc-car-actuator` followed by a rerun
    # upgrades noop -> rc-car without touching the pairing.
    #
    # `caller`, not `name`: the field names the audit trail's actor and the
    # gateway KeyErrors on anything else — caught live when the first scratch
    # robot's gateway crash-looped on exactly this. rc-car with an empty
    # config is the SIMULATED-wheels default — real PWM is a deliberate later
    # flip in gateway-policy.env, never a setup default.
    actuator_name, actuator_note = resolve_actuator(archetype)
    bearers.write_text(
        "# robot-md-gateway bearers — generated by `castor up`.\n"
        "bearers:\n"
        f"  - token: {actuate}\n    tier: actuate\n    caller: {name}-phone\n"
        f"  - token: {read_tok}\n    tier: read\n    caller: {name}-runtime\n"
        "actuator:\n"
        f"  name: {actuator_name}\n"
        "  config: {}\n"
    )
    bearers.chmod(0o600)
    if actuator_note:
        _say(f"actuator: {actuator_name} — {actuator_note}", started)
    else:
        _say(f"actuator: {actuator_name} (simulated wheels)", started)
    tokens = home / "tokens.env"
    if not tokens.exists():
        # OPENCASTOR_API_TOKEN guards the runtime's own /api endpoints; the
        # runtime REFUSES TO START without it (fail closed, correctly). Its
        # own token, not the gateway read bearer: leaking a camera URL must
        # not also hand out the runtime's stop endpoint.
        #
        # THE ADMIN BEARER IS NOT IN THIS FILE, and that is the whole point of
        # the second credential. tokens.env is owned by the runtime uid, so
        # everything running as the robot — the AI agent included — can read
        # every line of it. A token in here is a token the agent holds.
        tokens.write_text(
            f"ACTUATE_TOKEN={actuate}\nREAD_TOKEN={read_tok}\n"
            f"OPENCASTOR_API_TOKEN=oc_api_{secrets.token_hex(16)}\n"
        )
        tokens.chmod(0o600)
    estop_auth = ensure_estop_auth(home)
    admin_token = mint_admin_token(home)
    _say("owner token: minted — printed once below, only its digest on disk", started)
    _commit_key, _commit_reused = mint_commitment_key(home)
    _say(
        "commitment key: reused" if _commit_reused else "commitment key: minted (0600)",
        started,
    )
    console_token, console_reused = ensure_console_token(home)
    _say(
        "console: token reused" if console_reused else "console: read-only token generated", started
    )

    # -- rrf stub key + attestation identity --------------------------------
    stub_dir = home / "keys" / "rrf"
    publish_manifest_key(manifest_key, manifest_kid, stub_dir)

    from castor.pairing import generate_attestation_identity, set_env_var

    attest_key = home / "keys" / "attestation-ed25519-private.pem"
    identity = generate_attestation_identity(attest_key, kid=f"{name}-gw-attest")
    env_file = home / "gateway-attestation.env"
    set_env_var(env_file, "ROBOT_MD_ATTESTATION_KEY_FILE", str(identity.key_file))
    set_env_var(env_file, "ROBOT_MD_ATTESTATION_KID", identity.kid)
    # The gateway's receipts must also resolve, same stub.
    publish_manifest_key(attest_key, identity.kid, stub_dir)
    _say(f"attestation: {identity.kid}", started)

    # -- AI models -----------------------------------------------------------
    provider, model = detect_brain()
    (home / "active-model.json").write_text(
        json.dumps({"provider": provider, "model": model, "updated_at": time.time()}, indent=2)
    )
    _say(f"brain: {provider} {model or '(subscription)'}".rstrip(), started)

    state_file.write_text(
        json.dumps(
            {
                "rrn": rrn,
                "uuid": robot_uuid,
                "archetype": archetype,
                "base_port": base_port,
                **({"duck_host": host} if host else {}),
                **({"duck_user": user} if user else {}),
            },
            indent=2,
        )
    )

    # -- services ------------------------------------------------------------
    python = python or shutil.which("python3") or "/usr/bin/python3"
    # The gateway NEXT TO the chosen python first: `which` can find a stale
    # copy on PATH (a ~/.local/bin shim did exactly that on this bench) while
    # the real one lives in the venv the services run from.
    sibling = Path(python).parent / "robot-md-gateway"
    gateway_bin = str(sibling) if sibling.exists() else shutil.which("robot-md-gateway")
    if not gateway_bin:
        # Writing a unit for a binary that is not there produces a crash-loop
        # a beginner has to diagnose through journalctl. Failing HERE, with
        # the fix in the message, is the ten-minute behaviour.
        raise SystemExit(
            "robot-md-gateway is not installed in this environment.\n"
            "It is a dependency of opencastor — `pip install opencastor` "
            "(or `pip install robot-md-gateway`), then rerun `castor up`."
        )
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    rendered = unit_files(plan, python=python, gateway_bin=gateway_bin)

    stub_running = _port_answers(RRF_STUB_PORT)
    if stub_running:
        # Another robot's stub already serves :8090 — publish into ITS key dir
        # too, if we can find it, rather than fighting over the port.
        rendered.pop(f"{name}-rrf-stub.service")
        for candidate in (Path.home() / "bob" / "keys" / "rrf",):
            if candidate.is_dir():
                publish_manifest_key(manifest_key, manifest_kid, candidate)
                publish_manifest_key(attest_key, identity.kid, candidate)
                _say(f"rrf stub: reusing :{RRF_STUB_PORT}, keys published to {candidate}", started)
                break
    for unit_name, content in rendered.items():
        (unit_dir / unit_name).write_text(content)
    _say(f"services written: {', '.join(rendered)}", started)
    _say(
        f"discovery: advertising {rrn} as _opencastor._tcp "
        f"(gateway {plan.gateway_port}, runtime {plan.runtime_port}, "
        f"console {plan.console_port}) — prove it with `castor discovery check`",
        started,
    )

    if plan.is_duck:
        _say(
            f"duck relay: 127.0.0.1:{plan.bridge_port} -> "
            f"{plan.ssh_dest or 'this machine'}{plan.duck_socket}, deadman "
            f"{plan.bridge_deadman_ms} ms — the SAME port Microduck Studio dials, "
            "so there is one relay and one token rather than two of each",
            started,
        )

    if start_services:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        for unit_name in rendered:
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", unit_name],
                check=True,
                capture_output=True,
            )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if _port_answers(plan.runtime_port):
                break
            time.sleep(0.5)
        _say("services started", started)

    # -- pair ----------------------------------------------------------------
    from castor.pairing import (
        build_pair_payload,
        capability_surface_from_manifest,
        default_gateway_url,
        write_pair_artifacts,
    )

    gateway_url = default_gateway_url(port=plan.gateway_port)
    host = gateway_url.split("//")[1].rsplit(":", 1)[0]
    payload = build_pair_payload(
        gateway_url=gateway_url,
        bearer=actuate,
        manifest_path=str(home / "ROBOT.md"),
        rrn=rrn,
        estop_url=f"http://{host}:{plan.runtime_port}/api/stop",
        # The console rides in the QR with its OWN read-only token, so a phone
        # that scans this robot gets its brains and its live capability surface
        # (GET /surface) without ever holding a credential that can move it.
        console_url=f"http://{host}:{plan.console_port}",
        console_token=console_token,
        attest_kid=identity.kid,
        attest_pub=base64.b64encode(_spki_der(identity.pub_file)).decode(),
        capability_surface=capability_surface_from_manifest(home / "ROBOT.md"),
        for_link=link,
    )
    write_pair_artifacts(payload, home, link=link)
    _say(
        f"pairing QR: {home / 'pair-qr.png'}"
        + (" (opens the app from any phone camera)" if link else ""),
        started,
    )

    # -- gaps: what this host's hardware could do that its software can't yet.
    # Data, not log lines — the app renders it, an AI can read it, and closing
    # one is always an operator-gated act (docs/SKILL-GAPS.md).
    from castor.gaps import collect
    from castor.gaps import write as write_gaps

    found_gaps = collect(home=home)
    if found_gaps:
        write_gaps(found_gaps, home)
        _say(
            f"gaps: {len(found_gaps)} noted in gaps.json ({', '.join(g.kind for g in found_gaps)})",
            started,
        )
    else:
        write_gaps([], home)
        _say("gaps: none — everything detected has a driver and a brain", started)
    scan_with = "with any phone camera" if link else "with the OpenCastor app"
    print(
        f"\nDone in {time.monotonic() - started:.0f}s. "
        f"Scan {home / 'pair-qr.png'} {scan_with}, "
        "then follow “Run your first drive”."
    )
    # THE ADMIN BEARER IS PRINTED HERE AND NOWHERE ELSE, and the QR does not
    # carry it — same reasoning as the duck's bridge token below. The phone
    # scans a credential that drives the robot; this one authorizes the human
    # gates in front of driving, and the robot must not be able to hand it out.
    # Only its digest is on disk, so `castor up` is the only way to get another.
    print(
        "\n  Owner token (shown once, not saved anywhere you can read it back):\n"
        f"    {admin_token}\n"
        "  It is the only credential that can authorize a paused action,\n"
        "  clear an emergency stop, mint a key, or reboot this host.\n"
        "  Keep it off the robot. Lost it? Re-run `castor up` for a new one.\n"
    )
    # A SECOND FACTOR FOR ONE ACT ONLY: clearing a stop. The owner token proves
    # who you are; this proves you meant this. It is reused across reruns, so it
    # is printed every time rather than once.
    print(
        f"  E-stop clear code: {estop_auth}\n"
        f"  (also {home / 'tokens.env'}). Clearing a stop needs BOTH this and the\n"
        "  owner token. A stop a sensor set is not clearable over the network at\n"
        "  all: run `castor resume --clear-estop` at the robot. This is a\n"
        "  best-effort software hold, not a hardware cut.\n"
    )
    if plan.is_duck:
        # THE TOKEN IS PRINTED, and the QR does not carry it. The pairing QR
        # carries this robot home's credentials; the bridge token is the duck's,
        # it was typed into a phone by hand once already, and a person who has
        # to read it off a screen is a person who knows they are holding a
        # secret. It is also the only way the OpenCastor app and Microduck
        # Studio end up holding the same one.
        token = Path(plan.bridge_token_file).read_text().strip()
        print(
            "\n  Microduck Studio > Robot > Bridge wants two things:\n"
            f"    address   this machine, port {plan.bridge_port}\n"
            f"    token     {token}\n"
            f"  (from {plan.bridge_token_file}, mode 0600, never rotated by a rerun)\n"
            "\n  Then, in order:\n"
            "    castor duck health          did robotd actually answer?\n"
            f"    systemctl --user status {name}-duckbridge\n"
            f"    castor run --config {home / 'robot.rcan.yaml'}\n"
        )
    return plan


def resolve_actuator(archetype: str = "rc-car") -> tuple[str, str | None]:
    """Which gateway actuator this host can actually construct.

    THE MICRODUCK ANSWER IS `noop`, AND SAYING SO IS THE POINT. There is no
    duck actuator for this gateway and inventing an allowlist entry for one
    would produce the worst failure in the review this archetype came from:
    a command accepted, signed, receipted, and delivered to nothing. A duck's
    motion travels the runtime's MicroduckDriver to the relay to robotd, which
    is a path where a refusal is a refusal, and the gateway's job on this robot
    is the signed identity, the read tier and the stop.

    `rc-car-actuator` is a separate package and — as of this writing — not on
    PyPI, so a fresh `pip install opencastor` does not have it. Writing
    `actuator: rc-car` anyway would crash-loop the gateway on an entry-point
    error no beginner can parse, at minute two of the ten minutes. The
    gateway's built-in `noop` actuator keeps the whole signed path alive —
    receipts, tiers, allowlists — with nothing to move, and the returned note
    tells the operator the one command that upgrades it.
    """
    try:
        from importlib.metadata import entry_points

        names = {ep.name for ep in entry_points(group="robot_md_gateway.actuators")}
    except Exception:  # noqa: BLE001
        names = set()
    if archetype == MICRODUCK:
        if "microduck" in names:
            return "microduck", None
        return "noop", (
            "no gateway actuator for a duck exists yet, and this one moves nothing "
            "on purpose — the duck's motion goes runtime -> duckbridge -> robotd"
        )
    if "rc-car" in names:
        return "rc-car", None
    return "noop", (
        "the rc-car actuator is not installed — "
        "`pip install rc-car-actuator`, then rerun `castor up`"
    )


def detect_brain() -> tuple[str, str]:
    """Local-first: the smallest Ollama model, else the Claude subscription,
    else Ollama-with-no-model (the console explains how to pull one)."""
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as r:
            models = json.load(r).get("models", [])
        if models:
            smallest = min(models, key=lambda m: m.get("size", 0))
            return "ollama", smallest["name"]
    except Exception:  # noqa: BLE001
        pass
    if (Path.home() / ".claude" / ".credentials.json").is_file():
        return "anthropic-sub", ""
    return "ollama", ""


def _spki_der(pub_file: Path) -> bytes:
    from cryptography.hazmat.primitives import serialization

    pub = serialization.load_pem_public_key(pub_file.read_bytes())
    return pub.public_bytes(
        encoding=serialization.Encoding.DER, format=serialization.PublicFormat.SubjectPublicKeyInfo
    )


def _port_answers(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _path_exists(path: str) -> bool:
    return Path(path).exists()


def _tcp_answers(host: str, port: int, timeout: float = 0.4) -> bool:
    """Does anything accept a connection at host:port?

    Short and forgiving on purpose. This runs up to four times on a bare host
    with no duck anywhere near it, at the very start of a command whose whole
    promise is ten minutes, so a name that does not resolve must cost a DNS
    failure and not a timeout. `create_connection` gives us both: resolution
    errors raise immediately, and the timeout only applies to a host that
    answered ARP and then went quiet.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

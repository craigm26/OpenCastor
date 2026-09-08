"""Keep one robot's ``_opencastor._tcp`` record on the air, so a phone that
already trusts it can find it again after its address moves.

WHY THIS FILE EXISTS. The pairing QR pins a literal IP. Home DHCP leases move.
When this bench's Pi went from .93 to .90, every URL the app had stored pointed
at a dead address and neither fallback could save it: mDNS found nothing,
because nothing was advertising, and the LAN sweep probed ports the robot was
not on. Three independent failures, one symptom — "I can't find the robot even
though it's on the same network" — and it was exactly true.

The fix was written on 2026-08-14 as a hand-run script next to one robot's
files, wired to a hand-written systemd unit. It worked, and it never came into
the package, so nothing `castor up` produced advertised anything at all. This
module is that script, packaged: same service type, same TXT keys, same
address-change watch, configured from the environment so ONE file serves every
robot on the box and each unit supplies its own EnvironmentFile.

WHAT IT IS NOT. It carries no credential. The record is an RRN, a name, three
port numbers and a path — public information on your own LAN — and it answers
exactly one question: "where is RRN-x now?". The app matches the RRN against
bearers it already holds in its Keychain; pairing still happens through the QR.

THE KEYS ARE A CONTRACT WITH A CLIENT THAT IS NOT IN THIS REPOSITORY. The app
reads `rrn` (required — a record without it is skipped), `gateway_port`,
`console_port`, `castor_port`, `manifest_path`, `name`, and the optional
`estop_url`; the SRV port is the console. Everything else it ignores. Renaming
one of those is a change no test in this repo would see and no error message
would name: the record stays well-formed and the phone stops finding the robot.
`tests/test_discovery.py` pins them for that reason.

THE SERVICE TYPE IS THE APP'S, NOT THE RUNTIME'S HISTORICAL ONE. `_rcan._tcp`
(castor/rcan/mdns.py) was a peer-discovery record: it advertises the runtime
port with no RRN and no manifest path, so a client that followed it would
address the wrong process with no way to name the robot it found. Both
publishers now share `SERVICE_TYPE` and the key names below; the legacy type
survives only so a browser still sees robots running older builds.

Run it:

    python -m castor.discovery          # advertise until killed (the unit)
    castor discovery check              # browse for it and print what is there
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("OpenCastor.Discovery")

#: The type the OpenCastor app browses (RobotDiscovery.swift: `serviceType`).
SERVICE_TYPE = "_opencastor._tcp.local."

#: The RCAN peer-discovery type this project published before the app existed.
#: Kept for BROWSING only, so `castor discovery check` still sees a robot
#: running an older build; nothing new publishes on it.
LEGACY_SERVICE_TYPE = "_rcan._tcp.local."

#: TXT `v`. The app-facing contract version of the record: the promise that
#: these key names mean what they mean here. Bump it only when a key changes
#: meaning, never when one is added — a reader that does not know a key ignores
#: it, and every field below has a documented default on the app side.
RECORD_VERSION = "1"

#: Defaults matching `castor up`'s layout (base_port + 0/1/2) on a
#: default-everything host. Every unit sets them explicitly; these are what a
#: hand run falls back to, and they are the same numbers RobotDiscovery.swift
#: falls back to when a TXT key is missing.
DEFAULT_GATEWAY_PORT = 8080
DEFAULT_CASTOR_PORT = 8001
DEFAULT_CONSOLE_PORT = 8002


# ---------------------------------------------------------------------------
# The record (pure, testable — no network)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RobotRecord:
    """Everything a client needs to re-address a robot it already trusts.

    The field names are not free: each one is read by name out of the TXT
    record in the iOS app (`RobotDiscovery.swift`), so renaming one here makes
    every phone on the LAN silently stop finding this robot while the record is
    still perfectly well-formed. That is the failure mode this whole module
    exists to end, so the key names live in one place — :meth:`txt` — and the
    tests pin them.
    """

    rrn: str
    name: str
    gateway_port: int
    castor_port: int
    console_port: int
    manifest_path: str
    #: OPTIONAL, AND USUALLY EMPTY ON PURPOSE. The app builds
    #: ``http://<host>:<castor_port>/api/stop`` when this key is absent, which
    #: is byte-for-byte what `castor up` puts in the pairing QR. Publishing it
    #: anyway would mean baking a host into a record whose entire job is to
    #: survive that host's address changing — the stale-QR bug, reintroduced by
    #: its own fix. Set ``ROBOT_ESTOP_URL`` only when the stop really does live
    #: somewhere the fallback cannot derive.
    estop_url: str = ""

    def txt(self) -> dict[str, str]:
        """The TXT properties, exactly as the app parses them.

        ``rrn`` is the load-bearing one: the app skips any record whose ``rrn``
        is missing or empty, because an address with no identity cannot be
        matched to stored credentials — the host that answers may simply have
        taken this robot's old DHCP lease.

        An empty ``estop_url`` is OMITTED rather than sent blank: the app tests
        for the key's presence and would take an empty string over its own
        correct fallback.
        """
        txt = {
            "rrn": self.rrn,
            "name": self.name,
            "gateway_port": str(self.gateway_port),
            "console_port": str(self.console_port),
            "castor_port": str(self.castor_port),
            "manifest_path": self.manifest_path,
            "v": RECORD_VERSION,
        }
        if self.estop_url:
            txt["estop_url"] = self.estop_url
        return txt

    @property
    def instance_name(self) -> str:
        """Unique on the LAN, because the RRN already is.

        Deliberately not the robot's human name: two robots called "robot" on
        one bench would collide, and zeroconf would resolve that by renaming
        one of them to something the operator never chose.
        """
        return f"{self.rrn}.{SERVICE_TYPE}"

    @property
    def srv_port(self) -> int:
        """The port that goes in the SRV record.

        The console, when there is one: it is the surface a client probes first
        and the only one that answers without a credential. A robot with no
        console (every vehicle on this bench) advertises its runtime instead,
        whose /health is also unauthenticated. Everything a client actually
        needs rides in TXT; SRV is the sanity probe.
        """
        return self.console_port or self.castor_port


def _int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _port_from_url(url: str | None) -> int | None:
    """The port out of ``http://host:port`` — the shape the castor unit already
    has in ``ROBOT_GATEWAY_URL``, so a unit that sets that need not repeat it."""
    if not url or "//" not in url:
        return None
    _, _, rest = url.partition("//")
    host_port = rest.split("/", 1)[0]
    if ":" not in host_port:
        return None
    return _int(host_port.rsplit(":", 1)[1], 0) or None


def record_from_env(env: dict[str, str] | None = None) -> RobotRecord:
    """Build the record from the environment `castor up`'s units already set.

    Accepts BOTH spellings on purpose. ``ROBOT_MANIFEST`` and
    ``ROBOT_RUNTIME_PORT`` are what the gateway and castor units set;
    ``ROBOT_MANIFEST_PATH`` and ``ROBOT_CASTOR_PORT`` are what the hand-written
    advertiser units on this bench set. Refusing one of the two would have made
    the packaged advertiser unable to read the very units it replaces.

    ``ROBOT_HOME`` is the last resort for both the RRN and the manifest: the
    home dir already holds ``.castor-up.json`` (the identity `up` reused) and
    ``ROBOT.md``, so a unit that sets nothing but ROBOT_HOME still advertises a
    correct record.

    Raises ``ValueError`` when no RRN can be found. That is deliberate and
    loud: a record with no identity is worse than no record, because it makes
    a robot *appear* discoverable while matching nothing the app holds.
    """
    env = dict(os.environ if env is None else env)
    home = Path(env.get("ROBOT_HOME", "")).expanduser() if env.get("ROBOT_HOME") else None

    rrn = (env.get("ROBOT_RRN") or "").strip()
    if not rrn and home is not None:
        rrn = _rrn_from_home(home)
    if not rrn:
        raise ValueError(
            "no RRN: set ROBOT_RRN, or ROBOT_HOME to a robot home containing "
            ".castor-up.json. The RRN is what makes the record useful — it is "
            "how the app matches a found robot to credentials it already holds."
        )

    manifest = (env.get("ROBOT_MANIFEST_PATH") or env.get("ROBOT_MANIFEST") or "").strip()
    if not manifest and home is not None:
        manifest = str(home / "ROBOT.md")

    gateway = env.get("ROBOT_GATEWAY_PORT")
    gateway_port = (
        _int(gateway, 0)
        if gateway
        else (_port_from_url(env.get("ROBOT_GATEWAY_URL")) or DEFAULT_GATEWAY_PORT)
    )

    castor_port = _int(
        env.get("ROBOT_CASTOR_PORT") or env.get("ROBOT_RUNTIME_PORT"), DEFAULT_CASTOR_PORT
    )

    return RobotRecord(
        rrn=rrn,
        name=(env.get("ROBOT_NAME") or "").strip() or rrn,
        gateway_port=gateway_port or DEFAULT_GATEWAY_PORT,
        castor_port=castor_port,
        console_port=_int(env.get("ROBOT_CONSOLE_PORT"), DEFAULT_CONSOLE_PORT),
        manifest_path=manifest,
        estop_url=(env.get("ROBOT_ESTOP_URL") or "").strip(),
    )


def _rrn_from_home(home: Path) -> str:
    """The RRN `castor up` recorded for this robot, or empty."""
    try:
        state = json.loads((home / ".castor-up.json").read_text())
    except Exception:  # noqa: BLE001 - a missing/damaged state file is not fatal
        return ""
    return str(state.get("rrn", "")).strip()


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


def lan_ip() -> str:
    """This host's primary LAN address.

    The UDP "connect" sends nothing; it only asks the routing table which
    source address it would use to reach the outside, which is the address a
    phone on the same LAN can reach us at. TEST-NET-1 so nothing real is named.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class RobotAdvertiser:
    """Publishes one ``_opencastor._tcp`` record for one robot."""

    SERVICE_TYPE = SERVICE_TYPE

    def __init__(self, record: RobotRecord) -> None:
        self.record = record
        self._zc = None
        self._info = None
        self.published_ip: str | None = None

    def service_info(self, ip: str):
        """The zeroconf ServiceInfo for this record. Split out so a test can
        read the bytes that would go on the wire without a network."""
        from zeroconf import ServiceInfo

        return ServiceInfo(
            SERVICE_TYPE,
            self.record.instance_name,
            addresses=[socket.inet_aton(ip)],
            port=self.record.srv_port,
            properties=self.record.txt(),
            server=f"{socket.gethostname()}.local.",
        )

    def start(self) -> bool:
        try:
            from zeroconf import Zeroconf
        except ImportError:
            logger.warning("zeroconf not installed — robot discovery disabled")
            return False
        try:
            ip = lan_ip()
            self._info = self.service_info(ip)
            self._zc = Zeroconf()
            # allow_name_change lets a leftover registration from a previous
            # process on this LAN resolve instead of aborting discovery.
            self._zc.register_service(self._info, allow_name_change=True)
            self.published_ip = ip
            logger.info(
                "advertising %s at %s:%s (%s)",
                self.record.rrn,
                ip,
                self.record.srv_port,
                SERVICE_TYPE,
            )
            return True
        except Exception as exc:  # noqa: BLE001 - never take the robot down for this
            logger.warning("could not advertise robot: %r", exc)
            self.stop()
            return False

    def stop(self) -> None:
        try:
            if self._zc is not None and self._info is not None:
                self._zc.unregister_service(self._info)
            if self._zc is not None:
                self._zc.close()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._zc = None
            self._info = None
            self.published_ip = None


# ---------------------------------------------------------------------------
# Browsing — `castor discovery check`
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoundRobot:
    """One record seen on the wire, decoded the way the app decodes it."""

    rrn: str
    name: str
    addresses: list[str]
    port: int
    gateway_port: int
    castor_port: int
    console_port: int
    manifest_path: str
    estop_url: str
    version: str
    service_type: str

    @property
    def usable(self) -> bool:
        """Whether the app could actually act on this record.

        An empty RRN is the whole failure this module fixes, arriving in a new
        costume: the record resolves, the robot looks discoverable, and the app
        discards it because it cannot be matched to a credential.
        """
        return bool(self.rrn)


def decode(info) -> FoundRobot:
    """A zeroconf ServiceInfo into a FoundRobot, tolerating bytes keys."""
    props: dict[str, str] = {}
    for key, value in (info.properties or {}).items():
        k = key.decode("utf-8", "replace") if isinstance(key, bytes) else str(key)
        if value is None:
            props[k] = ""
        elif isinstance(value, bytes):
            props[k] = value.decode("utf-8", "replace")
        else:
            props[k] = str(value)

    addresses: list[str] = []
    try:
        addresses = list(info.parsed_addresses())
    except Exception:  # noqa: BLE001
        addresses = [socket.inet_ntoa(a) for a in getattr(info, "addresses", []) if len(a) == 4]

    return FoundRobot(
        rrn=props.get("rrn", ""),
        name=props.get("name", ""),
        addresses=addresses,
        port=getattr(info, "port", 0) or 0,
        gateway_port=_int(props.get("gateway_port"), DEFAULT_GATEWAY_PORT),
        castor_port=_int(props.get("castor_port"), DEFAULT_CASTOR_PORT),
        console_port=_int(props.get("console_port"), DEFAULT_CONSOLE_PORT),
        manifest_path=props.get("manifest_path", ""),
        estop_url=props.get("estop_url", ""),
        version=props.get("v", ""),
        service_type=getattr(info, "type", SERVICE_TYPE),
    )


def browse(timeout: float = 3.0, *, include_legacy: bool = True) -> list[FoundRobot]:
    """Browse the LAN and return what is advertising, one entry per robot.

    Uses python-zeroconf rather than `avahi-browse`, and that is not a
    preference. avahi-browse on this bench reports nothing for records that
    python-zeroconf resolves in under a second, so an operator who trusts it
    concludes the advertiser is broken when it is working. The 2026-08-14
    discovery fix was delayed by exactly that.
    """
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        logger.warning("zeroconf not installed — cannot browse")
        return []

    types = [SERVICE_TYPE] + ([LEGACY_SERVICE_TYPE] if include_legacy else [])
    found: dict[str, FoundRobot] = {}
    zc = Zeroconf()

    def on_change(zeroconf, service_type, name, state_change, **_kw):  # noqa: ANN001
        if getattr(state_change, "name", str(state_change)) != "Added":
            return
        info = zeroconf.get_service_info(service_type, name, timeout=1500)
        if info is None:
            return
        robot = decode(info)
        # ONE ROBOT, ONE LINE. A restart can leave the previous registration on
        # the LAN until it ages out, and zeroconf resolves the collision by
        # publishing the new one as "<rrn>-2" — so a healthy robot shows up
        # twice under two instance names, which reads like a configuration
        # problem and is not one. Keyed on identity AND address, so a genuinely
        # stale record still pointing at an old IP stays visible, which is the
        # one duplicate worth seeing.
        found.setdefault(browse_key(name, robot), robot)

    try:
        ServiceBrowser(zc, types, handlers=[on_change])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        zc.close()
    return list(found.values())


def browse_key(name: str, robot: FoundRobot) -> str:
    """One robot, one line. See the note in :func:`browse`."""
    if not robot.rrn:
        return name
    return f"{robot.rrn}@{','.join(sorted(robot.addresses))}"


def format_found(robots: list[FoundRobot]) -> str:
    """The human report `castor discovery check` prints.

    Prints the RECORD, not a verdict, because the whole class of bug here is a
    record that exists and says the wrong thing. A newcomer who can see the
    ports and the RRN can tell in one glance whether the phone would be able to
    use what it found.
    """
    if not robots:
        return (
            "No robots advertising on this LAN.\n"
            "  - is the advertiser running?  systemctl --user status <name>-discovery\n"
            "  - is this host on the same network as the phone?\n"
            "  - some routers block multicast between wireless clients; the app\n"
            "    falls back to a LAN sweep, and the pairing QR always works."
        )
    lines = [f"{len(robots)} robot(s) advertising:"]
    for robot in sorted(robots, key=lambda r: (r.rrn, r.name)):
        lines.append("")
        lines.append(f"  {robot.name or '(unnamed)'}  [{robot.service_type}]")
        lines.append(f"    rrn           {robot.rrn or '(none — the app will IGNORE this record)'}")
        lines.append(f"    address       {', '.join(robot.addresses) or '(unresolved)'}")
        lines.append(f"    gateway_port  {robot.gateway_port}   (signed /v1/invoke)")
        lines.append(f"    castor_port   {robot.castor_port}   (/health, /api/stop)")
        lines.append(f"    console_port  {robot.console_port}   (/console/health, /surface)")
        lines.append(f"    manifest_path {robot.manifest_path or '(none)'}")
        lines.append(
            f"    estop_url     {robot.estop_url or f'(derived: http://<host>:{robot.castor_port}/api/stop)'}"
        )
        lines.append(f"    record v      {robot.version or '(unversioned)'}")
        if not robot.usable:
            lines.append(
                "    ^ no rrn in the TXT record: the phone cannot match this "
                "robot to stored credentials, so it will not offer it."
            )
    return "\n".join(lines)


def check(timeout: float = 3.0) -> int:
    """Browse for ourselves and print the record. Exit code is the answer."""
    robots = browse(timeout=timeout)
    print(format_found(robots))
    if not robots:
        return 1
    return 0 if any(r.usable for r in robots) else 2


# ---------------------------------------------------------------------------
# The unit's entry point
# ---------------------------------------------------------------------------


def run_forever(record: RobotRecord, *, poll: float = 5.0) -> int:
    """Advertise until told to stop, re-publishing if this host's IP moves.

    Zeroconf binds the address at registration, so a DHCP renewal that moved
    the lease would otherwise leave a confidently-advertised record pointing
    somewhere dead — the same failure as the pinned QR, reintroduced by the fix
    for it.
    """
    advertiser = RobotAdvertiser(record)
    if not advertiser.start():
        # Loud, and a non-zero exit so systemd restarts rather than sitting
        # "active" while publishing nothing — the failure this file exists to
        # end, wearing a green status dot.
        logger.error("could not start advertising %s", record.rrn)
        return 1

    stopping = False

    def _stop(_signum, _frame):  # noqa: ANN001
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    published = advertiser.published_ip
    try:
        while not stopping:
            time.sleep(poll)
            current = lan_ip()
            if current != published:
                logger.warning("address moved %s -> %s, re-advertising", published, current)
                advertiser.stop()
                if advertiser.start():
                    published = advertiser.published_ip
    finally:
        advertiser.stop()
        logger.info("stopped advertising %s", record.rrn)
    return 0


#: Exit code for a condition no restart can fix. The unit pairs it with
#: RestartPreventExitStatus, so a host missing zeroconf shows one failed unit
#: with a readable reason instead of looping every five seconds forever.
EX_UNFIXABLE = 78  # EX_CONFIG


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    argv = list(argv if argv is not None else [])
    if argv and argv[0] == "check":
        return check()
    try:
        import zeroconf  # noqa: F401
    except ImportError:
        logger.error(
            "zeroconf is not installed, so this robot cannot advertise itself and "
            "the app will only find it by QR or subnet sweep. Fix: "
            "`pip install zeroconf` in the environment this unit runs from, then "
            "`systemctl --user restart` it. (It is a dependency of opencastor; a "
            "host without it was installed from something older.)"
        )
        return EX_UNFIXABLE
    try:
        record = record_from_env()
    except ValueError as exc:
        logger.error("%s", exc)
        return 2
    return run_forever(record)


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
